use openbar_core::analysis::ImplementationProvenance;
use openbar_core::benchmark::{evaluate_filter_case, FilterBenchmarkParameters, FilterMetrics};
use openbar_core::filtering::{apply_filter, FilterBehavior, FilterConfig};
use openbar_core::trajectory::MetricPositionSample;
use serde::Serialize;
use std::env;
use std::error::Error;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::time::Instant;

type AnyResult<T> = Result<T, Box<dyn Error>>;

#[derive(Debug, Serialize)]
struct FilterExperimentArtifact {
    schema_version: u32,
    experiment_version: &'static str,
    purpose: &'static str,
    development_policy: DevelopmentPolicy,
    development: Vec<DevelopmentFamilyResult>,
    held_out_validation: Vec<ScenarioResult>,
    production_selection: ProductionSelection,
    limitations: Vec<&'static str>,
}

#[derive(Debug, Serialize)]
struct DevelopmentPolicy {
    split: &'static str,
    selection_rule: &'static str,
    note: &'static str,
}

#[derive(Debug, Serialize)]
struct DevelopmentFamilyResult {
    family: &'static str,
    candidates: Vec<CandidateSummary>,
    selected: ImplementationProvenance,
}

#[derive(Debug, Serialize)]
struct CandidateSummary {
    filter: ImplementationProvenance,
    mean_position_rmse_m: f64,
    mean_velocity_rmse_mps: f64,
    scenarios: Vec<ScenarioResult>,
}

#[derive(Debug, Serialize)]
struct ScenarioResult {
    split: &'static str,
    scenario: &'static str,
    condition: &'static str,
    noise_seed: u64,
    peak_metrics_applicable: bool,
    filter: ImplementationProvenance,
    behavior: BehaviorRecord,
    metrics: FilterMetrics,
    input_samples: usize,
    output_samples: usize,
    filter_segment_count: usize,
    max_input_gap_s: Option<f64>,
    edge_position_mae_m: Option<f64>,
    #[serde(skip_serializing)]
    runtime_ms: f64,
}

#[derive(Debug, Serialize)]
struct BehaviorRecord {
    causal: bool,
    confidence_behavior: &'static str,
    irregular_timestamp_behavior: &'static str,
    gap_behavior: &'static str,
    edge_behavior: &'static str,
    latency_behavior: &'static str,
}

impl From<FilterBehavior> for BehaviorRecord {
    fn from(value: FilterBehavior) -> Self {
        Self {
            causal: value.causal,
            confidence_behavior: value.confidence_behavior,
            irregular_timestamp_behavior: value.irregular_timestamp_behavior,
            gap_behavior: value.gap_behavior,
            edge_behavior: value.edge_behavior,
            latency_behavior: value.latency_behavior,
        }
    }
}

#[derive(Debug, Serialize)]
struct ProductionSelection {
    status: &'static str,
    rationale: Vec<&'static str>,
    next_evidence: Vec<&'static str>,
}

#[derive(Debug, Clone)]
struct Scenario {
    split: &'static str,
    name: &'static str,
    condition: &'static str,
    noise_seed: u64,
    peak_metrics_applicable: bool,
    truth: Vec<MetricPositionSample>,
    observed: Vec<MetricPositionSample>,
}

pub fn run_cli() -> AnyResult<()> {
    let args = env::args().skip(2).collect::<Vec<_>>();
    let mut output_path: Option<PathBuf> = None;
    let mut index = 0usize;
    while index < args.len() {
        match args[index].as_str() {
            "--output" => {
                index += 1;
                output_path = Some(PathBuf::from(
                    args.get(index)
                        .ok_or_else(|| data_error("--output requires a path"))?,
                ));
            }
            "--help" | "-h" => {
                println!(
                    "Usage: openbar-cli filter-experiment [--output <path>]\n\
                     Tunes filter-family parameters only on deterministic development signals,\n\
                     then evaluates the selected configurations on held-out synthetic signals."
                );
                return Ok(());
            }
            other => {
                return Err(data_error(format!(
                    "unknown filter-experiment argument '{other}'"
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

fn run_experiment() -> AnyResult<FilterExperimentArtifact> {
    let scenarios = scenarios();
    let development_scenarios = scenarios
        .iter()
        .filter(|scenario| scenario.split == "development")
        .collect::<Vec<_>>();
    let validation_scenarios = scenarios
        .iter()
        .filter(|scenario| scenario.split == "held_out_validation")
        .collect::<Vec<_>>();

    let mut development = Vec::new();
    let mut selected_configs = Vec::new();

    for (family, configs) in candidate_families() {
        let mut candidates = Vec::new();
        for config in &configs {
            let scenario_results = development_scenarios
                .iter()
                .map(|scenario| evaluate_scenario(scenario, *config))
                .collect::<AnyResult<Vec<_>>>()?;
            candidates.push(summarize_candidate(*config, scenario_results)?);
        }

        let selected_index = select_candidate(&candidates)
            .ok_or_else(|| data_error(format!("no development candidate for family {family}")))?;
        selected_configs.push((family, configs[selected_index]));
        development.push(DevelopmentFamilyResult {
            family,
            candidates,
            selected: configs[selected_index].provenance(),
        });
    }

    let mut held_out_validation = Vec::new();
    for scenario in validation_scenarios {
        for (_, config) in &selected_configs {
            held_out_validation.push(evaluate_scenario(scenario, *config)?);
        }
    }

    Ok(FilterExperimentArtifact {
        schema_version: 2,
        experiment_version: "m0-filter-comparison-v2",
        purpose: "Quantitative M0 comparison of raw, moving-average, timestamp-aware Savitzky-Golay, and constant-velocity Kalman filtering under one deterministic contract.",
        development_policy: DevelopmentPolicy {
            split: "Synthetic parameter-development signals are disjoint from held-out synthetic validation signals.",
            selection_rule: "Within each filter family, minimize mean development velocity RMSE; break ties with mean position RMSE. The rule chooses a family configuration, not a production winner.",
            note: "Development metrics are averaged across three independent deterministic Gaussian-noise seeds per signal; held-out validation metrics use separate seeds and are not fed back into parameter selection.",
        },
        development,
        held_out_validation,
        production_selection: ProductionSelection {
            status: "deferred",
            rationale: vec![
                "Synthetic signals prove contract behavior and expose trade-offs but are not sufficient evidence for a production default.",
                "Real decoded-video fixture coverage is still too small to establish robustness across blur, codec artifacts, occlusion, camera movement, distance, and lift-specific conditions.",
                "A default must be selected from measured real-fixture trade-offs, not from overlay appearance or synthetic tuning alone.",
            ],
            next_evidence: vec![
                "Run the selected per-family configurations on the same real, annotated M0 fixtures used for tracker validation.",
                "Add reference velocity evidence where the measured construct is explicitly aligned.",
                "Revisit the engineering gates and select a default only after condition-stratified position/velocity/peak/lag evidence is sufficient.",
            ],
        },
        limitations: vec![
            "Synthetic measurement noise is seeded pseudo-random Gaussian noise used for repeatable regression/tuning; it is not a model of blur, compression, occlusion, camera motion, or tracker-correlated error.",
            "Velocity is the existing timestamp-based backward difference and is used consistently for reference and filtered trajectories.",
            "Runtime is environment-sensitive, printed only as a console diagnostic, and excluded from the retained deterministic JSON evidence artifact.",
            "No missing timestamp is synthesized; long-gap scenarios contain only observed samples on each side of the loss span.",
            "Peak attenuation/timing fields are emitted only for scenarios with an intentionally defined velocity peak.",
        ],
    })
}

fn candidate_families() -> Vec<(&'static str, Vec<FilterConfig>)> {
    vec![
        ("raw", vec![FilterConfig::Raw]),
        (
            "moving_average",
            vec![
                FilterConfig::MovingAverage {
                    window: 3,
                    max_gap_s: 0.05,
                },
                FilterConfig::MovingAverage {
                    window: 5,
                    max_gap_s: 0.05,
                },
                FilterConfig::MovingAverage {
                    window: 7,
                    max_gap_s: 0.05,
                },
            ],
        ),
        (
            "savitzky_golay",
            vec![
                FilterConfig::SavitzkyGolay {
                    window: 5,
                    polynomial_order: 2,
                    max_gap_s: 0.05,
                },
                FilterConfig::SavitzkyGolay {
                    window: 7,
                    polynomial_order: 2,
                    max_gap_s: 0.05,
                },
                FilterConfig::SavitzkyGolay {
                    window: 7,
                    polynomial_order: 3,
                    max_gap_s: 0.05,
                },
            ],
        ),
        (
            "kalman",
            vec![
                FilterConfig::Kalman {
                    acceleration_variance_m2_s4: 0.5,
                    measurement_variance_m2: 0.000_004,
                    initial_velocity_variance_m2_s2: 1.0,
                    confidence_window_samples: 3,
                    max_gap_s: 0.05,
                },
                FilterConfig::Kalman {
                    acceleration_variance_m2_s4: 0.5,
                    measurement_variance_m2: 0.000_016,
                    initial_velocity_variance_m2_s2: 1.0,
                    confidence_window_samples: 3,
                    max_gap_s: 0.05,
                },
                FilterConfig::Kalman {
                    acceleration_variance_m2_s4: 2.0,
                    measurement_variance_m2: 0.000_004,
                    initial_velocity_variance_m2_s2: 1.0,
                    confidence_window_samples: 3,
                    max_gap_s: 0.05,
                },
                FilterConfig::Kalman {
                    acceleration_variance_m2_s4: 2.0,
                    measurement_variance_m2: 0.000_016,
                    initial_velocity_variance_m2_s2: 1.0,
                    confidence_window_samples: 3,
                    max_gap_s: 0.05,
                },
                FilterConfig::Kalman {
                    acceleration_variance_m2_s4: 8.0,
                    measurement_variance_m2: 0.000_004,
                    initial_velocity_variance_m2_s2: 1.0,
                    confidence_window_samples: 3,
                    max_gap_s: 0.05,
                },
                FilterConfig::Kalman {
                    acceleration_variance_m2_s4: 8.0,
                    measurement_variance_m2: 0.000_016,
                    initial_velocity_variance_m2_s2: 1.0,
                    confidence_window_samples: 3,
                    max_gap_s: 0.05,
                },
            ],
        ),
    ]
}

fn summarize_candidate(
    config: FilterConfig,
    scenarios: Vec<ScenarioResult>,
) -> AnyResult<CandidateSummary> {
    let mean_position_rmse_m = mean_metric(&scenarios, |metrics| metrics.position_rmse_m)
        .ok_or_else(|| data_error("development position RMSE unexpectedly unavailable"))?;
    let mean_velocity_rmse_mps = mean_metric(&scenarios, |metrics| metrics.velocity_rmse_mps)
        .ok_or_else(|| data_error("development velocity RMSE unexpectedly unavailable"))?;

    Ok(CandidateSummary {
        filter: config.provenance(),
        mean_position_rmse_m,
        mean_velocity_rmse_mps,
        scenarios,
    })
}

fn select_candidate(candidates: &[CandidateSummary]) -> Option<usize> {
    let mut best: Option<usize> = None;
    for (index, candidate) in candidates.iter().enumerate() {
        let Some(best_index) = best else {
            best = Some(index);
            continue;
        };
        let incumbent = &candidates[best_index];
        let velocity_better =
            candidate.mean_velocity_rmse_mps < incumbent.mean_velocity_rmse_mps - 1.0e-12;
        let velocity_tied =
            (candidate.mean_velocity_rmse_mps - incumbent.mean_velocity_rmse_mps).abs() <= 1.0e-12;
        let position_better =
            candidate.mean_position_rmse_m < incumbent.mean_position_rmse_m - 1.0e-12;
        if velocity_better || (velocity_tied && position_better) {
            best = Some(index);
        }
    }
    best
}

fn mean_metric(
    scenarios: &[ScenarioResult],
    metric: impl Fn(&FilterMetrics) -> Option<f64>,
) -> Option<f64> {
    let values = scenarios
        .iter()
        .filter_map(|scenario| metric(&scenario.metrics))
        .collect::<Vec<_>>();
    (!values.is_empty()).then(|| values.iter().sum::<f64>() / values.len() as f64)
}

fn evaluate_scenario(scenario: &Scenario, config: FilterConfig) -> AnyResult<ScenarioResult> {
    let started = Instant::now();
    let run = apply_filter(&scenario.observed, config)?;
    let runtime_ms = started.elapsed().as_secs_f64() * 1_000.0;
    let metrics = evaluate_filter_case(
        &scenario.truth,
        &run.trajectory.samples,
        FilterBenchmarkParameters {
            max_velocity_gap_s: 0.05,
            evaluate_peak_metrics: scenario.peak_metrics_applicable,
        },
    )?;
    let edge_position_mae_m = edge_position_mae(&scenario.truth, &run.trajectory.samples, 2);

    Ok(ScenarioResult {
        split: scenario.split,
        scenario: scenario.name,
        condition: scenario.condition,
        noise_seed: scenario.noise_seed,
        peak_metrics_applicable: scenario.peak_metrics_applicable,
        filter: run.trajectory.filter,
        behavior: run.behavior.into(),
        metrics,
        input_samples: scenario.observed.len(),
        output_samples: run.trajectory.samples.len(),
        filter_segment_count: run.segment_count,
        max_input_gap_s: max_gap(&scenario.observed),
        edge_position_mae_m,
        runtime_ms,
    })
}

fn edge_position_mae(
    truth: &[MetricPositionSample],
    actual: &[MetricPositionSample],
    edge_samples: usize,
) -> Option<f64> {
    if truth.is_empty() || truth.len() != actual.len() {
        return None;
    }
    let mut selected = Vec::new();
    for index in 0..edge_samples.min(truth.len()) {
        selected.push(index);
    }
    let tail_start = truth.len().saturating_sub(edge_samples);
    for index in tail_start..truth.len() {
        if !selected.contains(&index) {
            selected.push(index);
        }
    }

    let total = selected
        .iter()
        .map(|index| {
            let dx = actual[*index].x_m - truth[*index].x_m;
            let dy = actual[*index].y_m - truth[*index].y_m;
            dx.hypot(dy)
        })
        .sum::<f64>();
    Some(total / selected.len() as f64)
}

fn max_gap(samples: &[MetricPositionSample]) -> Option<f64> {
    samples
        .windows(2)
        .map(|pair| pair[1].timestamp_s - pair[0].timestamp_s)
        .reduce(f64::max)
}

fn scenarios() -> Vec<Scenario> {
    const DEVELOPMENT_NOISE_SEEDS: [u64; 3] = [
        0x4f50_454e_4241_5201,
        0x4f50_454e_4241_5202,
        0x4f50_454e_4241_5203,
    ];
    const HELD_OUT_NOISE_SEED: u64 = 0x4845_4c44_4f55_5401;

    let mut scenarios = Vec::new();
    for seed in DEVELOPMENT_NOISE_SEEDS {
        scenarios.push(build_regular_scenario(
            "development",
            "constant-position-noise",
            "constant position + seeded Gaussian measurement noise",
            61,
            1.0 / 60.0,
            |_, _| (0.15, 0.55),
            0.003,
            seed,
            false,
        ));
        scenarios.push(build_regular_scenario(
            "development",
            "constant-velocity-noise",
            "constant velocity + seeded Gaussian measurement noise",
            61,
            1.0 / 60.0,
            |time, _| (0.10 + 0.08 * time, 0.20 + 0.55 * time),
            0.003,
            seed,
            false,
        ));
        scenarios.push(build_regular_scenario(
            "development",
            "smooth-trajectory",
            "smooth curved trajectory + seeded Gaussian measurement noise",
            61,
            1.0 / 60.0,
            |time, _| {
                (
                    0.03 * (std::f64::consts::TAU * time).sin(),
                    0.20 + 0.50 * time - 0.12 * time * time,
                )
            },
            0.003,
            seed,
            false,
        ));
        scenarios.push(build_irregular_scenario(seed));
    }

    scenarios.push(build_regular_scenario(
        "held_out_validation",
        "sharp-peak",
        "sharp velocity feature to expose peak attenuation and phase shift",
        61,
        1.0 / 60.0,
        |time, _| {
            let centered = (time - 0.52) / 0.065;
            (
                0.02 * (4.0 * time).sin(),
                0.20 + 0.35 * time + 0.055 * (-centered * centered).exp(),
            )
        },
        0.003,
        HELD_OUT_NOISE_SEED,
        true,
    ));
    scenarios.push(build_regular_scenario(
        "held_out_validation",
        "clip-boundaries",
        "short sequence and boundary behavior",
        7,
        1.0 / 60.0,
        |time, _| (0.01 + 0.02 * time, 0.30 + 0.45 * time),
        0.003,
        HELD_OUT_NOISE_SEED ^ 0x11,
        false,
    ));
    scenarios.push(build_gap_scenario(
        "short-missing-span",
        "short missing span below the configured continuity threshold; no sample is synthesized",
        0.04,
        HELD_OUT_NOISE_SEED ^ 0x22,
    ));
    scenarios.push(build_gap_scenario(
        "long-loss-span",
        "long tracking-loss span above max_gap_s; centered windows and state-space history must reset",
        0.40,
        HELD_OUT_NOISE_SEED ^ 0x33,
    ));

    scenarios
}

fn build_regular_scenario(
    split: &'static str,
    name: &'static str,
    condition: &'static str,
    count: usize,
    dt: f64,
    truth_fn: impl Fn(f64, usize) -> (f64, f64),
    noise_sigma_m: f64,
    noise_seed: u64,
    peak_metrics_applicable: bool,
) -> Scenario {
    let timestamps = (0..count)
        .map(|index| index as f64 * dt)
        .collect::<Vec<_>>();
    build_scenario(
        split,
        name,
        condition,
        timestamps,
        truth_fn,
        noise_sigma_m,
        noise_seed,
        peak_metrics_applicable,
    )
}

fn build_irregular_scenario(noise_seed: u64) -> Scenario {
    let mut timestamps = Vec::new();
    let mut time = 0.0;
    for index in 0..55 {
        timestamps.push(time);
        let dt = match index % 4 {
            0 => 0.011,
            1 => 0.021,
            2 => 0.014,
            _ => 0.019,
        };
        time += dt;
    }
    build_scenario(
        "development",
        "irregular-timestamps",
        "VFR-style irregular timestamp spacing",
        timestamps,
        |time, _| (0.04 * time, 0.25 + 0.42 * time - 0.08 * time * time),
        0.003,
        noise_seed,
        false,
    )
}

fn build_gap_scenario(
    name: &'static str,
    condition: &'static str,
    gap_s: f64,
    noise_seed: u64,
) -> Scenario {
    let dt = 1.0 / 60.0;
    let mut timestamps = (0..13).map(|index| index as f64 * dt).collect::<Vec<_>>();
    let resume_at = timestamps.last().copied().unwrap_or_default() + gap_s;
    timestamps.extend((0..13).map(|index| resume_at + index as f64 * dt));
    build_scenario(
        "held_out_validation",
        name,
        condition,
        timestamps,
        |time, _| (0.025 * time, 0.18 + 0.48 * time),
        0.003,
        noise_seed,
        false,
    )
}

fn build_scenario(
    split: &'static str,
    name: &'static str,
    condition: &'static str,
    timestamps: Vec<f64>,
    truth_fn: impl Fn(f64, usize) -> (f64, f64),
    noise_sigma_m: f64,
    noise_seed: u64,
    peak_metrics_applicable: bool,
) -> Scenario {
    let truth = timestamps
        .iter()
        .enumerate()
        .map(|(index, time)| {
            let (x_m, y_m) = truth_fn(*time, index);
            MetricPositionSample {
                timestamp_s: *time,
                x_m,
                y_m,
                confidence: 1.0,
            }
        })
        .collect::<Vec<_>>();
    let observed = truth
        .iter()
        .enumerate()
        .map(|(index, sample)| MetricPositionSample {
            timestamp_s: sample.timestamp_s,
            x_m: sample.x_m + deterministic_gaussian(noise_seed, index, 0) * noise_sigma_m,
            y_m: sample.y_m + deterministic_gaussian(noise_seed, index, 1) * noise_sigma_m,
            confidence: 1.0,
        })
        .collect();

    Scenario {
        split,
        name,
        condition,
        noise_seed,
        peak_metrics_applicable,
        truth,
        observed,
    }
}

fn splitmix64(mut value: u64) -> u64 {
    value = value.wrapping_add(0x9e37_79b9_7f4a_7c15);
    let mut mixed = value;
    mixed = (mixed ^ (mixed >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    mixed = (mixed ^ (mixed >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    mixed ^ (mixed >> 31)
}

fn unit_interval_open(key: u64) -> f64 {
    let mantissa = splitmix64(key) >> 11;
    (mantissa as f64 + 0.5) / ((1_u64 << 53) as f64)
}

fn deterministic_gaussian(seed: u64, index: usize, axis: usize) -> f64 {
    let key = seed
        ^ (index as u64).wrapping_mul(0xd6e8_feb8_6659_fd93)
        ^ (axis as u64).wrapping_mul(0xa5a3_564e_27f8_862d);
    let u1 = unit_interval_open(key);
    let u2 = unit_interval_open(key ^ 0x632b_e59b_d9b4_e019);
    (-2.0 * u1.ln()).sqrt() * (std::f64::consts::TAU * u2).cos()
}

fn render_summary(artifact: &FilterExperimentArtifact) -> String {
    let mut lines = vec![
        "M0 filter comparison".to_owned(),
        "development selection: mean velocity RMSE, tie-break mean position RMSE".to_owned(),
    ];
    for family in &artifact.development {
        lines.push(format!(
            "selected {:>15}: {}@{} {:?}",
            family.family,
            family.selected.implementation,
            family.selected.version,
            family.selected.parameters
        ));
    }
    lines.push("held-out validation:".to_owned());
    for result in &artifact.held_out_validation {
        lines.push(format!(
            "  {:>18} | {:>29} | pos_rmse={} m | vel_rmse={} m/s | peak_att={} | shift={} s | edge_mae={} m | samples={}/{} | segments={} | runtime_ms={:.3}",
            result.scenario,
            result.filter.implementation,
            display_option(result.metrics.position_rmse_m),
            display_option(result.metrics.velocity_rmse_mps),
            display_option(result.metrics.peak_attenuation_fraction),
            display_option(result.metrics.peak_timing_shift_s),
            display_option(result.edge_position_mae_m),
            result.output_samples,
            result.input_samples,
            result.filter_segment_count,
            result.runtime_ms,
        ));
    }
    lines.push(format!(
        "production default: {} — synthetic evidence is not sufficient for production selection",
        artifact.production_selection.status
    ));
    lines.join("\n")
}

fn display_option(value: Option<f64>) -> String {
    value
        .map(|value| format!("{value:.6}"))
        .unwrap_or_else(|| "n/a".to_owned())
}

fn data_error(message: impl Into<String>) -> Box<dyn Error> {
    Box::new(io::Error::new(io::ErrorKind::InvalidData, message.into()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn experiment_has_disjoint_development_and_validation_scenarios() {
        let scenarios = scenarios();
        let development = scenarios
            .iter()
            .filter(|scenario| scenario.split == "development")
            .map(|scenario| scenario.name)
            .collect::<Vec<_>>();
        let validation = scenarios
            .iter()
            .filter(|scenario| scenario.split == "held_out_validation")
            .map(|scenario| scenario.name)
            .collect::<Vec<_>>();

        assert!(!development.is_empty());
        assert!(!validation.is_empty());
        assert!(development.iter().all(|name| !validation
            .iter()
            .any(|validation_name| validation_name == name)));
    }

    #[test]
    fn serialized_artifact_excludes_environment_sensitive_runtime() {
        let artifact = run_experiment().unwrap();
        let serialized = serde_json::to_string(&artifact).unwrap();
        assert!(!serialized.contains("runtime_ms"));
    }

    #[test]
    fn deterministic_gaussian_noise_is_repeatable_and_axis_seed_specific() {
        let a = deterministic_gaussian(7, 11, 0);
        assert_eq!(a, deterministic_gaussian(7, 11, 0));
        assert_ne!(a, deterministic_gaussian(7, 11, 1));
        assert_ne!(a, deterministic_gaussian(8, 11, 0));
    }

    #[test]
    fn development_scenarios_average_multiple_noise_seeds() {
        let scenarios = scenarios();
        for name in [
            "constant-position-noise",
            "constant-velocity-noise",
            "smooth-trajectory",
            "irregular-timestamps",
        ] {
            let seeds = scenarios
                .iter()
                .filter(|scenario| scenario.split == "development" && scenario.name == name)
                .map(|scenario| scenario.noise_seed)
                .collect::<Vec<_>>();
            assert_eq!(seeds.len(), 3);
            assert_ne!(seeds[0], seeds[1]);
            assert_ne!(seeds[1], seeds[2]);
        }
    }

    #[test]
    fn only_intentional_peak_scenario_enables_peak_metrics() {
        for scenario in scenarios() {
            assert_eq!(
                scenario.peak_metrics_applicable,
                scenario.name == "sharp-peak"
            );
        }
    }

    #[test]
    fn every_filter_preserves_observed_sample_count_in_gap_scenarios() {
        for scenario in scenarios()
            .into_iter()
            .filter(|scenario| scenario.name.contains("span"))
        {
            for (_, configs) in candidate_families() {
                for config in configs {
                    let run = apply_filter(&scenario.observed, config).unwrap();
                    assert_eq!(run.trajectory.samples.len(), scenario.observed.len());
                    assert_eq!(
                        run.trajectory
                            .samples
                            .iter()
                            .map(|sample| sample.timestamp_s)
                            .collect::<Vec<_>>(),
                        scenario
                            .observed
                            .iter()
                            .map(|sample| sample.timestamp_s)
                            .collect::<Vec<_>>()
                    );
                }
            }
        }
    }
}
