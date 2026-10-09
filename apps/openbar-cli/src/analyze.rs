//! End-to-end M0 analysis command (issue #12).
//!
//! Media/process orchestration lives here; authoritative calibration, filtering, kinematics,
//! canonical validation and serialization remain in openbar-core.

#[path = "filter_request.rs"]
mod filter_request;
use filter_request::{parse_filter_config, FilterRequest};

use crate::cli_error::{CliError, CliErrorKind, CliResult};
use crate::external_observations::{
    load_external_observations, DecodedTimelineFrame, ImportedObservations,
};
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
use openbar_core::calibration::{
    CalibrationQuality, CalibrationQualityStatus, CalibrationWarning, PlateDiameterCalibration,
};
use openbar_core::filtering::apply_filter;
use openbar_core::kinematics::{derive_kinematic_trajectory, KinematicsConfig};
use openbar_core::manual_seed::{ManualTargetSeedDocument, SeedValidationContext};
use openbar_core::recording_support::{
    assess_recording_support, CameraMovement, CameraView, LightingCondition, MotionBlurCondition,
    OcclusionCondition, PlateVisibilityCondition, RecordingConditions, RecordingSupportAssessment,
    RecordingSupportStatus,
};
use openbar_core::trajectory::{MetricPositionSample, PixelObservation};
use openbar_tracking::{
    LocalContrastConfig, LocalContrastTracker, ManualSeedTracker, TemplateMatchConfig,
    TemplateMatchTracker, TrackerIdentity, TrackerObservationState, TrackerRun,
    TrackerVisibilityState,
};
use serde::Deserialize;
use std::collections::BTreeMap;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

const DEFAULT_MAX_FRAME_MEMORY_MIB: u64 = 2048;
pub(crate) const SEED_TIMESTAMP_TOLERANCE_S: f64 = 0.0005;

const USAGE: &str = "Usage: openbar-cli analyze (--video <path> | --manifest <path> --fixture <id> [--video <override>]) --seed <path>\n\
     \x20      --plate-diameter-m <m> (--tracker <template|contrast> | --observations <tracker-prediction-v1.json>)\n\
     \x20      --filter <raw|moving-average|savitzky-golay|kalman>\n\
     \x20      --kinematics-max-gap-s <s> --kinematics-min-confidence <0..1> --output <analysis.json>\n\
     \x20      [--start-s <s> --end-s <s>] [--max-frame-memory-mib <MiB>] [--tracker-search-radius-px <px>]\n\
     \x20      [tracker-specific options] [filter-specific options] [--diagnostics <quiet|normal|verbose>]\n\
     \x20      [--recording-support-output <support.json>] [--force]\n\
\n\
Direct-video recording metadata (fixture mode reads these from the manifest):\n\
  --camera-view <side|oblique_45|front|rear|unknown>\n\
  --camera-movement <fixed|handheld|panning|moving_other|unknown>\n\
  [--approx-yaw-deg <deg>] [--approx-pitch-deg <deg>] [--camera-roll-deg <deg>]\n\
  [--camera-distance-m <m>]\n\
\n\
Tracker options:\n\
  template: [--template-low-confidence-nmad <0..1>] [--template-max-nmad <0..1>]\n\
  contrast: [--contrast-min-seed-contrast <value>] [--contrast-min-mass-ratio <0..1>]\n\
            [--contrast-low-confidence-mass-ratio <0..1>]\n\
\n\
Filter options (there is deliberately no production default):\n\
  raw: no additional parameters\n\
  moving-average: (--filter-window <odd> | --filter-window-s <s>) --filter-max-gap-s <s>\n\
  savitzky-golay: (--filter-window <odd> | --filter-window-s <s>) --filter-polynomial-order <n> --filter-max-gap-s <s>\n\
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
    camera_view: Option<CameraView>,
    camera_movement: Option<CameraMovement>,
    approx_yaw_deg: Option<f64>,
    approx_pitch_deg: Option<f64>,
    camera_roll_deg: Option<f64>,
    camera_distance_m: Option<f64>,
    seed: PathBuf,
    plate_diameter_m: f64,
    tracker: Option<TrackerChoice>,
    observations: Option<PathBuf>,
    tracker_search_radius_px: Option<u32>,
    template_low_confidence_nmad: Option<f64>,
    template_max_nmad: Option<f64>,
    contrast_min_seed_contrast: Option<f64>,
    contrast_min_mass_ratio: Option<f64>,
    contrast_low_confidence_mass_ratio: Option<f64>,
    filter: FilterRequest,
    kinematics: KinematicsConfig,
    selection: Option<MediaTimeRange>,
    max_frame_bytes: u64,
    output: PathBuf,
    recording_support_output: Option<PathBuf>,
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
    camera: ManifestCamera,
    load: ManifestLoad,
    conditions: ManifestConditions,
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
    nominal_fps: f64,
    width_px: u32,
    height_px: u32,
    #[serde(default)]
    rotation_deg: Option<u16>,
}

#[derive(Debug, Clone, Deserialize)]
struct ManifestCamera {
    view: CameraView,
    #[serde(default)]
    approx_yaw_deg: Option<f64>,
    #[serde(default)]
    approx_pitch_deg: Option<f64>,
    #[serde(default)]
    distance_m: Option<f64>,
    movement: CameraMovement,
}

#[derive(Debug, Clone, Deserialize)]
struct ManifestConditions {
    lighting: LightingCondition,
    plate_visibility: PlateVisibilityCondition,
    occlusion: OcclusionCondition,
    motion_blur: MotionBlurCondition,
}

#[derive(Debug, Clone, Deserialize)]
struct ManifestLoad {
    plate_diameter_m: f64,
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
    if let Some(fixture) = fixture.as_ref() {
        validate_fixture_plate_diameter(fixture, args.plate_diameter_m)?;
    }
    let media_path = resolve_media_path(args, fixture.as_ref())?;
    let seed = read_seed(&args.seed, args.fixture_id.as_deref())?;
    let tracker = args
        .tracker
        .map(|choice| build_tracker(choice, args))
        .transpose()?;

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

    // Validate the seed against the decoded media before deriving any support metadata from it.
    // Otherwise an invalid target radius/timestamp could be reclassified as recording metadata
    // and, when requested, persisted into a misleading support sidecar.
    validate_seed(&seed, &stream, args.selection, &clip)?;
    let filter_config = args.filter.resolve(measured_fps(&clip))?;

    let imported_observations = args
        .observations
        .as_deref()
        .map(|path| {
            let timeline = clip
                .frames
                .iter()
                .map(|frame| DecodedTimelineFrame {
                    timestamp_s: frame.timestamp_s,
                    frame_index: frame.frame_index,
                })
                .collect::<Vec<_>>();
            load_external_observations(
                path,
                args.fixture_id.as_deref(),
                &source_sha256,
                stream.display_width_px,
                stream.display_height_px,
                &timeline,
                seed.seed().timestamp_s(),
                SEED_TIMESTAMP_TOLERANCE_S,
            )
        })
        .transpose()?;

    let recording_conditions = recording_conditions(
        args,
        fixture.as_ref(),
        &stream,
        &clip,
        seed.seed().target().diameter_px(),
    );
    let recording_support = assess_recording_support(&recording_conditions)
        .map_err(|error| CliError::invalid_input(format!("invalid recording metadata: {error}")))?;
    if let Some(path) = args.recording_support_output.as_deref() {
        write_recording_support(path, &recording_support, args.force)?;
    }
    if recording_support.status == RecordingSupportStatus::Unsupported {
        let reasons = recording_support.unsupported_reason_codes().join(",");
        return Err(CliError::unsupported(format!(
            "recording is outside the M0 supported geometry: {reasons}; no physical analysis was written"
        )));
    }

    let (raw_observations, tracker_provenance) =
        measurement_observations(imported_observations, tracker, &clip, &seed)?;

    let calibration = PlateDiameterCalibration::try_from_manual_seed(
        args.plate_diameter_m,
        seed.seed(),
        calibration_quality_for_recording(&recording_conditions),
    )
    .map_err(|error| CliError::seed_calibration(format!("invalid plate calibration: {error}")))?;

    let calibrated_samples = calibrate_measurements(&raw_observations, &calibration)?;
    let filter_run = apply_filter(&calibrated_samples, filter_config).map_err(|error| {
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
        analysis_video_metadata(
            &clip,
            &stream,
            seed.seed().timestamp_s(),
            args.selection,
            args.observations.is_some().then_some(&raw_observations),
        ),
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
        analysis_provenance(tracker_provenance, &clip)?,
    )
    .map_err(|error| {
        CliError::internal(format!(
            "pipeline produced an invalid canonical analysis; this is an OpenBar bug: {error}"
        ))
    })?;

    write_analysis(&args.output, &analysis, args.force)?;
    emit_diagnostics(args, &analysis, &clip, &recording_support);
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

    let camera_view = values
        .remove("--camera-view")
        .map(|value| parse_camera_view(&value))
        .transpose()?;
    let camera_movement = values
        .remove("--camera-movement")
        .map(|value| parse_camera_movement(&value))
        .transpose()?;
    let approx_yaw_deg = take_parsed(&mut values, "--approx-yaw-deg")?;
    let approx_pitch_deg = take_parsed(&mut values, "--approx-pitch-deg")?;
    let camera_roll_deg = take_parsed(&mut values, "--camera-roll-deg")?;
    let camera_distance_m = take_parsed(&mut values, "--camera-distance-m")?;
    validate_recording_metadata_source(
        manifest.as_ref(),
        camera_view,
        camera_movement,
        approx_yaw_deg,
        approx_pitch_deg,
        camera_roll_deg,
        camera_distance_m,
    )?;

    let seed = PathBuf::from(required(&mut values, "--seed")?);
    let output = PathBuf::from(required(&mut values, "--output")?);
    let recording_support_output = take_path(&mut values, "--recording-support-output");
    let observations = take_path(&mut values, "--observations");
    let tracker_value = values.remove("--tracker");
    let tracker = match (tracker_value.as_deref(), observations.as_ref()) {
        (Some(_), Some(_)) => {
            return Err(CliError::invalid_input(
                "--tracker and --observations are mutually exclusive; provide exactly one",
            ));
        }
        (None, None) => {
            return Err(CliError::invalid_input(
                "analyze requires exactly one of --tracker <template|contrast> or --observations <path>",
            ));
        }
        (Some(value), None) => Some(match value {
            "template" => TrackerChoice::Template,
            "contrast" => TrackerChoice::Contrast,
            value => {
                return Err(CliError::invalid_input(format!(
                    "--tracker must be 'template' or 'contrast', got '{value}'"
                )));
            }
        }),
        (None, Some(_)) => None,
    };
    validate_output_paths(
        video.as_ref(),
        manifest.as_ref(),
        &seed,
        observations.as_ref(),
        &output,
        recording_support_output.as_ref(),
    )?;
    let plate_diameter_m = parse_f64(
        &required(&mut values, "--plate-diameter-m")?,
        "--plate-diameter-m",
    )?;
    if !plate_diameter_m.is_finite() || plate_diameter_m <= 0.0 {
        return Err(CliError::seed_calibration(
            "--plate-diameter-m must be finite and positive",
        ));
    }

    let tracker_search_radius_px = take_parsed(&mut values, "--tracker-search-radius-px")?;
    let template_low_confidence_nmad = take_parsed(&mut values, "--template-low-confidence-nmad")?;
    let template_max_nmad = take_parsed(&mut values, "--template-max-nmad")?;
    let contrast_min_seed_contrast = take_parsed(&mut values, "--contrast-min-seed-contrast")?;
    let contrast_min_mass_ratio = take_parsed(&mut values, "--contrast-min-mass-ratio")?;
    let contrast_low_confidence_mass_ratio =
        take_parsed(&mut values, "--contrast-low-confidence-mass-ratio")?;
    validate_tracker_specific_options(
        tracker,
        tracker_search_radius_px,
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
        camera_view,
        camera_movement,
        approx_yaw_deg,
        approx_pitch_deg,
        camera_roll_deg,
        camera_distance_m,
        seed,
        plate_diameter_m,
        tracker,
        observations,
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
        recording_support_output,
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
            | "--camera-view"
            | "--camera-movement"
            | "--approx-yaw-deg"
            | "--approx-pitch-deg"
            | "--camera-roll-deg"
            | "--camera-distance-m"
            | "--seed"
            | "--plate-diameter-m"
            | "--tracker"
            | "--observations"
            | "--tracker-search-radius-px"
            | "--template-low-confidence-nmad"
            | "--template-max-nmad"
            | "--contrast-min-seed-contrast"
            | "--contrast-min-mass-ratio"
            | "--contrast-low-confidence-mass-ratio"
            | "--filter"
            | "--filter-window"
            | "--filter-window-s"
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
            | "--recording-support-output"
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

fn validate_recording_metadata_source(
    manifest: Option<&PathBuf>,
    camera_view: Option<CameraView>,
    camera_movement: Option<CameraMovement>,
    approx_yaw_deg: Option<f64>,
    approx_pitch_deg: Option<f64>,
    camera_roll_deg: Option<f64>,
    camera_distance_m: Option<f64>,
) -> CliResult<()> {
    let has_direct_metadata = camera_view.is_some()
        || camera_movement.is_some()
        || approx_yaw_deg.is_some()
        || approx_pitch_deg.is_some()
        || camera_roll_deg.is_some()
        || camera_distance_m.is_some();

    if manifest.is_some() {
        if has_direct_metadata {
            return Err(CliError::invalid_input(
                "camera recording flags cannot override fixture metadata; update the manifest instead",
            ));
        }
        return Ok(());
    }

    if camera_view.is_none() || camera_movement.is_none() {
        return Err(CliError::invalid_input(
            "direct --video analysis requires --camera-view and --camera-movement; OpenBar will not assume side/fixed geometry",
        ));
    }
    Ok(())
}

fn validate_output_paths(
    video: Option<&PathBuf>,
    manifest: Option<&PathBuf>,
    seed: &Path,
    observations: Option<&PathBuf>,
    output: &Path,
    recording_support_output: Option<&PathBuf>,
) -> CliResult<()> {
    if recording_support_output.is_some_and(|path| path.as_path() == output) {
        return Err(CliError::invalid_input(
            "--recording-support-output must differ from --output",
        ));
    }

    let explicit_inputs = [
        ("--video", video.map(PathBuf::as_path)),
        ("--manifest", manifest.map(PathBuf::as_path)),
        ("--seed", Some(seed)),
        ("--observations", observations.map(PathBuf::as_path)),
    ];
    for (output_flag, destination) in [
        ("--output", Some(output)),
        (
            "--recording-support-output",
            recording_support_output.map(PathBuf::as_path),
        ),
    ] {
        let Some(destination) = destination else {
            continue;
        };
        if let Some((input_flag, _)) = explicit_inputs
            .iter()
            .find(|(_, input)| input.is_some_and(|input| input == destination))
        {
            return Err(CliError::invalid_input(format!(
                "{output_flag} must not overwrite the explicit input path supplied by {input_flag}"
            )));
        }
    }
    Ok(())
}

fn parse_camera_view(value: &str) -> CliResult<CameraView> {
    match value {
        "side" => Ok(CameraView::Side),
        "oblique_45" => Ok(CameraView::Oblique45),
        "front" => Ok(CameraView::Front),
        "rear" => Ok(CameraView::Rear),
        "unknown" => Ok(CameraView::Unknown),
        _ => Err(CliError::invalid_input(format!(
            "--camera-view must be side, oblique_45, front, rear, or unknown, got '{value}'"
        ))),
    }
}

fn parse_camera_movement(value: &str) -> CliResult<CameraMovement> {
    match value {
        "fixed" => Ok(CameraMovement::Fixed),
        "handheld" => Ok(CameraMovement::Handheld),
        "panning" => Ok(CameraMovement::Panning),
        "moving_other" => Ok(CameraMovement::MovingOther),
        "unknown" => Ok(CameraMovement::Unknown),
        _ => Err(CliError::invalid_input(format!(
            "--camera-movement must be fixed, handheld, panning, moving_other, or unknown, got '{value}'"
        ))),
    }
}

fn validate_tracker_specific_options(
    tracker: Option<TrackerChoice>,
    search_radius: Option<u32>,
    template_low: Option<f64>,
    template_max: Option<f64>,
    contrast_seed: Option<f64>,
    contrast_min: Option<f64>,
    contrast_low: Option<f64>,
) -> CliResult<()> {
    match tracker {
        None if search_radius.is_some()
            || template_low.is_some()
            || template_max.is_some()
            || contrast_seed.is_some()
            || contrast_min.is_some()
            || contrast_low.is_some() =>
        {
            Err(CliError::invalid_input(
                "tracker-specific options cannot be used with --observations",
            ))
        }
        Some(TrackerChoice::Template)
            if contrast_seed.is_some() || contrast_min.is_some() || contrast_low.is_some() =>
        {
            Err(CliError::invalid_input(
                "contrast-specific options cannot be used with --tracker template",
            ))
        }
        Some(TrackerChoice::Contrast) if template_low.is_some() || template_max.is_some() => {
            Err(CliError::invalid_input(
                "template-specific options cannot be used with --tracker contrast",
            ))
        }
        _ => Ok(()),
    }
}

fn build_tracker(
    tracker_choice: TrackerChoice,
    args: &Args,
) -> CliResult<Box<dyn ManualSeedTracker>> {
    match tracker_choice {
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

fn measurement_observations(
    imported: Option<ImportedObservations>,
    tracker: Option<Box<dyn ManualSeedTracker>>,
    clip: &DecodedClip,
    seed: &ManualTargetSeedDocument,
) -> CliResult<(Vec<RawObservation>, TrackerProvenance)> {
    if let Some(imported) = imported {
        return Ok((imported.raw_observations, imported.tracker_provenance));
    }

    let tracker = tracker.expect("parse_args guarantees a tracker when observations are absent");
    let tracker_run = tracker
        .track(&clip.frame_samples(), seed.seed())
        .map_err(|error| CliError::tracking(format!("selected tracker failed: {error}")))?;
    let tracker_provenance = canonical_tracker_provenance(&tracker_run.tracker);
    let raw_observations = canonical_raw_observations(&tracker_run)?;
    Ok((raw_observations, tracker_provenance))
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
                visibility: canonical_visibility(observation.visibility),
                measurement,
                target_bounds_px,
                tracker_id: run.tracker.id.clone(),
            })
        })
        .collect()
}

fn canonical_visibility(visibility: TrackerVisibilityState) -> VisibilityState {
    match visibility {
        TrackerVisibilityState::Unknown => VisibilityState::Unknown,
    }
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

fn measured_fps(clip: &DecodedClip) -> Option<f64> {
    if clip.frames.len() <= 1 {
        return None;
    }
    let duration_s =
        clip.provenance.last_selected_timestamp_s - clip.provenance.first_selected_timestamp_s;
    (duration_s > 0.0).then(|| (clip.frames.len() - 1) as f64 / duration_s)
}

fn recording_conditions(
    args: &Args,
    fixture: Option<&ManifestFixture>,
    stream: &StreamProvenance,
    clip: &DecodedClip,
    plate_diameter_px: f64,
) -> RecordingConditions {
    let (
        camera_view,
        approx_yaw_deg,
        approx_pitch_deg,
        camera_roll_deg,
        distance_m,
        camera_movement,
        nominal_fps,
        lighting,
        plate_visibility,
        occlusion,
        motion_blur,
    ) = if let Some(fixture) = fixture {
        (
            fixture.camera.view,
            fixture.camera.approx_yaw_deg,
            fixture.camera.approx_pitch_deg,
            None,
            fixture.camera.distance_m,
            fixture.camera.movement,
            Some(fixture.video.nominal_fps),
            fixture.conditions.lighting,
            fixture.conditions.plate_visibility,
            fixture.conditions.occlusion,
            fixture.conditions.motion_blur,
        )
    } else {
        (
            args.camera_view
                .expect("direct video metadata validated during argument parsing"),
            args.approx_yaw_deg,
            args.approx_pitch_deg,
            args.camera_roll_deg,
            args.camera_distance_m,
            args.camera_movement
                .expect("direct video metadata validated during argument parsing"),
            None,
            LightingCondition::Unknown,
            PlateVisibilityCondition::Unknown,
            OcclusionCondition::Unknown,
            MotionBlurCondition::Unknown,
        )
    };

    RecordingConditions {
        camera_view,
        approx_yaw_deg,
        approx_pitch_deg,
        camera_roll_deg,
        distance_m,
        camera_movement,
        nominal_fps,
        // Derivatives and validation are timestamp-authoritative, so report the rate observed
        // from the decoded timestamps rather than preferring manifest-authored metadata.
        measured_fps: measured_fps(clip),
        width_px: stream.display_width_px,
        height_px: stream.display_height_px,
        plate_diameter_px: Some(plate_diameter_px),
        lighting,
        plate_visibility,
        occlusion,
        motion_blur,
    }
}

fn calibration_quality_for_recording(conditions: &RecordingConditions) -> CalibrationQuality {
    let mut warnings = vec![CalibrationWarning::GeometryUnassessed];

    if conditions.approx_yaw_deg.is_some_and(|yaw| yaw != 0.0) {
        warnings.push(CalibrationWarning::CameraYaw);
        warnings.push(CalibrationWarning::PlateForeshortening);
    }
    if conditions
        .approx_pitch_deg
        .is_some_and(|pitch| pitch != 0.0)
    {
        warnings.push(CalibrationWarning::CameraPitchOrHeight);
        warnings.push(CalibrationWarning::PerspectiveOrParallax);
    }
    if conditions.camera_roll_deg.is_some_and(|roll| roll != 0.0)
        && !warnings.contains(&CalibrationWarning::PerspectiveOrParallax)
    {
        warnings.push(CalibrationWarning::PerspectiveOrParallax);
    }

    CalibrationQuality::new(CalibrationQualityStatus::Warning, warnings)
}

fn write_recording_support(
    path: &Path,
    support: &RecordingSupportAssessment,
    force: bool,
) -> CliResult<()> {
    let serialized = serde_json::to_string_pretty(support).map_err(|error| {
        CliError::output(format!(
            "failed to serialize recording-support assessment: {error}"
        ))
    })?;
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).map_err(|error| {
                CliError::output(format!(
                    "failed to create recording-support output directory '{}': {error}",
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
            "failed to open recording-support output '{}': {error}{hint}",
            path.display()
        ))
    })?;
    file.write_all(serialized.as_bytes())
        .and_then(|()| file.write_all(b"\n"))
        .map_err(|error| {
            CliError::output(format!(
                "failed to write recording-support output '{}': {error}",
                path.display()
            ))
        })
}

fn analysis_video_metadata(
    clip: &DecodedClip,
    stream: &StreamProvenance,
    seed_timestamp_s: f64,
    selection: Option<MediaTimeRange>,
    external_observations: Option<&[RawObservation]>,
) -> VideoMetadata {
    let mut video = video_metadata(clip, stream, seed_timestamp_s, selection);
    if let Some(observations) = external_observations {
        if let Some(first) = observations.first() {
            video.trim.start_s = video.trim.start_s.min(first.timestamp_s);
        }
        if let Some(last) = observations.last() {
            video.trim.end_s = video.trim.end_s.max(last.timestamp_s);
        }
    }
    video
}

fn video_metadata(
    clip: &DecodedClip,
    stream: &StreamProvenance,
    seed_timestamp_s: f64,
    selection: Option<MediaTimeRange>,
) -> VideoMetadata {
    let measured_fps = measured_fps(clip);
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

fn canonical_tracker_provenance(tracker: &TrackerIdentity) -> TrackerProvenance {
    let mut parameters = Configuration::new();
    for (key, value) in &tracker.config {
        parameters.insert(key.clone(), parameter_value(value));
    }
    parameters.insert(
        "confidence_semantics".to_owned(),
        ParameterValue::Text(tracker.confidence_semantics.clone()),
    );
    TrackerProvenance {
        id: tracker.id.clone(),
        implementation: ImplementationProvenance {
            implementation: tracker.implementation.clone(),
            version: tracker.version.clone(),
            parameters,
        },
    }
}

fn analysis_provenance(
    tracker: TrackerProvenance,
    clip: &DecodedClip,
) -> CliResult<AnalysisProvenance> {
    let decoder = serde_json::to_string(&clip.provenance).map_err(|error| {
        CliError::output(format!("failed to serialize decoder provenance: {error}"))
    })?;

    Ok(AnalysisProvenance {
        pipeline: PipelineProvenance {
            openbar_version: env!("CARGO_PKG_VERSION").to_owned(),
            git_commit: option_env!("OPENBAR_GIT_COMMIT").map(str::to_owned),
        },
        tracker,
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

fn emit_diagnostics(
    args: &Args,
    analysis: &Analysis,
    clip: &DecodedClip,
    recording_support: &RecordingSupportAssessment,
) {
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
    let warning = recording_support.status != RecordingSupportStatus::Supported
        || low_confidence > 0
        || lost > 0
        || !clip.provenance.decoder_diagnostics.is_empty()
        || !analysis.calibration().quality().warnings().is_empty();
    let support_reasons = recording_support.reason_codes().join(",");
    eprintln!(
        "status={} output={} recording_support={} support_reasons={} tracked={} low_confidence={} lost={} decoder_diagnostics={}",
        if warning { "warning" } else { "success" },
        args.output.display(),
        recording_support.status.as_str(),
        support_reasons,
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

fn validate_fixture_plate_diameter(
    fixture: &ManifestFixture,
    plate_diameter_m: f64,
) -> CliResult<()> {
    let expected = fixture.load.plate_diameter_m;
    if !expected.is_finite() || expected <= 0.0 {
        return Err(CliError::invalid_input(format!(
            "fixture '{}' has invalid load.plate_diameter_m {expected}; expected a finite positive value",
            fixture.id
        )));
    }
    if plate_diameter_m != expected {
        return Err(CliError::seed_calibration(format!(
            "--plate-diameter-m {plate_diameter_m} conflicts with fixture '{}' load.plate_diameter_m {expected}; use the fixture's recorded diameter or analyze without fixture identity",
            fixture.id
        )));
    }
    Ok(())
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
    use openbar_core::filtering::FilterConfig;
    use std::process::Command;

    fn strings(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_owned()).collect()
    }

    fn base_args(output: &str) -> Vec<String> {
        strings(&[
            "--video",
            "clip.mp4",
            "--camera-view",
            "side",
            "--camera-movement",
            "fixed",
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

    fn smoothing_args(filter: &str, options: &[&str]) -> Vec<String> {
        let mut args = base_args("analysis.json");
        let index = args.iter().position(|arg| arg == "--filter").unwrap();
        args[index + 1] = filter.to_owned();
        args.extend(strings(options));
        args
    }

    #[test]
    fn duration_filter_parses_before_media_io() {
        for filter in ["moving-average", "savitzky-golay"] {
            let mut args = smoothing_args(
                filter,
                &["--filter-window-s", "0.25", "--filter-max-gap-s", "0.2"],
            );
            if filter == "savitzky-golay" {
                args.extend(strings(&["--filter-polynomial-order", "2"]));
            }
            let parsed = parse_args(args).unwrap().unwrap();
            let expected = if filter == "moving-average" {
                FilterRequest::MovingAverageDuration {
                    window_s: 0.25,
                    max_gap_s: 0.2,
                }
            } else {
                FilterRequest::SavitzkyGolayDuration {
                    window_s: 0.25,
                    polynomial_order: 2,
                    max_gap_s: 0.2,
                }
            };
            assert_eq!(parsed.filter, expected);
        }
    }

    #[test]
    fn smoothing_filters_require_exactly_one_window() {
        for filter in ["moving-average", "savitzky-golay"] {
            for options in [
                vec![],
                strings(&["--filter-window", "3", "--filter-window-s", "0.25"]),
            ] {
                let mut args = smoothing_args(filter, &["--filter-max-gap-s", "0.2"]);
                if filter == "savitzky-golay" {
                    args.extend(strings(&["--filter-polynomial-order", "2"]));
                }
                args.extend(options);
                let error = parse_args(args).unwrap_err();
                assert_eq!(error.kind(), CliErrorKind::InvalidInput);
                assert!(error
                    .to_string()
                    .contains("exactly one of --filter-window and --filter-window-s"));
            }
        }
    }

    #[test]
    fn duration_filter_rejects_invalid_seconds_before_media_io() {
        for filter in ["moving-average", "savitzky-golay"] {
            for value in ["0", "-0.1", "NaN", "inf", "-inf", "bad"] {
                let mut args = smoothing_args(
                    filter,
                    &["--filter-window-s", value, "--filter-max-gap-s", "0.2"],
                );
                if filter == "savitzky-golay" {
                    args.extend(strings(&["--filter-polynomial-order", "2"]));
                }
                let error = parse_args(args).unwrap_err();
                assert_eq!(error.kind(), CliErrorKind::InvalidInput);
                assert!(error.to_string().contains("--filter-window-s"));
            }
        }
    }

    #[test]
    fn duration_window_is_rejected_for_raw_and_kalman() {
        for filter in ["raw", "kalman"] {
            let error =
                parse_args(smoothing_args(filter, &["--filter-window-s", "0.25"])).unwrap_err();
            assert_eq!(error.kind(), CliErrorKind::InvalidInput);
            assert!(error.message().contains("--filter-window-s is not valid"));
        }
    }

    #[test]
    fn duration_filter_rejects_invalid_order_and_gap_before_media_io() {
        for (order, gap, message) in [
            ("6", "0.2", "supported maximum"),
            ("18446744073709551615", "0.2", "polynomial"),
            ("2", "0", "max_gap_s"),
            ("2", "NaN", "max_gap_s"),
        ] {
            let error = parse_args(smoothing_args(
                "savitzky-golay",
                &[
                    "--filter-window-s",
                    "0.25",
                    "--filter-polynomial-order",
                    order,
                    "--filter-max-gap-s",
                    gap,
                ],
            ))
            .unwrap_err();
            assert_eq!(error.kind(), CliErrorKind::InvalidInput);
            assert!(error.to_string().contains(message));
        }
    }

    #[test]
    fn duration_filter_requires_measured_rate() {
        let request = FilterRequest::MovingAverageDuration {
            window_s: 0.25,
            max_gap_s: 0.2,
        };
        let error = request.resolve(None).unwrap_err();
        assert_eq!(error.kind(), CliErrorKind::InvalidInput);
        assert!(error.to_string().contains("at least two selected frames"));
        assert_eq!(
            FilterRequest::Fixed(FilterConfig::Raw)
                .resolve(None)
                .unwrap(),
            FilterConfig::Raw
        );
    }

    #[test]
    fn duration_filter_single_selected_frame_fails_before_tracking() {
        if !ffmpeg_available() {
            return;
        }
        let output = scratch_file("duration-single-frame.json");
        let _ = fs::remove_file(&output);
        let mut args = fixture_args(&output);
        args.selection = Some(MediaTimeRange::try_new(0.0, 0.0).unwrap());
        args.filter = FilterRequest::MovingAverageDuration {
            window_s: 0.25,
            max_gap_s: 0.2,
        };
        // An impossible tracker configuration would fail if tracking ran first.
        args.tracker = Some(TrackerChoice::Contrast);
        args.contrast_min_seed_contrast = Some(1.0e9);
        let error = run(&args).unwrap_err();
        assert_eq!(error.kind(), CliErrorKind::InvalidInput);
        assert!(error.to_string().contains("at least two selected frames"));
        assert!(!output.exists());
    }

    #[test]
    fn duration_filter_end_to_end_matches_fixed_window_and_is_deterministic() {
        if !ffmpeg_available() {
            return;
        }
        let paths = [
            scratch_file("duration-a.json"),
            scratch_file("duration-b.json"),
            scratch_file("fixed-window.json"),
        ];
        for path in &paths {
            let _ = fs::remove_file(path);
        }
        for (index, path) in paths.iter().enumerate() {
            let mut args = fixture_args(path);
            args.filter = if index == 2 {
                FilterRequest::Fixed(FilterConfig::SavitzkyGolay {
                    window: 3,
                    window_s: None,
                    polynomial_order: 2,
                    max_gap_s: 0.2,
                })
            } else {
                FilterRequest::SavitzkyGolayDuration {
                    window_s: 0.25,
                    polynomial_order: 2,
                    max_gap_s: 0.2,
                }
            };
            run(&args).unwrap();
        }
        let bytes = paths
            .iter()
            .map(|path| fs::read(path).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(bytes[0], bytes[1]);
        let duration = Analysis::from_json(std::str::from_utf8(&bytes[0]).unwrap()).unwrap();
        let fixed = Analysis::from_json(std::str::from_utf8(&bytes[2]).unwrap()).unwrap();
        assert_eq!(duration.raw_observations(), fixed.raw_observations());
        let derived = duration.derived().filtered.as_ref().unwrap();
        let fixed_derived = fixed.derived().filtered.as_ref().unwrap();
        assert_eq!(derived.samples, fixed_derived.samples);
        let mut expected = fixed_derived.filter.clone();
        expected
            .parameters
            .insert("window_s".to_owned(), ParameterValue::Float(0.25));
        assert_eq!(derived.filter, expected);
        assert_eq!(
            derived.filter.parameters["window"],
            ParameterValue::Integer(3)
        );
        for path in paths {
            fs::remove_file(path).unwrap();
        }
    }

    #[test]
    fn parses_explicit_raw_pipeline_without_choosing_a_hidden_filter() {
        let parsed = parse_args(base_args("analysis.json"))
            .expect("arguments parse")
            .expect("not help");
        assert_eq!(parsed.tracker, Some(TrackerChoice::Template));
        assert!(parsed.observations.is_none());
        assert_eq!(parsed.filter, FilterRequest::Fixed(FilterConfig::Raw));
        assert_eq!(parsed.kinematics.max_gap_s, 0.2);
        assert_eq!(parsed.kinematics.min_confidence, 0.0);
    }

    #[test]
    fn tracker_and_observations_are_exactly_one_required() {
        let mut both = base_args("analysis.json");
        both.extend(strings(&["--observations", "prediction.json"]));
        let error = parse_args(both).expect_err("both measurement sources must fail");
        assert_eq!(error.kind(), CliErrorKind::InvalidInput);
        assert!(error.to_string().contains("mutually exclusive"));

        let mut neither = base_args("analysis.json");
        let tracker_index = neither
            .iter()
            .position(|arg| arg == "--tracker")
            .expect("tracker flag");
        neither.drain(tracker_index..=tracker_index + 1);
        let error = parse_args(neither).expect_err("missing measurement source must fail");
        assert_eq!(error.kind(), CliErrorKind::InvalidInput);
        assert!(error.to_string().contains("exactly one"));
    }

    #[test]
    fn observations_reject_internal_tracker_options() {
        let mut args = base_args("analysis.json");
        let tracker_index = args
            .iter()
            .position(|arg| arg == "--tracker")
            .expect("tracker flag");
        args.splice(
            tracker_index..=tracker_index + 1,
            strings(&["--observations", "prediction.json"]),
        );
        args.extend(strings(&["--tracker-search-radius-px", "20"]));
        let error = parse_args(args).expect_err("external observations own tracker configuration");
        assert_eq!(error.kind(), CliErrorKind::InvalidInput);
        assert!(error
            .to_string()
            .contains("tracker-specific options cannot be used with --observations"));
    }

    #[test]
    fn direct_video_requires_explicit_geometry_metadata() {
        let mut args = base_args("analysis.json");
        let view_index = args
            .iter()
            .position(|arg| arg == "--camera-view")
            .expect("camera view flag");
        args.drain(view_index..=view_index + 1);
        let error = parse_args(args).expect_err("missing direct geometry must fail");
        assert_eq!(error.kind(), CliErrorKind::InvalidInput);
        assert!(error.to_string().contains("--camera-view"));
    }

    #[test]
    fn fixture_metadata_cannot_be_silently_overridden() {
        let mut args = base_args("analysis.json");
        args[0] = "--manifest".to_owned();
        args[1] = "manifest.json".to_owned();
        args.extend(strings(&["--fixture", "synthetic-clean-side-12"]));
        let error = parse_args(args).expect_err("fixture camera override must fail");
        assert_eq!(error.kind(), CliErrorKind::InvalidInput);
        assert!(error
            .to_string()
            .contains("cannot override fixture metadata"));
    }

    #[test]
    fn output_paths_cannot_alias_explicit_inputs() {
        let error = parse_args(base_args("seed.json"))
            .expect_err("analysis output must not alias the seed input");
        assert_eq!(error.kind(), CliErrorKind::InvalidInput);
        assert!(error.to_string().contains("--output"));
        assert!(error.to_string().contains("--seed"));

        let mut args = base_args("analysis.json");
        args.extend(strings(&["--recording-support-output", "seed.json"]));
        let error = parse_args(args).expect_err("support output must not alias the seed input");
        assert_eq!(error.kind(), CliErrorKind::InvalidInput);
        assert!(error.to_string().contains("--recording-support-output"));
        assert!(error.to_string().contains("--seed"));
    }

    #[test]
    fn output_paths_cannot_alias_external_observations() {
        let mut args = base_args("prediction.json");
        let tracker_index = args
            .iter()
            .position(|arg| arg == "--tracker")
            .expect("tracker flag");
        args.splice(
            tracker_index..=tracker_index + 1,
            strings(&["--observations", "prediction.json"]),
        );
        let error =
            parse_args(args).expect_err("analysis output must not alias observations input");
        assert_eq!(error.kind(), CliErrorKind::InvalidInput);
        assert!(error.to_string().contains("--observations"));
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
        assert_eq!(error.kind(), CliErrorKind::SeedCalibration);
    }

    #[test]
    fn missing_video_is_classified_as_media_failure() {
        let output = scratch_file("missing-video.json");
        let mut values = base_args(&output.display().to_string());
        values[1] = scratch_file("definitely-missing.mp4").display().to_string();
        let seed_index = values
            .iter()
            .position(|arg| arg == "--seed")
            .expect("seed flag");
        values[seed_index + 1] = repo_path(
            "validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json",
        )
        .display()
        .to_string();
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
    fn fixture_plate_diameter_must_match_manifest_before_media_io() {
        let fixture = load_fixture(
            &repo_path("validation/fixtures/public/manifest.json"),
            "synthetic-clean-side-12",
        )
        .expect("fixture loads");

        validate_fixture_plate_diameter(&fixture, 0.45).expect("matching diameter is accepted");
        let error = validate_fixture_plate_diameter(&fixture, 0.50)
            .expect_err("mismatched fixture diameter must fail");
        assert_eq!(error.kind(), CliErrorKind::SeedCalibration);
        assert!(error.to_string().contains("load.plate_diameter_m"));
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
                    "nominal_fps": 12.0,
                    "width_px": 999,
                    "height_px": 96,
                    "rotation_deg": 0
                },
                "camera": {
                    "view": "side",
                    "movement": "fixed"
                },
                "load": {
                    "plate_diameter_m": 0.45
                },
                "conditions": {
                    "lighting": "good",
                    "plate_visibility": "clear",
                    "occlusion": "none",
                    "motion_blur": "none"
                }
            }]
        });
        fs::write(
            &manifest,
            format!(
                "{}\n",
                serde_json::to_string_pretty(&wrong_manifest).unwrap()
            ),
        )
        .expect("write manifest");

        let mut unsupported = fixture_args(&output);
        unsupported.manifest = Some(manifest.clone());
        let error = run(&unsupported).expect_err("fixture geometry mismatch must fail");
        assert_eq!(error.kind(), CliErrorKind::Unsupported);

        let mut tracking = fixture_args(&output);
        tracking.tracker = Some(TrackerChoice::Contrast);
        tracking.contrast_min_seed_contrast = Some(1.0e9);
        let error = run(&tracking).expect_err("impossible seed contrast must fail tracking");
        assert_eq!(error.kind(), CliErrorKind::Tracking);

        let _ = fs::remove_file(manifest);
        let _ = fs::remove_file(output);
    }

    #[test]
    fn external_observations_public_fixture_is_deterministic() {
        if !ffmpeg_available() {
            return;
        }
        let output_a = scratch_file("external-analysis-a.json");
        let output_b = scratch_file("external-analysis-b.json");
        let _ = fs::remove_file(&output_a);
        let _ = fs::remove_file(&output_b);

        let prediction = repo_path(
            "validation/fixtures/public/predictions/synthetic-perfect.prediction-v1.json",
        );
        let args_a = external_fixture_args(&output_a, &prediction);
        let args_b = external_fixture_args(&output_b, &prediction);
        run(&args_a).expect("first external analysis succeeds");
        run(&args_b).expect("second external analysis succeeds");

        let bytes_a = fs::read(&output_a).expect("first output");
        let bytes_b = fs::read(&output_b).expect("second output");
        assert_eq!(bytes_a, bytes_b, "external analysis must be deterministic");

        let parsed = Analysis::from_json(
            std::str::from_utf8(&bytes_a).expect("analysis JSON must be UTF-8"),
        )
        .expect("external output round-trips through canonical model");
        assert_eq!(parsed.schema_version(), 1);
        assert_eq!(parsed.provenance().tracker.id, "synthetic-perfect");
        assert_eq!(parsed.raw_observations().len(), 10);
        assert_eq!(parsed.raw_observations()[0].timestamp_s, 0.0);
        assert_eq!(
            parsed.raw_observations()[0]
                .measurement
                .expect("first sample tracked")
                .x_px,
            100.0
        );
        assert!(parsed
            .provenance()
            .tracker
            .implementation
            .parameters
            .contains_key("prediction_sha256"));
        assert!(parsed.derived().filtered.is_some());
        assert!(parsed.derived().kinematics.is_some());

        let _ = fs::remove_file(output_a);
        let _ = fs::remove_file(output_b);
    }

    #[test]
    fn invalid_external_observations_write_no_analysis() {
        if !ffmpeg_available() {
            return;
        }
        let output = scratch_file("external-invalid-output.json");
        let prediction = scratch_file("external-invalid-hash.prediction-v1.json");
        let _ = fs::remove_file(&output);
        let _ = fs::remove_file(&prediction);

        let source = repo_path(
            "validation/fixtures/public/predictions/synthetic-perfect.prediction-v1.json",
        );
        let mut document: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(source).expect("read public prediction"))
                .expect("parse public prediction");
        document["source_video_sha256"] = serde_json::Value::String(
            "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc".to_owned(),
        );
        fs::write(
            &prediction,
            format!(
                "{}\n",
                serde_json::to_string_pretty(&document).expect("serialize prediction")
            ),
        )
        .expect("write invalid prediction");

        let args = external_fixture_args(&output, &prediction);
        let error = run(&args).expect_err("wrong source hash must fail before output");
        assert_eq!(error.kind(), CliErrorKind::InvalidInput);
        assert!(error.to_string().contains("does not match decoded media"));
        assert!(
            !output.exists(),
            "invalid prediction must not create analysis output"
        );

        let _ = fs::remove_file(output);
        let _ = fs::remove_file(prediction);
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

    fn external_fixture_args(output: &Path, observations: &Path) -> Args {
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
            "--observations".to_owned(),
            observations.display().to_string(),
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
            .expect("external fixture args parse")
            .expect("not help")
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
