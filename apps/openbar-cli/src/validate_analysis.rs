//! Read-only canonical validation for research consumers; no measurement pipeline runs.
use crate::analyze::SEED_TIMESTAMP_TOLERANCE_S;
use crate::cli_error::{CliError, CliResult};
use crate::external_observations::{match_timeline_frame, DecodedTimelineFrame};
use crate::media::{MediaError, ProbedVideo};
use openbar_core::analysis::Analysis;
use openbar_core::manual_seed::{ManualTargetSeedDocument, MANUAL_TARGET_SEED_SCHEMA_VERSION};
use std::{env, fs, path::Path};

const USAGE: &str = "Usage: openbar-cli validate-analysis --analysis <analysis.json> [--video <source-video>] [--seed <seed.json>]\n\
Validates canonical semantics; with --video, verifies source hash, geometry, seed and all raw PTS/frame identities.\n\
Probes the source timeline without decoding pixels or establishing physical accuracy.";

pub fn run_cli() -> CliResult<()> {
    let args: Vec<String> = env::args().skip(2).collect();
    if args == ["--help"] || args == ["-h"] {
        println!("{USAGE}");
        return Ok(());
    }
    let mut analysis_path = None;
    let mut video_path = None;
    let mut seed_path = None;
    for pair in args.chunks(2) {
        if pair.len() != 2 || pair[1].starts_with("--") {
            return Err(CliError::invalid_input(USAGE));
        }
        let slot = match pair[0].as_str() {
            "--analysis" => &mut analysis_path,
            "--video" => &mut video_path,
            "--seed" => &mut seed_path,
            _ => return Err(CliError::invalid_input(USAGE)),
        };
        if slot.replace(pair[1].as_str()).is_some() {
            return Err(CliError::invalid_input("duplicate validation argument"));
        }
    }
    let path = analysis_path.ok_or_else(|| CliError::invalid_input(USAGE))?;
    let json = fs::read_to_string(path)
        .map_err(|error| CliError::invalid_input(format!("cannot read analysis: {error}")))?;
    let analysis = Analysis::from_json(&json)
        .map_err(|error| CliError::invalid_input(format!("invalid canonical analysis: {error}")))?;
    if let Some(path) = seed_path {
        let json = fs::read_to_string(path)
            .map_err(|error| CliError::invalid_input(format!("cannot read seed: {error}")))?;
        validate_seed(&analysis, &json)?;
    }
    if let Some(path) = video_path {
        let source = verified_source(&analysis, Path::new(path))?;
        let timeline: Vec<_> = source
            .timestamps_s()
            .iter()
            .enumerate()
            .map(|(index, &timestamp_s)| DecodedTimelineFrame {
                timestamp_s,
                frame_index: index as u64,
            })
            .collect();
        validate_timeline(&analysis, &timeline)?;
    }
    println!("valid");
    Ok(())
}

pub(crate) fn verified_source(analysis: &Analysis, path: &Path) -> CliResult<ProbedVideo> {
    let expected_hash = analysis
        .identity()
        .source_sha256
        .as_deref()
        .ok_or_else(|| CliError::invalid_input("canonical source hash is missing"))?;
    let source = ProbedVideo::open(path).map_err(classify_probe_error)?;
    if !expected_hash.eq_ignore_ascii_case(source.source_sha256()) {
        return Err(CliError::invalid_input(
            "source hash does not match canonical analysis",
        ));
    }
    let stream = source.stream();
    let video = analysis.video();
    if (
        stream.coded_width_px,
        stream.coded_height_px,
        stream.display_width_px,
        stream.display_height_px,
        stream.rotation_deg,
    ) != (
        video.decoded_width_px,
        video.decoded_height_px,
        video.display_width_px,
        video.display_height_px,
        video.source_rotation_deg,
    ) {
        return Err(CliError::invalid_input(
            "source geometry/rotation does not match canonical analysis",
        ));
    }
    Ok(source)
}

fn classify_probe_error(error: MediaError) -> CliError {
    match error {
        MediaError::UnsupportedRotation { .. }
        | MediaError::UnsupportedSampleAspectRatio { .. } => {
            CliError::unsupported(error.to_string())
        }
        MediaError::NoVideoStream
        | MediaError::MissingStreamField { .. }
        | MediaError::InvalidTimeBase { .. }
        | MediaError::InvalidDimensions { .. }
        | MediaError::NoFrames
        | MediaError::MissingPts { .. }
        | MediaError::NonIncreasingPts { .. }
        | MediaError::PtsBeforeMediaStart { .. } => CliError::invalid_input(error.to_string()),
        other => CliError::media(other.to_string()),
    }
}

fn validate_seed(analysis: &Analysis, json: &str) -> CliResult<()> {
    let seed: ManualTargetSeedDocument = serde_json::from_str(json)
        .map_err(|error| CliError::invalid_input(format!("invalid seed: {error}")))?;
    if seed.schema_version() != MANUAL_TARGET_SEED_SCHEMA_VERSION
        || seed.fixture_id() != analysis.identity().fixture_id.as_deref()
        || seed.seed() != analysis.manual_seed()
    {
        return Err(CliError::invalid_input(
            "canonical seed does not match retained seed",
        ));
    }
    Ok(())
}

fn validate_timeline(analysis: &Analysis, timeline: &[DecodedTimelineFrame]) -> CliResult<()> {
    let seed = analysis.manual_seed();
    let frame = match_timeline_frame(timeline, seed.timestamp_s(), SEED_TIMESTAMP_TOLERANCE_S, 0)?;
    if seed
        .frame_index()
        .is_some_and(|index| index != frame.frame_index)
    {
        return Err(CliError::invalid_input(
            "seed frame index does not match source PTS",
        ));
    }
    let mut previous = None;
    for (index, sample) in analysis.raw_observations().iter().enumerate() {
        let frame = match_timeline_frame(
            timeline,
            sample.timestamp_s,
            SEED_TIMESTAMP_TOLERANCE_S,
            index,
        )?;
        if previous.is_some_and(|previous| frame.frame_index <= previous)
            || sample
                .frame_index
                .is_some_and(|index| index != frame.frame_index)
        {
            return Err(CliError::invalid_input(format!(
                "raw observation {index} has inconsistent source frame identity"
            )));
        }
        previous = Some(frame.frame_index);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn analysis() -> Analysis {
        Analysis::from_json(include_str!(
            "../../../crates/openbar-core/tests/fixtures/analysis-v1.golden.json"
        ))
        .unwrap()
    }

    #[test]
    fn seed_comparison_uses_canonical_confidence_precision() {
        let mut value: serde_json::Value =
            serde_json::from_str(&analysis().to_json_pretty().unwrap()).unwrap();
        value["manual_seed"]["selection_confidence"] = serde_json::json!(0.123456789);
        let analysis = Analysis::from_json(&value.to_string()).unwrap();
        let mut seed = serde_json::json!({"schema_version": 1, "fixture_id": value["identity"]["fixture_id"], "seed": value["manual_seed"]});
        validate_seed(&analysis, &seed.to_string()).unwrap();
        seed["seed"]["selection_confidence"] = serde_json::json!(0.9);
        assert!(validate_seed(&analysis, &seed.to_string()).is_err());
    }

    #[test]
    fn confirmed_invalid_pts_is_distinct_from_missing_probe_tools() {
        assert_eq!(
            classify_probe_error(MediaError::MissingPts { index: 1 }).exit_code(),
            2
        );
        assert_eq!(
            classify_probe_error(MediaError::NonIncreasingPts {
                index: 1,
                previous: 1,
                current: 1
            })
            .exit_code(),
            2
        );
        assert_eq!(
            classify_probe_error(MediaError::ToolUnavailable {
                tool: "ffprobe",
                detail: "absent".into()
            })
            .exit_code(),
            3
        );
        assert_eq!(
            classify_probe_error(MediaError::UnsupportedRotation { value: 45.0 }).exit_code(),
            4
        );
    }

    #[test]
    fn verifies_all_pts_and_optional_indices_with_import_tolerance() {
        let analysis = analysis();
        let mut timeline: Vec<_> = analysis
            .raw_observations()
            .iter()
            .map(|sample| DecodedTimelineFrame {
                timestamp_s: sample.timestamp_s + 0.0001,
                frame_index: sample.frame_index.unwrap(),
            })
            .collect();
        validate_timeline(&analysis, &timeline).unwrap();
        timeline.last_mut().unwrap().timestamp_s += 0.01;
        assert!(validate_timeline(&analysis, &timeline).is_err());
        assert!(validate_timeline(&analysis, &[]).is_err());
    }

    #[test]
    fn rejects_mismatched_seed_and_reused_frame_identity() {
        let analysis = analysis();
        let mut timeline: Vec<_> = analysis
            .raw_observations()
            .iter()
            .map(|sample| DecodedTimelineFrame {
                timestamp_s: sample.timestamp_s,
                frame_index: sample.frame_index.unwrap(),
            })
            .collect();
        timeline[0].frame_index += 1;
        assert!(validate_timeline(&analysis, &timeline).is_err());
        timeline[0].frame_index -= 1;
        timeline[1].frame_index = timeline[0].frame_index;
        assert!(validate_timeline(&analysis, &timeline).is_err());
    }
}
