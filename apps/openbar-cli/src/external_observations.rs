//! Import and validate external tracker-prediction-v1 streams for `analyze`.
//!
//! This is CLI boundary code only. It adapts an externally-produced tracker stream into the
//! canonical raw-observation/provenance types; calibration, filtering and kinematics remain in
//! openbar-core.

use crate::cli_error::{CliError, CliResult};
use crate::sha256::Sha256;
use openbar_core::analysis::{
    Configuration, ImplementationProvenance, ParameterValue, RawObservation, TrackerProvenance,
    TrackingState, VisibilityState,
};
use openbar_core::trajectory::PixelObservation;
use serde::Deserialize;
use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

const PREDICTION_SCHEMA_VERSION: u32 = 1;
const PREDICTION_COORDINATE_SPACE: &str = "decoded_display_pixels";
const PREDICTION_SHA256_PARAMETER: &str = "prediction_sha256";

#[derive(Debug, Clone, Copy)]
pub struct DecodedTimelineFrame {
    pub timestamp_s: f64,
    pub frame_index: u64,
}

#[derive(Debug)]
pub struct ImportedObservations {
    pub raw_observations: Vec<RawObservation>,
    pub tracker_provenance: TrackerProvenance,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct PredictionDocument {
    schema_version: u32,
    fixture_id: String,
    #[serde(default)]
    source_video_sha256: Option<String>,
    coordinate_space: String,
    implementation: PredictionImplementation,
    #[serde(default)]
    runtime: Option<PredictionRuntime>,
    samples: Vec<PredictionSample>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct PredictionImplementation {
    name: String,
    version: String,
    #[serde(default)]
    config: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(deny_unknown_fields)]
struct PredictionRuntime {
    processing_wall_s: f64,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct PredictionSample {
    timestamp_s: f64,
    state: PredictionState,
    #[serde(default)]
    center_px: Option<PredictionPoint>,
    #[serde(default)]
    confidence: Option<f32>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
enum PredictionState {
    Tracked,
    Lost,
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(deny_unknown_fields)]
struct PredictionPoint {
    x_px: f64,
    y_px: f64,
}

#[allow(clippy::too_many_arguments)]
pub fn load_external_observations(
    path: &Path,
    expected_fixture_id: Option<&str>,
    expected_source_video_sha256: &str,
    display_width_px: u32,
    display_height_px: u32,
    decoded_timeline: &[DecodedTimelineFrame],
    seed_timestamp_s: f64,
    timestamp_tolerance_s: f64,
) -> CliResult<ImportedObservations> {
    let bytes = fs::read(path).map_err(|error| {
        CliError::invalid_input(format!(
            "failed to read external observations '{}': {error}",
            path.display()
        ))
    })?;
    let mut hasher = Sha256::new();
    hasher.update(&bytes);
    let prediction_sha256 = hasher.finalize_hex();
    let document: PredictionDocument = serde_json::from_slice(&bytes).map_err(|error| {
        CliError::invalid_input(format!(
            "failed to parse external observations '{}': {error}",
            path.display()
        ))
    })?;

    validate_and_adapt(
        document,
        &prediction_sha256,
        expected_fixture_id,
        expected_source_video_sha256,
        display_width_px,
        display_height_px,
        decoded_timeline,
        seed_timestamp_s,
        timestamp_tolerance_s,
    )
}

#[allow(clippy::too_many_arguments)]
fn validate_and_adapt(
    document: PredictionDocument,
    prediction_sha256: &str,
    expected_fixture_id: Option<&str>,
    expected_source_video_sha256: &str,
    display_width_px: u32,
    display_height_px: u32,
    decoded_timeline: &[DecodedTimelineFrame],
    seed_timestamp_s: f64,
    timestamp_tolerance_s: f64,
) -> CliResult<ImportedObservations> {
    if document.schema_version != PREDICTION_SCHEMA_VERSION {
        return Err(CliError::invalid_input(format!(
            "external observations use unsupported schema_version {}; expected {}",
            document.schema_version, PREDICTION_SCHEMA_VERSION
        )));
    }
    if document.coordinate_space != PREDICTION_COORDINATE_SPACE {
        return Err(CliError::invalid_input(format!(
            "external observations coordinate_space must be '{PREDICTION_COORDINATE_SPACE}', got '{}'",
            document.coordinate_space
        )));
    }
    if !is_valid_identifier(&document.fixture_id) {
        return Err(CliError::invalid_input(format!(
            "external observations fixture_id '{}' must match ^[a-z0-9][a-z0-9._-]*$",
            document.fixture_id
        )));
    }
    if let Some(expected_fixture_id) = expected_fixture_id {
        if document.fixture_id != expected_fixture_id {
            return Err(CliError::invalid_input(format!(
                "external observations reference fixture '{}' instead of '{expected_fixture_id}'",
                document.fixture_id
            )));
        }
    }

    let source_video_sha256 = document.source_video_sha256.as_deref().ok_or_else(|| {
        CliError::invalid_input(
            "external observations must include source_video_sha256 so media identity can be verified",
        )
    })?;
    if !is_sha256(source_video_sha256) {
        return Err(CliError::invalid_input(
            "external observations source_video_sha256 must be exactly 64 hexadecimal characters",
        ));
    }
    if !source_video_sha256.eq_ignore_ascii_case(expected_source_video_sha256) {
        return Err(CliError::invalid_input(format!(
            "external observations source_video_sha256 {source_video_sha256} does not match decoded media {expected_source_video_sha256}"
        )));
    }

    if !is_valid_identifier(&document.implementation.name) {
        return Err(CliError::invalid_input(format!(
            "external tracker identifier '{}' must start with a lowercase ASCII letter or digit and contain only lowercase ASCII letters, digits, '.', '_' or '-'",
            document.implementation.name
        )));
    }
    if document.implementation.version.trim().is_empty() {
        return Err(CliError::invalid_input(
            "external tracker implementation.version must not be blank",
        ));
    }
    if let Some(runtime) = document.runtime {
        if !runtime.processing_wall_s.is_finite() || runtime.processing_wall_s <= 0.0 {
            return Err(CliError::invalid_input(
                "external observations runtime.processing_wall_s must be finite and positive",
            ));
        }
    }
    if !timestamp_tolerance_s.is_finite() || timestamp_tolerance_s < 0.0 {
        return Err(CliError::internal(
            "external-observation timestamp tolerance is invalid",
        ));
    }
    if decoded_timeline.is_empty() {
        return Err(CliError::invalid_input(
            "external observations cannot be matched because the decoded timeline is empty",
        ));
    }

    let first = document.samples.first().ok_or_else(|| {
        CliError::invalid_input(
            "external observations must start with a sample at the manual-seed timestamp",
        )
    })?;
    if !first.timestamp_s.is_finite()
        || (first.timestamp_s - seed_timestamp_s).abs() > timestamp_tolerance_s
    {
        return Err(CliError::invalid_input(format!(
            "external observations must start at seed timestamp {seed_timestamp_s} s within {timestamp_tolerance_s} s"
        )));
    }

    let tracker_id = document.implementation.name.clone();
    let mut raw_observations = Vec::with_capacity(document.samples.len());
    let mut previous_timestamp_s = None;
    let mut previous_frame_index = None;
    for (index, sample) in document.samples.into_iter().enumerate() {
        if !sample.timestamp_s.is_finite() || sample.timestamp_s < 0.0 {
            return Err(CliError::invalid_input(format!(
                "external observation {index} timestamp_s must be finite and non-negative"
            )));
        }
        if previous_timestamp_s.is_some_and(|previous| sample.timestamp_s <= previous) {
            return Err(CliError::invalid_input(format!(
                "external observation {index} timestamp_s {} is not strictly greater than the previous timestamp",
                sample.timestamp_s
            )));
        }
        previous_timestamp_s = Some(sample.timestamp_s);

        let matched_frame = decoded_timeline
            .iter()
            .min_by(|left, right| {
                (left.timestamp_s - sample.timestamp_s)
                    .abs()
                    .total_cmp(&(right.timestamp_s - sample.timestamp_s).abs())
            })
            .expect("decoded timeline was checked non-empty");
        let gap_s = (matched_frame.timestamp_s - sample.timestamp_s).abs();
        if gap_s > timestamp_tolerance_s {
            return Err(CliError::invalid_input(format!(
                "external observation {index} timestamp_s {} has no decoded frame within {timestamp_tolerance_s} s (nearest is {gap_s:.6} s away)",
                sample.timestamp_s
            )));
        }
        if let Some(previous) = previous_frame_index
            && matched_frame.frame_index <= previous
        {
            return Err(CliError::invalid_input(format!(
                "external observation {index} resolves to decoded frame {} after frame {previous}; each sample must map to a distinct, strictly advancing decoded frame",
                matched_frame.frame_index
            )));
        }
        previous_frame_index = Some(matched_frame.frame_index);

        let (tracking_state, measurement) = match sample.state {
            PredictionState::Tracked => {
                let center = sample.center_px.ok_or_else(|| {
                    CliError::invalid_input(format!(
                        "external observation {index} is tracked but has no center_px"
                    ))
                })?;
                let confidence = sample.confidence.ok_or_else(|| {
                    CliError::invalid_input(format!(
                        "external observation {index} is tracked but has no confidence"
                    ))
                })?;
                if !center.x_px.is_finite() || !center.y_px.is_finite() {
                    return Err(CliError::invalid_input(format!(
                        "external observation {index} center_px coordinates must be finite"
                    )));
                }
                if center.x_px < 0.0
                    || center.x_px >= f64::from(display_width_px)
                    || center.y_px < 0.0
                    || center.y_px >= f64::from(display_height_px)
                {
                    return Err(CliError::invalid_input(format!(
                        "external observation {index} center_px lies outside the decoded display window"
                    )));
                }
                if !confidence.is_finite() || !(0.0..=1.0).contains(&confidence) {
                    return Err(CliError::invalid_input(format!(
                        "external observation {index} confidence must be finite and in [0, 1]"
                    )));
                }
                (
                    TrackingState::Tracked,
                    Some(PixelObservation {
                        timestamp_s: sample.timestamp_s,
                        x_px: center.x_px,
                        y_px: center.y_px,
                        confidence,
                    }),
                )
            }
            PredictionState::Lost => {
                if sample.center_px.is_some() || sample.confidence.is_some() {
                    return Err(CliError::invalid_input(format!(
                        "external observation {index} is lost and must not carry center_px or confidence"
                    )));
                }
                (TrackingState::Lost, None)
            }
        };

        raw_observations.push(RawObservation {
            timestamp_s: sample.timestamp_s,
            frame_index: Some(matched_frame.frame_index),
            tracking_state,
            visibility: VisibilityState::Unknown,
            measurement,
            target_bounds_px: None,
            tracker_id: tracker_id.clone(),
        });
    }

    let mut parameters = Configuration::new();
    for (key, value) in document.implementation.config {
        if key.trim().is_empty() {
            return Err(CliError::invalid_input(
                "external tracker config keys must not be blank",
            ));
        }
        if key == PREDICTION_SHA256_PARAMETER {
            return Err(CliError::invalid_input(format!(
                "external tracker config key '{PREDICTION_SHA256_PARAMETER}' is reserved for OpenBar input provenance"
            )));
        }
        parameters.insert(key, parameter_value(value)?);
    }
    parameters.insert(
        PREDICTION_SHA256_PARAMETER.to_owned(),
        ParameterValue::Text(prediction_sha256.to_owned()),
    );

    Ok(ImportedObservations {
        raw_observations,
        tracker_provenance: TrackerProvenance {
            id: tracker_id.clone(),
            implementation: ImplementationProvenance {
                implementation: tracker_id,
                version: document.implementation.version,
                parameters,
            },
        },
    })
}

fn parameter_value(value: serde_json::Value) -> CliResult<ParameterValue> {
    match value {
        serde_json::Value::Bool(value) => Ok(ParameterValue::Boolean(value)),
        serde_json::Value::Number(value) => {
            if let Some(integer) = value.as_i64() {
                Ok(ParameterValue::Integer(integer))
            } else if let Some(integer) = value.as_u64() {
                // analysis-v1 has an i64 integer parameter type. Preserve larger JSON integers
                // exactly as decimal text rather than silently rounding them through f64.
                Ok(ParameterValue::Text(integer.to_string()))
            } else if let Some(float) = value.as_f64() {
                if float.is_finite() {
                    Ok(ParameterValue::Float(float))
                } else {
                    Err(CliError::invalid_input(
                        "external tracker config contains a non-finite number",
                    ))
                }
            } else {
                Ok(ParameterValue::Text(value.to_string()))
            }
        }
        serde_json::Value::String(value) => Ok(ParameterValue::Text(value)),
        other => serde_json::to_string(&other)
            .map(ParameterValue::Text)
            .map_err(|error| {
                CliError::invalid_input(format!(
                    "failed to preserve external tracker config value: {error}"
                ))
            }),
    }
}

fn is_valid_identifier(value: &str) -> bool {
    let mut bytes = value.bytes();
    bytes
        .next()
        .is_some_and(|first| first.is_ascii_lowercase() || first.is_ascii_digit())
        && bytes.all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'.' | b'_' | b'-')
        })
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

#[cfg(test)]
mod tests {
    use super::*;

    const SOURCE_SHA256: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    fn timeline() -> Vec<DecodedTimelineFrame> {
        vec![
            DecodedTimelineFrame {
                timestamp_s: 0.0,
                frame_index: 10,
            },
            DecodedTimelineFrame {
                timestamp_s: 0.1,
                frame_index: 11,
            },
            DecodedTimelineFrame {
                timestamp_s: 0.2,
                frame_index: 12,
            },
        ]
    }

    fn valid_document() -> PredictionDocument {
        PredictionDocument {
            schema_version: 1,
            fixture_id: "fixture-1".to_owned(),
            source_video_sha256: Some(SOURCE_SHA256.to_owned()),
            coordinate_space: "decoded_display_pixels".to_owned(),
            implementation: PredictionImplementation {
                name: "external-tracker".to_owned(),
                version: "1".to_owned(),
                config: BTreeMap::from([
                    ("threads".to_owned(), serde_json::json!(1)),
                    (
                        "refinement".to_owned(),
                        serde_json::json!({"method": "circle", "radius": [0.8, 1.2]}),
                    ),
                ]),
            },
            runtime: Some(PredictionRuntime {
                processing_wall_s: 0.1,
            }),
            samples: vec![
                PredictionSample {
                    timestamp_s: 0.0,
                    state: PredictionState::Tracked,
                    center_px: Some(PredictionPoint {
                        x_px: 10.0,
                        y_px: 20.0,
                    }),
                    confidence: Some(0.9),
                },
                PredictionSample {
                    timestamp_s: 0.1,
                    state: PredictionState::Lost,
                    center_px: None,
                    confidence: None,
                },
                PredictionSample {
                    timestamp_s: 0.2,
                    state: PredictionState::Tracked,
                    center_px: Some(PredictionPoint {
                        x_px: 12.0,
                        y_px: 18.0,
                    }),
                    confidence: Some(0.8),
                },
            ],
        }
    }

    fn validate(document: PredictionDocument) -> CliResult<ImportedObservations> {
        validate_and_adapt(
            document,
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            Some("fixture-1"),
            SOURCE_SHA256,
            100,
            80,
            &timeline(),
            0.0,
            0.0005,
        )
    }

    #[test]
    fn valid_stream_preserves_samples_and_external_provenance() {
        let imported = validate(valid_document()).expect("valid prediction imports");
        assert_eq!(imported.raw_observations.len(), 3);
        assert_eq!(imported.raw_observations[0].timestamp_s, 0.0);
        assert_eq!(imported.raw_observations[0].frame_index, Some(10));
        assert_eq!(
            imported.raw_observations[0]
                .measurement
                .expect("tracked measurement"),
            PixelObservation {
                timestamp_s: 0.0,
                x_px: 10.0,
                y_px: 20.0,
                confidence: 0.9,
            }
        );
        assert_eq!(
            imported.raw_observations[1].tracking_state,
            TrackingState::Lost
        );
        assert!(imported.raw_observations[1].measurement.is_none());
        assert_eq!(imported.tracker_provenance.id, "external-tracker");
        assert_eq!(
            imported
                .tracker_provenance
                .implementation
                .parameters
                .get(PREDICTION_SHA256_PARAMETER),
            Some(&ParameterValue::Text(
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb".to_owned()
            ))
        );
        assert_eq!(
            imported
                .tracker_provenance
                .implementation
                .parameters
                .get("refinement"),
            Some(&ParameterValue::Text(
                "{\"method\":\"circle\",\"radius\":[0.8,1.2]}".to_owned()
            ))
        );
    }

    #[test]
    fn wrong_video_hash_is_rejected() {
        let mut document = valid_document();
        document.source_video_sha256 =
            Some("cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc".to_owned());
        let error = validate(document).expect_err("wrong hash must fail");
        assert!(error.to_string().contains("does not match decoded media"));
    }

    #[test]
    fn timestamp_missing_from_decoded_timeline_is_rejected() {
        let mut document = valid_document();
        document.samples[1].timestamp_s = 0.15;
        let error = validate(document).expect_err("unmatched timestamp must fail");
        assert!(error.to_string().contains("has no decoded frame"));
    }

    #[test]
    fn non_monotonic_timestamp_is_rejected() {
        let mut document = valid_document();
        document.samples[1].timestamp_s = 0.0;
        let error = validate(document).expect_err("non-monotonic timestamps must fail");
        assert!(error.to_string().contains("not strictly greater"));
    }

    #[test]
    fn multiple_samples_for_same_decoded_frame_are_rejected() {
        let mut document = valid_document();
        document.samples[1].timestamp_s = 0.0004;
        let error = validate(document).expect_err("one decoded frame cannot back two samples");
        assert!(error
            .to_string()
            .contains("distinct, strictly advancing decoded frame"));
    }

    #[test]
    fn stream_without_seed_sample_is_rejected() {
        let mut document = valid_document();
        document.samples.remove(0);
        let error = validate(document).expect_err("missing seed sample must fail");
        assert!(error.to_string().contains("start at seed timestamp"));
    }

    #[test]
    fn non_finite_coordinate_is_rejected() {
        let mut document = valid_document();
        document.samples[0].center_px.as_mut().expect("center").x_px = f64::NAN;
        let error = validate(document).expect_err("NaN coordinate must fail");
        assert!(error.to_string().contains("coordinates must be finite"));
    }

    #[test]
    fn confidence_outside_unit_interval_is_rejected() {
        let mut document = valid_document();
        document.samples[0].confidence = Some(1.01);
        let error = validate(document).expect_err("confidence > 1 must fail");
        assert!(error
            .to_string()
            .contains("confidence must be finite and in [0, 1]"));
    }

    #[test]
    fn lost_sample_with_center_is_rejected() {
        let mut document = valid_document();
        document.samples[1].center_px = Some(PredictionPoint {
            x_px: 10.0,
            y_px: 10.0,
        });
        let error = validate(document).expect_err("lost sample with center must fail");
        assert!(error
            .to_string()
            .contains("must not carry center_px or confidence"));
    }

    #[test]
    fn unknown_prediction_schema_version_is_rejected() {
        let mut document = valid_document();
        document.schema_version = 2;
        let error = validate(document).expect_err("unknown schema must fail");
        assert!(error.to_string().contains("unsupported schema_version"));
    }

    #[test]
    fn invalid_tracker_identifier_is_rejected_instead_of_rewritten() {
        let mut document = valid_document();
        document.implementation.name = "opencv-csrt+lk".to_owned();
        let error = validate(document).expect_err("invalid id must fail");
        assert!(error.to_string().contains("external tracker identifier"));
        assert!(error.to_string().contains("opencv-csrt+lk"));
    }

    #[test]
    fn large_unsigned_config_integer_is_preserved_without_f64_rounding() {
        let mut document = valid_document();
        document
            .implementation
            .config
            .insert("large_counter".to_owned(), serde_json::json!(u64::MAX));
        let imported = validate(document).expect("large unsigned integer remains valid provenance");
        assert_eq!(
            imported
                .tracker_provenance
                .implementation
                .parameters
                .get("large_counter"),
            Some(&ParameterValue::Text(u64::MAX.to_string()))
        );
    }

    #[test]
    fn blank_config_key_is_rejected_before_canonical_analysis() {
        let mut document = valid_document();
        document
            .implementation
            .config
            .insert("   ".to_owned(), serde_json::json!("value"));
        let error = validate(document).expect_err("blank config key must fail");
        assert!(error.to_string().contains("config keys must not be blank"));
    }
}
