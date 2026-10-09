//! `tracker-run`: decode a manifest fixture and run both M0 baseline trackers on it (#40).
//!
//! Output is one tracker-prediction-v1 document per tracker, ready for the benchmark harness.
//! Decoder provenance travels inside `implementation.config.frame_source`.

use crate::media::{DecodedClip, FrameSourceOptions, ProbedVideo, StreamProvenance, TimeRange};
use openbar_core::manual_seed::{ManualTargetSeedDocument, SeedValidationContext};
use openbar_tracking::{
    LocalContrastConfig, LocalContrastTracker, ManualSeedTracker, TemplateMatchConfig,
    TemplateMatchTracker, TrackerObservationState, TrackerRun,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::env;
use std::error::Error;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

type AnyResult<T> = Result<T, Box<dyn Error>>;

const PREDICTION_SCHEMA_VERSION: u32 = 1;
const DEFAULT_MAX_FRAME_MEMORY_MIB: u64 = 2048;
/// Matches the annotation contract's decoder match tolerance, so a seed written with
/// rounded timestamps still resolves to exactly one decoded frame.
const SEED_TIMESTAMP_TOLERANCE_S: f64 = 0.0005;

const USAGE: &str = "Usage: openbar-cli tracker-run --manifest <path> --fixture <id> --seed <path> --output-dir <dir>\n\
     \x20      [--media <path>] [--start-s <s> --end-s <s>] [--max-frame-memory-mib <MiB>] [--search-radius-px <px>]\n\
     Decodes the fixture through ffmpeg/ffprobe (must be on PATH) and runs both baseline trackers.\n\
     Writes <dir>/<fixture>.<tracker>.prediction-v1.json per tracker; the summary goes to stderr.\n\
     Media defaults to the manifest's repository_path, resolved from the current directory.";

#[derive(Debug)]
struct Args {
    manifest: PathBuf,
    fixture_id: String,
    seed: PathBuf,
    output_dir: PathBuf,
    media: Option<PathBuf>,
    selection: Option<TimeRange>,
    max_frame_bytes: u64,
    search_radius_px: Option<u32>,
}

#[derive(Debug, Deserialize)]
struct FixtureManifest {
    schema_version: u32,
    fixtures: Vec<ManifestFixture>,
}

#[derive(Debug, Deserialize)]
struct ManifestFixture {
    id: String,
    #[serde(default)]
    media: Option<ManifestMedia>,
    video: ManifestVideo,
}

#[derive(Debug, Deserialize)]
struct ManifestMedia {
    #[serde(default)]
    repository_path: Option<String>,
    #[serde(default)]
    sha256: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ManifestVideo {
    width_px: u32,
    height_px: u32,
    #[serde(default)]
    rotation_deg: Option<u16>,
}

#[derive(Debug, Serialize)]
struct PredictionDocument<'a> {
    schema_version: u32,
    fixture_id: &'a str,
    source_video_sha256: &'a str,
    coordinate_space: &'static str,
    implementation: PredictionImplementation,
    runtime: PredictionRuntime,
    samples: Vec<PredictionSample>,
}

#[derive(Debug, Serialize)]
struct PredictionImplementation {
    name: String,
    version: String,
    config: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Serialize)]
struct PredictionRuntime {
    processing_wall_s: f64,
}

#[derive(Debug, Serialize)]
struct PredictionSample {
    timestamp_s: f64,
    state: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    center_px: Option<PredictionPoint>,
    #[serde(skip_serializing_if = "Option::is_none")]
    confidence: Option<f32>,
}

#[derive(Debug, Serialize)]
struct PredictionPoint {
    x_px: f64,
    y_px: f64,
}

pub fn run_cli() -> AnyResult<()> {
    let Some(args) = parse_args(env::args().skip(2).collect())? else {
        println!("{USAGE}");
        return Ok(());
    };
    run(&args)
}

fn run(args: &Args) -> AnyResult<()> {
    let fixture = load_fixture(&args.manifest, &args.fixture_id)?;
    let media_path = resolve_media_path(args, &fixture)?;
    let seed = read_seed(&args.seed, &fixture.id)?;
    let media_error = |error| data_error(format!("{}: {error}", media_path.display()));

    let decode_started = Instant::now();
    let probed = ProbedVideo::open(&media_path).map_err(media_error)?;
    check_fixture_matches_media(&fixture, probed.source_sha256(), &probed.stream())?;
    let clip = probed
        .decode(FrameSourceOptions {
            selection: args.selection,
            max_frame_bytes: args.max_frame_bytes,
        })
        .map_err(media_error)?;
    let decode_wall = decode_started.elapsed();

    let frame_timestamps_s = clip
        .frames
        .iter()
        .map(|frame| frame.timestamp_s)
        .collect::<Vec<_>>();
    validate_seed(
        &seed,
        &clip.provenance.stream,
        clip.provenance.selected_range_s,
        &frame_timestamps_s,
    )
    .map_err(|error| data_error(format!("manual seed '{}': {error}", args.seed.display())))?;

    fs::create_dir_all(&args.output_dir)?;
    run_trackers(args, &fixture.id, &clip, &seed, decode_wall)
}

fn run_trackers(
    args: &Args,
    fixture_id: &str,
    clip: &DecodedClip,
    seed: &ManualTargetSeedDocument,
    decode_wall: Duration,
) -> AnyResult<()> {
    let mut failures = Vec::new();
    for tracker in build_trackers(args.search_radius_px)? {
        let identity = tracker.identity();
        let track_started = Instant::now();
        match tracker.track(&clip.frame_samples(), seed.seed()) {
            Ok(run) => {
                let track_wall = track_started.elapsed();
                let path = args
                    .output_dir
                    .join(format!("{fixture_id}.{}.prediction-v1.json", identity.id));
                write_prediction(
                    &path,
                    fixture_id,
                    clip,
                    seed,
                    &run,
                    decode_wall + track_wall,
                )?;
                eprintln!("{}", summarize(&run, clip, decode_wall, track_wall, &path));
            }
            Err(error) => {
                eprintln!("tracker {} failed: {error}", identity.id);
                failures.push(format!("{}: {error}", identity.id));
            }
        }
    }
    if !clip.provenance.decoder_diagnostics.is_empty() {
        eprintln!(
            "warning: ffmpeg reported {} diagnostic line(s), recorded in frame_source.decoder_diagnostics",
            clip.provenance.decoder_diagnostics.len()
        );
    }
    if failures.is_empty() {
        Ok(())
    } else {
        Err(data_error(format!(
            "tracking failed for {} tracker(s): {}",
            failures.len(),
            failures.join("; ")
        )))
    }
}

fn parse_args(args: Vec<String>) -> AnyResult<Option<Args>> {
    let Some(mut values) = collect_flag_values(&args)? else {
        return Ok(None);
    };
    let mut required = |key: &str| {
        values
            .remove(key)
            .ok_or_else(|| data_error(format!("tracker-run requires {key}")))
    };
    let manifest = PathBuf::from(required("--manifest")?);
    let fixture_id = required("--fixture")?;
    let seed = PathBuf::from(required("--seed")?);
    let output_dir = PathBuf::from(required("--output-dir")?);

    let selection = match (values.remove("--start-s"), values.remove("--end-s")) {
        (None, None) => None,
        (Some(start), Some(end)) => Some(TimeRange::try_new(
            parse_number(&start, "--start-s")?,
            parse_number(&end, "--end-s")?,
        )?),
        _ => return Err(data_error("--start-s and --end-s must be given together")),
    };
    let max_frame_memory_mib = match values.remove("--max-frame-memory-mib") {
        Some(value) => parse_number::<u64>(&value, "--max-frame-memory-mib")?,
        None => DEFAULT_MAX_FRAME_MEMORY_MIB,
    };
    if max_frame_memory_mib == 0 {
        return Err(data_error("--max-frame-memory-mib must be positive"));
    }
    let search_radius_px = values
        .remove("--search-radius-px")
        .map(|value| parse_number::<u32>(&value, "--search-radius-px"))
        .transpose()?;

    Ok(Some(Args {
        manifest,
        fixture_id,
        seed,
        output_dir,
        media: values.remove("--media").map(PathBuf::from),
        selection,
        max_frame_bytes: max_frame_memory_mib.saturating_mul(1024 * 1024),
        search_radius_px,
    }))
}

/// Returns `None` for a help request; every flag takes exactly one value and may appear once.
fn collect_flag_values(args: &[String]) -> AnyResult<Option<BTreeMap<&'static str, String>>> {
    const FLAGS: [&str; 9] = [
        "--manifest",
        "--fixture",
        "--seed",
        "--output-dir",
        "--media",
        "--start-s",
        "--end-s",
        "--max-frame-memory-mib",
        "--search-radius-px",
    ];
    let mut values = BTreeMap::new();
    let mut index = 0usize;
    while index < args.len() {
        let flag = args[index].as_str();
        if matches!(flag, "--help" | "-h") {
            return Ok(None);
        }
        let key = FLAGS
            .into_iter()
            .find(|candidate| *candidate == flag)
            .ok_or_else(|| data_error(format!("unknown tracker-run argument '{flag}'")))?;
        let value = args
            .get(index + 1)
            .ok_or_else(|| data_error(format!("{key} requires a value")))?;
        if values.insert(key, value.clone()).is_some() {
            return Err(data_error(format!("{key} was given more than once")));
        }
        index += 2;
    }
    Ok(Some(values))
}

fn parse_number<T: std::str::FromStr>(value: &str, flag: &str) -> AnyResult<T> {
    value
        .parse::<T>()
        .map_err(|_| data_error(format!("{flag} value '{value}' is not a valid number")))
}

fn load_fixture(manifest_path: &Path, fixture_id: &str) -> AnyResult<ManifestFixture> {
    // Fixture ids become output file names, so enforce the manifest schema's id pattern here.
    if !is_valid_fixture_id(fixture_id) {
        return Err(data_error(format!(
            "fixture id '{fixture_id}' must match ^[a-z0-9][a-z0-9._-]*$"
        )));
    }
    let manifest: FixtureManifest = read_json(manifest_path)?;
    if manifest.schema_version != 1 {
        return Err(data_error(format!(
            "fixture manifest '{}' has unsupported schema version {}",
            manifest_path.display(),
            manifest.schema_version
        )));
    }
    manifest
        .fixtures
        .into_iter()
        .find(|fixture| fixture.id == fixture_id)
        .ok_or_else(|| {
            data_error(format!(
                "fixture '{fixture_id}' was not found in '{}'",
                manifest_path.display()
            ))
        })
}

fn resolve_media_path(args: &Args, fixture: &ManifestFixture) -> AnyResult<PathBuf> {
    if let Some(path) = &args.media {
        return Ok(path.clone());
    }
    fixture
        .media
        .as_ref()
        .and_then(|media| media.repository_path.as_deref())
        .map(PathBuf::from)
        .ok_or_else(|| {
            data_error(format!(
                "fixture '{}' has no media.repository_path; pass --media <path>",
                fixture.id
            ))
        })
}

/// The manifest describes the media; any disagreement means the wrong file or a stale record.
fn check_fixture_matches_media(
    fixture: &ManifestFixture,
    source_sha256: &str,
    stream: &StreamProvenance,
) -> AnyResult<()> {
    if let Some(expected) = fixture
        .media
        .as_ref()
        .and_then(|media| media.sha256.as_deref())
    {
        if !expected.eq_ignore_ascii_case(source_sha256) {
            return Err(data_error(format!(
                "media SHA-256 {source_sha256} does not match fixture '{}' manifest value {expected}",
                fixture.id
            )));
        }
    }
    // The manifest records the encoded raster (docs/validation/ANNOTATION.md), not the display.
    let coded = (stream.coded_width_px, stream.coded_height_px);
    if coded != (fixture.video.width_px, fixture.video.height_px) {
        return Err(data_error(format!(
            "encoded size {}x{} does not match fixture '{}' manifest size {}x{}",
            coded.0, coded.1, fixture.id, fixture.video.width_px, fixture.video.height_px
        )));
    }
    let manifest_rotation = fixture.video.rotation_deg.unwrap_or(0);
    if manifest_rotation != stream.rotation_deg {
        return Err(data_error(format!(
            "media rotation {} does not match fixture '{}' manifest rotation {manifest_rotation}",
            stream.rotation_deg, fixture.id
        )));
    }
    Ok(())
}

fn read_seed(seed_path: &Path, fixture_id: &str) -> AnyResult<ManualTargetSeedDocument> {
    let seed: ManualTargetSeedDocument = read_json(seed_path)?;
    if let Some(seed_fixture_id) = seed.fixture_id() {
        if seed_fixture_id != fixture_id {
            return Err(data_error(format!(
                "manual seed '{}' references fixture '{seed_fixture_id}' instead of '{fixture_id}'",
                seed_path.display()
            )));
        }
    }
    Ok(seed)
}

/// Validates the seed against the decoded display frame and requires a selected frame within
/// the seed tolerance, so a mistyped timestamp or a range that cuts off the seed frame fails
/// with an explanation instead of an opaque tracker error.
fn validate_seed(
    seed: &ManualTargetSeedDocument,
    stream: &StreamProvenance,
    selection: Option<TimeRange>,
    frame_timestamps_s: &[f64],
) -> AnyResult<()> {
    let (Some(&first_s), Some(&last_s)) = (frame_timestamps_s.first(), frame_timestamps_s.last())
    else {
        return Err(data_error("no decoded frames to validate the seed against"));
    };
    // Without an explicit range, the decoded span widened by the tolerance is the valid window.
    let range = selection.unwrap_or(TimeRange {
        start_s: (first_s - SEED_TIMESTAMP_TOLERANCE_S).max(0.0),
        end_s: last_s + SEED_TIMESTAMP_TOLERANCE_S,
    });
    seed.validate(SeedValidationContext {
        frame_width_px: stream.display_width_px,
        frame_height_px: stream.display_height_px,
        selected_range_start_s: range.start_s,
        selected_range_end_s: range.end_s,
        source_rotation_deg: stream.rotation_deg,
    })?;

    let seed_s = seed.seed().timestamp_s();
    let nearest_gap_s = frame_timestamps_s
        .iter()
        .map(|timestamp_s| (timestamp_s - seed_s).abs())
        .fold(f64::INFINITY, f64::min);
    if nearest_gap_s > SEED_TIMESTAMP_TOLERANCE_S {
        return Err(data_error(format!(
            "seed timestamp {seed_s} s has no selected decoded frame within \
             {SEED_TIMESTAMP_TOLERANCE_S} s (nearest is {nearest_gap_s:.6} s away); \
             use a decoded frame timestamp and keep it inside the selected range"
        )));
    }
    Ok(())
}

fn build_trackers(search_radius_px: Option<u32>) -> AnyResult<Vec<Box<dyn ManualSeedTracker>>> {
    let template_defaults = TemplateMatchConfig::default();
    let contrast_defaults = LocalContrastConfig::default();
    let template = TemplateMatchTracker::try_new(TemplateMatchConfig {
        search_radius_px: search_radius_px.unwrap_or(template_defaults.search_radius_px),
        seed_timestamp_tolerance_s: SEED_TIMESTAMP_TOLERANCE_S,
        ..template_defaults
    })?;
    let contrast = LocalContrastTracker::try_new(LocalContrastConfig {
        search_radius_px: search_radius_px.unwrap_or(contrast_defaults.search_radius_px),
        seed_timestamp_tolerance_s: SEED_TIMESTAMP_TOLERANCE_S,
        ..contrast_defaults
    })?;
    Ok(vec![Box::new(template), Box::new(contrast)])
}

fn write_prediction(
    path: &Path,
    fixture_id: &str,
    clip: &DecodedClip,
    seed: &ManualTargetSeedDocument,
    run: &TrackerRun,
    processing_wall: Duration,
) -> AnyResult<()> {
    let document = PredictionDocument {
        schema_version: PREDICTION_SCHEMA_VERSION,
        fixture_id,
        source_video_sha256: &clip.provenance.source_sha256,
        coordinate_space: "decoded_display_pixels",
        implementation: prediction_implementation(clip, seed, run)?,
        runtime: PredictionRuntime {
            processing_wall_s: processing_wall.as_secs_f64().max(f64::MIN_POSITIVE),
        },
        samples: run.observations.iter().map(prediction_sample).collect(),
    };
    let serialized = serde_json::to_string_pretty(&document)?;
    fs::write(path, format!("{serialized}\n"))?;
    Ok(())
}

fn prediction_implementation(
    clip: &DecodedClip,
    seed: &ManualTargetSeedDocument,
    run: &TrackerRun,
) -> AnyResult<PredictionImplementation> {
    let tracker = &run.tracker;
    let mut config = BTreeMap::new();
    config.insert(
        "tracker_implementation".to_owned(),
        serde_json::Value::from(tracker.implementation.clone()),
    );
    config.insert(
        "tracker_config".to_owned(),
        serde_json::to_value(&tracker.config)?,
    );
    config.insert(
        "confidence_semantics".to_owned(),
        serde_json::Value::from(tracker.confidence_semantics.clone()),
    );
    config.insert(
        "frame_source".to_owned(),
        serde_json::to_value(&clip.provenance)?,
    );
    config.insert("manual_seed".to_owned(), serde_json::to_value(seed.seed())?);
    Ok(PredictionImplementation {
        name: tracker.id.clone(),
        version: tracker.version.clone(),
        config,
    })
}

/// Low-confidence observations stay `tracked` with their reported confidence, mirroring
/// `TrackerRun::benchmark_predictions`; lost observations carry no coordinate.
fn prediction_sample(observation: &openbar_tracking::TrackerObservation) -> PredictionSample {
    match observation.state {
        TrackerObservationState::Tracked { center, confidence }
        | TrackerObservationState::LowConfidence { center, confidence } => PredictionSample {
            timestamp_s: observation.timestamp_s,
            state: "tracked",
            center_px: Some(PredictionPoint {
                x_px: center.x_px(),
                y_px: center.y_px(),
            }),
            confidence: Some(confidence),
        },
        TrackerObservationState::Lost { .. } => PredictionSample {
            timestamp_s: observation.timestamp_s,
            state: "lost",
            center_px: None,
            confidence: None,
        },
    }
}

fn summarize(
    run: &TrackerRun,
    clip: &DecodedClip,
    decode_wall: Duration,
    track_wall: Duration,
    path: &Path,
) -> String {
    let mut tracked = 0usize;
    let mut low_confidence = 0usize;
    let mut lost = 0usize;
    let mut first_lost_s = None;
    for observation in &run.observations {
        match observation.state {
            TrackerObservationState::Tracked { .. } => tracked += 1,
            TrackerObservationState::LowConfidence { .. } => low_confidence += 1,
            TrackerObservationState::Lost { .. } => {
                lost += 1;
                first_lost_s.get_or_insert(observation.timestamp_s);
            }
        }
    }
    let provenance = &clip.provenance;
    let media_s = provenance.last_selected_timestamp_s - provenance.first_selected_timestamp_s;
    let total_s = (decode_wall + track_wall).as_secs_f64();
    let observed_range = match (run.observations.first(), run.observations.last()) {
        (Some(first), Some(last)) => format!("{:.3}-{:.3} s", first.timestamp_s, last.timestamp_s),
        _ => "none".to_owned(),
    };
    // Trackers observe from the seed frame onward, so this can be fewer than the decoded frames.
    format!(
        "{}: {} observations ({observed_range}) of {} selected frames; tracked {tracked}, low-confidence {low_confidence}, lost {lost}{}\n  \
         decode {:.2} s + track {:.2} s for {media_s:.2} s of selected media ({:.2}x real time) -> {}",
        run.tracker.id,
        run.observations.len(),
        provenance.selected_frame_count,
        first_lost_s.map_or(String::new(), |s| format!(" (first lost at {s:.3} s)")),
        decode_wall.as_secs_f64(),
        track_wall.as_secs_f64(),
        if total_s > 0.0 { media_s / total_s } else { 0.0 },
        path.display()
    )
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

fn read_json<T: serde::de::DeserializeOwned>(path: &Path) -> AnyResult<T> {
    let content = fs::read_to_string(path)
        .map_err(|error| data_error(format!("failed to read '{}': {error}", path.display())))?;
    serde_json::from_str(&content)
        .map_err(|error| data_error(format!("failed to parse '{}': {error}", path.display())))
}

fn data_error(message: impl Into<String>) -> Box<dyn Error> {
    io::Error::new(io::ErrorKind::InvalidData, message.into()).into()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_owned()).collect()
    }

    const REQUIRED: [&str; 8] = [
        "--manifest",
        "m.json",
        "--fixture",
        "fx",
        "--seed",
        "s.json",
        "--output-dir",
        "out",
    ];

    fn parse(extra: &[&str]) -> AnyResult<Option<Args>> {
        parse_args(args(&[&REQUIRED[..], extra].concat()))
    }

    #[test]
    fn parses_required_and_optional_arguments() {
        let parsed = parse(&[
            "--start-s",
            "1.5",
            "--end-s",
            "4",
            "--max-frame-memory-mib",
            "64",
            "--search-radius-px",
            "40",
            "--media",
            "clip.mp4",
        ])
        .expect("arguments parse")
        .expect("not a help request");
        assert_eq!(parsed.fixture_id, "fx");
        assert_eq!(parsed.output_dir, PathBuf::from("out"));
        assert_eq!(parsed.media, Some(PathBuf::from("clip.mp4")));
        assert_eq!(
            parsed.selection,
            Some(TimeRange {
                start_s: 1.5,
                end_s: 4.0
            })
        );
        assert_eq!(parsed.max_frame_bytes, 64 * 1024 * 1024);
        assert_eq!(parsed.search_radius_px, Some(40));
    }

    #[test]
    fn defaults_to_whole_clip_and_two_gib_budget() {
        let parsed = parse(&[]).expect("parses").expect("not help");
        assert_eq!(parsed.selection, None);
        assert_eq!(parsed.max_frame_bytes, 2048 * 1024 * 1024);
        assert_eq!(parsed.search_radius_px, None);
    }

    #[test]
    fn rejects_invalid_arguments() {
        for extra in [
            &["--start-s", "1"][..],
            &["--start-s", "2", "--end-s", "1"],
            &["--start-s", "x", "--end-s", "1"],
            &["--max-frame-memory-mib", "0"],
            &["--search-radius-px", "-3"],
            &["--fixture", "again"],
            &["--bogus", "1"],
            &["--media"],
        ] {
            assert!(parse(extra).is_err(), "accepted {extra:?}");
        }
        assert!(parse_args(args(&["--fixture", "fx"])).is_err());
        assert!(parse(&["--help"]).expect("help parses").is_none());
    }

    const SYNTHETIC_SHA256: &str =
        "a175d350c96db3df1771c1eb141a017eaed012ae6bbd6cda739510b244113096";

    fn repo_path(relative: &str) -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .join(relative)
    }

    fn stream(coded: (u32, u32), rotation_deg: u16) -> StreamProvenance {
        let display = if rotation_deg % 180 == 90 {
            (coded.1, coded.0)
        } else {
            coded
        };
        StreamProvenance {
            stream_index: 0,
            codec_name: "h264".to_owned(),
            pix_fmt: "yuv420p".to_owned(),
            color_range: None,
            color_transfer: None,
            coded_width_px: coded.0,
            coded_height_px: coded.1,
            display_width_px: display.0,
            display_height_px: display.1,
            rotation_deg,
            rotation_convention: "ffmpeg_display_matrix_counter_clockwise",
            time_base: crate::media::TimeBase {
                num: 1,
                den: 12_288,
            },
            start_pts: 0,
        }
    }

    fn fixture(
        sha256: Option<&str>,
        size: (u32, u32),
        rotation_deg: Option<u16>,
    ) -> ManifestFixture {
        ManifestFixture {
            id: "fx".to_owned(),
            media: Some(ManifestMedia {
                repository_path: None,
                sha256: sha256.map(str::to_owned),
            }),
            video: ManifestVideo {
                width_px: size.0,
                height_px: size.1,
                rotation_deg,
            },
        }
    }

    fn synthetic_seed() -> ManualTargetSeedDocument {
        read_seed(
            &repo_path(
                "validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json",
            ),
            "synthetic-clean-side-12",
        )
        .expect("committed seed is valid")
    }

    fn error_text<T>(result: AnyResult<T>) -> String {
        match result {
            Ok(_) => panic!("expected an error"),
            Err(error) => error.to_string(),
        }
    }

    #[test]
    fn fixture_ids_follow_the_manifest_pattern() {
        for valid in ["synthetic-clean-side-12", "a", "0.v_1-x"] {
            assert!(is_valid_fixture_id(valid), "{valid}");
        }
        for invalid in ["", "-x", ".x", "A", "a/b", "a\\b", "a b", "é"] {
            assert!(!is_valid_fixture_id(invalid), "{invalid}");
        }
        assert!(error_text(load_fixture(Path::new("unused.json"), "../x")).contains("must match"));
    }

    #[test]
    fn manifest_checks_compare_hash_encoded_size_and_rotation() {
        let rotated = stream((1280, 720), 90);
        assert!(check_fixture_matches_media(
            &fixture(Some("ABCD"), (1280, 720), Some(90)),
            "abcd",
            &rotated
        )
        .is_ok());
        assert!(
            check_fixture_matches_media(&fixture(None, (1280, 720), Some(90)), "x", &rotated)
                .is_ok()
        );
        assert!(error_text(check_fixture_matches_media(
            &fixture(Some("abcd"), (1280, 720), Some(90)),
            "abce",
            &rotated
        ))
        .contains("SHA-256"));
        // A manifest that records the display size instead of the encoded raster is rejected.
        assert!(error_text(check_fixture_matches_media(
            &fixture(None, (720, 1280), Some(90)),
            "x",
            &rotated
        ))
        .contains("encoded size"));
        assert!(error_text(check_fixture_matches_media(
            &fixture(None, (1280, 720), None),
            "x",
            &rotated
        ))
        .contains("rotation"));
    }

    #[test]
    fn seed_validation_uses_display_frame_and_requires_a_nearby_decoded_frame() {
        let seed = synthetic_seed();
        let timestamps = (0..12).map(|k| f64::from(k) / 12.0).collect::<Vec<_>>();
        assert!(validate_seed(&seed, &stream((320, 240), 0), None, &timestamps).is_ok());

        let range = TimeRange::try_new(0.0, 0.5).expect("valid range");
        let message = error_text(validate_seed(
            &seed,
            &stream((320, 240), 0),
            Some(range),
            &[0.01, 0.1],
        ));
        assert!(message.contains("no selected decoded frame"), "{message}");

        let message = error_text(validate_seed(
            &seed,
            &stream((240, 320), 90),
            None,
            &timestamps,
        ));
        assert!(message.contains("rotation"), "{message}");

        assert!(validate_seed(&seed, &stream((320, 240), 0), None, &[]).is_err());
    }

    #[test]
    fn seed_for_another_fixture_is_rejected() {
        let message = error_text(read_seed(
            &repo_path(
                "validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json",
            ),
            "other-fixture",
        ));
        assert!(message.contains("references fixture"), "{message}");
    }

    #[test]
    fn lost_observations_serialize_without_coordinate_or_confidence() {
        let sample = prediction_sample(&openbar_tracking::TrackerObservation {
            timestamp_s: 0.5,
            frame_index: Some(6),
            state: TrackerObservationState::Lost {
                reason: openbar_tracking::TrackerLossReason::PoorMatch,
            },
            visibility: openbar_tracking::TrackerVisibilityState::Unknown,
            target_bounds_px: None,
            diagnostics: openbar_tracking::TrackerDiagnostics {
                quality_score: None,
                displacement_px: None,
                reacquired_after_loss: false,
            },
        });
        assert_eq!(
            serde_json::to_value(&sample).expect("serializes"),
            serde_json::json!({"timestamp_s": 0.5, "state": "lost"})
        );
    }

    fn ffmpeg_available() -> bool {
        let available = ["ffmpeg", "ffprobe"].iter().all(|tool| {
            std::process::Command::new(tool)
                .arg("-version")
                .output()
                .is_ok_and(|output| output.status.success())
        });
        if !available {
            assert!(
                env::var_os("OPENBAR_REQUIRE_FFMPEG").is_none(),
                "OPENBAR_REQUIRE_FFMPEG is set but ffmpeg/ffprobe are not on PATH"
            );
            eprintln!("SKIPPED: ffmpeg/ffprobe not on PATH; tracker-run end-to-end test not run");
        }
        available
    }

    fn synthetic_args(manifest: PathBuf, output_dir: PathBuf) -> Args {
        Args {
            manifest,
            fixture_id: "synthetic-clean-side-12".to_owned(),
            seed: repo_path(
                "validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json",
            ),
            output_dir,
            media: Some(repo_path("validation/fixtures/public/synthetic-clean-side-12.mp4")),
            selection: None,
            max_frame_bytes: DEFAULT_MAX_FRAME_MEMORY_MIB * 1024 * 1024,
            search_radius_px: None,
        }
    }

    #[test]
    fn end_to_end_run_writes_one_prediction_per_tracker() {
        if !ffmpeg_available() {
            return;
        }
        let output_dir =
            env::temp_dir().join(format!("openbar-tracker-run-{}", std::process::id()));
        let _ = fs::remove_dir_all(&output_dir);
        let args = synthetic_args(
            repo_path("validation/fixtures/public/manifest.json"),
            output_dir.clone(),
        );
        run(&args).expect("synthetic fixture runs end to end");

        let trackers = ["template-sad-v1", "local-contrast-centroid-v1"];
        let documents: Vec<(&str, serde_json::Value)> = trackers
            .iter()
            .map(|&tracker| {
                let path = output_dir.join(format!(
                    "synthetic-clean-side-12.{tracker}.prediction-v1.json"
                ));
                let content = fs::read_to_string(&path).expect("prediction written");
                let document: serde_json::Value =
                    serde_json::from_str(&content).expect("prediction is JSON");
                (tracker, document)
            })
            .collect();

        for (tracker, document) in documents {
            assert_eq!(document["source_video_sha256"], SYNTHETIC_SHA256);
            assert_eq!(document["implementation"]["name"], tracker);
            assert_eq!(document["samples"].as_array().map(Vec::len), Some(12));
            assert_eq!(
                document["implementation"]["config"]["frame_source"]["selected_frame_count"],
                12
            );
        }
        let _ = fs::remove_dir_all(&output_dir);
    }

    #[test]
    fn end_to_end_run_rejects_media_that_does_not_match_the_manifest() {
        if !ffmpeg_available() {
            return;
        }
        let scratch =
            env::temp_dir().join(format!("openbar-tracker-run-bad-{}", std::process::id()));
        fs::create_dir_all(&scratch).expect("scratch directory");
        let manifest = fs::read_to_string(repo_path("validation/fixtures/public/manifest.json"))
            .expect("manifest readable")
            .replace(SYNTHETIC_SHA256, &"0".repeat(64));
        let manifest_path = scratch.join("manifest.json");
        fs::write(&manifest_path, manifest).expect("manifest written");

        let output_dir = scratch.join("out");
        let message = error_text(run(&synthetic_args(manifest_path, output_dir.clone())));
        assert!(message.contains("SHA-256"), "{message}");
        assert!(
            !output_dir.exists(),
            "no output may be written for mismatched media"
        );
        let _ = fs::remove_dir_all(&scratch);
    }
}
