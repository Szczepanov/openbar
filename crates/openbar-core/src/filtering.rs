use crate::analysis::{
    Configuration, FilteredTrajectory, ImplementationProvenance, ParameterValue,
};
use crate::trajectory::MetricPositionSample;
use std::fmt;

const RAW_IMPLEMENTATION: &str = "raw-identity";
const MOVING_AVERAGE_IMPLEMENTATION: &str = "centered-moving-average";
const SAVITZKY_GOLAY_IMPLEMENTATION: &str = "timestamp-aware-savitzky-golay";
const KALMAN_IMPLEMENTATION: &str = "constant-velocity-kalman";
const FILTER_VERSION: &str = "1";
const MAX_POLYNOMIAL_ORDER: usize = 5;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum FilterConfig {
    Raw,
    MovingAverage {
        window: usize,
        max_gap_s: f64,
    },
    SavitzkyGolay {
        window: usize,
        polynomial_order: usize,
        max_gap_s: f64,
    },
    Kalman {
        process_noise: f64,
        measurement_noise: f64,
        initial_velocity_variance: f64,
        max_gap_s: f64,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FilterBehavior {
    pub causal: bool,
    pub irregular_timestamp_behavior: &'static str,
    pub gap_behavior: &'static str,
    pub edge_behavior: &'static str,
    pub latency_behavior: &'static str,
}

#[derive(Debug, Clone, PartialEq)]
pub struct FilterRun {
    pub trajectory: FilteredTrajectory,
    pub behavior: FilterBehavior,
}

#[derive(Debug, Clone, PartialEq)]
pub enum FilterError {
    InvalidSample { index: usize, reason: String },
    NonIncreasingTimestamp { previous_index: usize, index: usize },
    InvalidWindow { window: usize },
    EvenWindow { window: usize },
    InvalidPolynomialOrder { order: usize, window: usize },
    PolynomialOrderTooHigh { order: usize, max_supported: usize },
    InvalidGapThreshold { value: f64 },
    InvalidProcessNoise { value: f64 },
    InvalidMeasurementNoise { value: f64 },
    InvalidInitialVelocityVariance { value: f64 },
    SingularPolynomialFit,
}

impl fmt::Display for FilterError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidSample { index, reason } => {
                write!(formatter, "input sample {index} is invalid: {reason}")
            }
            Self::NonIncreasingTimestamp {
                previous_index,
                index,
            } => write!(
                formatter,
                "input timestamps must be strictly increasing (indices {previous_index} and {index})"
            ),
            Self::InvalidWindow { window } => {
                write!(formatter, "filter window must be at least 1, got {window}")
            }
            Self::EvenWindow { window } => {
                write!(formatter, "centered filter window must be odd, got {window}")
            }
            Self::InvalidPolynomialOrder { order, window } => write!(
                formatter,
                "polynomial order {order} must be smaller than window {window}"
            ),
            Self::PolynomialOrderTooHigh {
                order,
                max_supported,
            } => write!(
                formatter,
                "polynomial order {order} exceeds the M0 supported maximum {max_supported}"
            ),
            Self::InvalidGapThreshold { value } => write!(
                formatter,
                "max_gap_s must be finite and positive, got {value}"
            ),
            Self::InvalidProcessNoise { value } => write!(
                formatter,
                "process_noise must be finite and non-negative, got {value}"
            ),
            Self::InvalidMeasurementNoise { value } => write!(
                formatter,
                "measurement_noise must be finite and positive, got {value}"
            ),
            Self::InvalidInitialVelocityVariance { value } => write!(
                formatter,
                "initial_velocity_variance must be finite and positive, got {value}"
            ),
            Self::SingularPolynomialFit => formatter.write_str(
                "timestamp-aware polynomial fit became singular for a validated timestamp window",
            ),
        }
    }
}

impl std::error::Error for FilterError {}

impl FilterConfig {
    pub fn provenance(self) -> ImplementationProvenance {
        let mut parameters = Configuration::new();
        let (implementation, version) = match self {
            Self::Raw => (RAW_IMPLEMENTATION, FILTER_VERSION),
            Self::MovingAverage { window, max_gap_s } => {
                parameters.insert(
                    "window".to_owned(),
                    ParameterValue::Integer(i64::try_from(window).unwrap_or(i64::MAX)),
                );
                parameters.insert("max_gap_s".to_owned(), ParameterValue::Float(max_gap_s));
                (MOVING_AVERAGE_IMPLEMENTATION, FILTER_VERSION)
            }
            Self::SavitzkyGolay {
                window,
                polynomial_order,
                max_gap_s,
            } => {
                parameters.insert(
                    "window".to_owned(),
                    ParameterValue::Integer(i64::try_from(window).unwrap_or(i64::MAX)),
                );
                parameters.insert(
                    "polynomial_order".to_owned(),
                    ParameterValue::Integer(i64::try_from(polynomial_order).unwrap_or(i64::MAX)),
                );
                parameters.insert("max_gap_s".to_owned(), ParameterValue::Float(max_gap_s));
                (SAVITZKY_GOLAY_IMPLEMENTATION, FILTER_VERSION)
            }
            Self::Kalman {
                process_noise,
                measurement_noise,
                initial_velocity_variance,
                max_gap_s,
            } => {
                parameters.insert(
                    "process_noise".to_owned(),
                    ParameterValue::Float(process_noise),
                );
                parameters.insert(
                    "measurement_noise".to_owned(),
                    ParameterValue::Float(measurement_noise),
                );
                parameters.insert(
                    "initial_velocity_variance".to_owned(),
                    ParameterValue::Float(initial_velocity_variance),
                );
                parameters.insert("max_gap_s".to_owned(), ParameterValue::Float(max_gap_s));
                (KALMAN_IMPLEMENTATION, FILTER_VERSION)
            }
        };

        ImplementationProvenance {
            implementation: implementation.to_owned(),
            version: version.to_owned(),
            parameters,
        }
    }

    pub const fn behavior(self) -> FilterBehavior {
        match self {
            Self::Raw => FilterBehavior {
                causal: true,
                irregular_timestamp_behavior:
                    "identity; authoritative input timestamps are preserved exactly",
                gap_behavior:
                    "identity; missing spans remain absent and are never interpolated",
                edge_behavior: "identity at every sample including clip boundaries",
                latency_behavior: "zero algorithmic delay and zero timestamp shift",
            },
            Self::MovingAverage { .. } => FilterBehavior {
                causal: false,
                irregular_timestamp_behavior:
                    "uses timestamp-ordered neighboring samples with equal sample weights; does not assume nominal FPS",
                gap_behavior:
                    "windows never cross a timestamp gap larger than max_gap_s; no samples are synthesized",
                edge_behavior:
                    "centered window is truncated at segment boundaries; no padding or extrapolation",
                latency_behavior:
                    "zero output timestamp shift but non-causal because future samples contribute",
            },
            Self::SavitzkyGolay { .. } => FilterBehavior {
                causal: false,
                irregular_timestamp_behavior:
                    "fits local polynomials against actual timestamp offsets; no uniform resampling is performed",
                gap_behavior:
                    "fits never cross a timestamp gap larger than max_gap_s; no samples are synthesized",
                edge_behavior:
                    "window shifts asymmetrically at segment boundaries; segments shorter than order+1 pass through unchanged",
                latency_behavior:
                    "zero output timestamp shift but non-causal because future samples contribute",
            },
            Self::Kalman { .. } => FilterBehavior {
                causal: true,
                irregular_timestamp_behavior:
                    "state transition and process covariance use each measured timestamp delta",
                gap_behavior:
                    "state resets on a timestamp gap larger than max_gap_s; no prediction is emitted for missing timestamps",
                edge_behavior:
                    "first sample initializes position state and is emitted unchanged",
                latency_behavior:
                    "zero-frame algorithmic delay; causal state estimation can introduce dynamic phase lag",
            },
        }
    }

    pub const fn family(self) -> &'static str {
        match self {
            Self::Raw => "raw",
            Self::MovingAverage { .. } => "moving_average",
            Self::SavitzkyGolay { .. } => "savitzky_golay",
            Self::Kalman { .. } => "kalman",
        }
    }

    fn validate(self) -> Result<(), FilterError> {
        match self {
            Self::Raw => Ok(()),
            Self::MovingAverage { window, max_gap_s } => {
                validate_centered_window(window)?;
                validate_gap(max_gap_s)
            }
            Self::SavitzkyGolay {
                window,
                polynomial_order,
                max_gap_s,
            } => {
                validate_centered_window(window)?;
                if polynomial_order >= window {
                    return Err(FilterError::InvalidPolynomialOrder {
                        order: polynomial_order,
                        window,
                    });
                }
                if polynomial_order > MAX_POLYNOMIAL_ORDER {
                    return Err(FilterError::PolynomialOrderTooHigh {
                        order: polynomial_order,
                        max_supported: MAX_POLYNOMIAL_ORDER,
                    });
                }
                validate_gap(max_gap_s)
            }
            Self::Kalman {
                process_noise,
                measurement_noise,
                initial_velocity_variance,
                max_gap_s,
            } => {
                if !process_noise.is_finite() || process_noise < 0.0 {
                    return Err(FilterError::InvalidProcessNoise {
                        value: process_noise,
                    });
                }
                if !measurement_noise.is_finite() || measurement_noise <= 0.0 {
                    return Err(FilterError::InvalidMeasurementNoise {
                        value: measurement_noise,
                    });
                }
                if !initial_velocity_variance.is_finite() || initial_velocity_variance <= 0.0 {
                    return Err(FilterError::InvalidInitialVelocityVariance {
                        value: initial_velocity_variance,
                    });
                }
                validate_gap(max_gap_s)
            }
        }
    }
}

pub fn apply_filter(
    samples: &[MetricPositionSample],
    config: FilterConfig,
) -> Result<FilterRun, FilterError> {
    validate_input(samples)?;
    config.validate()?;

    let filtered = match config {
        FilterConfig::Raw => samples.to_vec(),
        FilterConfig::MovingAverage { window, max_gap_s } => {
            moving_average_segmented(samples, window, max_gap_s)
        }
        FilterConfig::SavitzkyGolay {
            window,
            polynomial_order,
            max_gap_s,
        } => savitzky_golay_segmented(samples, window, polynomial_order, max_gap_s)?,
        FilterConfig::Kalman {
            process_noise,
            measurement_noise,
            initial_velocity_variance,
            max_gap_s,
        } => kalman_segmented(
            samples,
            process_noise,
            measurement_noise,
            initial_velocity_variance,
            max_gap_s,
        ),
    };

    Ok(FilterRun {
        trajectory: FilteredTrajectory {
            filter: config.provenance(),
            samples: filtered,
        },
        behavior: config.behavior(),
    })
}

/// Compatibility wrapper for the original M0 moving-average baseline.
///
/// New benchmark and production-facing code should prefer apply_filter so implementation identity,
/// gap semantics, edge behavior, and effective parameters are always explicit.
pub fn moving_average(
    samples: &[MetricPositionSample],
    window: usize,
) -> Result<Vec<MetricPositionSample>, FilterError> {
    validate_input(samples)?;
    validate_centered_window(window)?;
    if samples.is_empty() {
        return Ok(Vec::new());
    }

    Ok(moving_average_range(samples, window))
}

fn validate_input(samples: &[MetricPositionSample]) -> Result<(), FilterError> {
    let mut previous_timestamp = None;
    for (index, sample) in samples.iter().copied().enumerate() {
        sample
            .validate()
            .map_err(|error| FilterError::InvalidSample {
                index,
                reason: error.to_string(),
            })?;
        if let Some(previous) = previous_timestamp {
            if sample.timestamp_s <= previous {
                return Err(FilterError::NonIncreasingTimestamp {
                    previous_index: index - 1,
                    index,
                });
            }
        }
        previous_timestamp = Some(sample.timestamp_s);
    }
    Ok(())
}

fn validate_centered_window(window: usize) -> Result<(), FilterError> {
    if window == 0 {
        return Err(FilterError::InvalidWindow { window });
    }
    if window % 2 == 0 {
        return Err(FilterError::EvenWindow { window });
    }
    Ok(())
}

fn validate_gap(max_gap_s: f64) -> Result<(), FilterError> {
    if !max_gap_s.is_finite() || max_gap_s <= 0.0 {
        return Err(FilterError::InvalidGapThreshold { value: max_gap_s });
    }
    Ok(())
}

fn segment_ranges(samples: &[MetricPositionSample], max_gap_s: f64) -> Vec<(usize, usize)> {
    if samples.is_empty() {
        return Vec::new();
    }

    let mut ranges = Vec::new();
    let mut start = 0usize;
    for index in 1..samples.len() {
        if samples[index].timestamp_s - samples[index - 1].timestamp_s > max_gap_s {
            ranges.push((start, index));
            start = index;
        }
    }
    ranges.push((start, samples.len()));
    ranges
}

fn moving_average_segmented(
    samples: &[MetricPositionSample],
    window: usize,
    max_gap_s: f64,
) -> Vec<MetricPositionSample> {
    let mut output = Vec::with_capacity(samples.len());
    for (start, end) in segment_ranges(samples, max_gap_s) {
        output.extend(moving_average_range(&samples[start..end], window));
    }
    output
}

fn moving_average_range(
    samples: &[MetricPositionSample],
    window: usize,
) -> Vec<MetricPositionSample> {
    if samples.is_empty() {
        return Vec::new();
    }

    let radius = window / 2;
    let mut output = Vec::with_capacity(samples.len());
    for (index, sample) in samples.iter().enumerate() {
        let start = index.saturating_sub(radius);
        let end = (index + radius + 1).min(samples.len());
        let slice = &samples[start..end];
        let count = slice.len() as f64;
        let x_m = slice.iter().map(|value| value.x_m).sum::<f64>() / count;
        let y_m = slice.iter().map(|value| value.y_m).sum::<f64>() / count;
        let confidence = slice
            .iter()
            .map(|value| value.confidence)
            .fold(1.0_f32, f32::min);

        output.push(MetricPositionSample {
            timestamp_s: sample.timestamp_s,
            x_m,
            y_m,
            confidence,
        });
    }
    output
}

fn savitzky_golay_segmented(
    samples: &[MetricPositionSample],
    window: usize,
    polynomial_order: usize,
    max_gap_s: f64,
) -> Result<Vec<MetricPositionSample>, FilterError> {
    let mut output = Vec::with_capacity(samples.len());
    for (start, end) in segment_ranges(samples, max_gap_s) {
        output.extend(savitzky_golay_range(
            &samples[start..end],
            window,
            polynomial_order,
        )?);
    }
    Ok(output)
}

fn savitzky_golay_range(
    samples: &[MetricPositionSample],
    window: usize,
    polynomial_order: usize,
) -> Result<Vec<MetricPositionSample>, FilterError> {
    if samples.len() < polynomial_order + 1 {
        return Ok(samples.to_vec());
    }

    let mut output = Vec::with_capacity(samples.len());
    for index in 0..samples.len() {
        let (start, end) = local_window_bounds(samples.len(), index, window);
        let slice = &samples[start..end];
        if slice.len() < polynomial_order + 1 {
            output.push(samples[index]);
            continue;
        }

        let target_timestamp = samples[index].timestamp_s;
        let x_m = local_polynomial_value(slice, polynomial_order, target_timestamp, |sample| {
            sample.x_m
        })?;
        let y_m = local_polynomial_value(slice, polynomial_order, target_timestamp, |sample| {
            sample.y_m
        })?;
        let confidence = slice
            .iter()
            .map(|sample| sample.confidence)
            .fold(1.0_f32, f32::min);

        output.push(MetricPositionSample {
            timestamp_s: target_timestamp,
            x_m,
            y_m,
            confidence,
        });
    }
    Ok(output)
}

fn local_window_bounds(length: usize, index: usize, window: usize) -> (usize, usize) {
    if length <= window {
        return (0, length);
    }

    let radius = window / 2;
    let mut start = index.saturating_sub(radius);
    if start + window > length {
        start = length - window;
    }
    (start, start + window)
}

fn local_polynomial_value(
    samples: &[MetricPositionSample],
    order: usize,
    target_timestamp: f64,
    value: impl Fn(&MetricPositionSample) -> f64,
) -> Result<f64, FilterError> {
    let dimension = order + 1;
    let mut normal = vec![vec![0.0_f64; dimension]; dimension];
    let mut rhs = vec![0.0_f64; dimension];

    for sample in samples {
        let dt = sample.timestamp_s - target_timestamp;
        let mut powers = vec![1.0_f64; dimension];
        for power in 1..dimension {
            powers[power] = powers[power - 1] * dt;
        }
        for row in 0..dimension {
            rhs[row] += powers[row] * value(sample);
            for column in 0..dimension {
                normal[row][column] += powers[row] * powers[column];
            }
        }
    }

    let coefficients = solve_linear_system(normal, rhs)?;
    Ok(coefficients[0])
}

fn solve_linear_system(
    mut matrix: Vec<Vec<f64>>,
    mut rhs: Vec<f64>,
) -> Result<Vec<f64>, FilterError> {
    let dimension = rhs.len();
    for pivot in 0..dimension {
        let mut best_row = pivot;
        let mut best_value = matrix[pivot][pivot].abs();
        for (row, values) in matrix.iter().enumerate().skip(pivot + 1) {
            let candidate = values[pivot].abs();
            if candidate > best_value {
                best_value = candidate;
                best_row = row;
            }
        }
        if best_value <= 1.0e-14 {
            return Err(FilterError::SingularPolynomialFit);
        }
        if best_row != pivot {
            matrix.swap(pivot, best_row);
            rhs.swap(pivot, best_row);
        }

        let pivot_value = matrix[pivot][pivot];
        for column in pivot..dimension {
            matrix[pivot][column] /= pivot_value;
        }
        rhs[pivot] /= pivot_value;

        for row in 0..dimension {
            if row == pivot {
                continue;
            }
            let factor = matrix[row][pivot];
            if factor == 0.0 {
                continue;
            }
            for column in pivot..dimension {
                matrix[row][column] -= factor * matrix[pivot][column];
            }
            rhs[row] -= factor * rhs[pivot];
        }
    }
    Ok(rhs)
}

#[derive(Debug, Clone, Copy)]
struct KalmanAxis {
    position: f64,
    velocity: f64,
    p00: f64,
    p01: f64,
    p11: f64,
}

impl KalmanAxis {
    fn new(position: f64, measurement_noise: f64, initial_velocity_variance: f64) -> Self {
        Self {
            position,
            velocity: 0.0,
            p00: measurement_noise,
            p01: 0.0,
            p11: initial_velocity_variance,
        }
    }

    fn update(&mut self, measurement: f64, dt: f64, process_noise: f64, measurement_noise: f64) {
        self.position += self.velocity * dt;

        let dt2 = dt * dt;
        let dt3 = dt2 * dt;
        let dt4 = dt2 * dt2;
        let predicted_p00 =
            self.p00 + 2.0 * dt * self.p01 + dt2 * self.p11 + process_noise * dt4 / 4.0;
        let predicted_p01 = self.p01 + dt * self.p11 + process_noise * dt3 / 2.0;
        let predicted_p11 = self.p11 + process_noise * dt2;

        let innovation = measurement - self.position;
        let innovation_variance = predicted_p00 + measurement_noise;
        let k0 = predicted_p00 / innovation_variance;
        let k1 = predicted_p01 / innovation_variance;

        self.position += k0 * innovation;
        self.velocity += k1 * innovation;
        self.p00 = (1.0 - k0) * predicted_p00;
        self.p01 = (1.0 - k0) * predicted_p01;
        self.p11 = predicted_p11 - k1 * predicted_p01;
    }
}

fn kalman_segmented(
    samples: &[MetricPositionSample],
    process_noise: f64,
    measurement_noise: f64,
    initial_velocity_variance: f64,
    max_gap_s: f64,
) -> Vec<MetricPositionSample> {
    if samples.is_empty() {
        return Vec::new();
    }

    let mut output = Vec::with_capacity(samples.len());
    let mut x_state = KalmanAxis::new(samples[0].x_m, measurement_noise, initial_velocity_variance);
    let mut y_state = KalmanAxis::new(samples[0].y_m, measurement_noise, initial_velocity_variance);
    let mut state_confidence = samples[0].confidence;
    output.push(samples[0]);

    for index in 1..samples.len() {
        let sample = samples[index];
        let dt = sample.timestamp_s - samples[index - 1].timestamp_s;
        if dt > max_gap_s {
            x_state = KalmanAxis::new(sample.x_m, measurement_noise, initial_velocity_variance);
            y_state = KalmanAxis::new(sample.y_m, measurement_noise, initial_velocity_variance);
            state_confidence = sample.confidence;
            output.push(sample);
            continue;
        }

        x_state.update(sample.x_m, dt, process_noise, measurement_noise);
        y_state.update(sample.y_m, dt, process_noise, measurement_noise);
        state_confidence = state_confidence.min(sample.confidence);
        output.push(MetricPositionSample {
            timestamp_s: sample.timestamp_s,
            x_m: x_state.position,
            y_m: y_state.position,
            confidence: state_confidence,
        });
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(timestamp_s: f64, x_m: f64, y_m: f64) -> MetricPositionSample {
        MetricPositionSample {
            timestamp_s,
            x_m,
            y_m,
            confidence: 1.0,
        }
    }

    #[test]
    fn raw_identity_preserves_input_exactly_without_mutating_it() {
        let input = vec![sample(0.0, 1.0, 2.0), sample(0.02, 1.1, 2.2)];
        let before = input.clone();
        let run = apply_filter(&input, FilterConfig::Raw).unwrap();

        assert_eq!(input, before);
        assert_eq!(run.trajectory.samples, input);
        assert_eq!(run.trajectory.filter.implementation, RAW_IMPLEMENTATION);
    }

    #[test]
    fn moving_average_preserves_timestamps_and_is_conservative_about_confidence() {
        let mut input = vec![
            sample(0.0, 0.0, 0.0),
            sample(0.5, 3.0, 6.0),
            sample(1.0, 6.0, 12.0),
        ];
        input[0].confidence = 0.5;

        let output = moving_average(&input, 3).unwrap();
        assert_eq!(output.len(), input.len());
        assert_eq!(output[1].timestamp_s, 0.5);
        assert_eq!(output[1].x_m, 3.0);
        assert_eq!(output[1].y_m, 6.0);
        assert_eq!(output[0].confidence, 0.5);
        assert_eq!(output[1].confidence, 0.5);
    }

    #[test]
    fn centered_filters_do_not_cross_long_timestamp_gaps() {
        let input = vec![
            sample(0.00, 0.0, 0.0),
            sample(0.02, 2.0, 2.0),
            sample(1.00, 100.0, 100.0),
            sample(1.02, 102.0, 102.0),
        ];
        let run = apply_filter(
            &input,
            FilterConfig::MovingAverage {
                window: 3,
                max_gap_s: 0.1,
            },
        )
        .unwrap();

        assert_eq!(run.trajectory.samples[1].x_m, 1.0);
        assert_eq!(run.trajectory.samples[2].x_m, 101.0);
        assert_eq!(run.trajectory.samples.len(), input.len());
    }

    #[test]
    fn savitzky_golay_uses_irregular_timestamps_and_preserves_a_quadratic() {
        let timestamps = [0.0, 0.03, 0.08, 0.14, 0.23, 0.31, 0.44];
        let input = timestamps
            .iter()
            .map(|timestamp| sample(*timestamp, timestamp * timestamp, 2.0 * timestamp + 1.0))
            .collect::<Vec<_>>();

        let run = apply_filter(
            &input,
            FilterConfig::SavitzkyGolay {
                window: 5,
                polynomial_order: 2,
                max_gap_s: 0.2,
            },
        )
        .unwrap();

        for (actual, expected) in run.trajectory.samples.iter().zip(&input) {
            assert_eq!(actual.timestamp_s, expected.timestamp_s);
            assert!((actual.x_m - expected.x_m).abs() < 1.0e-9);
            assert!((actual.y_m - expected.y_m).abs() < 1.0e-9);
        }
    }

    #[test]
    fn savitzky_golay_short_segment_passes_through_instead_of_fabricating_support() {
        let input = vec![sample(0.0, 1.0, 2.0), sample(0.02, 2.0, 3.0)];
        let run = apply_filter(
            &input,
            FilterConfig::SavitzkyGolay {
                window: 5,
                polynomial_order: 2,
                max_gap_s: 0.1,
            },
        )
        .unwrap();
        assert_eq!(run.trajectory.samples, input);
    }

    #[test]
    fn kalman_is_deterministic_timestamp_aware_and_resets_after_long_gap() {
        let input = vec![
            sample(0.0, 0.0, 0.0),
            sample(0.01, 0.2, 0.4),
            sample(0.04, 0.6, 1.2),
            sample(1.0, 10.0, 20.0),
        ];
        let config = FilterConfig::Kalman {
            process_noise: 1.0,
            measurement_noise: 0.01,
            initial_velocity_variance: 1.0,
            max_gap_s: 0.1,
        };
        let first = apply_filter(&input, config).unwrap();
        let second = apply_filter(&input, config).unwrap();

        assert_eq!(first, second);
        assert_eq!(first.trajectory.samples[3], input[3]);
    }

    #[test]
    fn effective_parameters_are_persisted_in_filter_provenance() {
        let config = FilterConfig::SavitzkyGolay {
            window: 7,
            polynomial_order: 3,
            max_gap_s: 0.05,
        };
        let provenance = config.provenance();

        assert_eq!(provenance.implementation, SAVITZKY_GOLAY_IMPLEMENTATION);
        assert_eq!(
            provenance.parameters.get("window"),
            Some(&ParameterValue::Integer(7))
        );
        assert_eq!(
            provenance.parameters.get("polynomial_order"),
            Some(&ParameterValue::Integer(3))
        );
        assert_eq!(
            provenance.parameters.get("max_gap_s"),
            Some(&ParameterValue::Float(0.05))
        );
    }

    #[test]
    fn rejects_invalid_parameters_and_non_increasing_timestamps() {
        let input = vec![sample(0.0, 0.0, 0.0), sample(0.0, 1.0, 1.0)];
        assert!(matches!(
            apply_filter(&input, FilterConfig::Raw),
            Err(FilterError::NonIncreasingTimestamp { .. })
        ));

        let valid = vec![sample(0.0, 0.0, 0.0), sample(0.02, 1.0, 1.0)];
        assert!(matches!(
            apply_filter(
                &valid,
                FilterConfig::SavitzkyGolay {
                    window: 4,
                    polynomial_order: 2,
                    max_gap_s: 0.1,
                }
            ),
            Err(FilterError::EvenWindow { .. })
        ));
        assert!(matches!(
            apply_filter(
                &valid,
                FilterConfig::Kalman {
                    process_noise: 1.0,
                    measurement_noise: 0.0,
                    initial_velocity_variance: 1.0,
                    max_gap_s: 0.1,
                }
            ),
            Err(FilterError::InvalidMeasurementNoise { .. })
        ));
    }
}
