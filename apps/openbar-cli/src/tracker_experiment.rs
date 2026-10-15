use openbar_core::benchmark::{
    evaluate_tracker_case, BenchmarkParameters, GroundTruthSample, TrackerMetrics,
};
use openbar_core::manual_seed::{ManualTargetSeed, PixelPoint, PlateTarget, SeedValidationContext};
use openbar_tracking::{
    FrameSample, GrayFrame, LocalContrastTracker, ManualSeedTracker, TemplateMatchTracker,
    TrackerLossReason, TrackerObservationState, TrackerRun,
};
use serde::Serialize;
use std::collections::BTreeMap;
use std::env;
use std::error::Error;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::time::Instant;

const WIDTH: u32 = 64;
const HEIGHT: u32 = 64;
const BASE_RADIUS: i32 = 6;

type AnyResult<T> = Result<T, Box<dyn Error>>;

#[derive(Debug, Serialize)]
struct ExperimentArtifact {
    schema_version: u32,
    experiment_version: &'static str,
    purpose: &'static str,
    benchmark_policy: ExperimentBenchmarkPolicy,
    results: Vec<ScenarioResult>,
    recommendation: Recommendation,
    limitations: Vec<&'static str>,
}

#[derive(Debug, Serialize)]
struct ExperimentBenchmarkPolicy {
    timestamp_tolerance_s: f64,
    min_confidence: f32,
    confidence_note: &'static str,
}

#[derive(Debug, Serialize)]
struct ScenarioResult {
    scenario: &'static str,
    factor: &'static str,
    tracker_id: String,
    tracker_implementation: String,
    tracker_version: String,
    tracker_config: BTreeMap<String, String>,
    confidence_semantics: String,
    metrics: TrackerMetrics,
    loss_reasons: BTreeMap<String, usize>,
    low_confidence_samples: usize,
    mean_reported_confidence: Option<f64>,
    high_confidence_error_over_2px: usize,
    runtime_ms: f64,
    selected_media_duration_s: f64,
    media_seconds_per_wall_second: Option<f64>,
}

#[derive(Debug, Serialize)]
struct Recommendation {
    carry_forward: Vec<&'static str>,
    rationale: Vec<&'static str>,
    production_selection: &'static str,
}

#[derive(Debug, Clone)]
struct Scenario {
    name: &'static str,
    factor: &'static str,
    timestamps: Vec<f64>,
    truth_centers: Vec<(i32, i32)>,
    rendered_centers: Vec<Option<(i32, i32)>>,
    target_intensities: Vec<u8>,
    rendered_radii: Vec<i32>,
    hide_right_half: Vec<bool>,
}

pub fn run_cli() -> AnyResult<()> {
    run_cli_from_args(env::args().skip(2))
}

fn run_cli_from_args<I, S>(args: I) -> AnyResult<()>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let args = args.into_iter().collect::<Vec<_>>();
    let mut output_path: Option<PathBuf> = None;
    let mut index = 0usize;
    while index < args.len() {
        match args[index].as_ref() {
            "--output" => {
                index += 1;
                output_path = Some(PathBuf::from(
                    args.get(index)
                        .ok_or_else(|| data_error("--output requires a path"))?
                        .as_ref(),
                ));
            }
            "--help" | "-h" => {
                println!(
                    "Usage: openbar-cli tracker-experiment [--output <path>]\n\
                     Runs deterministic procedural tracker scenarios through the common benchmark evaluator."
                );
                return Ok(());
            }
            other => {
                return Err(data_error(format!(
                    "unknown tracker-experiment argument '{other}'"
                )))
            }
        }
        index += 1;
    }

    let artifact = run_experiment()?;
    eprintln!("{}", render_summary(&artifact));
    let serialized = serde_json::to_string_pretty(&artifact)?;
    if let Some(path) = output_path {
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                fs::create_dir_all(parent)?;
            }
        }
        fs::write(path, format!("{serialized}\n"))?;
    } else {
        println!("{serialized}");
    }
    Ok(())
}

fn run_experiment() -> AnyResult<ExperimentArtifact> {
    let parameters = BenchmarkParameters {
        timestamp_tolerance_s: 0.0,
        min_confidence: 0.0,
    };
    let template = TemplateMatchTracker::default();
    let contrast = LocalContrastTracker::default();
    let mut results = Vec::new();

    for scenario in scenarios() {
        validate_scenario(&scenario)?;
        let frames = scenario
            .rendered_centers
            .iter()
            .zip(&scenario.target_intensities)
            .zip(&scenario.rendered_radii)
            .zip(&scenario.hide_right_half)
            .map(|(((center, intensity), radius), hide)| {
                render_frame(*center, *intensity, *radius, *hide)
            })
            .collect::<Result<Vec<_>, _>>()?;
        let frame_samples = frames
            .iter()
            .zip(&scenario.timestamps)
            .enumerate()
            .map(|(index, (image, timestamp_s))| FrameSample {
                timestamp_s: *timestamp_s,
                frame_index: Some(index as u64),
                image,
            })
            .collect::<Vec<_>>();
        let manual_seed = scenario_seed(scenario.truth_centers[0], scenario.timestamps[0])?;
        let truth = scenario
            .truth_centers
            .iter()
            .zip(&scenario.timestamps)
            .map(|(center, timestamp_s)| GroundTruthSample {
                timestamp_s: *timestamp_s,
                center: PixelPoint::new(f64::from(center.0), f64::from(center.1)),
            })
            .collect::<Vec<_>>();

        for tracker in [
            TrackerKind::Template(&template),
            TrackerKind::Contrast(&contrast),
        ] {
            let started = Instant::now();
            let run = tracker.track(&frame_samples, &manual_seed)?;
            let runtime_s = started.elapsed().as_secs_f64();
            let metrics = evaluate_tracker_case(&truth, &run.benchmark_predictions(), parameters)?;
            let selected_media_duration_s = scenario.timestamps.last().copied().unwrap_or_default()
                - scenario.timestamps.first().copied().unwrap_or_default();
            let identity = &run.tracker;

            results.push(ScenarioResult {
                scenario: scenario.name,
                factor: scenario.factor,
                tracker_id: identity.id.clone(),
                tracker_implementation: identity.implementation.clone(),
                tracker_version: identity.version.clone(),
                tracker_config: identity.config.clone(),
                confidence_semantics: identity.confidence_semantics.clone(),
                metrics,
                loss_reasons: loss_reason_counts(&run),
                low_confidence_samples: low_confidence_count(&run),
                mean_reported_confidence: mean_confidence(&run),
                high_confidence_error_over_2px: high_confidence_error_count(&run, &truth),
                runtime_ms: runtime_s * 1_000.0,
                selected_media_duration_s,
                media_seconds_per_wall_second: (runtime_s > 0.0)
                    .then_some(selected_media_duration_s / runtime_s),
            });
        }
    }

    Ok(ExperimentArtifact {
        schema_version: 1,
        experiment_version: "m0-first-trackers-v1",
        purpose: "Deterministic contract/regression comparison of the first two manual-seed tracker families; not a production accuracy claim.",
        benchmark_policy: ExperimentBenchmarkPolicy {
            timestamp_tolerance_s: parameters.timestamp_tolerance_s,
            min_confidence: parameters.min_confidence,
            confidence_note: "Tracker-specific confidence is not thresholded for cross-algorithm selection here; explicit loss is comparable, confidence semantics are not calibrated across trackers.",
        },
        results,
        recommendation: Recommendation {
            carry_forward: vec!["template-sad-v1", "local-contrast-centroid-v1"],
            rationale: vec![
                "Keep fixed-template matching as a deterministic correlation-family baseline.",
                "Keep local-contrast centroid tracking as a materially different appearance/segmentation baseline with different failure modes.",
                "Run both on real decoded-video fixtures before selecting a production tracker or hybrid/reinitialization policy.",
            ],
            production_selection: "No production winner is selected from procedural evidence alone.",
        },
        limitations: vec![
            "Procedural cases cover stationary motion, two speed proxies, full/partial occlusion, appearance contrast, and target-size change.",
            "They do not establish behavior under real blur, codec artifacts, camera movement, perspective, lens distortion, or lift-specific backgrounds.",
            "Runtime is environment-sensitive and is intentionally separate from deterministic accuracy/loss metrics.",
            "Confidence values are algorithm-specific signals, not interchangeable probabilities.",
        ],
    })
}

enum TrackerKind<'a> {
    Template(&'a TemplateMatchTracker),
    Contrast(&'a LocalContrastTracker),
}

impl TrackerKind<'_> {
    fn track(
        &self,
        frames: &[FrameSample<'_>],
        seed: &ManualTargetSeed,
    ) -> Result<TrackerRun, openbar_tracking::TrackerError> {
        match self {
            Self::Template(tracker) => tracker.track(frames, seed),
            Self::Contrast(tracker) => tracker.track(frames, seed),
        }
    }
}

fn scenarios() -> Vec<Scenario> {
    vec![
        simple_scenario(
            "stationary",
            "baseline",
            &[(24, 40), (24, 40), (24, 40), (24, 40), (24, 40)],
        ),
        simple_scenario(
            "slow-translation",
            "lift-speed-proxy",
            &[(24, 40), (26, 39), (28, 38), (30, 37), (32, 36)],
        ),
        simple_scenario(
            "fast-translation",
            "lift-speed-proxy",
            &[(16, 40), (24, 38), (32, 36), (40, 34), (48, 32)],
        ),
        Scenario {
            name: "full-occlusion-reentry",
            factor: "occlusion",
            timestamps: default_timestamps(5),
            truth_centers: vec![(24, 40), (26, 39), (28, 38), (30, 37), (32, 36)],
            rendered_centers: vec![
                Some((24, 40)),
                Some((26, 39)),
                None,
                Some((30, 37)),
                Some((32, 36)),
            ],
            target_intensities: vec![40; 5],
            rendered_radii: vec![BASE_RADIUS; 5],
            hide_right_half: vec![false; 5],
        },
        Scenario {
            name: "appearance-low-contrast",
            factor: "appearance",
            timestamps: default_timestamps(5),
            truth_centers: vec![(24, 40), (26, 39), (28, 38), (30, 37), (32, 36)],
            rendered_centers: vec![
                Some((24, 40)),
                Some((26, 39)),
                Some((28, 38)),
                Some((30, 37)),
                Some((32, 36)),
            ],
            target_intensities: vec![40, 110, 110, 110, 110],
            rendered_radii: vec![BASE_RADIUS; 5],
            hide_right_half: vec![false; 5],
        },
        Scenario {
            name: "partial-occlusion",
            factor: "occlusion",
            timestamps: default_timestamps(5),
            truth_centers: vec![(24, 40), (26, 39), (28, 38), (30, 37), (32, 36)],
            rendered_centers: vec![
                Some((24, 40)),
                Some((26, 39)),
                Some((28, 38)),
                Some((30, 37)),
                Some((32, 36)),
            ],
            target_intensities: vec![40; 5],
            rendered_radii: vec![BASE_RADIUS; 5],
            hide_right_half: vec![false, false, true, false, false],
        },
        Scenario {
            name: "smaller-target",
            factor: "distance-size-proxy",
            timestamps: default_timestamps(5),
            truth_centers: vec![(24, 40), (26, 39), (28, 38), (30, 37), (32, 36)],
            rendered_centers: vec![
                Some((24, 40)),
                Some((26, 39)),
                Some((28, 38)),
                Some((30, 37)),
                Some((32, 36)),
            ],
            target_intensities: vec![40; 5],
            rendered_radii: vec![BASE_RADIUS, 4, 4, 4, 4],
            hide_right_half: vec![false; 5],
        },
    ]
}

fn simple_scenario(name: &'static str, factor: &'static str, centers: &[(i32, i32)]) -> Scenario {
    Scenario {
        name,
        factor,
        timestamps: default_timestamps(centers.len()),
        truth_centers: centers.to_vec(),
        rendered_centers: centers.iter().copied().map(Some).collect(),
        target_intensities: vec![40; centers.len()],
        rendered_radii: vec![BASE_RADIUS; centers.len()],
        hide_right_half: vec![false; centers.len()],
    }
}

fn default_timestamps(count: usize) -> Vec<f64> {
    (0..count).map(|index| index as f64 * 0.1).collect()
}

fn scenario_seed(center: (i32, i32), timestamp_s: f64) -> AnyResult<ManualTargetSeed> {
    Ok(ManualTargetSeed::try_new(
        timestamp_s,
        Some(0),
        PlateTarget::new(
            PixelPoint::new(f64::from(center.0), f64::from(center.1)),
            f64::from(BASE_RADIUS),
        ),
        0,
        Some(1.0),
        Some("procedural tracker comparison seed".to_owned()),
        SeedValidationContext {
            frame_width_px: WIDTH,
            frame_height_px: HEIGHT,
            selected_range_start_s: timestamp_s,
            selected_range_end_s: 1.0,
            source_rotation_deg: 0,
        },
    )?)
}

fn validate_scenario(scenario: &Scenario) -> AnyResult<()> {
    let expected = scenario.timestamps.len();
    if expected == 0
        || scenario.truth_centers.len() != expected
        || scenario.rendered_centers.len() != expected
        || scenario.target_intensities.len() != expected
        || scenario.rendered_radii.len() != expected
        || scenario.hide_right_half.len() != expected
    {
        return Err(data_error(format!(
            "scenario '{}' has misaligned inputs",
            scenario.name
        )));
    }
    Ok(())
}

fn render_frame(
    center: Option<(i32, i32)>,
    target_intensity: u8,
    radius: i32,
    hide_right_half: bool,
) -> Result<GrayFrame, openbar_tracking::TrackerError> {
    let mut pixels = vec![220u8; (WIDTH * HEIGHT) as usize];
    if let Some((cx, cy)) = center {
        for y in 0..HEIGHT as i32 {
            for x in 0..WIDTH as i32 {
                let dx = x - cx;
                let dy = y - cy;
                if dx * dx + dy * dy <= radius * radius && !(hide_right_half && x >= cx) {
                    pixels[y as usize * WIDTH as usize + x as usize] = target_intensity;
                }
            }
        }
    }
    GrayFrame::try_new(WIDTH, HEIGHT, pixels)
}

fn loss_reason_counts(run: &TrackerRun) -> BTreeMap<String, usize> {
    let mut counts = BTreeMap::new();
    for observation in &run.observations {
        if let TrackerObservationState::Lost { reason } = observation.state {
            let key = match reason {
                TrackerLossReason::PoorMatch => "poor_match",
                TrackerLossReason::InsufficientContrast => "insufficient_contrast",
                TrackerLossReason::TargetOutsideFrame => "target_outside_frame",
                TrackerLossReason::NoCandidate => "no_candidate",
            };
            *counts.entry(key.to_owned()).or_insert(0) += 1;
        }
    }
    counts
}

fn low_confidence_count(run: &TrackerRun) -> usize {
    run.observations
        .iter()
        .filter(|observation| {
            matches!(
                observation.state,
                TrackerObservationState::LowConfidence { .. }
            )
        })
        .count()
}

fn mean_confidence(run: &TrackerRun) -> Option<f64> {
    let values = run
        .observations
        .iter()
        .filter_map(|observation| match observation.state {
            TrackerObservationState::Tracked { confidence, .. }
            | TrackerObservationState::LowConfidence { confidence, .. } => {
                Some(f64::from(confidence))
            }
            TrackerObservationState::Lost { .. } => None,
        })
        .collect::<Vec<_>>();
    (!values.is_empty()).then(|| values.iter().sum::<f64>() / values.len() as f64)
}

fn high_confidence_error_count(run: &TrackerRun, truth: &[GroundTruthSample]) -> usize {
    run.observations
        .iter()
        .zip(truth)
        .filter(|(observation, truth)| match observation.state {
            TrackerObservationState::Tracked { center, confidence }
            | TrackerObservationState::LowConfidence { center, confidence } => {
                confidence >= 0.8
                    && (center.x_px() - truth.center.x_px())
                        .hypot(center.y_px() - truth.center.y_px())
                        > 2.0
            }
            TrackerObservationState::Lost { .. } => false,
        })
        .count()
}

fn render_summary(artifact: &ExperimentArtifact) -> String {
    let mut output = String::from("OpenBar first tracker comparison\n");
    for result in &artifact.results {
        let mae = result
            .metrics
            .plate_center_mae_px
            .map_or_else(|| "n/a".to_owned(), |value| format!("{value:.3}"));
        let availability = result.metrics.tracking_availability.map_or_else(
            || "n/a".to_owned(),
            |value| format!("{:.1}%", value * 100.0),
        );
        output.push_str(&format!(
            "- {} / {}: MAE={} px, availability={}, loss={}\n",
            result.scenario, result.tracker_id, mae, availability, result.metrics.lost_samples
        ));
    }
    output.push_str("Recommendation: carry both families into real-fixture benchmarking; no production winner from procedural evidence.\n");
    output
}

fn data_error(message: impl Into<String>) -> Box<dyn Error> {
    Box::new(io::Error::new(io::ErrorKind::InvalidData, message.into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;

    #[test]
    fn run_experiment_produces_valid_artifact_and_scenarios() {
        let artifact = run_experiment().expect("experiment should run without error");
        assert_eq!(artifact.schema_version, 1);
        assert_eq!(artifact.experiment_version, "m0-first-trackers-v1");
        assert!(!artifact.results.is_empty());

        // Every scenario is evaluated by both trackers, exactly once.
        let actual: BTreeSet<(&str, &str)> = artifact
            .results
            .iter()
            .map(|r| (r.scenario, r.tracker_id.as_str()))
            .collect();
        let expected: BTreeSet<(&str, &str)> = [
            "stationary",
            "slow-translation",
            "fast-translation",
            "full-occlusion-reentry",
            "appearance-low-contrast",
            "partial-occlusion",
            "smaller-target",
        ]
        .into_iter()
        .flat_map(|scenario| {
            ["template-sad-v1", "local-contrast-centroid-v1"]
                .into_iter()
                .map(move |tracker| (scenario, tracker))
        })
        .collect();
        assert_eq!(actual, expected);
        assert_eq!(artifact.results.len(), expected.len());

        let summary = render_summary(&artifact);
        assert!(summary.contains("OpenBar first tracker comparison"));
        assert!(summary.contains("Recommendation:"));
    }

    #[test]
    fn validate_scenario_detects_misaligned_inputs() {
        let mut scenario = scenarios()[0].clone();
        scenario.timestamps.pop(); // create misalignment
        let err = validate_scenario(&scenario).unwrap_err();
        assert!(err.to_string().contains("misaligned inputs"));
    }

    #[test]
    fn validate_scenario_rejects_empty_inputs() {
        let mut empty_scenario = scenarios()[0].clone();
        empty_scenario.timestamps.clear();
        empty_scenario.truth_centers.clear();
        empty_scenario.rendered_centers.clear();
        empty_scenario.target_intensities.clear();
        empty_scenario.rendered_radii.clear();
        empty_scenario.hide_right_half.clear();
        let err_empty = validate_scenario(&empty_scenario).unwrap_err();
        assert!(err_empty.to_string().contains("misaligned inputs"));
    }

    #[test]
    fn run_cli_handles_help_and_errors() {
        assert!(run_cli_from_args(["--help"]).is_ok());
        assert!(run_cli_from_args(["-h"]).is_ok());

        let err_missing = run_cli_from_args(["--output"]).unwrap_err();
        assert!(err_missing.to_string().contains("--output requires a path"));

        let err_unknown = run_cli_from_args(["--unknown-flag"]).unwrap_err();
        assert!(err_unknown
            .to_string()
            .contains("unknown tracker-experiment argument"));
    }

    struct TempDir(PathBuf);

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn run_cli_writes_output_file() {
        let temp_dir = TempDir(
            std::env::temp_dir().join(format!("openbar-tracker-experiment-{}", std::process::id())),
        );
        let _ = fs::remove_dir_all(&temp_dir.0);
        let output_file = temp_dir.0.join("subfolder").join("output.json");

        run_cli_from_args(["--output", output_file.to_str().unwrap()]).expect("run should succeed");

        let content = fs::read_to_string(&output_file).expect("output file written");
        assert!(content.ends_with('\n'));
        let value: serde_json::Value = serde_json::from_str(&content).expect("valid JSON");
        assert_eq!(value["schema_version"], 1);
        assert_eq!(value["experiment_version"], "m0-first-trackers-v1");
        assert_eq!(value["results"].as_array().map(Vec::len), Some(14));
    }

    #[test]
    fn loss_counts_low_confidence_and_mean_confidence_from_run() {
        let dummy_diagnostics = openbar_tracking::TrackerDiagnostics {
            quality_score: None,
            displacement_px: None,
            reacquired_after_loss: false,
        };
        let mut run = TrackerRun {
            tracker: openbar_tracking::TrackerIdentity {
                id: "test".into(),
                implementation: "test".into(),
                version: "1.0".into(),
                config: BTreeMap::new(),
                confidence_semantics: "unit".into(),
            },
            seed_timestamp_s: 0.0,
            observations: vec![
                openbar_tracking::TrackerObservation {
                    timestamp_s: 0.0,
                    frame_index: Some(0),
                    target_bounds_px: None,
                    visibility: openbar_tracking::TrackerVisibilityState::Unknown,
                    diagnostics: dummy_diagnostics,
                    state: TrackerObservationState::Tracked {
                        center: PixelPoint::new(10.0, 10.0),
                        confidence: 0.9,
                    },
                },
                openbar_tracking::TrackerObservation {
                    timestamp_s: 0.1,
                    frame_index: Some(1),
                    target_bounds_px: None,
                    visibility: openbar_tracking::TrackerVisibilityState::Unknown,
                    diagnostics: dummy_diagnostics,
                    state: TrackerObservationState::LowConfidence {
                        center: PixelPoint::new(10.0, 10.0),
                        confidence: 0.5,
                    },
                },
                openbar_tracking::TrackerObservation {
                    timestamp_s: 0.2,
                    frame_index: Some(2),
                    target_bounds_px: None,
                    visibility: openbar_tracking::TrackerVisibilityState::Unknown,
                    diagnostics: dummy_diagnostics,
                    state: TrackerObservationState::Lost {
                        reason: TrackerLossReason::PoorMatch,
                    },
                },
                openbar_tracking::TrackerObservation {
                    timestamp_s: 0.3,
                    frame_index: Some(3),
                    target_bounds_px: None,
                    visibility: openbar_tracking::TrackerVisibilityState::Unknown,
                    diagnostics: dummy_diagnostics,
                    state: TrackerObservationState::Lost {
                        reason: TrackerLossReason::InsufficientContrast,
                    },
                },
                openbar_tracking::TrackerObservation {
                    timestamp_s: 0.4,
                    frame_index: Some(4),
                    target_bounds_px: None,
                    visibility: openbar_tracking::TrackerVisibilityState::Unknown,
                    diagnostics: dummy_diagnostics,
                    state: TrackerObservationState::Lost {
                        reason: TrackerLossReason::TargetOutsideFrame,
                    },
                },
                openbar_tracking::TrackerObservation {
                    timestamp_s: 0.5,
                    frame_index: Some(5),
                    target_bounds_px: None,
                    visibility: openbar_tracking::TrackerVisibilityState::Unknown,
                    diagnostics: dummy_diagnostics,
                    state: TrackerObservationState::Lost {
                        reason: TrackerLossReason::NoCandidate,
                    },
                },
            ],
        };

        let loss_counts = loss_reason_counts(&run);
        assert_eq!(loss_counts.get("poor_match"), Some(&1));
        assert_eq!(loss_counts.get("insufficient_contrast"), Some(&1));
        assert_eq!(loss_counts.get("target_outside_frame"), Some(&1));
        assert_eq!(loss_counts.get("no_candidate"), Some(&1));

        assert_eq!(low_confidence_count(&run), 1);

        let mean_conf = mean_confidence(&run).unwrap();
        assert!((mean_conf - 0.7).abs() < 1e-6);

        run.observations.clear();
        assert!(mean_confidence(&run).is_none());
    }
}
