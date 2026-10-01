use crate::calibration::{CalibrationProvenance, PlateDiameterCalibration};
use crate::manual_seed::{
    ManualTargetSeed, PixelBoundingBox, SeedValidationContext,
};
use crate::trajectory::{
    KinematicSample, MetricPositionSample, PixelObservation, TrajectoryValidationError,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fmt;

pub const ANALYSIS_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(try_from = "AnalysisRepr", into = "AnalysisRepr")]
pub struct Analysis {
    schema_version: u32,
    identity: AnalysisIdentity,
    video: VideoMetadata,
    manual_seed: ManualTargetSeed,
    calibration: PlateDiameterCalibration,
    raw_observations: Vec<RawObservation>,
    derived: DerivedData,
    provenance: AnalysisProvenance,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct AnalysisRepr {
    schema_version: u32,
    identity: AnalysisIdentity,
    video: VideoMetadata,
    manual_seed: ManualTargetSeed,
    calibration: PlateDiameterCalibration,
    raw_observations: Vec<RawObservation>,
    derived: DerivedData,
    provenance: AnalysisProvenance,
}

impl Analysis {
    #[allow(clippy::too_many_arguments)]
    pub fn try_new(
        identity: AnalysisIdentity,
        video: VideoMetadata,
        manual_seed: ManualTargetSeed,
        calibration: PlateDiameterCalibration,
        raw_observations: Vec<RawObservation>,
        derived: DerivedData,
        provenance: AnalysisProvenance,
    ) -> Result<Self, AnalysisValidationError> {
        let analysis = Self {
            schema_version: ANALYSIS_SCHEMA_VERSION,
            identity,
            video,
            manual_seed,
            calibration,
            raw_observations,
            derived,
            provenance,
        };
        analysis.validate()?;
        Ok(analysis)
    }

    pub fn validate(&self) -> Result<(), AnalysisValidationError> {
        if self.schema_version != ANALYSIS_SCHEMA_VERSION {
            return Err(invalid(format!(
                "analysis schema version {} is unsupported; expected {}",
                self.schema_version, ANALYSIS_SCHEMA_VERSION
            )));
        }

        self.identity.validate()?;
        self.video.validate()?;
        self.provenance.validate()?;

        let seed_context = SeedValidationContext {
            frame_width_px: self.video.display_width_px,
            frame_height_px: self.video.display_height_px,
            selected_range_start_s: self.video.trim.start_s,
            selected_range_end_s: self.video.trim.end_s,
            source_rotation_deg: self.video.source_rotation_deg,
        };
        self.manual_seed
            .validate(seed_context)
            .map_err(|error| invalid(format!("manual seed: {error}")))?;
        self.calibration
            .validate()
            .map_err(|error| invalid(format!("calibration: {error}")))?;
        self.validate_calibration_context()?;
        self.validate_raw_observations()?;
        self.validate_derived_data()?;

        Ok(())
    }

    pub const fn schema_version(&self) -> u32 {
        self.schema_version
    }

    pub const fn identity(&self) -> &AnalysisIdentity {
        &self.identity
    }

    pub const fn video(&self) -> &VideoMetadata {
        &self.video
    }

    pub const fn manual_seed(&self) -> &ManualTargetSeed {
        &self.manual_seed
    }

    pub const fn calibration(&self) -> &PlateDiameterCalibration {
        &self.calibration
    }

    pub fn raw_observations(&self) -> &[RawObservation] {
        &self.raw_observations
    }

    pub const fn derived(&self) -> &DerivedData {
        &self.derived
    }

    pub const fn provenance(&self) -> &AnalysisProvenance {
        &self.provenance
    }

    pub fn to_json_pretty(&self) -> Result<String, AnalysisJsonError> {
        self.validate().map_err(AnalysisJsonError::Validation)?;
        serde_json::to_string_pretty(self)
            .map_err(|error| AnalysisJsonError::Json(error.to_string()))
    }

    pub fn from_json(input: &str) -> Result<Self, AnalysisJsonError> {
        serde_json::from_str(input).map_err(|error| AnalysisJsonError::Json(error.to_string()))
    }

    fn validate_calibration_context(&self) -> Result<(), AnalysisValidationError> {
        let reference = self.calibration.reference();
        if reference.source_rotation_deg() != self.video.source_rotation_deg {
            return Err(invalid(
                "calibration source rotation does not match video source rotation",
            ));
        }
        if !self.video.trim.contains(reference.timestamp_s()) {
            return Err(invalid(
                "calibration reference timestamp lies outside the selected video range",
            ));
        }

        if matches!(reference.provenance(), CalibrationProvenance::ManualTargetSeed { .. }) {
            let seed = &self.manual_seed;
            if reference.timestamp_s() != seed.timestamp_s()
                || reference.frame_index() != seed.frame_index()
                || reference.source_rotation_deg() != seed.source_rotation_deg()
                || reference.coordinate_space() != seed.coordinate_space()
                || reference.geometry().center_px() != seed.target().center()
                || reference.geometry().bounds_px() != seed.target().bounding_box()
            {
                return Err(invalid(
                    "manual-seed calibration reference does not match the embedded manual seed",
                ));
            }
        }

        Ok(())
    }

    fn validate_raw_observations(&self) -> Result<(), AnalysisValidationError> {
        let mut previous_timestamp = None;
        for (index, observation) in self.raw_observations.iter().enumerate() {
            observation
                .validate()
                .map_err(|error| invalid(format!("raw observation {index}: {error}")))?;

            if observation.tracker_id != self.provenance.tracker.id {
                return Err(invalid(format!(
                    "raw observation {index} references tracker_id '{}' but provenance tracker id is '{}'",
                    observation.tracker_id, self.provenance.tracker.id
                )));
            }
            if !self.video.trim.contains(observation.timestamp_s) {
                return Err(invalid(format!(
                    "raw observation {index} timestamp lies outside the selected video range"
                )));
            }
            if let Some(previous) = previous_timestamp {
                if observation.timestamp_s <= previous {
                    return Err(invalid(
                        "raw observation timestamps must be strictly increasing",
                    ));
                }
            }
            previous_timestamp = Some(observation.timestamp_s);

            if let Some(measurement) = observation.measurement {
                if measurement.x_px < 0.0
                    || measurement.x_px >= f64::from(self.video.display_width_px)
                    || measurement.y_px < 0.0
                    || measurement.y_px >= f64::from(self.video.display_height_px)
                {
                    return Err(invalid(format!(
                        "raw observation {index} measured centre lies outside the display-oriented frame"
                    )));
                }
            }
        }
        Ok(())
    }

    fn validate_derived_data(&self) -> Result<(), AnalysisValidationError> {
        let measured: Vec<PixelObservation> = self
            .raw_observations
            .iter()
            .filter_map(|observation| observation.measurement)
            .collect();
        let calibrated = &self.derived.calibrated.samples;

        if measured.len() != calibrated.len() {
            return Err(invalid(
                "calibrated trajectory must contain exactly one sample for every measured raw observation",
            ));
        }

        validate_metric_series(calibrated, "calibrated")?;
        for (index, (raw, metric)) in measured.iter().zip(calibrated).enumerate() {
            if raw.timestamp_s != metric.timestamp_s {
                return Err(invalid(format!(
                    "calibrated sample {index} timestamp does not match its raw observation"
                )));
            }
            if raw.confidence != metric.confidence {
                return Err(invalid(format!(
                    "calibrated sample {index} must preserve raw measurement confidence"
                )));
            }

            let expected = self
                .calibration
                .calibrate_observation(*raw)
                .map_err(|error| invalid(format!("calibrated sample {index}: {error}")))?;
            if !approximately_equal(expected.x_m, metric.x_m)
                || !approximately_equal(expected.y_m, metric.y_m)
            {
                return Err(invalid(format!(
                    "calibrated sample {index} is inconsistent with the recorded calibration"
                )));
            }
        }

        if let Some(filtered) = &self.derived.filtered {
            filtered.filter.validate("filter")?;
            validate_metric_series(&filtered.samples, "filtered")?;
            validate_aligned_metric_series(calibrated, &filtered.samples, "filtered")?;
        }

        if let Some(kinematics) = &self.derived.kinematics {
            kinematics.method.validate("kinematics.method")?;
            validate_kinematic_series(&kinematics.samples)?;
            let input = match kinematics.input {
                KinematicsInput::Calibrated => calibrated,
                KinematicsInput::Filtered => self
                    .derived
                    .filtered
                    .as_ref()
                    .ok_or_else(|| {
                        invalid("kinematics declares filtered input but no filtered series exists")
                    })?
                    .samples
                    .as_slice(),
            };
            if input.len() != kinematics.samples.len() {
                return Err(invalid(
                    "kinematic series must stay sample-aligned with its declared position input",
                ));
            }
            for (index, (position, sample)) in input.iter().zip(&kinematics.samples).enumerate() {
                if position.timestamp_s != sample.timestamp_s
                    || !approximately_equal(position.x_m, sample.x_m)
                    || !approximately_equal(position.y_m, sample.y_m)
                {
                    return Err(invalid(format!(
                        "kinematic sample {index} does not match its declared position input"
                    )));
                }
                if sample.confidence > position.confidence {
                    return Err(invalid(format!(
                        "kinematic sample {index} confidence exceeds its position input confidence"
                    )));
                }
            }
        }

        Ok(())
    }
}

impl TryFrom<AnalysisRepr> for Analysis {
    type Error = AnalysisValidationError;

    fn try_from(value: AnalysisRepr) -> Result<Self, Self::Error> {
        let analysis = Self {
            schema_version: value.schema_version,
            identity: value.identity,
            video: value.video,
            manual_seed: value.manual_seed,
            calibration: value.calibration,
            raw_observations: value.raw_observations,
            derived: value.derived,
            provenance: value.provenance,
        };
        analysis.validate()?;
        Ok(analysis)
    }
}

impl From<Analysis> for AnalysisRepr {
    fn from(value: Analysis) -> Self {
        Self {
            schema_version: value.schema_version,
            identity: value.identity,
            video: value.video,
            manual_seed: value.manual_seed,
            calibration: value.calibration,
            raw_observations: value.raw_observations,
            derived: value.derived,
            provenance: value.provenance,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AnalysisIdentity {
    pub source_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fixture_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_sha256: Option<String>,
}

impl AnalysisIdentity {
    fn validate(&self) -> Result<(), AnalysisValidationError> {
        validate_non_blank("identity.source_id", &self.source_id)?;
        if let Some(fixture_id) = self.fixture_id.as_deref() {
            validate_identifier("identity.fixture_id", fixture_id)?;
        }
        if let Some(hash) = self.source_sha256.as_deref() {
            validate_sha256("identity.source_sha256", hash)?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TimeRange {
    pub start_s: f64,
    pub end_s: f64,
}

impl TimeRange {
    pub fn contains(self, timestamp_s: f64) -> bool {
        timestamp_s >= self.start_s && timestamp_s <= self.end_s
    }

    fn validate(self) -> Result<(), AnalysisValidationError> {
        if !self.start_s.is_finite() || !self.end_s.is_finite() {
            return Err(invalid("video trim range must be finite"));
        }
        if self.start_s < 0.0 || self.end_s < self.start_s {
            return Err(invalid(
                "video trim range must be non-negative and ordered",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TimestampBasis {
    DecodedPresentationTimestamp,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FrameRateMetadata {
    pub timestamp_basis: TimestampBasis,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub nominal_fps: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub measured_fps: Option<f64>,
}

impl FrameRateMetadata {
    fn validate(self) -> Result<(), AnalysisValidationError> {
        for (name, value) in [
            ("video.frame_rate.nominal_fps", self.nominal_fps),
            ("video.frame_rate.measured_fps", self.measured_fps),
        ] {
            if let Some(value) = value {
                if !value.is_finite() || value <= 0.0 {
                    return Err(invalid(format!("{name} must be finite and positive")));
                }
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VideoMetadata {
    pub decoded_width_px: u32,
    pub decoded_height_px: u32,
    pub display_width_px: u32,
    pub display_height_px: u32,
    pub source_rotation_deg: u16,
    pub frame_rate: FrameRateMetadata,
    pub trim: TimeRange,
}

impl VideoMetadata {
    fn validate(&self) -> Result<(), AnalysisValidationError> {
        if self.decoded_width_px == 0
            || self.decoded_height_px == 0
            || self.display_width_px == 0
            || self.display_height_px == 0
        {
            return Err(invalid("video dimensions must be positive"));
        }
        if !matches!(self.source_rotation_deg, 0 | 90 | 180 | 270) {
            return Err(invalid(
                "video source rotation must be 0, 90, 180, or 270 degrees",
            ));
        }
        self.frame_rate.validate()?;
        self.trim.validate()?;
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TrackingState {
    Tracked,
    LowConfidence,
    Lost,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VisibilityState {
    Visible,
    PartiallyOccluded,
    Occluded,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RawObservation {
    pub timestamp_s: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub frame_index: Option<u64>,
    pub tracking_state: TrackingState,
    pub visibility: VisibilityState,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub measurement: Option<PixelObservation>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_bounds_px: Option<PixelBoundingBox>,
    pub tracker_id: String,
}

impl RawObservation {
    pub fn validate(&self) -> Result<(), AnalysisValidationError> {
        if !self.timestamp_s.is_finite() || self.timestamp_s < 0.0 {
            return Err(invalid(
                "raw observation timestamp must be finite and non-negative",
            ));
        }
        validate_non_blank("raw observation tracker_id", &self.tracker_id)?;

        match (self.tracking_state, self.measurement) {
            (TrackingState::Lost, Some(_)) => {
                return Err(invalid(
                    "lost raw observation must not contain measured coordinates",
                ));
            }
            (TrackingState::Tracked | TrackingState::LowConfidence, None) => {
                return Err(invalid(
                    "tracked/low-confidence raw observation requires measured coordinates",
                ));
            }
            _ => {}
        }

        if let Some(measurement) = self.measurement {
