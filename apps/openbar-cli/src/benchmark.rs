use crate::cli_error::{CliError, CliResult};
use openbar_core::benchmark::{
    aggregate_metrics, evaluate_tracker_case, BenchmarkParameters, GroundTruthSample,
    TrackerMetrics, TrackerPrediction, TrackerPredictionState, BENCHMARK_METRIC_VERSION,
};
use openbar_core::manual_seed::{ManualTargetSeedDocument, PixelPoint, SeedValidationContext};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::error::Error;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

const BENCHMARK_DOCUMENT_SCHEMA_VERSION: u32 = 1;
const PREDICTION_DOCUMENT_SCHEMA_VERSION: u32 = 1;

type AnyResult<T> = Result<T, Box<dyn Error>>;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct BenchmarkSuiteDocument {
    schema_version: u32,
    pipeline_version: String,
    #[serde(default)]
    git_commit: Option<String>,
    cases: Vec<BenchmarkCaseSpec>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct BenchmarkCaseSpec {
    id: String,
    fixture_manifest: String,
    fixture_id: String,
    annotations: String,
    #[serde(default)]
    manual_seed: Option<String>,
    predictions: String,
    selected_range_s: SelectedRange,
    timestamp_tolerance_s: f64,
    min_confidence: f32,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SelectedRange {
    start_s: f64,
    end_s: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ImplementationIdentity {
    name: String,
    version: String,
    #[serde(default)]
    config: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Deserialize)]
struct FixtureManifestDocument {
    schema_version: u32,
    fixtures: Vec<FixtureEntry>,
}

#[derive(Debug, Clone, Deserialize)]
struct FixtureEntry {
    id: String,
    exercise: String,
    media: FixtureMedia,
    video: FixtureVideo,
    camera: FixtureCamera,
    conditions: FixtureConditions,
}

#[derive(Debug, Clone, Deserialize)]
struct FixtureMedia {
    #[serde(default)]
    sha256: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct FixtureVideo {
    width_px: u32,
    height_px: u32,
    duration_s: f64,
    rotation_deg: u16,
}

fn fixture_display_dimensions(fixture: &FixtureEntry) -> (u32, u32) {
    match fixture.video.rotation_deg {
        90 | 270 => (fixture.video.height_px, fixture.video.width_px),
        _ => (fixture.video.width_px, fixture.video.height_px),
    }
}

#[derive(Debug, Clone, Deserialize)]
struct FixtureCamera {
    view: String,
}

#[derive(Debug, Clone, Deserialize)]
struct FixtureConditions {
    lighting: String,
    plate_visibility: String,
    occlusion: String,
    motion_blur: String,
    #[serde(default)]
    challenge_tags: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct AnnotationDocument {
    schema_version: u32,
    fixture_id: String,
    #[serde(default)]
    source_video_sha256: Option<String>,
    coordinate_system: AnnotationCoordinateSystem,
    samples: Vec<AnnotationSample>,
}

#[derive(Debug, Deserialize)]
struct AnnotationCoordinateSystem {
    width_px: u32,
    height_px: u32,
}

#[derive(Debug, Deserialize)]
struct AnnotationSample {
    timestamp_s: f64,
    annotation_state: String,
    quality: String,
    #[serde(default)]
    center_px: Option<JsonPoint>,
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(deny_unknown_fields)]
struct JsonPoint {
    x_px: f64,
    y_px: f64,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PredictionDocument {
    schema_version: u32,
    fixture_id: String,
    #[serde(default)]
    source_video_sha256: Option<String>,
    coordinate_space: String,
    implementation: ImplementationIdentity,
    #[serde(default)]
    runtime: Option<PredictionRuntime>,
    samples: Vec<PredictionSample>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PredictionRuntime {
    processing_wall_s: f64,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PredictionSample {
    timestamp_s: f64,
    state: PredictionState,
    #[serde(default)]
    center_px: Option<JsonPoint>,
    #[serde(default)]
    confidence: Option<f32>,
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
enum PredictionState {
    Tracked,
    Lost,
}

#[derive(Debug, Serialize)]
struct BenchmarkArtifact {
    schema_version: u32,
    benchmark_metric_version: &'static str,
    pipeline_version: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    git_commit: Option<String>,
    environment: RunnerEnvironment,
    cases: Vec<CaseResult>,
    aggregates: Vec<AggregateResult>,
}

#[derive(Debug, Serialize)]
struct RunnerEnvironment {
    openbar_cli_version: &'static str,
    os: &'static str,
    arch: &'static str,
}

#[derive(Debug, Clone, Serialize)]
struct CaseResult {
    case_id: String,
    fixture_id: String,
    exercise: String,
    camera_view: String,
    conditions: Vec<String>,
    implementation: ImplementationIdentity,
    selected_range_s: SelectedRange,
    alignment: AlignmentPolicy,
    metrics: TrackerMetrics,
    #[serde(skip_serializing_if = "Option::is_none")]
    runtime: Option<RuntimeReport>,
    warnings: Vec<String>,
}

#[derive(Debug, Clone, Copy, Serialize)]
struct AlignmentPolicy {
    timestamp_tolerance_s: f64,
    min_confidence: f32,
}

#[derive(Debug, Clone, Copy, Serialize)]
struct RuntimeReport {
    processing_wall_s: f64,
    selected_media_duration_s: f64,
    media_seconds_per_wall_second: f64,
}

#[derive(Debug, Serialize)]
struct AggregateResult {
    implementation: ImplementationIdentity,
    group: String,
    case_count: usize,
    metrics: TrackerMetrics,
}

pub fn run_cli() -> CliResult<()> {
    let args = env::args().skip(1).collect::<Vec<_>>();
    if args.is_empty() {
        println!("OpenBar M0 foundation");
        println!(
            "Use 'openbar-cli benchmark --suite <path> [--output <path>]' to run the benchmark harness."
        );
        return Ok(());
    }

    if args[0] != "benchmark" {
        return Err(CliError::invalid_input(format!(
            "unknown command '{}'; expected 'benchmark'",
            args[0]
        )));
    }

    let mut suite_path: Option<PathBuf> = None;
    let mut output_path: Option<PathBuf> = None;
    let mut index = 1usize;
    while index < args.len() {
        match args[index].as_str() {
            "--suite" => {
                index += 1;
                let value = args
                    .get(index)
                    .ok_or_else(|| CliError::invalid_input("--suite requires a path"))?;
                suite_path = Some(PathBuf::from(value));
            }
            "--output" => {
                index += 1;
                let value = args
                    .get(index)
                    .ok_or_else(|| CliError::invalid_input("--output requires a path"))?;
                output_path = Some(PathBuf::from(value));
            }
            "--help" | "-h" => {
                println!(
                    "Usage: openbar-cli benchmark --suite <path> [--output <path>]\n\
                     Writes the versioned JSON artifact to --output or stdout; the concise report goes to stderr."
                );
                return Ok(());
            }
            other => {
                return Err(CliError::invalid_input(format!(
                    "unknown benchmark argument '{other}'"
                )));
            }
        }
        index += 1;
    }

    let suite_path =
        suite_path.ok_or_else(|| CliError::invalid_input("benchmark requires --suite <path>"))?;
    let artifact = run_suite(&suite_path)
        .map_err(|error| CliError::benchmark(format!("benchmark data/config mismatch: {error}")))?;
    eprintln!("{}", render_summary(&artifact));

    let serialized = serde_json::to_string_pretty(&artifact).map_err(|error| {
        CliError::output(format!("failed to serialize benchmark output: {error}"))
    })?;
    if let Some(output_path) = output_path {
        if let Some(parent) = output_path.parent() {
            if !parent.as_os_str().is_empty() {
                fs::create_dir_all(parent).map_err(|error| {
                    CliError::output(format!(
                        "failed to create benchmark output directory '{}': {error}",
                        parent.display()
                    ))
                })?;
            }
        }
        fs::write(&output_path, format!("{serialized}\n")).map_err(|error| {
            CliError::output(format!(
                "failed to write benchmark output '{}': {error}",
                output_path.display()
            ))
        })?;
    } else {
        println!("{serialized}");
    }

    Ok(())
}

fn run_suite(suite_path: &Path) -> AnyResult<BenchmarkArtifact> {
    let suite: BenchmarkSuiteDocument = read_json(suite_path)?;
    if suite.schema_version != BENCHMARK_DOCUMENT_SCHEMA_VERSION {
        return Err(data_error(format!(
            "benchmark suite schema version {} is unsupported; expected {}",
            suite.schema_version, BENCHMARK_DOCUMENT_SCHEMA_VERSION
        )));
    }
    if suite.pipeline_version.trim().is_empty() {
        return Err(data_error("pipeline_version must not be blank"));
    }
    if suite.cases.is_empty() {
        return Err(data_error("benchmark suite must contain at least one case"));
    }

    let suite_dir = suite_path.parent().unwrap_or_else(|| Path::new("."));
    let mut seen_case_ids = BTreeSet::new();
    let mut cases = Vec::with_capacity(suite.cases.len());

    for spec in &suite.cases {
        if spec.id.trim().is_empty() {
            return Err(data_error("benchmark case id must not be blank"));
        }
        if !seen_case_ids.insert(spec.id.as_str()) {
            return Err(data_error(format!(
                "duplicate benchmark case id '{}'",
                spec.id
            )));
        }
        cases.push(run_case(spec, suite_dir)?);
    }

    let aggregates = build_aggregates(&cases)?;
    let git_commit = suite
        .git_commit
        .filter(|value| !value.trim().is_empty())
        .or_else(|| env::var("GITHUB_SHA").ok())
        .or_else(|| env::var("OPENBAR_GIT_COMMIT").ok());

    Ok(BenchmarkArtifact {
        schema_version: BENCHMARK_DOCUMENT_SCHEMA_VERSION,
        benchmark_metric_version: BENCHMARK_METRIC_VERSION,
        pipeline_version: suite.pipeline_version,
        git_commit,
        environment: RunnerEnvironment {
            openbar_cli_version: env!("CARGO_PKG_VERSION"),
            os: env::consts::OS,
            arch: env::consts::ARCH,
        },
        cases,
        aggregates,
    })
}

fn run_case(spec: &BenchmarkCaseSpec, suite_dir: &Path) -> AnyResult<CaseResult> {
    validate_selected_range(spec.selected_range_s)?;

    let fixture = load_fixture(spec, suite_dir)?;
    validate_manual_seed(spec, suite_dir, &fixture)?;

    let ground_truth = load_ground_truth(spec, suite_dir, &fixture)?;
    let (predictions, tracker_predictions) = load_predictions(spec, suite_dir, &fixture)?;

    let metrics = evaluate_tracker_case(
        &ground_truth,
        &tracker_predictions,
        BenchmarkParameters {
            timestamp_tolerance_s: spec.timestamp_tolerance_s,
            min_confidence: spec.min_confidence,
        },
    )
    .map_err(|error| {
        data_error(format!(
            "benchmark case '{}' failed metric evaluation: {error}",
            spec.id
        ))
    })?;

    let warnings = collect_case_warnings(spec, &metrics);
    let runtime = build_runtime_report(&predictions, spec, suite_dir)?;

    Ok(CaseResult {
        case_id: spec.id.clone(),
        fixture_id: fixture.id.clone(),
        exercise: fixture.exercise.clone(),
        camera_view: fixture.camera.view.clone(),
        conditions: condition_labels(&fixture),
        implementation: predictions.implementation,
        selected_range_s: spec.selected_range_s,
        alignment: AlignmentPolicy {
            timestamp_tolerance_s: spec.timestamp_tolerance_s,
            min_confidence: spec.min_confidence,
        },
        metrics,
        runtime,
        warnings,
    })
}

fn load_fixture(spec: &BenchmarkCaseSpec, suite_dir: &Path) -> AnyResult<FixtureEntry> {
    let manifest_path = resolve_path(suite_dir, &spec.fixture_manifest);
    let manifest: FixtureManifestDocument = read_json(&manifest_path)?;
    if manifest.schema_version != 1 {
        return Err(data_error(format!(
            "fixture manifest '{}' has unsupported schema version {}",
            manifest_path.display(),
            manifest.schema_version
        )));
    }

    let fixture = manifest
        .fixtures
        .into_iter()
        .find(|fixture| fixture.id == spec.fixture_id)
        .ok_or_else(|| {
            data_error(format!(
                "fixture '{}' was not found in '{}'",
                spec.fixture_id,
                manifest_path.display()
            ))
        })?;

    if spec.selected_range_s.end_s > fixture.video.duration_s {
        return Err(data_error(format!(
            "case '{}' selected range ends at {}s beyond fixture duration {}s",
            spec.id, spec.selected_range_s.end_s, fixture.video.duration_s
        )));
    }

    Ok(fixture)
}

fn validate_manual_seed(
    spec: &BenchmarkCaseSpec,
    suite_dir: &Path,
    fixture: &FixtureEntry,
) -> AnyResult<()> {
    let Some(seed_path) = spec.manual_seed.as_deref() else {
        return Ok(());
    };

    let path = resolve_path(suite_dir, seed_path);
    let seed: ManualTargetSeedDocument = read_json(&path)?;
    if let Some(seed_fixture_id) = seed.fixture_id() {
        if seed_fixture_id != fixture.id {
            return Err(data_error(format!(
                "manual seed '{}' references fixture '{}' instead of '{}'",
                path.display(),
                seed_fixture_id,
                fixture.id
            )));
        }
    }
    let (frame_width_px, frame_height_px) = fixture_display_dimensions(fixture);
    seed.validate(SeedValidationContext {
        frame_width_px,
        frame_height_px,
        selected_range_start_s: spec.selected_range_s.start_s,
        selected_range_end_s: spec.selected_range_s.end_s,
        source_rotation_deg: fixture.video.rotation_deg,
    })
    .map_err(|error| {
        data_error(format!(
            "manual seed '{}' is invalid for benchmark case '{}': {error}",
            path.display(),
            spec.id
        ))
    })?;

    Ok(())
}

fn load_ground_truth(
    spec: &BenchmarkCaseSpec,
    suite_dir: &Path,
    fixture: &FixtureEntry,
) -> AnyResult<Vec<GroundTruthSample>> {
    let annotation_path = resolve_path(suite_dir, &spec.annotations);
    let annotations: AnnotationDocument = read_json(&annotation_path)?;
    validate_annotations(&annotations, fixture, &annotation_path)?;

    annotations
        .samples
        .iter()
        .filter(|sample| {
            sample.timestamp_s >= spec.selected_range_s.start_s
                && sample.timestamp_s <= spec.selected_range_s.end_s
                && sample.annotation_state == "labelled"
                && sample.quality != "unusable"
        })
        .map(|sample| {
            let center = sample.center_px.ok_or_else(|| {
                data_error(format!(
                    "labelled annotation at {}s in '{}' has no center_px",
                    sample.timestamp_s,
                    annotation_path.display()
                ))
            })?;
            Ok(GroundTruthSample {
                timestamp_s: sample.timestamp_s,
                center: PixelPoint::new(center.x_px, center.y_px),
            })
        })
        .collect()
}

fn load_predictions(
    spec: &BenchmarkCaseSpec,
    suite_dir: &Path,
    fixture: &FixtureEntry,
) -> AnyResult<(PredictionDocument, Vec<TrackerPrediction>)> {
    let prediction_path = resolve_path(suite_dir, &spec.predictions);
    let predictions: PredictionDocument = read_json(&prediction_path)?;
    validate_prediction_document(&predictions, fixture, &prediction_path)?;

    let tracker_predictions = predictions
        .samples
        .iter()
        .filter(|sample| {
            sample.timestamp_s >= spec.selected_range_s.start_s
                && sample.timestamp_s <= spec.selected_range_s.end_s
        })
        .map(|sample| convert_prediction_sample(sample, fixture, &prediction_path))
        .collect::<AnyResult<Vec<_>>>()?;

    Ok((predictions, tracker_predictions))
}

fn collect_case_warnings(spec: &BenchmarkCaseSpec, metrics: &TrackerMetrics) -> Vec<String> {
    let mut warnings = Vec::new();
    if metrics.comparable_samples == 0 {
        warnings.push("no valid comparable annotation samples in selected range".to_owned());
    } else if metrics.tracked_samples == 0 {
        warnings.push(
            "no valid tracked samples; coordinate error metrics are null and loss metrics carry the failure"
                .to_owned(),
        );
    }
    if spec.manual_seed.is_none() {
        warnings.push(
            "no manual seed was attached to this case; acceptable only for prediction-only regression inputs"
                .to_owned(),
        );
    }
    warnings
}

fn build_runtime_report(
    predictions: &PredictionDocument,
    spec: &BenchmarkCaseSpec,
    suite_dir: &Path,
) -> AnyResult<Option<RuntimeReport>> {
    let selected_media_duration_s = spec.selected_range_s.end_s - spec.selected_range_s.start_s;
    predictions
        .runtime
        .as_ref()
        .map(|runtime| {
            if !runtime.processing_wall_s.is_finite() || runtime.processing_wall_s <= 0.0 {
                let prediction_path = resolve_path(suite_dir, &spec.predictions);
                return Err(data_error(format!(
                    "prediction runtime in '{}' must be finite and positive",
                    prediction_path.display()
                )));
            }
            Ok(RuntimeReport {
                processing_wall_s: runtime.processing_wall_s,
                selected_media_duration_s,
                media_seconds_per_wall_second: selected_media_duration_s
                    / runtime.processing_wall_s,
            })
        })
        .transpose()
}

fn validate_selected_range(range: SelectedRange) -> AnyResult<()> {
    if !range.start_s.is_finite()
        || !range.end_s.is_finite()
        || range.start_s < 0.0
        || range.end_s <= range.start_s
    {
        return Err(data_error(format!(
            "selected range must be finite, non-negative, and have end > start; got [{}, {}]",
            range.start_s, range.end_s
        )));
    }
    Ok(())
}

fn validate_annotations(
    annotations: &AnnotationDocument,
    fixture: &FixtureEntry,
    path: &Path,
) -> AnyResult<()> {
    if annotations.schema_version != 1 {
        return Err(data_error(format!(
            "annotation '{}' has unsupported schema version {}",
            path.display(),
            annotations.schema_version
        )));
    }
    if annotations.fixture_id != fixture.id {
        return Err(data_error(format!(
            "annotation '{}' references fixture '{}' instead of '{}'",
            path.display(),
            annotations.fixture_id,
            fixture.id
        )));
    }
    let (display_width_px, display_height_px) = fixture_display_dimensions(fixture);
    if annotations.coordinate_system.width_px != display_width_px
        || annotations.coordinate_system.height_px != display_height_px
    {
        return Err(data_error(format!(
            "annotation '{}' dimensions {}x{} do not match fixture display dimensions {}x{}",
            path.display(),
            annotations.coordinate_system.width_px,
            annotations.coordinate_system.height_px,
            display_width_px,
            display_height_px
        )));
    }
    verify_source_hash(
        "annotation",
        path,
        annotations.source_video_sha256.as_deref(),
        fixture.media.sha256.as_deref(),
    )
}

fn validate_prediction_document(
    predictions: &PredictionDocument,
    fixture: &FixtureEntry,
    path: &Path,
) -> AnyResult<()> {
    if predictions.schema_version != PREDICTION_DOCUMENT_SCHEMA_VERSION {
        return Err(data_error(format!(
            "prediction '{}' has unsupported schema version {}",
            path.display(),
            predictions.schema_version
        )));
    }
    if predictions.fixture_id != fixture.id {
        return Err(data_error(format!(
            "prediction '{}' references fixture '{}' instead of '{}'",
            path.display(),
            predictions.fixture_id,
            fixture.id
        )));
    }
    if predictions.coordinate_space != "decoded_display_pixels" {
        return Err(data_error(format!(
            "prediction '{}' uses unsupported coordinate_space '{}'",
            path.display(),
            predictions.coordinate_space
        )));
    }
    if predictions.implementation.name.trim().is_empty()
        || predictions.implementation.version.trim().is_empty()
    {
        return Err(data_error(format!(
            "prediction '{}' implementation name/version must not be blank",
            path.display()
        )));
    }
    verify_source_hash(
        "prediction",
        path,
        predictions.source_video_sha256.as_deref(),
        fixture.media.sha256.as_deref(),
    )
}

fn verify_source_hash(
    kind: &str,
    path: &Path,
    document_hash: Option<&str>,
    fixture_hash: Option<&str>,
) -> AnyResult<()> {
    if let (Some(document_hash), Some(fixture_hash)) = (document_hash, fixture_hash) {
        if !document_hash.eq_ignore_ascii_case(fixture_hash) {
            return Err(data_error(format!(
                "{kind} '{}' source hash does not match fixture media hash",
                path.display()
            )));
        }
    }
    Ok(())
}

fn convert_prediction_sample(
    sample: &PredictionSample,
    fixture: &FixtureEntry,
    path: &Path,
) -> AnyResult<TrackerPrediction> {
    let state = match sample.state {
        PredictionState::Tracked => {
            let center = sample.center_px.ok_or_else(|| {
                data_error(format!(
                    "tracked prediction at {}s in '{}' requires center_px",
                    sample.timestamp_s,
                    path.display()
                ))
            })?;
            let confidence = sample.confidence.ok_or_else(|| {
                data_error(format!(
                    "tracked prediction at {}s in '{}' requires confidence",
                    sample.timestamp_s,
                    path.display()
                ))
            })?;
            let (display_width_px, display_height_px) = fixture_display_dimensions(fixture);
            if !center.x_px.is_finite()
                || !center.y_px.is_finite()
                || center.x_px < 0.0
                || center.y_px < 0.0
                || center.x_px >= f64::from(display_width_px)
                || center.y_px >= f64::from(display_height_px)
            {
                return Err(data_error(format!(
                    "tracked prediction at {}s in '{}' has a non-finite or out-of-frame center",
                    sample.timestamp_s,
                    path.display()
                )));
            }
            TrackerPredictionState::Tracked {
                center: PixelPoint::new(center.x_px, center.y_px),
                confidence,
            }
        }
        PredictionState::Lost => {
            if sample.center_px.is_some() || sample.confidence.is_some() {
                return Err(data_error(format!(
                    "lost prediction at {}s in '{}' must not fabricate center/confidence",
                    sample.timestamp_s,
                    path.display()
                )));
            }
            TrackerPredictionState::Lost
        }
    };

    Ok(TrackerPrediction {
        timestamp_s: sample.timestamp_s,
        state,
    })
}

fn condition_labels(fixture: &FixtureEntry) -> Vec<String> {
    let mut labels = vec![
        format!("exercise={}", fixture.exercise),
        format!("camera_view={}", fixture.camera.view),
        format!("lighting={}", fixture.conditions.lighting),
        format!("plate_visibility={}", fixture.conditions.plate_visibility),
        format!("occlusion={}", fixture.conditions.occlusion),
        format!("motion_blur={}", fixture.conditions.motion_blur),
    ];
    labels.extend(
        fixture
            .conditions
            .challenge_tags
            .iter()
            .map(|tag| format!("challenge={tag}")),
    );
    labels.sort();
    labels.dedup();
    labels
}

fn build_aggregates(cases: &[CaseResult]) -> AnyResult<Vec<AggregateResult>> {
    let mut groups: BTreeMap<(&str, &str), (ImplementationIdentity, Vec<TrackerMetrics>)> =
        BTreeMap::new();
    let mut implementation_keys = Vec::with_capacity(cases.len());
    for case in cases {
        implementation_keys.push(serde_json::to_string(&case.implementation)?);
    }

    for (case, implementation_key) in cases.iter().zip(&implementation_keys) {
        push_aggregate_group(
            &mut groups,
            implementation_key,
            "overall",
            &case.implementation,
            &case.metrics,
        );
        for condition in &case.conditions {
            push_aggregate_group(
                &mut groups,
                implementation_key,
                condition,
                &case.implementation,
                &case.metrics,
            );
        }
    }

    Ok(groups
        .into_iter()
        .map(
            |((_implementation_key, group), (implementation, metrics))| AggregateResult {
                implementation,
                group: group.to_owned(),
                case_count: metrics.len(),
                metrics: aggregate_metrics(&metrics),
            },
        )
        .collect())
}

fn push_aggregate_group<'a>(
    groups: &mut BTreeMap<(&'a str, &'a str), (ImplementationIdentity, Vec<TrackerMetrics>)>,
    implementation_key: &'a str,
    group: &'a str,
    implementation: &ImplementationIdentity,
    metrics: &TrackerMetrics,
) {
    groups
        .entry((implementation_key, group))
        .or_insert_with(|| (implementation.clone(), Vec::new()))
        .1
        .push(metrics.clone());
}

fn render_summary(artifact: &BenchmarkArtifact) -> String {
    let mut lines = Vec::new();
    lines.push(format!(
        "OpenBar benchmark {} | pipeline {}",
        artifact.benchmark_metric_version, artifact.pipeline_version
    ));
    lines.push(
        "case | implementation | fixture | samples | availability | MAE px | RMSE px | max loss"
            .to_owned(),
    );

    for case in &artifact.cases {
        lines.push(format!(
            "{} | {}@{} | {} | {}/{} | {} | {} | {} | {} samples / {} s",
            case.case_id,
            case.implementation.name,
            case.implementation.version,
            case.fixture_id,
            case.metrics.tracked_samples,
            case.metrics.comparable_samples,
            format_percent(case.metrics.tracking_availability),
            format_optional(case.metrics.plate_center_mae_px),
            format_optional(case.metrics.plate_center_rmse_px),
            case.metrics
                .max_consecutive_tracking_loss_samples
                .map_or_else(|| "n/a".to_owned(), |value| value.to_string()),
            format_optional(case.metrics.max_consecutive_tracking_loss_duration_s),
        ));
        for warning in &case.warnings {
            lines.push(format!("  warning: {warning}"));
        }
    }

    lines.push("aggregates:".to_owned());
    for aggregate in artifact
        .aggregates
        .iter()
        .filter(|aggregate| aggregate.group == "overall")
    {
        lines.push(format!(
            "  {}@{} | cases={} | availability={} | MAE={} px | RMSE={} px | loss={}%",
            aggregate.implementation.name,
            aggregate.implementation.version,
            aggregate.case_count,
            format_percent(aggregate.metrics.tracking_availability),
            format_optional(aggregate.metrics.plate_center_mae_px),
            format_optional(aggregate.metrics.plate_center_rmse_px),
            aggregate
                .metrics
                .lost_frame_percentage
                .map_or_else(|| "n/a".to_owned(), |value| format!("{value:.3}")),
        ));
    }

    lines.join("\n")
}

fn format_optional(value: Option<f64>) -> String {
    value.map_or_else(|| "n/a".to_owned(), |value| format!("{value:.6}"))
}

fn format_percent(value: Option<f64>) -> String {
    value.map_or_else(
        || "n/a".to_owned(),
        |value| format!("{:.3}%", value * 100.0),
    )
}

fn resolve_path(base: &Path, value: &str) -> PathBuf {
    let path = Path::new(value);
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        base.join(path)
    }
}

fn read_json<T: DeserializeOwned>(path: &Path) -> AnyResult<T> {
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

    fn synthetic_suite_path() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../validation/benchmarks/synthetic-tracker-smoke.benchmark-v1.json")
    }

    #[test]
    fn malformed_benchmark_manifest_is_rejected() {
        let path = std::env::temp_dir().join(format!(
            "openbar-malformed-benchmark-{}.json",
            std::process::id()
        ));
        fs::write(&path, "{not-json").expect("write malformed benchmark suite");
        let error = run_suite(&path).expect_err("malformed benchmark input must fail");
        assert!(error.to_string().contains("failed to parse"));
        let _ = fs::remove_file(path);
    }

    #[test]
    fn committed_synthetic_suite_has_hand_checkable_metrics_and_is_repeatable() {
        let first = run_suite(&synthetic_suite_path()).unwrap();
        let second = run_suite(&synthetic_suite_path()).unwrap();

        assert_eq!(first.cases.len(), 2);
        assert_eq!(
            serde_json::to_string(&first).unwrap(),
            serde_json::to_string(&second).unwrap()
        );

        let imperfect = first
            .cases
            .iter()
            .find(|case| case.implementation.name == "synthetic-offset-loss")
            .unwrap();
        assert_eq!(imperfect.metrics.comparable_samples, 10);
        assert_eq!(imperfect.metrics.tracked_samples, 7);
        assert_eq!(imperfect.metrics.lost_samples, 3);
        assert_eq!(imperfect.metrics.tracker_declared_loss_samples, 1);
        assert_eq!(imperfect.metrics.low_confidence_samples, 1);
        assert_eq!(imperfect.metrics.timestamp_unmatched_samples, 1);
        assert!((imperfect.metrics.plate_center_mae_px.unwrap() - 25.0 / 7.0).abs() < 1e-12);
        assert!(
            (imperfect.metrics.plate_center_rmse_px.unwrap() - (125.0_f64 / 7.0).sqrt()).abs()
                < 1e-12
        );
        assert!((imperfect.metrics.x_bias_px.unwrap() - 9.0 / 7.0).abs() < 1e-12);
        assert!((imperfect.metrics.y_bias_px.unwrap() - 20.0 / 7.0).abs() < 1e-12);
        assert_eq!(imperfect.metrics.tracking_availability, Some(0.7));
        assert_eq!(imperfect.metrics.lost_frame_percentage, Some(30.0));
        assert_eq!(
            imperfect.metrics.max_consecutive_tracking_loss_samples,
            Some(1)
        );
        assert!(
            (imperfect
                .metrics
                .max_consecutive_tracking_loss_duration_s
                .unwrap()
                - 0.083334)
                .abs()
                < 1e-12
        );

        let perfect = first
            .cases
            .iter()
            .find(|case| case.implementation.name == "synthetic-perfect")
            .unwrap();
        assert_eq!(perfect.metrics.tracking_availability, Some(1.0));
        assert_eq!(perfect.metrics.plate_center_mae_px, Some(0.0));
        assert_eq!(perfect.metrics.plate_center_rmse_px, Some(0.0));
    }

    fn rotated_fixture() -> FixtureEntry {
        FixtureEntry {
            id: "rotated-fixture".to_owned(),
            exercise: "snatch".to_owned(),
            media: FixtureMedia { sha256: None },
            video: FixtureVideo {
                width_px: 1280,
                height_px: 720,
                duration_s: 2.0,
                rotation_deg: 90,
            },
            camera: FixtureCamera {
                view: "side".to_owned(),
            },
            conditions: FixtureConditions {
                lighting: "controlled".to_owned(),
                plate_visibility: "clear".to_owned(),
                occlusion: "none".to_owned(),
                motion_blur: "none".to_owned(),
                challenge_tags: Vec::new(),
            },
        }
    }

    fn rotated_seed_spec(manual_seed: &str) -> BenchmarkCaseSpec {
        BenchmarkCaseSpec {
            id: "rotated-seed".to_owned(),
            fixture_manifest: "unused-manifest.json".to_owned(),
            fixture_id: "rotated-fixture".to_owned(),
            annotations: "unused-annotations.json".to_owned(),
            manual_seed: Some(manual_seed.to_owned()),
            predictions: "unused-predictions.json".to_owned(),
            selected_range_s: SelectedRange {
                start_s: 0.0,
                end_s: 1.0,
            },
            timestamp_tolerance_s: 0.01,
            min_confidence: 0.0,
        }
    }

    fn seed_json(x_px: f64, y_px: f64) -> String {
        serde_json::json!({
            "schema_version": 1,
            "fixture_id": "rotated-fixture",
            "seed": {
                "timestamp_s": 0.5,
                "target": {
                    "center": {
                        "x_px": x_px,
                        "y_px": y_px
                    },
                    "radius_px": 1.0
                },
                "coordinate_space": "display_top_left",
                "source_rotation_deg": 90
            }
        })
        .to_string()
    }

    #[test]
    fn rotated_fixture_seed_validation_uses_display_dimensions() {
        let fixture = rotated_fixture();
        assert_eq!(fixture_display_dimensions(&fixture), (720, 1280));

        let temp_dir =
            std::env::temp_dir().join(format!("openbar-rotated-seed-{}", std::process::id()));
        let _ = fs::remove_dir_all(&temp_dir);
        fs::create_dir_all(&temp_dir).unwrap();

        fs::write(temp_dir.join("valid.json"), seed_json(700.0, 1000.0)).unwrap();
        let valid_spec = rotated_seed_spec("valid.json");
        assert!(validate_manual_seed(&valid_spec, &temp_dir, &fixture).is_ok());

        fs::write(temp_dir.join("invalid.json"), seed_json(721.0, 500.0)).unwrap();
        let invalid_spec = rotated_seed_spec("invalid.json");
        assert!(validate_manual_seed(&invalid_spec, &temp_dir, &fixture).is_err());

        let _ = fs::remove_dir_all(temp_dir);
    }

    #[test]
    fn rotated_fixture_annotation_dimensions_use_display_space() {
        let fixture = rotated_fixture();
        let path = Path::new("rotated.annotation-v1.json");

        let valid = AnnotationDocument {
            schema_version: 1,
            fixture_id: fixture.id.clone(),
            source_video_sha256: None,
            coordinate_system: AnnotationCoordinateSystem {
                width_px: 720,
                height_px: 1280,
            },
            samples: vec![AnnotationSample {
                timestamp_s: 0.5,
                annotation_state: "labelled".to_owned(),
                quality: "high".to_owned(),
                center_px: Some(JsonPoint {
                    x_px: 700.0,
                    y_px: 1000.0,
                }),
            }],
        };
        assert!(validate_annotations(&valid, &fixture, path).is_ok());

        let invalid = AnnotationDocument {
            coordinate_system: AnnotationCoordinateSystem {
                width_px: 1280,
                height_px: 720,
            },
            ..valid
        };
        assert!(validate_annotations(&invalid, &fixture, path).is_err());
    }

    #[test]
    fn rotated_fixture_prediction_bounds_use_display_dimensions() {
        let fixture = rotated_fixture();
        let path = Path::new("rotated.prediction-v1.json");

        let valid = PredictionSample {
            timestamp_s: 0.5,
            state: PredictionState::Tracked,
            center_px: Some(JsonPoint {
                x_px: 700.0,
                y_px: 1000.0,
            }),
            confidence: Some(1.0),
        };
        assert!(convert_prediction_sample(&valid, &fixture, path).is_ok());

        let invalid = PredictionSample {
            center_px: Some(JsonPoint {
                x_px: 721.0,
                y_px: 500.0,
            }),
            ..valid
        };
        assert!(convert_prediction_sample(&invalid, &fixture, path).is_err());
    }

    #[test]
    fn benchmark_build_aggregates_performance() {
        let suite = run_suite(&synthetic_suite_path()).unwrap();
        let mut cases = Vec::with_capacity(suite.cases.len() * 5000);
        // Repeat cases to create a non-trivial dataset for aggregation
        for _ in 0..5000 {
            cases.extend(suite.cases.iter().cloned());
        }

        let start = std::time::Instant::now();
        let aggregates = build_aggregates(&cases).unwrap();
        let duration = start.elapsed();

        assert!(!aggregates.is_empty());
        println!(
            "build_aggregates for {} cases took {:?}",
            cases.len(),
            duration
        );
    }
}
