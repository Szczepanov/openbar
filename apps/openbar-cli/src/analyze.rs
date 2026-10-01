//! End-to-end M0 analysis command (issue #12).
//!
//! Media/process orchestration lives here; authoritative calibration, filtering, kinematics,
//! canonical validation and serialization remain in openbar-core.

use crate::cli_error::{CliError, CliErrorKind, CliResult};
use crate::media::{
    DecodedClip, FrameSourceOptions, MediaError, ProbedVideo, StreamProvenance,
    TimeRange as MediaTimeRange,
};
use openbar_core::analysis::{
    Analysis, AnalysisIdentity, AnalysisProvenance, CalibratedTrajectory, Configuration,
    DerivedData, EnvironmentProvenance, FrameRateMetadata, ImplementationProvenance,
    KinematicsInput, ParameterValue, PipelineProvenance, RawObservation, TimeRange, TimestampBasis,
    TrackerProvenance, TrackingState, VideoMetadata, VisibilityState,
};
use openbar_core::calibration::{CalibrationQuality, PlateDiameterCalibration};
use openbar_core::filtering::{apply_filter, FilterConfig};
use openbar_core::kinematics::{derive_kinematic_trajectory, KinematicsConfig};
use openbar_core::manual_seed::{ManualTargetSeedDocument, SeedValidationContext};
use openbar_core::trajectory::{MetricPositionSample, PixelObservation};
use openbar_tracking::{
    LocalContrastConfig, LocalContrastTracker, ManualSeedTracker, TemplateMatchConfig,
    TemplateMatchTracker, TrackerIdentity, TrackerObservationState, TrackerRun,
};
use serde::Deserialize;
use std::collections::BTreeMap;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

const DEFAULT_MAX_FRAME_MEMORY_MIB: u64 = 2048;
const SEED_TIMESTAMP_TOLERANCE_S: f64 = 0.0005;

const USAGE: &str = "Usage: openbar-cli analyze (--video <path> | --manifest <path> --fixture <id> [--video <override>]) --seed <path>\n\
     \x20      --plate-diameter-m <m> --tracker <template|contrast> --filter <raw|moving-average|savitzky-golay|kalman>\n\
     \x20      --kinematics-max-gap-s <s> --kinematics-min-confidence <0..1> --output <analysis.json>\n\
     \x20      [--start-s <s> --end-s <s>] [--max-frame-memory-mib <MiB>] [--tracker-search-radius-px <px>]\n\
     \x20      [tracker-specific options] [filter-specific options] [--diagnostics <quiet|normal|verbose>] [--force]\n\
\n\
Tracker options:\n\
  template: [--template-low-confidence-nmad <0..1>] [--template-max-nmad <0..1>]\n\
  contrast: [--contrast-min-seed-contrast <value>] [--contrast-min-mass-ratio <0..1>]\n\
            [--contrast-low-confidence-mass-ratio <0..1>]\n\
\n\
Filter options (there is deliberately no production default):\n\
  raw: no additional parameters\n\
  moving-average: --filter-window <odd> --filter-max-gap-s <s>\n\
  savitzky-golay: --filter-window <odd> --filter-polynomial-order <n> --filter-max-gap-s <s>\n\
  kalman: --filter-acceleration-variance-m2-s4 <v> --filter-measurement-variance-m2 <v>\n\
          --filter-initial-velocity-variance-m2-s2 <v> --filter-confidence-window-samples <n>\n\
          --filter-max-gap-s <s>\n\
\n\
Machine-readable analysis is written only to --output. Diagnostics are written to stderr.";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TrackerChoice {
    Template,
    Contrast,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum DiagnosticsVerbosity {
    Quiet,
    Normal,
    Verbose,
}

#[derive(Debug)]
struct Args {
    video: Option<PathBuf>,
    manifest: Option<PathBuf>,
    fixture_id: Option<String>,
    seed: PathBuf,
    plate_diameter_m: f64,
    tracker: TrackerChoice,
    tracker_search_radius_px: Option<u32>,
    template_low_confidence_nmad: Option<f64>,
    template_max_nmad: Option<f64>,
    contrast_min_seed_contrast: Option<f64>,
    contrast_min_mass_ratio: Option<f64>,
    contrast_low_confidence_mass_ratio: Option<f64>,
    filter: FilterConfig,
    kinematics: KinematicsConfig,
    selection: Option<MediaTimeRange>,
    max_frame_bytes: u64,
    output: PathBuf,
    diagnostics: DiagnosticsVerbosity,
    force: bool,
}

#[derive(Debug, Deserialize)]
struct FixtureManifest {
    schema_version: u32,
    fixtures: Vec<ManifestFixture>,
}

#[derive(Debug, Clone, Deserialize)]
struct ManifestFixture {
    id: String,
    #[serde(default)]
    media: Option<ManifestMedia>,
    video: ManifestVideo,
}

#[derive(Debug, Clone, Deserialize)]
struct ManifestMedia {
    #[serde(default)]
    repository_path: Option<String>,
    #[serde(default)]
    sha256: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct ManifestVideo {
    width_px: u32,
    height_px: u32,
    #[serde(default)]
    rotation_deg: Option<u16>,
}

pub fn run_cli() -> CliResult<()> {
    let Some(args) = parse_args(env::args().skip(2).collect())? else {
        println!("{USAGE}");
        return Ok(());
    };
    run(&args)
}

fn run(args: &Args) -> CliResult<()> {
    let fixture = match (&args.manifest, args.fixture_id.as_deref()) {
        (Some(path), Some(fixture_id)) => Some(load_fixture(path, fixture_id)?),
        _ => None,
    };
    let media_path = resolve_media_path(args, fixture.as_ref())?;
    let seed = read_seed(&args.seed, args.fixture_id.as_deref())?;

    let probed = ProbedVideo::open(&media_path).map_err(classify_media_error)?;
    let stream = probed.stream();
    let source_sha256 = probed.source_sha256().to_owned();
    if let Some(fixture) = fixture.as_ref() {
        check_fixture_matches_media(fixture, &source_sha256, &stream)?;
    }

    let clip = probed
        .decode(FrameSourceOptions {
            selection: args.selection,
            max_frame_bytes: args.max_frame_bytes,
        })
        .map_err(classify_media_error)?;

    validate_seed(&seed, &stream, args.selection, &clip)?;
    let tracker = build_tracker(args)?;
    let tracker_run = tracker
        .track(&clip.frame_samples(), seed.seed())
        .map_err(|error| CliError::tracking(format!("selected tracker failed: {error}")))?;

    let calibration = PlateDiameterCalibration::try_from_manual_seed(
        args.plate_diameter_m,
        seed.seed(),
        CalibrationQuality::unassessed(),
    )
    .map_err(|error| CliError::seed_calibration(format!("invalid plate calibration: {error}")))?;

    let raw_observations = canonical_raw_observations(&tracker_run)?;
    let calibrated_samples = calibrate_measurements(&raw_observations, &calibration)?;
    let filter_run = apply_filter(&calibrated_samples, args.filter).map_err(|error| {
        CliError::invalid_input(format!("invalid filter configuration/input: {error}"))
    })?;
    let kinematic = derive_kinematic_trajectory(
        &filter_run.trajectory.samples,
        KinematicsInput::Filtered,
        args.kinematics,
    )
    .map_err(|error| {
        CliError::invalid_input(format!("invalid kinematics configuration/input: {error}"))
    })?;

    let analysis = Analysis::try_new(
        AnalysisIdentity {
            source_id: fixture.as_ref().map_or_else(
                || format!("sha256:{source_sha256}"),
                |fixture| fixture.id.clone(),
            ),
            fixture_id: fixture.as_ref().map(|fixture| fixture.id.clone()),
            source_sha256: Some(source_sha256),
        },
        video_metadata(&clip, &stream, seed.seed().timestamp_s(), args.selection),
        seed.seed().clone(),
        calibration,
        raw_observations,
        DerivedData {
            calibrated: CalibratedTrajectory {
                samples: calibrated_samples,
            },
            filtered: Some(filter_run.trajectory),
            kinematics: Some(kinematic),
        },
        analysis_provenance(&tracker_run.tracker, &clip)?,
    )
    .map_err(|error| {
        CliError::internal(format!(
            "pipeline produced an invalid canonical analysis; this is an OpenBar bug: {error}"
        ))
    })?;

    write_analysis(&args.output, &analysis, args.force)?;
    emit_diagnostics(args, &analysis, &clip);
    Ok(())
}

fn parse_args(args: Vec<String>) -> CliResult<Option<Args>> {
    if args
        .iter()
        .any(|value| matches!(value.as_str(), "--help" | "-h"))
    {
        return Ok(None);
    }

    let (mut values, force) = collect_flag_values(&args)?;

    let video = take_path(&mut values, "--video");
    let manifest = take_path(&mut values, "--manifest");
    let fixture_id = values.remove("--fixture");
    validate_source_selection(video.as_ref(), manifest.as_ref(), fixture_id.as_deref())?;

    let seed = PathBuf::from(required(&mut values, "--seed")?);
    let output = PathBuf::from(required(&mut values, "--output")?);
    let plate_diameter_m = parse_f64(
        &required(&mut values, "--plate-diameter-m")?,
        "--plate-diameter-m",
    )?;
    if !plate_diameter_m.is_finite() || plate_diameter_m <= 0.0 {
        return Err(CliError::invalid_input(
            "--plate-diameter-m must be finite and positive",
        ));
    }

    let tracker = match required(&mut values, "--tracker")?.as_str() {
        "template" => TrackerChoice::Template,
        "contrast" => TrackerChoice::Contrast,
        value => {
            return Err(CliError::invalid_input(format!(
                "--tracker must be 'template' or 'contrast', got '{value}'"
            )));
        }
    };
    let tracker_search_radius_px = take_parsed(&mut values, "--tracker-search-radius-px")?;
    let template_low_confidence_nmad = take_parsed(&mut values, "--template-low-confidence-nmad")?;
    let template_max_nmad = take_parsed(&mut values, "--template-max-nmad")?;
    let contrast_min_seed_contrast = take_parsed(&mut values, "--contrast-min-seed-contrast")?;
    let contrast_min_mass_ratio = take_parsed(&mut values, "--contrast-min-mass-ratio")?;
    let contrast_low_confidence_mass_ratio =
        take_parsed(&mut values, "--contrast-low-confidence-mass-ratio")?;
    validate_tracker_specific_options(
        tracker,
        template_low_confidence_nmad,
        template_max_nmad,
        contrast_min_seed_contrast,
        contrast_min_mass_ratio,
        contrast_low_confidence_mass_ratio,
    )?;

    let filter_name = required(&mut values, "--filter")?;
    let filter = parse_filter_config(&filter_name, &mut values)?;

    let kinematics = KinematicsConfig::try_new(
        parse_f64(
            &required(&mut values, "--kinematics-max-gap-s")?,
            "--kinematics-max-gap-s",
        )?,
        parse_f32(
            &required(&mut values, "--kinematics-min-confidence")?,
            "--kinematics-min-confidence",
        )?,
    )
    .map_err(|error| CliError::invalid_input(format!("invalid kinematics config: {error}")))?;

    let selection = match (values.remove("--start-s"), values.remove("--end-s")) {
        (None, None) => None,
        (Some(start), Some(end)) => Some(
            MediaTimeRange::try_new(parse_f64(&start, "--start-s")?, parse_f64(&end, "--end-s")?)
                .map_err(classify_media_error)?,
        ),
        _ => {
            return Err(CliError::invalid_input(
                "--start-s and --end-s must be provided together",
            ));
        }
    };

    let max_frame_memory_mib = values
        .remove("--max-frame-memory-mib")
        .map(|value| parse_u64(&value, "--max-frame-memory-mib"))
        .transpose()?
        .unwrap_or(DEFAULT_MAX_FRAME_MEMORY_MIB);
    if max_frame_memory_mib == 0 {
        return Err(CliError::invalid_input(
            "--max-frame-memory-mib must be greater than zero",
        ));
    }
    let max_frame_bytes = max_frame_memory_mib
        .checked_mul(1024 * 1024)
        .ok_or_else(|| CliError::invalid_input("--max-frame-memory-mib is too large"))?;

    let diagnostics = match values
        .remove("--diagnostics")
        .unwrap_or_else(|| "normal".to_owned())
        .as_str()
    {
        "quiet" => DiagnosticsVerbosity::Quiet,
        "normal" => DiagnosticsVerbosity::Normal,
        "verbose" => DiagnosticsVerbosity::Verbose,
        value => {
            return Err(CliError::invalid_input(format!(
                "--diagnostics must be quiet, normal, or verbose, got '{value}'"
            )));
        }
    };

    if let Some((flag, _)) = values.first_key_value() {
        return Err(CliError::invalid_input(format!(
            "unconsumed analyze option '{flag}'"
        )));
    }

    Ok(Some(Args {
        video,
        manifest,
        fixture_id,
        seed,
        plate_diameter_m,
        tracker,
        tracker_search_radius_px,
        template_low_confidence_nmad,
        template_max_nmad,
        contrast_min_seed_contrast,
        contrast_min_mass_ratio,
        contrast_low_confidence_mass_ratio,
        filter,
        kinematics,
        selection,
        max_frame_bytes,
        output,
        diagnostics,
        force,
    }))
}

fn collect_flag_values(args: &[String]) -> CliResult<(BTreeMap<String, String>, bool)> {
    let mut values = BTreeMap::new();
    let mut force = false;
    let mut index = 0usize;
    while index < args.len() {
        let flag = args[index].as_str();
        if flag == "--force" {
            if force {
                return Err(CliError::invalid_input("--force was given more than once"));
            }
            force = true;
            index += 1;
            continue;
        }
        if !is_known_value_flag(flag) {
            return Err(CliError::invalid_input(format!(
                "unknown analyze argument '{flag}'"
            )));
        }
        let value = args
            .get(index + 1)
            .ok_or_else(|| CliError::invalid_input(format!("{flag} requires a value")))?;
        if values.insert(flag.to_owned(), value.clone()).is_some() {
            return Err(CliError::invalid_input(format!(
                "{flag} was given more than once"
            )));
        }
        index += 2;
    }
    Ok((values, force))
}

fn is_known_value_flag(flag: &str) -> bool {
    matches!(
        flag,
        "--video"
            | "--manifest"
            | "--fixture"
            | "--seed"
            | "--plate-diameter-m"
            | "--tracker"
            | "--tracker-search-radius-px"
            | "--template-low-confidence-nmad"
            | "--template-max-nmad"
            | "--contrast-min-seed-contrast"
            | "--contrast-min-mass-ratio"
            | "--contrast-low-confidence-mass-ratio"
            | "--filter"
            | "--filter-window"
            | "--filter-polynomial-order"
            | "--filter-max-gap-s"
            | "--filter-acceleration-variance-m2-s4"
            | "--filter-measurement-variance-m2"
            | "--filter-initial-velocity-variance-m2-s2"
            | "--filter-confidence-window-samples"
            | "--kinematics-max-gap-s"
            | "--kinematics-min-confidence"
            | "--start-s"
            | "--end-s"
            | "--max-frame-memory-mib"
            | "--output"
            | "--diagnostics"
    )
}

fn validate_source_selection(
    video: Option<&PathBuf>,
    manifest: Option<&PathBuf>,
    fixture_id: Option<&str>,
) -> CliResult<()> {
    match (manifest, fixture_id, video) {
        (Some(_), Some(_), _) | (None, None, Some(_)) => Ok(()),
        (None, None, None) => Err(CliError::invalid_input(
            "analyze requires --video <path>, or --manifest <path> with --fixture <id>",
        )),
        _ => Err(CliError::invalid_input(
            "--manifest and --fixture must be provided together; --video may optionally override fixture media",
        )),
    }
}

fn validate_tracker_specific_options(
    tracker: TrackerChoice,
    template_low: Option<f64>,
    template_max: Option<f64>,
    contrast_seed: Option<f64>,
    contrast_min: Option<f64>,
    contrast_low: Option<f64>,
) -> CliResult<()> {
    match tracker {
        TrackerChoice::Template
            if contrast_seed.is_some() || contrast_min.is_some() || contrast_low.is_some() =>
        {
            Err(CliError::invalid_input(
                "contrast-specific options cannot be used with --tracker template",
            ))
        }
        TrackerChoice::Contrast if template_low.is_some() || template_max.is_some() => {
            Err(CliError::invalid_input(
                "template-specific options cannot be used with --tracker contrast",
            ))
        }
        _ => Ok(()),
    }
}

fn parse_filter_config(
    filter_name: &str,
    values: &mut BTreeMap<String, String>,
) -> CliResult<FilterConfig> {
    let window = take_raw(values, "--filter-window");
    let order = take_raw(values, "--filter-polynomial-order");
    let max_gap = take_raw(values, "--filter-max-gap-s");
    let acceleration = take_raw(values, "--filter-acceleration-variance-m2-s4");
    let measurement = take_raw(values, "--filter-measurement-variance-m2");
    let initial_velocity = take_raw(values, "--filter-initial-velocity-variance-m2-s2");
    let confidence_window = take_raw(values, "--filter-confidence-window-samples");

    let config = match filter_name {
        "raw" => {
            reject_present_filter_options(&[
                ("--filter-window", &window),
                ("--filter-polynomial-order", &order),
                ("--filter-max-gap-s", &max_gap),
                ("--filter-acceleration-variance-m2-s4", &acceleration),
                ("--filter-measurement-variance-m2", &measurement),
                (
                    "--filter-initial-velocity-variance-m2-s2",
                    &initial_velocity,
                ),
                ("--filter-confidence-window-samples", &confidence_window),
            ])?;
            FilterConfig::Raw
        }
        "moving-average" => {
            reject_present_filter_options(&[
                ("--filter-polynomial-order", &order),
                ("--filter-acceleration-variance-m2-s4", &acceleration),
                ("--filter-measurement-variance-m2", &measurement),
                (
                    "--filter-initial-velocity-variance-m2-s2",
                    &initial_velocity,
                ),
                ("--filter-confidence-window-samples", &confidence_window),
            ])?;
            FilterConfig::MovingAverage {
                window: parse_usize(&required_raw(window, "--filter-window")?, "--filter-window")?,
                max_gap_s: parse_f64(
                    &required_raw(max_gap, "--filter-max-gap-s")?,
                    "--filter-max-gap-s",
                )?,
            }
        }
        "savitzky-golay" => {
            reject_present_filter_options(&[
                ("--filter-acceleration-variance-m2-s4", &acceleration),
                ("--filter-measurement-variance-m2", &measurement),
                (
                    "--filter-initial-velocity-variance-m2-s2",
                    &initial_velocity,
                ),
                ("--filter-confidence-window-samples", &confidence_window),
            ])?;
            FilterConfig::SavitzkyGolay {
                window: parse_usize(&required_raw(window, "--filter-window")?, "--filter-window")?,
                polynomial_order: parse_usize(
                    &required_raw(order, "--filter-polynomial-order")?,
                    "--filter-polynomial-order",
                )?,
                max_gap_s: parse_f64(
                    &required_raw(max_gap, "--filter-max-gap-s")?,
                    "--filter-max-gap-s",
                )?,
            }
        }
        "kalman" => {
            reject_present_filter_options(&[
                ("--filter-window", &window),
                ("--filter-polynomial-order", &order),
            ])?;
            FilterConfig::Kalman {
                acceleration_variance_m2_s4: parse_f64(
                    &required_raw(acceleration, "--filter-acceleration-variance-m2-s4")?,
                    "--filter-acceleration-variance-m2-s4",
                )?,
                measurement_variance_m2: parse_f64(
                    &required_raw(measurement, "--filter-measurement-variance-m2")?,
                    "--filter-measurement-variance-m2",
                )?,
                initial_velocity_variance_m2_s2: parse_f64(
                    &required_raw(initial_velocity, "--filter-initial-velocity-variance-m2-s2")?,
                    "--filter-initial-velocity-variance-m2-s2",
                )?,
                confidence_window_samples: parse_usize(
                    &required_raw(confidence_window, "--filter-confidence-window-samples")?,
                    "--filter-confidence-window-samples",
                )?,
                max_gap_s: parse_f64(
                    &required_raw(max_gap, "--filter-max-gap-s")?,
                    "--filter-max-gap-s",
                )?,
            }
        }
        value => {
            return Err(CliError::invalid_input(format!(
                "--filter must be raw, moving-average, savitzky-golay, or kalman, got '{value}'"
            )));
        }
    };
    apply_filter(&[], config)
        .map_err(|error| CliError::invalid_input(format!("invalid filter config: {error}")))?;
    Ok(config)
}

fn reject_present_filter_options(options: &[(&str, &Option<String>)]) -> CliResult<()> {
    if let Some((flag, _)) = options.iter().find(|(_, value)| value.is_some()) {
        return Err(CliError::invalid_input(format!(
            "{flag} is not valid for the selected filter"
        )));
    }
    Ok(())
}

fn build_tracker(args: &Args) -> CliResult<Box<dyn ManualSeedTracker>> {
    match args.tracker {
        TrackerChoice::Template => {
            let defaults = TemplateMatchConfig::default();
            let tracker = TemplateMatchTracker::try_new(TemplateMatchConfig {
                search_radius_px: args
                    .tracker_search_radius_px
                    .unwrap_or(defaults.search_radius_px),
                low_confidence_normalized_mean_absolute_difference: args
                    .template_low_confidence_nmad
                    .unwrap_or(defaults.low_confidence_normalized_mean_absolute_difference),
                max_normalized_mean_absolute_difference: args
                    .template_max_nmad
                    .unwrap_or(defaults.max_normalized_mean_absolute_difference),
                seed_timestamp_tolerance_s: SEED_TIMESTAMP_TOLERANCE_S,
            })
            .map_err(|error| {
                CliError::invalid_input(format!("invalid template tracker config: {error}"))
            })?;
            Ok(Box::new(tracker))
        }
        TrackerChoice::Contrast => {
            let defaults = LocalContrastConfig::default();
            let tracker = LocalContrastTracker::try_new(LocalContrastConfig {
                search_radius_px: args
                    .tracker_search_radius_px
                    .unwrap_or(defaults.search_radius_px),
                min_seed_contrast: args
                    .contrast_min_seed_contrast
                    .unwrap_or(defaults.min_seed_contrast),
                min_mass_ratio: args
                    .contrast_min_mass_ratio
                    .unwrap_or(defaults.min_mass_ratio),
                low_confidence_mass_ratio: args
                    .contrast_low_confidence_mass_ratio
                    .unwrap_or(defaults.low_confidence_mass_ratio),
                seed_timestamp_tolerance_s: SEED_TIMESTAMP_TOLERANCE_S,
            })
            .map_err(|error| {
                CliError::invalid_input(format!("invalid contrast tracker config: {error}"))
            })?;
            Ok(Box::new(tracker))
        }
    }
}

fn canonical_raw_observations(run: &TrackerRun) -> CliResult<Vec<RawObservation>> {
    run.observations
        .iter()
        .map(|observation| {
            let (tracking_state, measurement, target_bounds_px) = match observation.state {
                TrackerObservationState::Tracked { center, confidence } => (
                    TrackingState::Tracked,
                    Some(PixelObservation {
                        timestamp_s: observation.timestamp_s,
                        x_px: center.x_px(),
                        y_px: center.y_px(),
                        confidence,
                    }),
                    observation.target_bounds_px,
                ),
                TrackerObservationState::LowConfidence { center, confidence } => (
                    TrackingState::LowConfidence,
                    Some(PixelObservation {
                        timestamp_s: observation.timestamp_s,
                        x_px: center.x_px(),
                        y_px: center.y_px(),
                        confidence,
                    }),
                    observation.target_bounds_px,
                ),
                TrackerObservationState::Lost { .. } => {
                    if observation.target_bounds_px.is_some() {
                        return Err(CliError::tracking(
                            "tracker returned target bounds for a lost observation",
                        ));
                    }
                    (TrackingState::Lost, None, None)
                }
            };
            Ok(RawObservation {
                timestamp_s: observation.timestamp_s,
                frame_index: observation.frame_index,
                tracking_state,
                visibility: VisibilityState::Unknown,
                measurement,
                target_bounds_px,
                tracker_id: run.tracker.id.clone(),
            })
        })
        .collect()
}

fn calibrate_measurements(
    raw: &[RawObservation],
    calibration: &PlateDiameterCalibration,
) -> CliResult<Vec<MetricPositionSample>> {
    raw.iter()
        .filter_map(|observation| observation.measurement)
        .map(|measurement| {
            let calibrated = calibration
                .calibrate_observation(measurement)
                .map_err(|error| {
                    CliError::seed_calibration(format!(
                        "calibration failed for tracked sample: {error}"
                    ))
                })?;
            Ok(MetricPositionSample {
                timestamp_s: calibrated.raw.timestamp_s,
                x_m: calibrated.x_m,
                y_m: calibrated.y_m,
                confidence: calibrated.raw.confidence,
            })
        })
        .collect()
}

fn video_metadata(
    clip: &DecodedClip,
    stream: &StreamProvenance,
    seed_timestamp_s: f64,
    selection: Option<MediaTimeRange>,
) -> VideoMetadata {
    let measured_fps = if clip.frames.len() > 1 {
        let duration_s =
            clip.provenance.last_selected_timestamp_s - clip.provenance.first_selected_timestamp_s;
        (duration_s > 0.0).then(|| (clip.frames.len() - 1) as f64 / duration_s)
    } else {
        None
    };
    let trim = selection.map_or_else(
        || TimeRange {
            start_s: clip
                .provenance
                .first_selected_timestamp_s
                .min(seed_timestamp_s),
            end_s: clip
                .provenance
                .last_selected_timestamp_s
                .max(seed_timestamp_s),
        },
        |range| TimeRange {
            start_s: range.start_s,
            end_s: range.end_s,
        },
    );
    VideoMetadata {
        decoded_width_px: stream.coded_width_px,
        decoded_height_px: stream.coded_height_px,
        display_width_px: stream.display_width_px,
        display_height_px: stream.display_height_px,
        source_rotation_deg: stream.rotation_deg,
        frame_rate: FrameRateMetadata {
            timestamp_basis: TimestampBasis::DecodedPresentationTimestamp,
            nominal_fps: None,
            measured_fps,
        },
        trim,
    }
}

fn analysis_provenance(
    tracker: &TrackerIdentity,
    clip: &DecodedClip,
) -> CliResult<AnalysisProvenance> {
    let mut parameters = Configuration::new();
    for (key, value) in &tracker.config {
        parameters.insert(key.clone(), parameter_value(value));
    }
    parameters.insert(
        "confidence_semantics".to_owned(),
        ParameterValue::Text(tracker.confidence_semantics.clone()),
    );
    let decoder = serde_json::to_string(&clip.provenance).map_err(|error| {
        CliError::output(format!("failed to serialize decoder provenance: {error}"))
    })?;

    Ok(AnalysisProvenance {
        pipeline: PipelineProvenance {
            openbar_version: env!("CARGO_PKG_VERSION").to_owned(),
            git_commit: option_env!("OPENBAR_GIT_COMMIT").map(str::to_owned),
        },
        tracker: TrackerProvenance {
            id: tracker.id.clone(),
            implementation: ImplementationProvenance {
                implementation: tracker.implementation.clone(),
                version: tracker.version.clone(),
                parameters,
            },
        },
        model: None,
        environment: Some(EnvironmentProvenance {
            os: None,
            architecture: None,
            device: None,
            decoder: Some(decoder),
        }),
    })
}

fn parameter_value(value: &str) -> ParameterValue {
    if let Ok(integer) = value.parse::<i64>() {
        ParameterValue::Integer(integer)
    } else if let Ok(float) = value.parse::<f64>() {
        ParameterValue::Float(float)
    } else if let Ok(boolean) = value.parse::<bool>() {
        ParameterValue::Boolean(boolean)
    } else {
        ParameterValue::Text(value.to_owned())
    }
}

fn write_analysis(path: &Path, analysis: &Analysis, force: bool) -> CliResult<()> {
    let serialized = analysis.to_json_pretty().map_err(|error| {
        CliError::output(format!("failed to serialize canonical analysis: {error}"))
    })?;
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).map_err(|error| {
                CliError::output(format!(
                    "failed to create output directory '{}': {error}",
                    parent.display()
                ))
            })?;
        }
    }

    let mut options = OpenOptions::new();
    options.write(true);
    if force {
        options.create(true).truncate(true);
    } else {
        options.create_new(true);
    }
    let mut file = options.open(path).map_err(|error| {
        let hint = if !force && error.kind() == std::io::ErrorKind::AlreadyExists {
            " (use --force to replace it)"
        } else {
            ""
        };
        CliError::output(format!(
            "failed to open analysis output '{}': {error}{hint}",
            path.display()
        ))
    })?;
    file.write_all(serialized.as_bytes())
        .and_then(|()| file.write_all(b"\n"))
        .map_err(|error| {
            CliError::output(format!(
                "failed to write analysis output '{}': {error}",
                path.display()
            ))
        })
}

fn emit_diagnostics(args: &Args, analysis: &Analysis, clip: &DecodedClip) {
    if args.diagnostics == DiagnosticsVerbosity::Quiet {
        return;
    }
    let tracked = analysis
        .raw_observations()
        .iter()
        .filter(|sample| sample.tracking_state == TrackingState::Tracked)
        .count();
    let low_confidence = analysis
        .raw_observations()
        .iter()
        .filter(|sample| sample.tracking_state == TrackingState::LowConfidence)
        .count();
    let lost = analysis
        .raw_observations()
        .iter()
        .filter(|sample| sample.tracking_state == TrackingState::Lost)
        .count();
    let warning = low_confidence > 0
        || lost > 0
        || !clip.provenance.decoder_diagnostics.is_empty()
        || !analysis.calibration().quality().warnings().is_empty();
    eprintln!(
        "status={} output={} tracked={} low_confidence={} lost={} decoder_diagnostics={}",
        if warning { "warning" } else { "success" },
        args.output.display(),
        tracked,
        low_confidence,
        lost,
        clip.provenance.decoder_diagnostics.len()
    );
    if args.diagnostics == DiagnosticsVerbosity::Verbose {
        eprintln!(
            "tracker={}@{} filter={}@{} kinematics={}@{} frames={} source_sha256={}",
            analysis.provenance().tracker.id,
            analysis.provenance().tracker.implementation.version,
            analysis
                .derived()
                .filtered
                .as_ref()
                .expect("analyze always records the selected filter")
                .filter
                .implementation,
            analysis
                .derived()
                .filtered
                .as_ref()
                .expect("analyze always records the selected filter")
                .filter
                .version,
            analysis
                .derived()
                .kinematics
                .as_ref()
                .expect("analyze always records kinematics")
                .method
                .implementation,
            analysis
                .derived()
                .kinematics
                .as_ref()
                .expect("analyze always records kinematics")
                .method
                .version,
            clip.frames.len(),
            clip.provenance.source_sha256
        );
    }
}

fn load_fixture(path: &Path, fixture_id: &str) -> CliResult<ManifestFixture> {
    if !is_valid_fixture_id(fixture_id) {
        return Err(CliError::invalid_input(format!(
            "fixture id '{fixture_id}' must match ^[a-z0-9][a-z0-9._-]*$"
        )));
    }
    let manifest: FixtureManifest = read_json(path, CliErrorKind::InvalidInput)?;
    if manifest.schema_version != 1 {
        return Err(CliError::invalid_input(format!(
            "fixture manifest '{}' has unsupported schema version {}",
            path.display(),
            manifest.schema_version
        )));
    }
    manifest
        .fixtures
        .into_iter()
        .find(|fixture| fixture.id == fixture_id)
        .ok_or_else(|| {
            CliError::invalid_input(format!(
                "fixture '{fixture_id}' was not found in '{}'",
                path.display()
            ))
        })
}

fn resolve_media_path(args: &Args, fixture: Option<&ManifestFixture>) -> CliResult<PathBuf> {
    if let Some(path) = &args.video {
        return Ok(path.clone());
    }
    fixture
        .and_then(|fixture| fixture.media.as_ref().map(|media| (fixture, media)))
        .and_then(|(fixture, media)| {
            media
                .repository_path
                .as_deref()
                .map(|path| (fixture.id.as_str(), PathBuf::from(path)))
        })
        .map(|(_, path)| path)
        .ok_or_else(|| {
            CliError::invalid_input(
                "fixture has no media.repository_path; pass --video <path> as an explicit override",
            )
        })
}

fn read_seed(path: &Path, fixture_id: Option<&str>) -> CliResult<ManualTargetSeedDocument> {
    let seed: ManualTargetSeedDocument = read_json(path, CliErrorKind::SeedCalibration)?;
    if let (Some(expected), Some(actual)) = (fixture_id, seed.fixture_id()) {
        if actual != expected {
            return Err(CliError::seed_calibration(format!(
                "manual seed '{}' references fixture '{actual}' instead of '{expected}'",
                path.display()
            )));
        }
    }
    Ok(seed)
}

fn validate_seed(
    seed: &ManualTargetSeedDocument,
    stream: &StreamProvenance,
    selection: Option<MediaTimeRange>,
    clip: &DecodedClip,
) -> CliResult<()> {
    let range = selection.unwrap_or(MediaTimeRange {
        start_s: (clip.provenance.first_selected_timestamp_s - SEED_TIMESTAMP_TOLERANCE_S).max(0.0),
        end_s: clip.provenance.last_selected_timestamp_s + SEED_TIMESTAMP_TOLERANCE_S,
    });
    seed.validate(SeedValidationContext {
        frame_width_px: stream.display_width_px,
        frame_height_px: stream.display_height_px,
        selected_range_start_s: range.start_s,
        selected_range_end_s: range.end_s,
        source_rotation_deg: stream.rotation_deg,
    })
    .map_err(|error| CliError::seed_calibration(format!("invalid manual seed: {error}")))?;

    let seed_s = seed.seed().timestamp_s();
    let nearest_gap_s = clip
        .frames
        .iter()
        .map(|frame| (frame.timestamp_s - seed_s).abs())
        .fold(f64::INFINITY, f64::min);
    if nearest_gap_s > SEED_TIMESTAMP_TOLERANCE_S {
        return Err(CliError::seed_calibration(format!(
            "seed timestamp {seed_s} s has no selected decoded frame within {SEED_TIMESTAMP_TOLERANCE_S} s (nearest is {nearest_gap_s:.6} s away)"
        )));
    }
    Ok(())
}

fn check_fixture_matches_media(
    fixture: &ManifestFixture,
    source_sha256: &str,
    stream: &StreamProvenance,
) -> CliResult<()> {
    if let Some(expected) = fixture
        .media
        .as_ref()
        .and_then(|media| media.sha256.as_deref())
    {
        if !expected.eq_ignore_ascii_case(source_sha256) {
            return Err(CliError::media(format!(
                "media SHA-256 {source_sha256} does not match fixture '{}' manifest value {expected}",
                fixture.id
            )));
        }
    }
    if (stream.coded_width_px, stream.coded_height_px)
        != (fixture.video.width_px, fixture.video.height_px)
    {
        return Err(CliError::unsupported(format!(
            "encoded media size {}x{} does not match fixture '{}' manifest size {}x{}",
            stream.coded_width_px,
            stream.coded_height_px,
            fixture.id,
            fixture.video.width_px,
            fixture.video.height_px
        )));
    }
    let expected_rotation = fixture.video.rotation_deg.unwrap_or(0);
    if stream.rotation_deg != expected_rotation {
        return Err(CliError::unsupported(format!(
            "media rotation {} does not match fixture '{}' manifest rotation {expected_rotation}",
            stream.rotation_deg, fixture.id
        )));
    }
    Ok(())
}

fn classify_media_error(error: MediaError) -> CliError {
    match error {
        MediaError::UnsupportedRotation { .. }
        | MediaError::UnsupportedSampleAspectRatio { .. } => {
            CliError::unsupported(error.to_string())
        }
        MediaError::InvalidRange { .. }
        | MediaError::EmptySelection { .. }
        | MediaError::FrameMemoryBudgetExceeded { .. } => {
            CliError::invalid_input(error.to_string())
        }
        _ => CliError::media(error.to_string()),
    }
}

fn read_json<T: serde::de::DeserializeOwned>(path: &Path, kind: CliErrorKind) -> CliResult<T> {
    let content = fs::read_to_string(path).map_err(|error| {
        CliError::new(
            kind,
            format!("failed to read '{}': {error}", path.display()),
        )
    })?;
    serde_json::from_str(&content).map_err(|error| {
        CliError::new(
            kind,
            format!("failed to parse '{}': {error}", path.display()),
        )
    })
}

fn required(values: &mut BTreeMap<String, String>, flag: &str) -> CliResult<String> {
    values
        .remove(flag)
        .ok_or_else(|| CliError::invalid_input(format!("analyze requires {flag}")))
}

fn take_path(values: &mut BTreeMap<String, String>, flag: &str) -> Option<PathBuf> {
    values.remove(flag).map(PathBuf::from)
}

fn take_raw(values: &mut BTreeMap<String, String>, flag: &str) -> Option<String> {
    values.remove(flag)
}

fn required_raw(value: Option<String>, flag: &str) -> CliResult<String> {
    value.ok_or_else(|| CliError::invalid_input(format!("selected filter requires {flag}")))
}

fn take_parsed<T: std::str::FromStr>(
    values: &mut BTreeMap<String, String>,
    flag: &str,
) -> CliResult<Option<T>> {
    values
        .remove(flag)
        .map(|value| {
            value.parse::<T>().map_err(|_| {
                CliError::invalid_input(format!("{flag} value '{value}' is not a valid number"))
            })
        })
        .transpose()
}

fn parse_f64(value: &str, flag: &str) -> CliResult<f64> {
    value.parse::<f64>().map_err(|_| {
        CliError::invalid_input(format!("{flag} value '{value}' is not a valid number"))
    })
}

fn parse_f32(value: &str, flag: &str) -> CliResult<f32> {
    value.parse::<f32>().map_err(|_| {
        CliError::invalid_input(format!("{flag} value '{value}' is not a valid number"))
    })
}

fn parse_u64(value: &str, flag: &str) -> CliResult<u64> {
    value.parse::<u64>().map_err(|_| {
        CliError::invalid_input(format!("{flag} value '{value}' is not a valid integer"))
    })
}

fn parse_usize(value: &str, flag: &str) -> CliResult<usize> {
    value.parse::<usize>().map_err(|_| {
        CliError::invalid_input(format!("{flag} value '{value}' is not a valid integer"))
    })
}

fn is_valid_fixture_id(value: &str) -> bool {
    let mut characters = value.chars();
    characters
        .next()
        .is_some_and(|first| first.is_ascii_lowercase() || first.is_ascii_digit())
        && characters.all(|character| {
            character.is_ascii_lowercase()
                || character.is_ascii_digit()
                || "._-".contains(character)
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;

    fn strings(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_owned()).collect()
    }

    fn base_args(output: &str) -> Vec<String> {
        strings(&[
            "--video",
            "clip.mp4",
            "--seed",
            "seed.json",
            "--plate-diameter-m",
            "0.45",
            "--tracker",
            "template",
            "--filter",
            "raw",
            "--kinematics-max-gap-s",
            "0.2",
            "--kinematics-min-confidence",
            "0",
            "--output",
            output,
        ])
    }

    #[test]
    fn parses_explicit_raw_pipeline_without_choosing_a_hidden_filter() {
        let parsed = parse_args(base_args("analysis.json"))
            .expect("arguments parse")
            .expect("not help");
        assert_eq!(parsed.tracker, TrackerChoice::Template);
        assert_eq!(parsed.filter, FilterConfig::Raw);
        assert_eq!(parsed.kinematics.max_gap_s, 0.2);
        assert_eq!(parsed.kinematics.min_confidence, 0.0);
    }

    #[test]
    fn filter_parameters_must_match_selected_filter() {
        let mut args = base_args("analysis.json");
        args.extend(strings(&["--filter-window", "5"]));
        let error = parse_args(args).expect_err("raw filter must reject window");
        assert_eq!(error.kind(), CliErrorKind::InvalidInput);
        assert!(error.to_string().contains("--filter-window"));
    }

    #[test]
    fn fixture_source_requires_manifest_and_fixture_together() {
        let mut args = base_args("analysis.json");
        args[0] = "--manifest".to_owned();
        args[1] = "manifest.json".to_owned();
        let error = parse_args(args).expect_err("manifest alone must fail");
        assert!(error.to_string().contains("--manifest and --fixture"));
    }

    #[test]
    fn invalid_plate_diameter_is_rejected_before_media_io() {
        let mut args = base_args("analysis.json");
        let index = args
            .iter()
            .position(|arg| arg == "--plate-diameter-m")
            .expect("flag");
        args[index + 1] = "0".to_owned();
        let error = parse_args(args).expect_err("zero plate diameter must fail");
        assert_eq!(error.kind(), CliErrorKind::InvalidInput);
    }

    #[test]
    fn missing_video_is_classified_as_media_failure() {
        let output = scratch_file("missing-video.json");
        let mut values = base_args(&output.display().to_string());
        values[1] = scratch_file("definitely-missing.mp4").display().to_string();
        let args = parse_args(values)
            .expect("arguments parse")
            .expect("not help");

        let error = run(&args).expect_err("missing video must fail");
        assert_eq!(error.kind(), CliErrorKind::Media);
        let _ = fs::remove_file(output);
    }

    #[test]
    fn malformed_seed_is_classified_before_media_execution() {
        let seed = scratch_file("malformed-seed.json");
        let output = scratch_file("malformed-seed-output.json");
        fs::write(&seed, "{not-json").expect("write malformed seed");

        let mut values = base_args(&output.display().to_string());
        let seed_index = values
            .iter()
            .position(|arg| arg == "--seed")
            .expect("seed flag");
        values[seed_index + 1] = seed.display().to_string();
        let args = parse_args(values)
            .expect("arguments parse")
            .expect("not help");

        let error = run(&args).expect_err("malformed seed must fail");
        assert_eq!(error.kind(), CliErrorKind::SeedCalibration);
        let _ = fs::remove_file(seed);
        let _ = fs::remove_file(output);
    }

    #[test]
    fn invalid_filter_configuration_is_rejected_before_media_io() {
        let mut values = base_args("analysis.json");
        let filter_index = values
            .iter()
            .position(|arg| arg == "--filter")
            .expect("filter flag");
        values[filter_index + 1] = "moving-average".to_owned();
        values.extend(strings(&[
            "--filter-window",
            "4",
            "--filter-max-gap-s",
            "0.2",
        ]));

        let error = parse_args(values).expect_err("even moving-average window must fail");
        assert_eq!(error.kind(), CliErrorKind::InvalidInput);
        assert!(error.to_string().contains("window"));
    }

    #[test]
    fn unsupported_fixture_geometry_and_tracking_failure_are_explicit() {
        if !ffmpeg_available() {
            return;
        }

        let output = scratch_file("failure-categories-output.json");
        let manifest = scratch_file("wrong-geometry-manifest.json");
        let wrong_manifest = serde_json::json!({
            "schema_version": 1,
            "fixtures": [{
                "id": "synthetic-clean-side-12",
                "video": {
                    "width_px": 999,
                    "height_px": 96,
                    "rotation_deg": 0
                }
            }]
        });
        fs::write(
            &manifest,
            format!("{}\n", serde_json::to_string_pretty(&wrong_manifest).unwrap()),
        )
        .expect("write manifest");

        let mut unsupported = fixture_args(&output);
        unsupported.manifest = Some(manifest.clone());
        let error = run(&unsupported).expect_err("fixture geometry mismatch must fail");
        assert_eq!(error.kind(), CliErrorKind::Unsupported);

        let mut tracking = fixture_args(&output);
        tracking.tracker = TrackerChoice::Contrast;
        tracking.contrast_min_seed_contrast = Some(1.0e9);
        let error = run(&tracking).expect_err("impossible seed contrast must fail tracking");
        assert_eq!(error.kind(), CliErrorKind::Tracking);

        let _ = fs::remove_file(manifest);
        let _ = fs::remove_file(output);
    }

    #[test]
    fn end_to_end_public_fixture_is_deterministic_and_refuses_overwrite() {
        if !ffmpeg_available() {
            return;
        }
        let output_a = scratch_file("analysis-a.json");
        let output_b = scratch_file("analysis-b.json");
        let _ = fs::remove_file(&output_a);
        let _ = fs::remove_file(&output_b);

        let args_a = fixture_args(&output_a);
        let args_b = fixture_args(&output_b);
        run(&args_a).expect("first analysis succeeds");
        run(&args_b).expect("second analysis succeeds");

        let bytes_a = fs::read(&output_a).expect("first output");
        let bytes_b = fs::read(&output_b).expect("second output");
        assert_eq!(bytes_a, bytes_b, "measurement output must be deterministic");

        let parsed = Analysis::from_json(
            std::str::from_utf8(&bytes_a).expect("analysis JSON must be UTF-8"),
        )
        .expect("output round-trips through canonical model");
        assert_eq!(parsed.schema_version(), 1);
        assert!(!parsed.raw_observations().is_empty());
        assert!(parsed.derived().filtered.is_some());
        assert!(parsed.derived().kinematics.is_some());

        let error = run(&args_a).expect_err("existing output must not be overwritten");
        assert_eq!(error.kind(), CliErrorKind::Output);
        assert!(error.to_string().contains("--force"));

        let _ = fs::remove_file(output_a);
        let _ = fs::remove_file(output_b);
    }

    fn fixture_args(output: &Path) -> Args {
        let values = vec![
            "--manifest".to_owned(),
            repo_path("validation/fixtures/public/manifest.json")
                .display()
                .to_string(),
            "--fixture".to_owned(),
            "synthetic-clean-side-12".to_owned(),
            "--video".to_owned(),
            repo_path("validation/fixtures/public/synthetic-clean-side-12.mp4")
                .display()
                .to_string(),
            "--seed".to_owned(),
            repo_path(
                "validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json",
            )
            .display()
            .to_string(),
            "--plate-diameter-m".to_owned(),
            "0.45".to_owned(),
            "--tracker".to_owned(),
            "template".to_owned(),
            "--filter".to_owned(),
            "raw".to_owned(),
            "--kinematics-max-gap-s".to_owned(),
            "0.2".to_owned(),
            "--kinematics-min-confidence".to_owned(),
            "0".to_owned(),
            "--output".to_owned(),
            output.display().to_string(),
            "--diagnostics".to_owned(),
            "quiet".to_owned(),
        ];
        parse_args(values)
            .expect("fixture args parse")
            .expect("not help")
    }

    fn repo_path(relative: &str) -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .join(relative)
    }

    fn scratch_file(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("openbar-analyze-{}-{name}", std::process::id()))
    }

    fn ffmpeg_available() -> bool {
        let available = ["ffmpeg", "ffprobe"].iter().all(|tool| {
            Command::new(tool)
                .arg("-version")
                .output()
                .is_ok_and(|output| output.status.success())
        });
        if !available {
            assert!(
                std::env::var_os("OPENBAR_REQUIRE_FFMPEG").is_none(),
                "OPENBAR_REQUIRE_FFMPEG is set but ffmpeg/ffprobe are not on PATH"
            );
            eprintln!("SKIPPED: ffmpeg/ffprobe not on PATH; analyze integration test not run");
        }
        available
    }
}
