use crate::analysis::{
    ImplementationProvenance, KinematicTrajectory, KinematicsInput, ParameterValue,
};
use crate::math::approximately_equal;
use crate::trajectory::{KinematicSample, MetricPositionSample, TrajectoryValidationError};
use std::collections::BTreeMap;
use std::fmt;

pub const VELOCITY_METHOD_IMPLEMENTATION: &str = "backward-difference";
pub const VELOCITY_METHOD_VERSION: &str = "1";

const MAX_GAP_PARAMETER: &str = "max_gap_s";
const MIN_CONFIDENCE_PARAMETER: &str = "min_confidence";

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct KinematicsConfig {
    pub max_gap_s: f64,
    pub min_confidence: f32,
}

impl KinematicsConfig {
    pub fn try_new(max_gap_s: f64, min_confidence: f32) -> Result<Self, KinematicsError> {
        if !max_gap_s.is_finite() || max_gap_s <= 0.0 {
            return Err(KinematicsError::InvalidMaximumGap { value: max_gap_s });
        }
        if !min_confidence.is_finite() || !(0.0..=1.0).contains(&min_confidence) {
            return Err(KinematicsError::InvalidMinimumConfidence {
                value: min_confidence,
            });
        }
        Ok(Self {
            max_gap_s,
            min_confidence,
        })
    }

    pub fn velocity_provenance(self) -> ImplementationProvenance {
        ImplementationProvenance {
            implementation: VELOCITY_METHOD_IMPLEMENTATION.to_owned(),
            version: VELOCITY_METHOD_VERSION.to_owned(),
            parameters: BTreeMap::from([
                (
                    MAX_GAP_PARAMETER.to_owned(),
                    ParameterValue::Float(self.max_gap_s),
                ),
                (
                    MIN_CONFIDENCE_PARAMETER.to_owned(),
                    ParameterValue::Float(persisted_confidence(self.min_confidence)),
                ),
            ]),
        }
    }

    /// Reconstructs the configuration recorded by [`Self::velocity_provenance`]. Fails closed on
    /// any other implementation/version, a missing parameter, or an unknown parameter.
    pub fn from_velocity_provenance(
        method: &ImplementationProvenance,
    ) -> Result<Self, KinematicsError> {
        if method.implementation != VELOCITY_METHOD_IMPLEMENTATION
            || method.version != VELOCITY_METHOD_VERSION
        {
            return Err(KinematicsError::UnsupportedMethod {
                implementation: method.implementation.clone(),
                version: method.version.clone(),
            });
        }
        if let Some(name) = method
            .parameters
            .keys()
            .find(|name| ![MAX_GAP_PARAMETER, MIN_CONFIDENCE_PARAMETER].contains(&name.as_str()))
        {
            return Err(KinematicsError::InvalidMethodParameter { name: name.clone() });
        }
        let max_gap_s = numeric_parameter(method, MAX_GAP_PARAMETER)?;
        let min_confidence =
            narrow_confidence(numeric_parameter(method, MIN_CONFIDENCE_PARAMETER)?);
        Self::try_new(max_gap_s, min_confidence)
    }
}

/// Shortest decimal that round-trips the `f32` threshold, so `0.4` is persisted as `0.4` rather
/// than its widened binary value `0.4000000059604645`.
fn persisted_confidence(value: f32) -> f64 {
    value.to_string().parse().unwrap_or(f64::from(value))
}

/// Restores the `f32` threshold that was applied. Narrowing through the shortest decimal is a
/// single correctly rounded step, so it is exact for every value `persisted_confidence` writes.
fn narrow_confidence(value: f64) -> f32 {
    value.to_string().parse().unwrap_or(value as f32)
}

fn numeric_parameter(
    method: &ImplementationProvenance,
    name: &'static str,
) -> Result<f64, KinematicsError> {
    match method.parameters.get(name) {
        Some(ParameterValue::Float(value)) => Ok(*value),
        Some(ParameterValue::Integer(value)) => Ok(*value as f64),
        _ => Err(KinematicsError::InvalidMethodParameter {
            name: name.to_owned(),
        }),
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MetricAxis {
    HorizontalX,
    VerticalY,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MetricInterval {
    pub start_s: f64,
    pub end_s: f64,
}

impl MetricInterval {
    pub fn try_new(start_s: f64, end_s: f64) -> Result<Self, KinematicsError> {
        if !start_s.is_finite() || !end_s.is_finite() || start_s < 0.0 || end_s <= start_s {
            return Err(KinematicsError::InvalidInterval { start_s, end_s });
        }
        Ok(Self { start_s, end_s })
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MetricEstimate {
    pub value: f64,
    pub confidence: f32,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TimedMetricEstimate {
    pub value: f64,
    pub timestamp_s: f64,
    pub confidence: f32,
}

#[derive(Debug, Clone, PartialEq)]
pub enum KinematicsError {
    InvalidMaximumGap {
        value: f64,
    },
    InvalidMinimumConfidence {
        value: f32,
    },
    InvalidSample {
        index: usize,
        error: TrajectoryValidationError,
    },
    NonIncreasingTimestamp {
        previous_index: usize,
        index: usize,
    },
    InvalidInterval {
        start_s: f64,
        end_s: f64,
    },
    IntervalBoundaryNotSampled {
        timestamp_s: f64,
    },
    NonFiniteDerivedValue {
        index: usize,
    },
    UnsupportedMethod {
        implementation: String,
        version: String,
    },
    InvalidMethodParameter {
        name: String,
    },
    TrajectoryMismatch {
        index: usize,
    },
}

impl fmt::Display for KinematicsError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidMaximumGap { value } => write!(
                formatter,
                "maximum continuity gap must be finite and positive, got {value}"
            ),
            Self::InvalidMinimumConfidence { value } => write!(
                formatter,
                "minimum confidence must be finite and within [0, 1], got {value}"
            ),
            Self::InvalidSample { index, error } => {
                write!(formatter, "kinematic input sample {index} is invalid: {error}")
            }
            Self::NonIncreasingTimestamp {
                previous_index,
                index,
            } => write!(
                formatter,
                "kinematic timestamps must be strictly increasing (indices {previous_index} and {index})"
            ),
            Self::InvalidInterval { start_s, end_s } => write!(
                formatter,
                "metric interval must be finite, non-negative, and have end > start, got [{start_s}, {end_s}]"
            ),
            Self::IntervalBoundaryNotSampled { timestamp_s } => write!(
                formatter,
                "metric interval boundary {timestamp_s} s does not match an authoritative sample timestamp"
            ),
            Self::NonFiniteDerivedValue { index } => write!(
                formatter,
                "kinematic calculation produced a non-finite value at sample {index}"
            ),
            Self::UnsupportedMethod {
                implementation,
                version,
            } => write!(
                formatter,
                "kinematics method {implementation}@{version} is not {VELOCITY_METHOD_IMPLEMENTATION}@{VELOCITY_METHOD_VERSION}"
            ),
            Self::InvalidMethodParameter { name } => write!(
                formatter,
                "kinematics method parameter {name:?} is missing, unknown, or not numeric"
            ),
            Self::TrajectoryMismatch { index } => write!(
                formatter,
                "kinematic sample {index} does not match a re-derivation with its recorded method"
            ),
        }
    }
}

impl std::error::Error for KinematicsError {}

pub fn derive_kinematic_trajectory(
    samples: &[MetricPositionSample],
    input: KinematicsInput,
    config: KinematicsConfig,
) -> Result<KinematicTrajectory, KinematicsError> {
    Ok(KinematicTrajectory {
        input,
        method: config.velocity_provenance(),
        samples: derive_velocity(samples, config)?,
    })
}

/// Checks that a persisted trajectory is exactly what its recorded `backward-difference@1`
/// method and parameters produce from `input`: no velocity at the first sample, across an
/// over-long gap, or from a below-threshold endpoint, and confidence equal to the pair minimum.
pub fn verify_kinematic_trajectory(
    input: &[MetricPositionSample],
    trajectory: &KinematicTrajectory,
) -> Result<(), KinematicsError> {
    let config = KinematicsConfig::from_velocity_provenance(&trajectory.method)?;
    let expected = derive_velocity(input, config)?;
    if expected.len() != trajectory.samples.len() {
        return Err(KinematicsError::TrajectoryMismatch {
            index: expected.len().min(trajectory.samples.len()),
        });
    }
    let mismatch = expected
        .iter()
        .zip(&trajectory.samples)
        .position(|(expected, actual)| !same_kinematic_sample(*expected, *actual));
    match mismatch {
        Some(index) => Err(KinematicsError::TrajectoryMismatch { index }),
        None => Ok(()),
    }
}

fn same_kinematic_sample(expected: KinematicSample, actual: KinematicSample) -> bool {
    expected.timestamp_s == actual.timestamp_s
        && approximately_equal(expected.x_m, actual.x_m)
        && approximately_equal(expected.y_m, actual.y_m)
        && same_velocity(expected.vx_mps, actual.vx_mps)
        && same_velocity(expected.vy_mps, actual.vy_mps)
        && expected.confidence == actual.confidence
}

fn same_velocity(expected: Option<f64>, actual: Option<f64>) -> bool {
    match (expected, actual) {
        (None, None) => true,
        (Some(expected), Some(actual)) => approximately_equal(expected, actual),
        _ => false,
    }
}

pub fn derive_velocity(
    samples: &[MetricPositionSample],
    config: KinematicsConfig,
) -> Result<Vec<KinematicSample>, KinematicsError> {
    validate_config(config)?;
    validate_samples(samples)?;

    if samples.is_empty() {
        return Ok(Vec::new());
    }

    let mut output = Vec::with_capacity(samples.len());
    output.push(KinematicSample {
        timestamp_s: samples[0].timestamp_s,
        x_m: samples[0].x_m,
        y_m: samples[0].y_m,
        vx_mps: None,
        vy_mps: None,
        confidence: samples[0].confidence,
    });

    for (index, pair) in samples.windows(2).enumerate() {
        let previous = pair[0];
        let current = pair[1];
        let dt = current.timestamp_s - previous.timestamp_s;
        let confidence = current.confidence.min(previous.confidence);
        let supported = dt <= config.max_gap_s
            && previous.confidence >= config.min_confidence
            && current.confidence >= config.min_confidence;

        let (vx_mps, vy_mps) = if supported {
            let vx = (current.x_m - previous.x_m) / dt;
            let vy = (current.y_m - previous.y_m) / dt;
            if !vx.is_finite() || !vy.is_finite() {
                return Err(KinematicsError::NonFiniteDerivedValue { index: index + 1 });
            }
            (Some(vx), Some(vy))
        } else {
            (None, None)
        };

        output.push(KinematicSample {
            timestamp_s: current.timestamp_s,
            x_m: current.x_m,
            y_m: current.y_m,
            vx_mps,
            vy_mps,
            confidence,
        });
    }

    Ok(output)
}

pub fn axis_displacement(
    samples: &[MetricPositionSample],
    axis: MetricAxis,
    config: KinematicsConfig,
) -> Result<Option<MetricEstimate>, KinematicsError> {
    validate_config(config)?;
    validate_samples(samples)?;

    if samples.len() < 2 || !series_supported(samples, 0, samples.len() - 1, config) {
        return Ok(None);
    }

    let value =
        axis_value(*samples.last().expect("length checked"), axis) - axis_value(samples[0], axis);
    if !value.is_finite() {
        return Err(KinematicsError::NonFiniteDerivedValue {
            index: samples.len() - 1,
        });
    }

    Ok(Some(MetricEstimate {
        value,
        confidence: minimum_confidence(samples),
    }))
}

pub fn range_of_motion(
    samples: &[MetricPositionSample],
    axis: MetricAxis,
    config: KinematicsConfig,
) -> Result<Option<MetricEstimate>, KinematicsError> {
    validate_config(config)?;
    validate_samples(samples)?;

    if samples.len() < 2 {
        return Ok(None);
    }
    if !series_supported(samples, 0, samples.len() - 1, config) {
        return Ok(None);
    }
    let mut minimum = axis_value(samples[0], axis);
    let mut maximum = minimum;
    for sample in &samples[1..] {
        let value = axis_value(*sample, axis);
        minimum = minimum.min(value);
        maximum = maximum.max(value);
    }
    let value = maximum - minimum;
    if !value.is_finite() {
        return Err(KinematicsError::NonFiniteDerivedValue {
            index: samples.len() - 1,
        });
    }

    Ok(Some(MetricEstimate {
        value,
        confidence: minimum_confidence(samples),
    }))
}

pub fn mean_axis_velocity(
    samples: &[MetricPositionSample],
    axis: MetricAxis,
    interval: MetricInterval,
    config: KinematicsConfig,
) -> Result<Option<MetricEstimate>, KinematicsError> {
    validate_config(config)?;
    validate_samples(samples)?;
    let (start_index, end_index) = match interval_indices(samples, interval)? {
        Some(indices) => indices,
        None => return Ok(None),
    };

    if !series_supported(samples, start_index, end_index, config) {
        return Ok(None);
    }

    let duration_s = interval.end_s - interval.start_s;
    let displacement =
        axis_value(samples[end_index], axis) - axis_value(samples[start_index], axis);
    let value = displacement / duration_s;
    if !value.is_finite() {
        return Err(KinematicsError::NonFiniteDerivedValue { index: end_index });
    }

    Ok(Some(MetricEstimate {
        value,
        confidence: minimum_confidence(&samples[start_index..=end_index]),
    }))
}

pub fn peak_axis_velocity(
    samples: &[MetricPositionSample],
    axis: MetricAxis,
    interval: MetricInterval,
    config: KinematicsConfig,
) -> Result<Option<TimedMetricEstimate>, KinematicsError> {
    validate_config(config)?;
    validate_samples(samples)?;
    let (start_index, end_index) = match interval_indices(samples, interval)? {
        Some(indices) => indices,
        None => return Ok(None),
    };

    if !series_supported(samples, start_index, end_index, config) {
        return Ok(None);
    }

    // Derive only inside the interval: the segment ending at the interval start is excluded by
    // definition, and samples outside the interval must not influence (or fail) the metric.
    let velocity =
        derive_velocity(&samples[start_index..=end_index], config).map_err(
            |error| match error {
                KinematicsError::NonFiniteDerivedValue { index } => {
                    KinematicsError::NonFiniteDerivedValue {
                        index: start_index + index,
                    }
                }
                other => other,
            },
        )?;
    let mut peak: Option<(f64, f64)> = None;
    for sample in &velocity[1..] {
        let Some(value) = velocity_axis_value(*sample, axis) else {
            return Ok(None);
        };
        if peak.is_none_or(|(current, _)| value > current) {
            peak = Some((value, sample.timestamp_s));
        }
    }

    Ok(peak.map(|(value, timestamp_s)| TimedMetricEstimate {
        value,
        timestamp_s,
        confidence: minimum_confidence(&samples[start_index..=end_index]),
    }))
}

fn validate_config(config: KinematicsConfig) -> Result<(), KinematicsError> {
    KinematicsConfig::try_new(config.max_gap_s, config.min_confidence).map(|_| ())
}

fn validate_samples(samples: &[MetricPositionSample]) -> Result<(), KinematicsError> {
    let mut previous_timestamp = None;
    for (index, sample) in samples.iter().enumerate() {
        sample
            .validate()
            .map_err(|error| KinematicsError::InvalidSample { index, error })?;
        if let Some(previous) = previous_timestamp {
            if sample.timestamp_s <= previous {
                return Err(KinematicsError::NonIncreasingTimestamp {
                    previous_index: index - 1,
                    index,
                });
            }
        }
        previous_timestamp = Some(sample.timestamp_s);
    }
    Ok(())
}

fn interval_indices(
    samples: &[MetricPositionSample],
    interval: MetricInterval,
) -> Result<Option<(usize, usize)>, KinematicsError> {
    MetricInterval::try_new(interval.start_s, interval.end_s)?;

    if samples.is_empty() {
        return Ok(None);
    }

    let start_index = samples
        .iter()
        .position(|sample| sample.timestamp_s == interval.start_s)
        .ok_or(KinematicsError::IntervalBoundaryNotSampled {
            timestamp_s: interval.start_s,
        })?;
    let end_index = samples
        .iter()
        .position(|sample| sample.timestamp_s == interval.end_s)
        .ok_or(KinematicsError::IntervalBoundaryNotSampled {
            timestamp_s: interval.end_s,
        })?;
    if end_index <= start_index {
        return Err(KinematicsError::InvalidInterval {
            start_s: interval.start_s,
            end_s: interval.end_s,
        });
    }
    Ok(Some((start_index, end_index)))
}

fn series_supported(
    samples: &[MetricPositionSample],
    start_index: usize,
    end_index: usize,
    config: KinematicsConfig,
) -> bool {
    if samples[start_index..=end_index]
        .iter()
        .any(|sample| sample.confidence < config.min_confidence)
    {
        return false;
    }

    samples[start_index..=end_index]
        .windows(2)
        .all(|pair| pair[1].timestamp_s - pair[0].timestamp_s <= config.max_gap_s)
}

fn axis_value(sample: MetricPositionSample, axis: MetricAxis) -> f64 {
    match axis {
        MetricAxis::HorizontalX => sample.x_m,
        MetricAxis::VerticalY => sample.y_m,
    }
}

fn velocity_axis_value(sample: KinematicSample, axis: MetricAxis) -> Option<f64> {
    match axis {
        MetricAxis::HorizontalX => sample.vx_mps,
        MetricAxis::VerticalY => sample.vy_mps,
    }
}

fn minimum_confidence(samples: &[MetricPositionSample]) -> f32 {
    samples
        .iter()
        .map(|sample| sample.confidence)
        .fold(1.0_f32, f32::min)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_utils::sample_metric_position;

    fn config(max_gap_s: f64, min_confidence: f32) -> KinematicsConfig {
        KinematicsConfig::try_new(max_gap_s, min_confidence).unwrap()
    }

    #[test]
    fn rejects_invalid_kinematics_configuration() {
        assert!(matches!(
            KinematicsConfig::try_new(0.0, 0.5),
            Err(KinematicsError::InvalidMaximumGap { .. })
        ));
        assert!(matches!(
            KinematicsConfig::try_new(f64::NAN, 0.5),
            Err(KinematicsError::InvalidMaximumGap { .. })
        ));
        assert!(matches!(
            KinematicsConfig::try_new(0.1, 1.1),
            Err(KinematicsError::InvalidMinimumConfidence { .. })
        ));
        assert!(matches!(
            KinematicsConfig::try_new(0.1, f32::NAN),
            Err(KinematicsError::InvalidMinimumConfidence { .. })
        ));
    }

    #[test]
    fn aggregate_metrics_are_unavailable_when_interval_contains_low_confidence_sample() {
        let samples = [
            sample_metric_position(0.0, 0.0, 0.0, 0.9),
            sample_metric_position(0.1, 0.2, 0.4, 0.4),
            sample_metric_position(0.2, 0.4, 0.8, 0.9),
        ];
        let cfg = config(0.2, 0.5);
        let interval = MetricInterval::try_new(0.0, 0.2).unwrap();

        assert_eq!(
            range_of_motion(&samples, MetricAxis::VerticalY, cfg).unwrap(),
            None
        );
        assert_eq!(
            mean_axis_velocity(&samples, MetricAxis::VerticalY, interval, cfg).unwrap(),
            None
        );
        assert_eq!(
            peak_axis_velocity(&samples, MetricAxis::VerticalY, interval, cfg).unwrap(),
            None
        );
    }

    #[test]
    fn constant_position_has_zero_velocity() {
        let samples = [
            sample_metric_position(0.0, 2.0, -1.0, 1.0),
            sample_metric_position(0.25, 2.0, -1.0, 0.9),
            sample_metric_position(0.75, 2.0, -1.0, 0.8),
        ];

        let result = derive_velocity(&samples, config(1.0, 0.0)).unwrap();

        assert_eq!(result[0].vx_mps, None);
        assert_eq!(result[1].vx_mps, Some(0.0));
        assert_eq!(result[1].vy_mps, Some(0.0));
        assert_eq!(result[2].vx_mps, Some(0.0));
        assert_eq!(result[2].vy_mps, Some(0.0));
    }

    #[test]
    fn linear_motion_is_exact_with_irregular_timestamps() {
        let samples = [
            sample_metric_position(0.0, 1.0, -2.0, 1.0),
            sample_metric_position(0.2, 1.6, -1.2, 0.9),
            sample_metric_position(0.7, 3.1, 0.8, 0.8),
        ];

        let result = derive_velocity(&samples, config(1.0, 0.0)).unwrap();

        for sample in &result[1..] {
            assert!((sample.vx_mps.unwrap() - 3.0).abs() < 1.0e-12);
            assert!((sample.vy_mps.unwrap() - 4.0).abs() < 1.0e-12);
        }
        assert!((result[2].confidence - 0.8).abs() < f32::EPSILON);
    }

    #[test]
    fn quadratic_motion_documents_backward_difference_semantics() {
        let samples = [
            sample_metric_position(0.0, 0.0, 0.0, 1.0),
            sample_metric_position(1.0, 1.0, 0.0, 1.0),
            sample_metric_position(3.0, 9.0, 0.0, 1.0),
        ];

        let result = derive_velocity(&samples, config(3.0, 0.0)).unwrap();

        assert_eq!(result[1].vx_mps, Some(1.0));
        assert_eq!(result[2].vx_mps, Some(4.0));
    }

    #[test]
    fn rejects_non_increasing_time() {
        let samples = [
            sample_metric_position(1.0, 0.0, 0.0, 1.0),
            sample_metric_position(1.0, 1.0, 1.0, 1.0),
        ];

        assert_eq!(
            derive_velocity(&samples, config(1.0, 0.0)),
            Err(KinematicsError::NonIncreasingTimestamp {
                previous_index: 0,
                index: 1,
            })
        );
    }

    #[test]
    fn long_gap_produces_missing_velocity_instead_of_cross_gap_derivative() {
        let samples = [
            sample_metric_position(0.0, 0.0, 0.0, 1.0),
            sample_metric_position(0.02, 0.02, 0.04, 1.0),
            sample_metric_position(0.50, 0.50, 1.00, 1.0),
            sample_metric_position(0.52, 0.52, 1.04, 1.0),
        ];

        let result = derive_velocity(&samples, config(0.05, 0.0)).unwrap();

        assert_eq!(result[1].vx_mps, Some(1.0));
        assert_eq!(result[2].vx_mps, None);
        assert_eq!(result[2].vy_mps, None);
        assert_eq!(result[3].vx_mps, Some(1.0));
    }

    #[test]
    fn low_confidence_endpoint_suppresses_derivative_and_preserves_quality() {
        let samples = [
            sample_metric_position(0.0, 0.0, 0.0, 0.9),
            sample_metric_position(0.1, 0.1, 0.2, 0.4),
            sample_metric_position(0.2, 0.2, 0.4, 0.9),
        ];

        let result = derive_velocity(&samples, config(0.2, 0.5)).unwrap();

        assert_eq!(result[1].vx_mps, None);
        assert_eq!(result[2].vx_mps, None);
        assert!((result[1].confidence - 0.4).abs() < f32::EPSILON);
        assert!((result[2].confidence - 0.4).abs() < f32::EPSILON);
    }

    #[test]
    fn short_sequence_has_no_velocity() {
        let samples = [sample_metric_position(0.0, 1.0, 2.0, 0.8)];

        let result = derive_velocity(&samples, config(0.1, 0.5)).unwrap();

        assert_eq!(result.len(), 1);
        assert_eq!(result[0].vx_mps, None);
        assert_eq!(result[0].vy_mps, None);
    }

    #[test]
    fn returns_empty_vec_for_empty_samples() {
        let result = derive_velocity(&[], config(0.1, 0.5)).unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn known_range_of_motion_and_displacement_preserve_minimum_confidence() {
        let samples = [
            sample_metric_position(0.0, -0.1, 0.2, 0.9),
            sample_metric_position(0.1, 0.3, 0.9, 0.8),
            sample_metric_position(0.2, 0.2, 0.5, 0.7),
        ];
        let cfg = config(0.2, 0.5);

        let horizontal_rom = range_of_motion(&samples, MetricAxis::HorizontalX, cfg)
            .unwrap()
            .unwrap();
        assert!((horizontal_rom.value - 0.4).abs() < 1.0e-12);
        assert!((horizontal_rom.confidence - 0.7).abs() < f32::EPSILON);

        let vertical_rom = range_of_motion(&samples, MetricAxis::VerticalY, cfg)
            .unwrap()
            .unwrap();
        assert!((vertical_rom.value - 0.7).abs() < 1.0e-12);
        assert!((vertical_rom.confidence - 0.7).abs() < f32::EPSILON);

        let vertical_displacement = axis_displacement(&samples, MetricAxis::VerticalY, cfg)
            .unwrap()
            .unwrap();
        assert!((vertical_displacement.value - 0.3).abs() < 1.0e-12);
        assert!((vertical_displacement.confidence - 0.7).abs() < f32::EPSILON);
    }

    #[test]
    fn range_of_motion_requires_at_least_two_supported_samples() {
        let samples = [sample_metric_position(0.0, 0.2, 0.4, 1.0)];

        assert_eq!(
            range_of_motion(&samples, MetricAxis::VerticalY, config(0.1, 0.0)).unwrap(),
            None
        );
    }

    #[test]
    fn range_of_motion_is_unavailable_across_unsupported_gap() {
        let samples = [
            sample_metric_position(0.0, 0.0, 0.0, 1.0),
            sample_metric_position(1.0, 1.0, 1.0, 1.0),
        ];

        assert_eq!(
            range_of_motion(&samples, MetricAxis::VerticalY, config(0.1, 0.0)).unwrap(),
            None
        );
    }

    #[test]
    fn mean_velocity_uses_explicit_exact_interval_and_actual_duration() {
        let samples = [
            sample_metric_position(0.0, 0.0, 0.0, 0.9),
            sample_metric_position(0.2, 0.4, 0.2, 0.8),
            sample_metric_position(0.7, 1.4, 0.7, 0.7),
        ];
        let interval = MetricInterval::try_new(0.0, 0.7).unwrap();

        let mean = mean_axis_velocity(
            &samples,
            MetricAxis::HorizontalX,
            interval,
            config(0.6, 0.5),
        )
        .unwrap()
        .unwrap();

        assert!((mean.value - 2.0).abs() < 1.0e-12);
        assert!((mean.confidence - 0.7).abs() < f32::EPSILON);
    }

    #[test]
    fn mean_velocity_is_unavailable_when_interval_contains_unsupported_span() {
        let samples = [
            sample_metric_position(0.0, 0.0, 0.0, 1.0),
            sample_metric_position(0.1, 0.1, 0.0, 1.0),
            sample_metric_position(1.0, 1.0, 0.0, 1.0),
        ];
        let interval = MetricInterval::try_new(0.0, 1.0).unwrap();

        assert_eq!(
            mean_axis_velocity(
                &samples,
                MetricAxis::HorizontalX,
                interval,
                config(0.2, 0.0)
            )
            .unwrap(),
            None
        );
    }

    #[test]
    fn peak_velocity_excludes_derivative_crossing_interval_start_and_allows_end_boundary() {
        let samples = [
            sample_metric_position(0.0, 0.0, 0.0, 1.0),
            sample_metric_position(1.0, 10.0, 0.0, 1.0),
            sample_metric_position(2.0, 11.0, 0.0, 0.9),
            sample_metric_position(3.0, 15.0, 0.0, 0.8),
        ];
        let interval = MetricInterval::try_new(1.0, 3.0).unwrap();

        let peak = peak_axis_velocity(
            &samples,
            MetricAxis::HorizontalX,
            interval,
            config(1.1, 0.0),
        )
        .unwrap()
        .unwrap();

        assert_eq!(peak.value, 4.0);
        assert_eq!(peak.timestamp_s, 3.0);
        assert!((peak.confidence - 0.8).abs() < f32::EPSILON);
    }

    #[test]
    fn peak_velocity_can_select_an_interior_segment() {
        let samples = [
            sample_metric_position(0.0, 0.0, 0.0, 1.0),
            sample_metric_position(1.0, 1.0, 0.0, 1.0),
            sample_metric_position(2.0, 6.0, 0.0, 0.9),
            sample_metric_position(3.0, 8.0, 0.0, 0.8),
        ];
        let interval = MetricInterval::try_new(0.0, 3.0).unwrap();

        let peak = peak_axis_velocity(
            &samples,
            MetricAxis::HorizontalX,
            interval,
            config(1.1, 0.0),
        )
        .unwrap()
        .unwrap();

        assert_eq!(peak.value, 5.0);
        assert_eq!(peak.timestamp_s, 2.0);
        assert!((peak.confidence - 0.8).abs() < f32::EPSILON);
    }

    #[test]
    fn exact_interval_boundaries_are_required() {
        let samples = [
            sample_metric_position(0.0, 0.0, 0.0, 1.0),
            sample_metric_position(0.1, 0.1, 0.0, 1.0),
        ];
        let interval = MetricInterval::try_new(0.0, 0.05).unwrap();

        assert_eq!(
            mean_axis_velocity(
                &samples,
                MetricAxis::HorizontalX,
                interval,
                config(0.2, 0.0)
            ),
            Err(KinematicsError::IntervalBoundaryNotSampled { timestamp_s: 0.05 })
        );
    }

    #[test]
    fn canonical_trajectory_retains_method_provenance() {
        let samples = [
            sample_metric_position(0.0, 0.0, 0.0, 1.0),
            sample_metric_position(0.1, 0.1, 0.2, 0.9),
        ];
        let cfg = config(0.2, 0.5);

        let trajectory =
            derive_kinematic_trajectory(&samples, KinematicsInput::Filtered, cfg).unwrap();

        assert_eq!(trajectory.input, KinematicsInput::Filtered);
        assert_eq!(trajectory.method, cfg.velocity_provenance());
        assert_eq!(trajectory.samples.len(), samples.len());
    }

    #[test]
    fn velocity_provenance_contains_method_version_and_quality_parameters() {
        let provenance = config(0.05, 0.4).velocity_provenance();

        assert_eq!(provenance.implementation, VELOCITY_METHOD_IMPLEMENTATION);
        assert_eq!(provenance.version, VELOCITY_METHOD_VERSION);
        assert_eq!(
            provenance.parameters.get("max_gap_s"),
            Some(&ParameterValue::Float(0.05))
        );
        assert_eq!(
            provenance.parameters.get("min_confidence"),
            Some(&ParameterValue::Float(0.4))
        );
    }

    #[test]
    fn peak_velocity_ignores_samples_outside_the_interval() {
        // The segment before the interval overflows to a non-finite derivative. It lies outside
        // the requested interval, so it must neither win the peak nor fail the metric.
        let samples = [
            sample_metric_position(0.0, -1.0e308, 0.0, 1.0),
            sample_metric_position(0.5, 1.0e308, 0.0, 1.0),
            sample_metric_position(1.0, 0.0, 0.0, 1.0),
            sample_metric_position(1.5, 1.0, 0.0, 0.9),
            sample_metric_position(2.0, 1.5, 0.0, 0.8),
        ];
        let interval = MetricInterval::try_new(1.0, 2.0).unwrap();
        let cfg = config(1.0, 0.0);

        assert_eq!(
            derive_velocity(&samples, cfg),
            Err(KinematicsError::NonFiniteDerivedValue { index: 1 })
        );
        let peak = peak_axis_velocity(&samples, MetricAxis::HorizontalX, interval, cfg)
            .unwrap()
            .unwrap();

        assert_eq!(
            peak,
            TimedMetricEstimate {
                value: 2.0,
                timestamp_s: 1.5,
                confidence: 0.8,
            }
        );
    }

    #[test]
    fn peak_velocity_reports_non_finite_index_in_caller_series() {
        let samples = [
            sample_metric_position(0.0, 0.0, 0.0, 1.0),
            sample_metric_position(0.5, -1.0e308, 0.0, 1.0),
            sample_metric_position(1.0, 1.0e308, 0.0, 1.0),
        ];
        let interval = MetricInterval::try_new(0.5, 1.0).unwrap();

        assert_eq!(
            peak_axis_velocity(
                &samples,
                MetricAxis::HorizontalX,
                interval,
                config(1.0, 0.0)
            ),
            Err(KinematicsError::NonFiniteDerivedValue { index: 2 })
        );
    }

    #[test]
    fn displacement_and_range_of_motion_are_unavailable_below_confidence_threshold() {
        let samples = [
            sample_metric_position(0.0, 0.0, 0.0, 0.4),
            sample_metric_position(0.1, 0.1, 0.2, 0.9),
            sample_metric_position(0.2, 0.2, 0.4, 0.9),
        ];
        let cfg = config(0.2, 0.5);

        assert_eq!(
            axis_displacement(&samples, MetricAxis::VerticalY, cfg).unwrap(),
            None
        );
        assert_eq!(
            range_of_motion(&samples, MetricAxis::VerticalY, cfg).unwrap(),
            None
        );
    }

    #[test]
    fn invalid_input_sample_reports_typed_validation_error() {
        let samples = [sample_metric_position(0.0, f64::NAN, 0.0, 1.0)];

        assert!(matches!(
            derive_velocity(&samples, config(0.1, 0.0)),
            Err(KinematicsError::InvalidSample {
                index: 0,
                error: TrajectoryValidationError::NonFiniteValue { field: "x_m", .. },
            })
        ));
    }

    #[test]
    fn velocity_provenance_round_trips_to_the_exact_config() {
        for min_confidence in [0.0, 0.1, 0.4, 1.0 / 3.0, 0.95, 7.038_531e-26, 1.0] {
            let cfg = config(0.0333, min_confidence);

            assert_eq!(
                KinematicsConfig::from_velocity_provenance(&cfg.velocity_provenance()),
                Ok(cfg)
            );
        }
    }

    #[test]
    fn velocity_provenance_accepts_integer_parameters() {
        let mut provenance = config(1.0, 1.0).velocity_provenance();
        provenance
            .parameters
            .insert("max_gap_s".to_owned(), ParameterValue::Integer(1));
        provenance
            .parameters
            .insert("min_confidence".to_owned(), ParameterValue::Integer(1));

        assert_eq!(
            KinematicsConfig::from_velocity_provenance(&provenance),
            Ok(config(1.0, 1.0))
        );
    }

    #[test]
    fn velocity_provenance_rejects_invalid_or_non_numeric_parameters() {
        let mut out_of_range = config(0.1, 0.5).velocity_provenance();
        out_of_range
            .parameters
            .insert("min_confidence".to_owned(), ParameterValue::Float(1.5));
        assert!(matches!(
            KinematicsConfig::from_velocity_provenance(&out_of_range),
            Err(KinematicsError::InvalidMinimumConfidence { .. })
        ));

        let mut text = config(0.1, 0.5).velocity_provenance();
        text.parameters.insert(
            "max_gap_s".to_owned(),
            ParameterValue::Text("0.1".to_owned()),
        );
        assert_eq!(
            KinematicsConfig::from_velocity_provenance(&text),
            Err(KinematicsError::InvalidMethodParameter {
                name: "max_gap_s".to_owned(),
            })
        );
    }

    #[test]
    fn verification_accepts_derived_trajectory_and_rejects_length_mismatch() {
        let samples = [
            sample_metric_position(0.0, 0.0, 0.0, 1.0),
            sample_metric_position(0.1, 0.1, 0.2, 0.9),
            sample_metric_position(0.3, 0.2, 0.4, 0.8),
        ];
        let mut trajectory =
            derive_kinematic_trajectory(&samples, KinematicsInput::Calibrated, config(0.15, 0.0))
                .unwrap();
        assert_eq!(trajectory.samples[2].vx_mps, None);
        assert_eq!(verify_kinematic_trajectory(&samples, &trajectory), Ok(()));

        trajectory.samples.pop();
        assert_eq!(
            verify_kinematic_trajectory(&samples, &trajectory),
            Err(KinematicsError::TrajectoryMismatch { index: 2 })
        );
    }

    #[test]
    fn verification_survives_exact_json_float_round_trip() {
        // Real tracker output has full-precision positions and 1/fps timestamps. JSON parsing may
        // move each input by an ulp, which the 1/dt of a derivative amplifies; a persisted
        // trajectory must still verify after it is written and read back.
        for fps in [30.0, 60.0, 240.0] {
            let samples: Vec<_> = (0..300)
                .map(|index| {
                    let phase = f64::from(index) * 0.37;
                    sample_metric_position(
                        f64::from(index) / fps,
                        0.123_456_789_012_345 + 0.731 * phase.sin(),
                        -0.987_654_321_098_765 + 1.137 * (phase * 0.5).cos(),
                        0.9,
                    )
                })
                .collect();
            let trajectory = derive_kinematic_trajectory(
                &samples,
                KinematicsInput::Calibrated,
                config(1.0, 0.0),
            )
            .unwrap();

            let input: Vec<MetricPositionSample> =
                serde_json::from_str(&serde_json::to_string(&samples).unwrap()).unwrap();
            let persisted: KinematicTrajectory =
                serde_json::from_str(&serde_json::to_string(&trajectory).unwrap()).unwrap();

            assert_eq!(input, samples);
            assert_eq!(persisted, trajectory);
            assert_eq!(verify_kinematic_trajectory(&input, &persisted), Ok(()));
        }
    }
    #[test]
    fn verification_rejects_forged_velocity_for_pathological_timestamp_spacing() {
        // A tolerance scaled by 1/dt can become enormous for a tiny but valid interval and
        // accidentally accept arbitrary finite velocity. Canonical verification must stay narrow.
        let samples = [
            sample_metric_position(0.0, 1.0, 0.0, 1.0),
            sample_metric_position(1.0e-300, 1.0, 0.0, 1.0),
        ];
        let mut trajectory =
            derive_kinematic_trajectory(&samples, KinematicsInput::Calibrated, config(1.0, 0.0))
                .unwrap();
        assert_eq!(trajectory.samples[1].vx_mps, Some(0.0));

        trajectory.samples[1].vx_mps = Some(1.0e200);

        assert_eq!(
            verify_kinematic_trajectory(&samples, &trajectory),
            Err(KinematicsError::TrajectoryMismatch { index: 1 })
        );
    }

}
