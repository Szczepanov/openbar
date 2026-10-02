use openbar_core::analysis::{
    Analysis, AnalysisProvenance, ImplementationProvenance, KinematicsInput, TrackerProvenance,
};
use openbar_core::calibration::{
    CalibrationMethod, MetricCoordinateConvention, PlateDiameterCalibration,
    PLATE_DIAMETER_CALIBRATION_METHOD_VERSION,
};
use openbar_core::kinematics::{
    mean_axis_velocity, peak_axis_velocity, range_of_motion, KinematicsConfig, MetricAxis,
    MetricInterval, VELOCITY_METHOD_IMPLEMENTATION, VELOCITY_METHOD_VERSION,
};
use openbar_core::trajectory::MetricPositionSample;
use serde::{Deserialize, Serialize};
use std::env;
use std::error::Error;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

type AnyResult<T> = Result<T, Box<dyn Error>>;

const STUDY_SCHEMA_VERSION: u32 = 1;
const RESULT_SCHEMA_VERSION: u32 = 1;
const ROM_TARGET_M: f64 = 0.01;
const MEAN_VELOCITY_TARGET_MPS: f64 = 0.05;
const PEAK_VELOCITY_TARGET_MPS: f64 = 0.10;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum EvidenceClass {
    IndependentPhysicalReference,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum ReferenceSourceType {
    ControlledGeometricRig,
    Encoder,
    LinearPositionTransducer,
    HighFrameRateReference,
    ValidatedVbtDevice,
    OtherIndependentPhysicalReference,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ReferenceProvenance {
    source_type: ReferenceSourceType,
    system: String,
    protocol: String,
    synchronization: String,
    coordinate_alignment: String,
    rights_or_access: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    notes: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum StudyAxis {
    HorizontalX,
    VerticalY,
}

impl StudyAxis {
    const fn core(self) -> MetricAxis {
        match self {
            Self::HorizontalX => MetricAxis::HorizontalX,
            Self::VerticalY => MetricAxis::VerticalY,
        }
    }

    const fn label(self) -> &'static str {
        match self {
            Self::HorizontalX => "horizontal_x",
            Self::VerticalY => "vertical_y",
        }
    }

    fn value(self, sample: MetricPositionSample) -> f64 {
        match self {
            Self::HorizontalX => sample.x_m,
            Self::VerticalY => sample.y_m,
        }
    }

    fn reference_sample(self, sample: &ReferenceSample) -> MetricPositionSample {
        match self {
            Self::HorizontalX => MetricPositionSample {
                timestamp_s: sample.timestamp_s,
                x_m: sample.position_m,
                y_m: 0.0,
                confidence: 1.0,
            },
            Self::VerticalY => MetricPositionSample {
                timestamp_s: sample.timestamp_s,
                x_m: 0.0,
                y_m: sample.position_m,
                confidence: 1.0,
            },
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum VelocityLayer {
    Calibrated,
    Filtered,
}

impl VelocityLayer {
    const fn expected_input(self) -> KinematicsInput {
        match self {
            Self::Calibrated => KinematicsInput::Calibrated,
            Self::Filtered => KinematicsInput::Filtered,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExpectedKinematics {
    implementation: String,
    version: String,
    max_gap_s: f64,
    min_confidence: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ConstructContract {
    coordinate_convention: MetricCoordinateConvention,
    axis: StudyAxis,
    interval_semantics: String,
    mean_velocity_definition: String,
    peak_velocity_definition: String,
    timestamp_alignment: String,
    calibration_method: CalibrationMethod,
    calibration_method_version: u32,
    kinematics: ExpectedKinematics,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ReferenceUncertainty {
    position_m: f64,
    velocity_mps: f64,
    method: String,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ReferenceSample {
    timestamp_s: f64,
    position_m: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum StudyCaseStatus {
    Supported,
    Unsupported,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StudyInterval {
    start_s: f64,
    end_s: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StudyCase {
    id: String,
    condition: String,
    status: StudyCaseStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    unsupported_reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    analysis_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    velocity_layer: Option<VelocityLayer>,
    #[serde(skip_serializing_if = "Option::is_none")]
    expected_tracker: Option<ImplementationProvenance>,
    #[serde(skip_serializing_if = "Option::is_none")]
    expected_filter: Option<ImplementationProvenance>,
    #[serde(skip_serializing_if = "Option::is_none")]
    interval: Option<StudyInterval>,
    #[serde(skip_serializing_if = "Option::is_none")]
    reference_uncertainty: Option<ReferenceUncertainty>,
    #[serde(default)]
    reference_samples: Vec<ReferenceSample>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct GateRevision {
    revised_threshold: f64,
    rationale: String,
    evidence: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct GateRevisions {
    #[serde(skip_serializing_if = "Option::is_none")]
    rom: Option<GateRevision>,
    #[serde(skip_serializing_if = "Option::is_none")]
    mean_velocity: Option<GateRevision>,
    #[serde(skip_serializing_if = "Option::is_none")]
    peak_velocity: Option<GateRevision>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StudySpec {
    schema_version: u32,
    study_id: String,
    evidence_class: EvidenceClass,
    purpose: String,
    reference: ReferenceProvenance,
    construct: ConstructContract,
    #[serde(default)]
    target_revisions: GateRevisions,
    cases: Vec<StudyCase>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
enum GateStatus {
    Pass,
    Fail,
    Revised,
}

impl GateStatus {
    const fn label(self) -> &'static str {
        match self {
            Self::Pass => "PASS",
            Self::Fail => "FAIL",
            Self::Revised => "REVISED",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum CaseOutcome {
    Evaluated,
    Failed,
    Unsupported,
}

#[derive(Debug, Clone, Serialize)]
struct ErrorDistribution {
    count: usize,
    mae: f64,
    rmse: f64,
    bias: f64,
    p50_absolute: f64,
    p90_absolute: f64,
    p95_absolute: f64,
    max_absolute: f64,
}

#[derive(Debug, Clone, Serialize)]
struct CaseMetrics {
    calibrated_position_error_m: ErrorDistribution,
    reference_rom_m: f64,
    openbar_rom_m: f64,
    rom_error_m: f64,
    reference_mean_velocity_mps: f64,
    openbar_mean_velocity_mps: f64,
    mean_velocity_error_mps: f64,
    reference_peak_velocity_mps: f64,
    reference_peak_timestamp_s: f64,
    openbar_peak_velocity_mps: f64,
    openbar_peak_timestamp_s: f64,
    peak_velocity_error_mps: f64,
}

#[derive(Debug, Clone, Serialize)]
struct CaseOpenBarProvenance {
    source_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    source_sha256: Option<String>,
    analysis: AnalysisProvenance,
    calibration: PlateDiameterCalibration,
    velocity_layer: VelocityLayer,
    #[serde(skip_serializing_if = "Option::is_none")]
    filter: Option<ImplementationProvenance>,
    kinematics_method: ImplementationProvenance,
}

#[derive(Debug, Clone, Serialize)]
struct CaseResult {
    id: String,
    condition: String,
    outcome: CaseOutcome,
    reference_sample_count: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    reference_uncertainty: Option<ReferenceUncertainty>,
    #[serde(skip_serializing_if = "Option::is_none")]
    failure_reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    metrics: Option<CaseMetrics>,
    #[serde(skip_serializing_if = "Option::is_none")]
    openbar_provenance: Option<CaseOpenBarProvenance>,
}

#[derive(Debug, Clone, Serialize)]
struct StudySummary {
    total_cases: usize,
    evaluated_cases: usize,
    failed_supported_cases: usize,
    unsupported_cases: usize,
    evaluated_reference_samples: usize,
}

#[derive(Debug, Clone, Serialize)]
struct GateResult {
    status: GateStatus,
    original_threshold: f64,
    effective_threshold: f64,
    evaluated_case_count: usize,
    failed_supported_case_count: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_distribution: Option<ErrorDistribution>,
    #[serde(skip_serializing_if = "Option::is_none")]
    revision: Option<GateRevision>,
    #[serde(skip_serializing_if = "Option::is_none")]
    meets_effective_target: Option<bool>,
    rationale: String,
}

#[derive(Debug, Clone, Serialize)]
struct GateResults {
    rom_mae_m: GateResult,
    mean_velocity_mae_mps: GateResult,
    peak_velocity_mae_mps: GateResult,
}

#[derive(Debug, Clone, Serialize)]
struct ReferenceStudyResult {
    schema_version: u32,
    study_id: String,
    evidence_class: EvidenceClass,
    purpose: String,
    reference: ReferenceProvenance,
    construct: ConstructContract,
    summary: StudySummary,
    cases: Vec<CaseResult>,
    gates: GateResults,
    limitations: Vec<&'static str>,
}

pub fn run_cli() -> AnyResult<()> {
    let args = env::args().skip(2).collect::<Vec<_>>();
    let mut study_path: Option<PathBuf> = None;
    let mut output_path: Option<PathBuf> = None;
    let mut report_path: Option<PathBuf> = None;
    let mut index = 0usize;

    while index < args.len() {
        match args[index].as_str() {
            "--study" => {
                index += 1;
                study_path = Some(PathBuf::from(required_arg(&args, index, "--study")?));
            }
            "--output" => {
                index += 1;
                output_path = Some(PathBuf::from(required_arg(&args, index, "--output")?));
            }
            "--report" => {
                index += 1;
                report_path = Some(PathBuf::from(required_arg(&args, index, "--report")?));
            }
            "--help" | "-h" => {
                println!(
                    "Usage: openbar-cli kinematic-reference --study <study.json> --output <result.json> --report <report.md>\n\
                     Evaluates calibrated position/ROM and canonical mean/peak signed-axis velocity\n\
                     against synchronized independent physical/reference observations.\n\
                     The study contract must pin axis/sign, interval semantics, timestamp alignment,\n\
                     calibration, tracker/filter provenance, and backward-difference parameters."
                );
                return Ok(());
            }
            other => {
                return Err(data_error(format!(
                    "unknown kinematic-reference argument '{other}'"
                )));
            }
        }
        index += 1;
    }

    let study_path = study_path.ok_or_else(|| data_error("--study is required"))?;
    let output_path = output_path.ok_or_else(|| data_error("--output is required"))?;
    let report_path = report_path.ok_or_else(|| data_error("--report is required"))?;

    let study_text = fs::read_to_string(&study_path)?;
    let study: StudySpec = serde_json::from_str(&study_text)?;
    validate_study(&study)?;

    let result = evaluate_study(&study, &study_path);
    write_text(
        &output_path,
        &(serde_json::to_string_pretty(&result)? + "\n"),
    )?;
    write_text(&report_path, &render_report(&result))?;

    eprintln!(
        "kinematic-reference study={} evaluated={} failed_supported={} unsupported={} gates={}/{}/{}",
        result.study_id,
        result.summary.evaluated_cases,
        result.summary.failed_supported_cases,
        result.summary.unsupported_cases,
        result.gates.rom_mae_m.status.label(),
        result.gates.mean_velocity_mae_mps.status.label(),
        result.gates.peak_velocity_mae_mps.status.label(),
    );
    Ok(())
}

fn required_arg<'a>(args: &'a [String], index: usize, flag: &str) -> AnyResult<&'a str> {
    args.get(index)
        .map(String::as_str)
        .ok_or_else(|| data_error(format!("{flag} requires a value")))
}

fn validate_study(study: &StudySpec) -> AnyResult<()> {
    if study.schema_version != STUDY_SCHEMA_VERSION {
        return Err(data_error(format!(
            "unsupported study schema_version {}; expected {STUDY_SCHEMA_VERSION}",
            study.schema_version
        )));
    }
    require_non_blank("study_id", &study.study_id)?;
    require_non_blank("purpose", &study.purpose)?;
    require_non_blank("reference.system", &study.reference.system)?;
    require_non_blank("reference.protocol", &study.reference.protocol)?;
    require_non_blank("reference.synchronization", &study.reference.synchronization)?;
    require_non_blank(
        "reference.coordinate_alignment",
        &study.reference.coordinate_alignment,
    )?;
    require_non_blank("reference.rights_or_access", &study.reference.rights_or_access)?;

    if study.construct.coordinate_convention
        != MetricCoordinateConvention::ReferenceCentreXRightYUp
    {
        return Err(data_error(
            "study coordinate convention must be reference_centre_x_right_y_up",
        ));
    }
    if study.construct.calibration_method != CalibrationMethod::PlateDiameter
        || study.construct.calibration_method_version
            != PLATE_DIAMETER_CALIBRATION_METHOD_VERSION
    {
        return Err(data_error(format!(
            "study calibration must be plate_diameter@{}",
            PLATE_DIAMETER_CALIBRATION_METHOD_VERSION
        )));
    }
    require_exact(
        "construct.interval_semantics",
        &study.construct.interval_semantics,
        "closed_exact_sample_boundaries",
    )?;
    require_exact(
        "construct.mean_velocity_definition",
        &study.construct.mean_velocity_definition,
        "axis_displacement_over_interval_duration",
    )?;
    require_exact(
        "construct.peak_velocity_definition",
        &study.construct.peak_velocity_definition,
        "maximum_signed_axis_velocity",
    )?;
    require_exact(
        "construct.timestamp_alignment",
        &study.construct.timestamp_alignment,
        "exact_sample_timestamp",
    )?;
    require_exact(
        "construct.kinematics.implementation",
        &study.construct.kinematics.implementation,
        VELOCITY_METHOD_IMPLEMENTATION,
    )?;
    require_exact(
        "construct.kinematics.version",
        &study.construct.kinematics.version,
        VELOCITY_METHOD_VERSION,
    )?;
    KinematicsConfig::try_new(
        study.construct.kinematics.max_gap_s,
        study.construct.kinematics.min_confidence,
    )?;

    if study.cases.is_empty() {
        return Err(data_error("study must contain at least one case"));
    }

    for (index, case) in study.cases.iter().enumerate() {
        require_non_blank(&format!("cases[{index}].id"), &case.id)?;
        require_non_blank(&format!("cases[{index}].condition"), &case.condition)?;
        match case.status {
            StudyCaseStatus::Unsupported => {
                let reason = case.unsupported_reason.as_deref().ok_or_else(|| {
                    data_error(format!(
                        "cases[{index}] unsupported case requires unsupported_reason"
                    ))
                })?;
                require_non_blank(&format!("cases[{index}].unsupported_reason"), reason)?;
            }
            StudyCaseStatus::Supported => {
                if case.unsupported_reason.is_some() {
                    return Err(data_error(format!(
                        "cases[{index}] supported case must not set unsupported_reason"
                    )));
                }
                if case.analysis_path.as_deref().is_none_or(str::is_empty) {
                    return Err(data_error(format!(
                        "cases[{index}] supported case requires analysis_path"
                    )));
                }
                let layer = case.velocity_layer.ok_or_else(|| {
                    data_error(format!(
                        "cases[{index}] supported case requires velocity_layer"
                    ))
                })?;
                if case.expected_tracker.is_none() {
                    return Err(data_error(format!(
                        "cases[{index}] supported case requires expected_tracker"
                    )));
                }
                match (layer, case.expected_filter.as_ref()) {
                    (VelocityLayer::Calibrated, Some(_)) => {
                        return Err(data_error(format!(
                            "cases[{index}] calibrated velocity layer must not set expected_filter"
                        )))
                    }
                    (VelocityLayer::Filtered, None) => {
                        return Err(data_error(format!(
                            "cases[{index}] filtered velocity layer requires expected_filter"
                        )))
                    }
                    _ => {}
                }
                let interval = case.interval.as_ref().ok_or_else(|| {
                    data_error(format!("cases[{index}] supported case requires interval"))
                })?;
                MetricInterval::try_new(interval.start_s, interval.end_s)?;
                let uncertainty = case.reference_uncertainty.as_ref().ok_or_else(|| {
                    data_error(format!(
                        "cases[{index}] supported case requires reference_uncertainty"
                    ))
                })?;
                validate_uncertainty(index, uncertainty)?;
                validate_reference_samples(index, interval, &case.reference_samples)?;
            }
        }
    }

    validate_revision("rom", study.target_revisions.rom.as_ref(), ROM_TARGET_M)?;
    validate_revision(
        "mean_velocity",
        study.target_revisions.mean_velocity.as_ref(),
        MEAN_VELOCITY_TARGET_MPS,
    )?;
    validate_revision(
        "peak_velocity",
        study.target_revisions.peak_velocity.as_ref(),
        PEAK_VELOCITY_TARGET_MPS,
    )?;
    Ok(())
}

fn validate_uncertainty(index: usize, uncertainty: &ReferenceUncertainty) -> AnyResult<()> {
    if !uncertainty.position_m.is_finite() || uncertainty.position_m < 0.0 {
        return Err(data_error(format!(
            "cases[{index}].reference_uncertainty.position_m must be finite and non-negative"
        )));
    }
    if !uncertainty.velocity_mps.is_finite() || uncertainty.velocity_mps < 0.0 {
        return Err(data_error(format!(
            "cases[{index}].reference_uncertainty.velocity_mps must be finite and non-negative"
        )));
    }
    require_non_blank(
        &format!("cases[{index}].reference_uncertainty.method"),
        &uncertainty.method,
    )
}

fn validate_reference_samples(
    case_index: usize,
    interval: &StudyInterval,
    samples: &[ReferenceSample],
) -> AnyResult<()> {
    if samples.len() < 2 {
        return Err(data_error(format!(
            "cases[{case_index}] requires at least two reference_samples"
        )));
    }
    let mut previous = None;
    for (sample_index, sample) in samples.iter().enumerate() {
        if !sample.timestamp_s.is_finite()
            || sample.timestamp_s < 0.0
            || !sample.position_m.is_finite()
        {
            return Err(data_error(format!(
                "cases[{case_index}].reference_samples[{sample_index}] must contain finite non-negative time and finite position"
            )));
        }
        if let Some(previous) = previous {
            if sample.timestamp_s <= previous {
                return Err(data_error(format!(
                    "cases[{case_index}] reference timestamps must be strictly increasing"
                )));
            }
        }
        previous = Some(sample.timestamp_s);
    }
    if samples.first().map(|sample| sample.timestamp_s) != Some(interval.start_s)
        || samples.last().map(|sample| sample.timestamp_s) != Some(interval.end_s)
    {
        return Err(data_error(format!(
            "cases[{case_index}] interval boundaries must equal the first and last exact reference sample timestamps"
        )));
    }
    Ok(())
}

fn validate_revision(
    name: &str,
    revision: Option<&GateRevision>,
    original_threshold: f64,
) -> AnyResult<()> {
    let Some(revision) = revision else {
        return Ok(());
    };
    if !revision.revised_threshold.is_finite() || revision.revised_threshold <= 0.0 {
        return Err(data_error(format!(
            "target_revisions.{name}.revised_threshold must be finite and positive"
        )));
    }
    if revision.revised_threshold == original_threshold {
        return Err(data_error(format!(
            "target_revisions.{name}.revised_threshold must differ from the preserved original target"
        )));
    }
    require_non_blank(
        &format!("target_revisions.{name}.rationale"),
        &revision.rationale,
    )?;
    require_non_blank(
        &format!("target_revisions.{name}.evidence"),
        &revision.evidence,
    )
}

fn require_non_blank(path: &str, value: &str) -> AnyResult<()> {
    if value.trim().is_empty() {
        return Err(data_error(format!("{path} must not be blank")));
    }
    Ok(())
}

fn require_exact(path: &str, actual: &str, expected: &str) -> AnyResult<()> {
    if actual != expected {
        return Err(data_error(format!(
            "{path} must be {expected:?}, got {actual:?}"
        )));
    }
    Ok(())
}

fn evaluate_study(study: &StudySpec, study_path: &Path) -> ReferenceStudyResult {
    let mut cases = Vec::with_capacity(study.cases.len());

    for case in &study.cases {
        if case.status == StudyCaseStatus::Unsupported {
            cases.push(CaseResult {
                id: case.id.clone(),
                condition: case.condition.clone(),
                outcome: CaseOutcome::Unsupported,
                reference_sample_count: case.reference_samples.len(),
                reference_uncertainty: case.reference_uncertainty.clone(),
                failure_reason: case.unsupported_reason.clone(),
                metrics: None,
                openbar_provenance: None,
            });
            continue;
        }

        match evaluate_supported_case(study, case, study_path) {
            Ok((metrics, provenance)) => cases.push(CaseResult {
                id: case.id.clone(),
                condition: case.condition.clone(),
                outcome: CaseOutcome::Evaluated,
                reference_sample_count: case.reference_samples.len(),
                reference_uncertainty: case.reference_uncertainty.clone(),
                failure_reason: None,
                metrics: Some(metrics),
                openbar_provenance: Some(provenance),
            }),
            Err(reason) => cases.push(CaseResult {
                id: case.id.clone(),
                condition: case.condition.clone(),
                outcome: CaseOutcome::Failed,
                reference_sample_count: case.reference_samples.len(),
                reference_uncertainty: case.reference_uncertainty.clone(),
                failure_reason: Some(reason),
                metrics: None,
                openbar_provenance: None,
            }),
        }
    }

    let evaluated_cases = cases
        .iter()
        .filter(|case| case.outcome == CaseOutcome::Evaluated)
        .count();
    let failed_supported_cases = cases
        .iter()
        .filter(|case| case.outcome == CaseOutcome::Failed)
        .count();
    let unsupported_cases = cases
        .iter()
        .filter(|case| case.outcome == CaseOutcome::Unsupported)
        .count();
    let evaluated_reference_samples = cases
        .iter()
        .filter(|case| case.outcome == CaseOutcome::Evaluated)
        .map(|case| case.reference_sample_count)
        .sum();

    let rom_errors = cases
        .iter()
        .filter_map(|case| case.metrics.as_ref().map(|metrics| metrics.rom_error_m))
        .collect::<Vec<_>>();
    let mean_errors = cases
        .iter()
        .filter_map(|case| {
            case.metrics
                .as_ref()
                .map(|metrics| metrics.mean_velocity_error_mps)
        })
        .collect::<Vec<_>>();
    let peak_errors = cases
        .iter()
        .filter_map(|case| {
            case.metrics
                .as_ref()
                .map(|metrics| metrics.peak_velocity_error_mps)
        })
        .collect::<Vec<_>>();

    let gates = GateResults {
        rom_mae_m: evaluate_gate(
            ROM_TARGET_M,
            &rom_errors,
            failed_supported_cases,
            study.target_revisions.rom.as_ref(),
        ),
        mean_velocity_mae_mps: evaluate_gate(
            MEAN_VELOCITY_TARGET_MPS,
            &mean_errors,
            failed_supported_cases,
            study.target_revisions.mean_velocity.as_ref(),
        ),
        peak_velocity_mae_mps: evaluate_gate(
            PEAK_VELOCITY_TARGET_MPS,
            &peak_errors,
            failed_supported_cases,
            study.target_revisions.peak_velocity.as_ref(),
        ),
    };

    ReferenceStudyResult {
        schema_version: RESULT_SCHEMA_VERSION,
        study_id: study.study_id.clone(),
        evidence_class: study.evidence_class,
        purpose: study.purpose.clone(),
        reference: study.reference.clone(),
        construct: study.construct.clone(),
        summary: StudySummary {
            total_cases: cases.len(),
            evaluated_cases,
            failed_supported_cases,
            unsupported_cases,
            evaluated_reference_samples,
        },
        cases,
        gates,
        limitations: vec![
            "Gate status is scoped only to the conditions, axis, intervals, reference system, and OpenBar provenance recorded in this study.",
            "Reference uncertainty is reported, not subtracted from OpenBar error and not converted into an unstated confidence interval.",
            "Unsupported cases remain explicit and are never included in gate averages; expected-supported failures force gate FAIL.",
            "PASS does not validate recording conditions or constructs outside the documented study envelope.",
            "This artifact does not authorize automatic detection or a Flutter/product accuracy claim beyond the study design.",
        ],
    }
}

fn evaluate_supported_case(
    study: &StudySpec,
    case: &StudyCase,
    study_path: &Path,
) -> Result<(CaseMetrics, CaseOpenBarProvenance), String> {
    let analysis_path = resolve_analysis_path(
        study_path,
        case.analysis_path
            .as_deref()
            .ok_or_else(|| "supported case is missing analysis_path".to_owned())?,
    );
    let text = fs::read_to_string(&analysis_path)
        .map_err(|error| format!("cannot read canonical analysis: {error}"))?;
    let analysis =
        Analysis::from_json(&text).map_err(|error| format!("canonical analysis is invalid: {error}"))?;

    let expected_tracker = case
        .expected_tracker
        .as_ref()
        .ok_or_else(|| "supported case is missing expected_tracker".to_owned())?;
    if &analysis.provenance().tracker.implementation != expected_tracker {
        return Err("analysis tracker provenance does not match the frozen study case".to_owned());
    }

    if analysis.calibration().method() != study.construct.calibration_method
        || analysis.calibration().method_version() != study.construct.calibration_method_version
        || analysis.calibration().coordinate_convention() != study.construct.coordinate_convention
    {
        return Err("analysis calibration provenance does not match the frozen study contract".to_owned());
    }

    let velocity_layer = case
        .velocity_layer
        .ok_or_else(|| "supported case is missing velocity_layer".to_owned())?;
    let derived = analysis.derived();
    let (velocity_source, filter_provenance) = match velocity_layer {
        VelocityLayer::Calibrated => {
            if case.expected_filter.is_some() {
                return Err("calibrated velocity layer cannot pin a filter".to_owned());
            }
            (derived.calibrated.samples.as_slice(), None)
        }
        VelocityLayer::Filtered => {
            let filtered = derived
                .filtered
                .as_ref()
                .ok_or_else(|| "study requests filtered velocity but analysis has no filtered layer".to_owned())?;
            let expected = case
                .expected_filter
                .as_ref()
                .ok_or_else(|| "filtered velocity layer is missing expected_filter".to_owned())?;
            if &filtered.filter != expected {
                return Err("analysis filter provenance does not match the frozen study case".to_owned());
            }
            (filtered.samples.as_slice(), Some(filtered.filter.clone()))
        }
    };

    let kinematics = derived
        .kinematics
        .as_ref()
        .ok_or_else(|| "analysis has no canonical kinematics layer".to_owned())?;
    if kinematics.input != velocity_layer.expected_input() {
        return Err("analysis kinematics input does not match the frozen velocity layer".to_owned());
    }
    if kinematics.method.implementation != study.construct.kinematics.implementation
        || kinematics.method.version != study.construct.kinematics.version
    {
        return Err("analysis kinematics method/version does not match the frozen study contract".to_owned());
    }
    let config = KinematicsConfig::from_velocity_provenance(&kinematics.method)
        .map_err(|error| format!("cannot reconstruct canonical kinematics configuration: {error}"))?;
    if config.max_gap_s != study.construct.kinematics.max_gap_s
        || config.min_confidence != study.construct.kinematics.min_confidence
    {
        return Err("analysis kinematics parameters do not match the frozen study contract".to_owned());
    }

    let reference = case
        .reference_samples
        .iter()
        .map(|sample| study.construct.axis.reference_sample(sample))
        .collect::<Vec<_>>();
    let calibrated = exact_aligned_samples(
        &derived.calibrated.samples,
        &case.reference_samples,
        "calibrated",
    )?;
    let velocity = exact_aligned_samples(velocity_source, &case.reference_samples, "velocity")?;

    let interval_spec = case
        .interval
        .as_ref()
        .ok_or_else(|| "supported case is missing interval".to_owned())?;
    let interval = MetricInterval::try_new(interval_spec.start_s, interval_spec.end_s)
        .map_err(|error| error.to_string())?;
    let axis = study.construct.axis.core();

    let reference_rom = require_metric(
        "reference ROM",
        range_of_motion(&reference, axis, config).map_err(|error| error.to_string())?,
    )?;
    let openbar_rom = require_metric(
        "OpenBar calibrated ROM",
        range_of_motion(&calibrated, axis, config).map_err(|error| error.to_string())?,
    )?;
    let reference_mean = require_metric(
        "reference mean velocity",
        mean_axis_velocity(&reference, axis, interval, config).map_err(|error| error.to_string())?,
    )?;
    let openbar_mean = require_metric(
        "OpenBar mean velocity",
        mean_axis_velocity(&velocity, axis, interval, config).map_err(|error| error.to_string())?,
    )?;
    let reference_peak = require_timed_metric(
        "reference peak velocity",
        peak_axis_velocity(&reference, axis, interval, config).map_err(|error| error.to_string())?,
    )?;
    let openbar_peak = require_timed_metric(
        "OpenBar peak velocity",
        peak_axis_velocity(&velocity, axis, interval, config).map_err(|error| error.to_string())?,
    )?;

    let position_errors = calibrated
        .iter()
        .zip(&case.reference_samples)
        .map(|(actual, reference)| study.construct.axis.value(*actual) - reference.position_m)
        .collect::<Vec<_>>();

    Ok((
        CaseMetrics {
            calibrated_position_error_m: error_distribution(&position_errors)
                .expect("supported case has at least two samples"),
            reference_rom_m: reference_rom.value,
            openbar_rom_m: openbar_rom.value,
            rom_error_m: openbar_rom.value - reference_rom.value,
            reference_mean_velocity_mps: reference_mean.value,
            openbar_mean_velocity_mps: openbar_mean.value,
            mean_velocity_error_mps: openbar_mean.value - reference_mean.value,
            reference_peak_velocity_mps: reference_peak.value,
            reference_peak_timestamp_s: reference_peak.timestamp_s,
            openbar_peak_velocity_mps: openbar_peak.value,
            openbar_peak_timestamp_s: openbar_peak.timestamp_s,
            peak_velocity_error_mps: openbar_peak.value - reference_peak.value,
        },
        CaseOpenBarProvenance {
            source_id: analysis.identity().source_id.clone(),
            source_sha256: analysis.identity().source_sha256.clone(),
            analysis: analysis.provenance().clone(),
            calibration: analysis.calibration().clone(),
            velocity_layer,
            filter: filter_provenance,
            kinematics_method: kinematics.method.clone(),
        },
    ))
}

fn resolve_analysis_path(study_path: &Path, value: &str) -> PathBuf {
    let path = Path::new(value);
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        study_path
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join(path)
    }
}

fn exact_aligned_samples(
    source: &[MetricPositionSample],
    reference: &[ReferenceSample],
    label: &str,
) -> Result<Vec<MetricPositionSample>, String> {
    let mut selected = Vec::with_capacity(reference.len());
    for sample in reference {
        let Some(actual) = source
            .iter()
            .find(|actual| actual.timestamp_s == sample.timestamp_s)
        else {
            return Err(format!(
                "{label} layer has no exact sample at reference timestamp {} s; interpolation is not allowed",
                sample.timestamp_s
            ));
        };
        selected.push(*actual);
    }
    Ok(selected)
}

fn require_metric(
    label: &str,
    value: Option<openbar_core::kinematics::MetricEstimate>,
) -> Result<openbar_core::kinematics::MetricEstimate, String> {
    value.ok_or_else(|| format!("{label} is unavailable under the frozen gap/confidence policy"))
}

fn require_timed_metric(
    label: &str,
    value: Option<openbar_core::kinematics::TimedMetricEstimate>,
) -> Result<openbar_core::kinematics::TimedMetricEstimate, String> {
    value.ok_or_else(|| format!("{label} is unavailable under the frozen gap/confidence policy"))
}

fn evaluate_gate(
    original_threshold: f64,
    signed_errors: &[f64],
    failed_supported_cases: usize,
    revision: Option<&GateRevision>,
) -> GateResult {
    let distribution = error_distribution(signed_errors);
    let effective_threshold = revision
        .map(|revision| revision.revised_threshold)
        .unwrap_or(original_threshold);

    if failed_supported_cases > 0 {
        return GateResult {
            status: GateStatus::Fail,
            original_threshold,
            effective_threshold,
            evaluated_case_count: signed_errors.len(),
            failed_supported_case_count: failed_supported_cases,
            error_distribution: distribution,
            revision: revision.cloned(),
            meets_effective_target: None,
            rationale: "At least one case declared supported could not produce the required metric; failures are not averaged away.".to_owned(),
        };
    }

    let Some(distribution) = distribution else {
        return GateResult {
            status: GateStatus::Fail,
            original_threshold,
            effective_threshold,
            evaluated_case_count: 0,
            failed_supported_case_count: 0,
            error_distribution: None,
            revision: revision.cloned(),
            meets_effective_target: None,
            rationale: "No supported evaluated case produced this metric.".to_owned(),
        };
    };

    let meets = distribution.mae < effective_threshold;
    let (status, rationale) = match revision {
        Some(_) => (
            GateStatus::Revised,
            "The original target is preserved and an explicit evidence/rationale-backed revised target is recorded; inspect meets_effective_target separately.".to_owned(),
        ),
        None if meets => (
            GateStatus::Pass,
            "Observed MAE is strictly below the original provisional engineering target for the recorded study scope.".to_owned(),
        ),
        None => (
            GateStatus::Fail,
            "Observed MAE is not strictly below the original provisional engineering target for the recorded study scope.".to_owned(),
        ),
    };

    GateResult {
        status,
        original_threshold,
        effective_threshold,
        evaluated_case_count: signed_errors.len(),
        failed_supported_case_count: 0,
        error_distribution: Some(distribution),
        revision: revision.cloned(),
        meets_effective_target: Some(meets),
        rationale,
    }
}

fn error_distribution(values: &[f64]) -> Option<ErrorDistribution> {
    if values.is_empty() {
        return None;
    }
    debug_assert!(values.iter().all(|value| value.is_finite()));

    let count = values.len();
    let denominator = count as f64;
    let bias = values.iter().sum::<f64>() / denominator;
    let mae = values.iter().map(|value| value.abs()).sum::<f64>() / denominator;
    let rmse = (values.iter().map(|value| value * value).sum::<f64>() / denominator).sqrt();
    let mut absolute = values.iter().map(|value| value.abs()).collect::<Vec<_>>();
    absolute.sort_by(f64::total_cmp);

    Some(ErrorDistribution {
        count,
        mae,
        rmse,
        bias,
        p50_absolute: nearest_rank(&absolute, 0.50),
        p90_absolute: nearest_rank(&absolute, 0.90),
        p95_absolute: nearest_rank(&absolute, 0.95),
        max_absolute: *absolute.last().expect("non-empty"),
    })
}

fn nearest_rank(sorted: &[f64], quantile: f64) -> f64 {
    let rank = (quantile * sorted.len() as f64).ceil() as usize;
    sorted[rank.saturating_sub(1).min(sorted.len() - 1)]
}

fn render_report(result: &ReferenceStudyResult) -> String {
    let mut report = String::new();
    report.push_str("# M0 independent kinematic reference report\n\n");
    report.push_str(&format!("- Study: `{}`\n", result.study_id));
    report.push_str(&format!("- Purpose: {}\n", result.purpose));
    report.push_str(&format!(
        "- Reference: {:?} — {}\n",
        result.reference.source_type, result.reference.system
    ));
    report.push_str(&format!(
        "- Axis: `{}`; coordinate convention: `reference_centre_x_right_y_up`\n",
        result.construct.axis.label()
    ));
    report.push_str(&format!(
        "- Kinematics: `{}@{}`, max gap {} s, min confidence {}\n",
        result.construct.kinematics.implementation,
        result.construct.kinematics.version,
        result.construct.kinematics.max_gap_s,
        result.construct.kinematics.min_confidence
    ));
    report.push_str(&format!(
        "- Timestamp alignment: `{}`; synchronization: {}\n\n",
        result.construct.timestamp_alignment, result.reference.synchronization
    ));

    report.push_str("## Coverage\n\n");
    report.push_str(&format!(
        "{} cases: {} evaluated, {} expected-supported failures, {} unsupported; {} reference samples evaluated.\n\n",
        result.summary.total_cases,
        result.summary.evaluated_cases,
        result.summary.failed_supported_cases,
        result.summary.unsupported_cases,
        result.summary.evaluated_reference_samples
    ));

    report.push_str("| Case | Condition | Outcome | Samples | Position uncertainty | Velocity uncertainty | Note |\n");
    report.push_str("| --- | --- | --- | ---: | ---: | ---: | --- |\n");
    for case in &result.cases {
        let (position_uncertainty, velocity_uncertainty) = case
            .reference_uncertainty
            .as_ref()
            .map(|value| {
                (
                    format!("{:.6} m", value.position_m),
                    format!("{:.6} m/s", value.velocity_mps),
                )
            })
            .unwrap_or_else(|| ("n/a".to_owned(), "n/a".to_owned()));
        report.push_str(&format!(
            "| {} | {} | {:?} | {} | {} | {} | {} |\n",
            case.id,
            case.condition,
            case.outcome,
            case.reference_sample_count,
            position_uncertainty,
            velocity_uncertainty,
            case.failure_reason.as_deref().unwrap_or("")
        ));
    }

    report.push_str("\n## Gate status\n\n");
    report.push_str("| Gate | Original target | Status | Observed MAE | Bias | Effective target |\n");
    report.push_str("| --- | ---: | --- | ---: | ---: | ---: |\n");
    render_gate_row(
        &mut report,
        "ROM MAE",
        "m",
        &result.gates.rom_mae_m,
    );
    render_gate_row(
        &mut report,
        "Mean velocity MAE",
        "m/s",
        &result.gates.mean_velocity_mae_mps,
    );
    render_gate_row(
        &mut report,
        "Peak velocity MAE",
        "m/s",
        &result.gates.peak_velocity_mae_mps,
    );

    report.push_str("\n## Evaluated cases\n\n");
    for case in result
        .cases
        .iter()
        .filter(|case| case.outcome == CaseOutcome::Evaluated)
    {
        let metrics = case.metrics.as_ref().expect("evaluated case has metrics");
        report.push_str(&format!("### {} — {}\n\n", case.id, case.condition));
        report.push_str(&format!(
            "- Calibrated position: MAE {:.6} m, RMSE {:.6} m, bias {:.6} m, p95 |error| {:.6} m, max |error| {:.6} m.\n",
            metrics.calibrated_position_error_m.mae,
            metrics.calibrated_position_error_m.rmse,
            metrics.calibrated_position_error_m.bias,
            metrics.calibrated_position_error_m.p95_absolute,
            metrics.calibrated_position_error_m.max_absolute
        ));
        report.push_str(&format!(
            "- ROM: reference {:.6} m, OpenBar {:.6} m, signed error {:.6} m.\n",
            metrics.reference_rom_m, metrics.openbar_rom_m, metrics.rom_error_m
        ));
        report.push_str(&format!(
            "- Mean signed-axis velocity: reference {:.6} m/s, OpenBar {:.6} m/s, signed error {:.6} m/s.\n",
            metrics.reference_mean_velocity_mps,
            metrics.openbar_mean_velocity_mps,
            metrics.mean_velocity_error_mps
        ));
        report.push_str(&format!(
            "- Peak signed-axis velocity: reference {:.6} m/s @ {:.6} s, OpenBar {:.6} m/s @ {:.6} s, signed error {:.6} m/s.\n\n",
            metrics.reference_peak_velocity_mps,
            metrics.reference_peak_timestamp_s,
            metrics.openbar_peak_velocity_mps,
            metrics.openbar_peak_timestamp_s,
            metrics.peak_velocity_error_mps
        ));
    }

    report.push_str("## Reference provenance and uncertainty\n\n");
    report.push_str(&format!("- Protocol: {}\n", result.reference.protocol));
    report.push_str(&format!(
        "- Coordinate alignment: {}\n",
        result.reference.coordinate_alignment
    ));
    report.push_str(&format!(
        "- Rights/access: {}\n",
        result.reference.rights_or_access
    ));
    if let Some(notes) = result.reference.notes.as_deref() {
        report.push_str(&format!("- Notes: {notes}\n"));
    }

    report.push_str("\n## Scope and limitations\n\n");
    for limitation in &result.limitations {
        report.push_str(&format!("- {limitation}\n"));
    }
    report.push('\n');
    report
}

fn render_gate_row(report: &mut String, name: &str, unit: &str, gate: &GateResult) {
    let (mae, bias) = gate
        .error_distribution
        .as_ref()
        .map(|distribution| {
            (
                format!("{:.6} {unit}", distribution.mae),
                format!("{:.6} {unit}", distribution.bias),
            )
        })
        .unwrap_or_else(|| ("n/a".to_owned(), "n/a".to_owned()));
    report.push_str(&format!(
        "| {name} | < {:.6} {unit} | **{}** | {} | {} | < {:.6} {unit} |\n",
        gate.original_threshold,
        gate.status.label(),
        mae,
        bias,
        gate.effective_threshold
    ));
}

fn write_text(path: &Path, text: &str) -> AnyResult<()> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)?;
        }
    }
    fs::write(path, text)?;
    Ok(())
}

fn data_error(message: impl Into<String>) -> Box<dyn Error> {
    Box::new(io::Error::new(io::ErrorKind::InvalidData, message.into()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(timestamp_s: f64, position_m: f64) -> ReferenceSample {
        ReferenceSample {
            timestamp_s,
            position_m,
        }
    }

    fn metric(timestamp_s: f64, y_m: f64) -> MetricPositionSample {
        MetricPositionSample {
            timestamp_s,
            x_m: 0.0,
            y_m,
            confidence: 1.0,
        }
    }

    #[test]
    fn distribution_reports_signed_bias_and_absolute_quantiles() {
        let distribution = error_distribution(&[-0.02, 0.01, 0.03, -0.04]).unwrap();
        assert_eq!(distribution.count, 4);
        assert!((distribution.mae - 0.025).abs() < 1.0e-12);
        assert!((distribution.bias + 0.005).abs() < 1.0e-12);
        assert_eq!(distribution.p50_absolute, 0.02);
        assert_eq!(distribution.p90_absolute, 0.04);
        assert_eq!(distribution.p95_absolute, 0.04);
        assert_eq!(distribution.max_absolute, 0.04);
    }

    #[test]
    fn gate_fails_when_expected_supported_case_failed_even_if_survivors_are_good() {
        let gate = evaluate_gate(0.01, &[0.001, -0.002], 1, None);
        assert_eq!(gate.status, GateStatus::Fail);
        assert_eq!(gate.failed_supported_case_count, 1);
        assert!(gate.meets_effective_target.is_none());
    }

    #[test]
    fn gate_preserves_original_target_when_revision_is_declared() {
        let revision = GateRevision {
            revised_threshold: 0.02,
            rationale: "measured reference floor".to_owned(),
            evidence: "study appendix A".to_owned(),
        };
        let gate = evaluate_gate(0.01, &[0.012, -0.014], 0, Some(&revision));
        assert_eq!(gate.status, GateStatus::Revised);
        assert_eq!(gate.original_threshold, 0.01);
        assert_eq!(gate.effective_threshold, 0.02);
        assert_eq!(gate.meets_effective_target, Some(true));
    }

    #[test]
    fn exact_alignment_refuses_interpolation() {
        let source = [metric(0.0, 0.0), metric(0.1, 0.1), metric(0.2, 0.2)];
        let reference = [sample(0.0, 0.0), sample(0.15, 0.15), sample(0.2, 0.2)];
        let error = exact_aligned_samples(&source, &reference, "calibrated").unwrap_err();
        assert!(error.contains("interpolation is not allowed"));
    }

    #[test]
    fn supported_reference_metrics_use_core_semantics() {
        let reference_samples = [
            sample(0.0, 0.0),
            sample(0.1, 0.12),
            sample(0.2, 0.20),
        ];
        let reference = reference_samples
            .iter()
            .map(|sample| StudyAxis::VerticalY.reference_sample(sample))
            .collect::<Vec<_>>();
        let openbar = [metric(0.0, 0.0), metric(0.1, 0.11), metric(0.2, 0.19)];
        let config = KinematicsConfig::try_new(0.2, 0.0).unwrap();
        let interval = MetricInterval::try_new(0.0, 0.2).unwrap();

        let reference_rom =
            range_of_motion(&reference, MetricAxis::VerticalY, config)
                .unwrap()
                .unwrap();
        let openbar_rom =
            range_of_motion(&openbar, MetricAxis::VerticalY, config)
                .unwrap()
                .unwrap();
        let reference_mean =
            mean_axis_velocity(&reference, MetricAxis::VerticalY, interval, config)
                .unwrap()
                .unwrap();
        let openbar_mean =
            mean_axis_velocity(&openbar, MetricAxis::VerticalY, interval, config)
                .unwrap()
                .unwrap();
        let reference_peak =
            peak_axis_velocity(&reference, MetricAxis::VerticalY, interval, config)
                .unwrap()
                .unwrap();
        let openbar_peak =
            peak_axis_velocity(&openbar, MetricAxis::VerticalY, interval, config)
                .unwrap()
                .unwrap();

        assert!((reference_rom.value - 0.20).abs() < 1.0e-12);
        assert!((openbar_rom.value - 0.19).abs() < 1.0e-12);
        assert!((reference_mean.value - 1.0).abs() < 1.0e-12);
        assert!((openbar_mean.value - 0.95).abs() < 1.0e-12);
        assert!((reference_peak.value - 1.2).abs() < 1.0e-12);
        assert!((openbar_peak.value - 1.1).abs() < 1.0e-12);
    }
}
