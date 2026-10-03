use crate::analysis::{
    Configuration, FilteredTrajectory, ImplementationProvenance, ParameterValue,
};
use crate::trajectory::MetricPositionSample;
use std::collections::VecDeque;
use std::fmt;

const RAW_IMPLEMENTATION: &str = "raw-identity";
const MOVING_AVERAGE_IMPLEMENTATION: &str = "centered-moving-average";
const SAVITZKY_GOLAY_IMPLEMENTATION: &str = "timestamp-aware-savitzky-golay";
const KALMAN_IMPLEMENTATION: &str = "constant-velocity-kalman";
const FILTER_VERSION: &str = "1";
const MAX_POLYNOMIAL_ORDER: usize = 5;

/// Maximum difference between requested and resolved sample counts.
pub const DURATION_WINDOW_TOLERANCE_SAMPLES: f64 = 1.0;
const DURATION_ROUNDING_EPSILON: f64 = 1e-9;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum FilterConfig {
    Raw,
    MovingAverage {
        window: usize,
        /// Requested duration for provenance only; filtering uses `window`.
        window_s: Option<f64>,
        max_gap_s: f64,
    },
    SavitzkyGolay {
        window: usize,
        /// Requested duration for provenance only; filtering uses `window`.
        window_s: Option<f64>,
        polynomial_order: usize,
        max_gap_s: f64,
    },
    Kalman {
        acceleration_variance_m2_s4: f64,
        measurement_variance_m2: f64,
        initial_velocity_variance_m2_s2: f64,
        confidence_window_samples: usize,
        max_gap_s: f64,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FilterBehavior {
    pub causal: bool,
    pub confidence_behavior: &'static str,
    pub irregular_timestamp_behavior: &'static str,
    pub gap_behavior: &'static str,
    pub edge_behavior: &'static str,
    pub latency_behavior: &'static str,
}

#[derive(Debug, Clone, PartialEq)]
pub struct FilterRun {
    pub trajectory: FilteredTrajectory,
    pub behavior: FilterBehavior,
    pub segment_count: usize,
}

#[derive(Debug, Clone, PartialEq)]
pub enum FilterError {
    InvalidSample {
        index: usize,
        reason: String,
    },
    NonIncreasingTimestamp {
        previous_index: usize,
        index: usize,
    },
    InvalidWindow {
        window: usize,
    },
    InvalidWindowDuration {
        window_s: f64,
    },
    InvalidMeasuredFps {
        measured_fps: f64,
    },
    WindowDurationOutOfRange {
        window_s: f64,
        measured_fps: f64,
    },
    WindowDurationUnresolvable {
        window_s: f64,
        measured_fps: f64,
        resolved_window: usize,
    },
    EvenWindow {
        window: usize,
    },
    InvalidPolynomialOrder {
        order: usize,
        window: usize,
    },
    PolynomialOrderTooHigh {
        order: usize,
        max_supported: usize,
    },
    InvalidGapThreshold {
        value: f64,
    },
    InvalidAccelerationVariance {
        value: f64,
    },
    InvalidMeasurementVariance {
        value: f64,
    },
    InvalidInitialVelocityVariance {
        value: f64,
    },
    InvalidConfidenceWindow {
        value: usize,
    },
    SingularPolynomialFit,
    InvalidCutoffFrequency {
        value: f64,
        reason: &'static str,
    },
    UnsupportedTimestampJitter {
        max_rel_dev: f64,
        max_allowed: f64,
    },
    UnsupportedButterworthOrder {
        order: usize,
    },
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
            Self::InvalidWindowDuration { window_s } => write!(formatter,
                "window_s must be finite and positive, got {window_s}"),
            Self::InvalidMeasuredFps { measured_fps } => write!(formatter,
                "measured_fps must be finite and positive, got {measured_fps}"),
            Self::WindowDurationOutOfRange { window_s, measured_fps } => write!(formatter,
                "window_s {window_s} at measured_fps {measured_fps} exceeds the representable sample window"),
            Self::WindowDurationUnresolvable { window_s, measured_fps, resolved_window } => write!(formatter,
                "window_s {window_s} at measured_fps {measured_fps} resolves to {resolved_window} samples, exceeding the {DURATION_WINDOW_TOLERANCE_SAMPLES}-sample tolerance"),
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
            Self::InvalidAccelerationVariance { value } => write!(
                formatter,
                "acceleration_variance_m2_s4 must be finite and non-negative, got {value}"
            ),
            Self::InvalidMeasurementVariance { value } => write!(
                formatter,
                "measurement_variance_m2 must be finite and positive, got {value}"
            ),
            Self::InvalidInitialVelocityVariance { value } => write!(
                formatter,
                "initial_velocity_variance_m2_s2 must be finite and positive, got {value}"
            ),
            Self::InvalidConfidenceWindow { value } => write!(
                formatter,
                "confidence_window_samples must be at least 1, got {value}"
            ),
            Self::SingularPolynomialFit => formatter.write_str(
                "timestamp-aware polynomial fit became singular for a validated timestamp window",
            ),
            Self::InvalidCutoffFrequency { value, reason } => write!(
                formatter,
                "cutoff frequency {value} is invalid: {reason}"
            ),
            Self::UnsupportedTimestampJitter {
                max_rel_dev,
                max_allowed,
            } => write!(
                formatter,
                "timestamp relative jitter {max_rel_dev} exceeds the maximum allowed threshold {max_allowed}"
            ),
            Self::UnsupportedButterworthOrder { order } => write!(
                formatter,
                "butterworth design order {order} is unsupported (supported: 2, 4)"
            ),
        }
    }
}

impl std::error::Error for FilterError {}

/// Resolve a duration using the mean rate of authoritative selected-frame timestamps.
/// Nearest-odd ties round up; the minimum must itself be a valid odd window.
pub fn resolve_window_samples(
    window_s: f64,
    measured_fps: f64,
    min_window: usize,
) -> Result<usize, FilterError> {
    validate_window_duration(window_s)?;
    if !measured_fps.is_finite() || measured_fps <= 0.0 {
        return Err(FilterError::InvalidMeasuredFps { measured_fps });
    }
    validate_centered_window(min_window)?;
    let requested = window_s * measured_fps;
    let nearest = 2.0 * ((requested - 1.0) / 2.0 + 0.5 + DURATION_ROUNDING_EPSILON).floor() + 1.0;
    // Check before casting: Rust's float-to-integer cast saturates. Also ensure
    // the resolved odd count is exactly representable in f64 and provenance.
    if !nearest.is_finite()
        || nearest >= usize::MAX as f64
        || nearest >= (1_u64 << 53) as f64
        || min_window as f64 >= (1_u64 << 53) as f64
    {
        return Err(FilterError::WindowDurationOutOfRange {
            window_s,
            measured_fps,
        });
    }
    let resolved_window = (nearest as usize).max(min_window);
    // The rounding epsilon can put a value just below a tie on the upper side.
    // Allow only that same numerical cushion at the one-sample boundary.
    if (resolved_window as f64 - requested).abs()
        > DURATION_WINDOW_TOLERANCE_SAMPLES + 2.0 * DURATION_ROUNDING_EPSILON
    {
        return Err(FilterError::WindowDurationUnresolvable {
            window_s,
            measured_fps,
            resolved_window,
        });
    }
    Ok(resolved_window)
}

fn validate_window_duration(window_s: f64) -> Result<(), FilterError> {
    if !window_s.is_finite() || window_s <= 0.0 {
        return Err(FilterError::InvalidWindowDuration { window_s });
    }
    Ok(())
}

impl FilterConfig {
    pub fn moving_average_for_duration(
        window_s: f64,
        measured_fps: f64,
        max_gap_s: f64,
    ) -> Result<Self, FilterError> {
        let config = Self::MovingAverage {
            window: resolve_window_samples(window_s, measured_fps, 1)?,
            window_s: Some(window_s),
            max_gap_s,
        };
        config.validate()?;
        Ok(config)
    }

    pub fn savitzky_golay_for_duration(
        window_s: f64,
        measured_fps: f64,
        polynomial_order: usize,
        max_gap_s: f64,
    ) -> Result<Self, FilterError> {
        if polynomial_order > MAX_POLYNOMIAL_ORDER {
            return Err(FilterError::PolynomialOrderTooHigh {
                order: polynomial_order,
                max_supported: MAX_POLYNOMIAL_ORDER,
            });
        }
        let min_window = polynomial_order + 1 + polynomial_order % 2;
        let config = Self::SavitzkyGolay {
            window: resolve_window_samples(window_s, measured_fps, min_window)?,
            window_s: Some(window_s),
            polynomial_order,
            max_gap_s,
        };
        config.validate()?;
        Ok(config)
    }

    pub fn provenance(self) -> ImplementationProvenance {
        let mut parameters = Configuration::new();
        let (implementation, version) = match self {
            Self::Raw => (RAW_IMPLEMENTATION, FILTER_VERSION),
            Self::MovingAverage {
                window,
                window_s,
                max_gap_s,
            } => {
                if let Some(duration) = window_s {
                    parameters.insert("window_s".to_owned(), ParameterValue::Float(duration));
                }
                parameters.insert(
                    "window".to_owned(),
                    ParameterValue::Integer(i64::try_from(window).unwrap_or(i64::MAX)),
                );
                parameters.insert("max_gap_s".to_owned(), ParameterValue::Float(max_gap_s));
                (MOVING_AVERAGE_IMPLEMENTATION, FILTER_VERSION)
            }
            Self::SavitzkyGolay {
                window,
                window_s,
                polynomial_order,
                max_gap_s,
            } => {
                if let Some(duration) = window_s {
                    parameters.insert("window_s".to_owned(), ParameterValue::Float(duration));
                }
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
                acceleration_variance_m2_s4,
                measurement_variance_m2,
                initial_velocity_variance_m2_s2,
                confidence_window_samples,
                max_gap_s,
            } => {
                parameters.insert(
                    "acceleration_variance_m2_s4".to_owned(),
                    ParameterValue::Float(acceleration_variance_m2_s4),
                );
                parameters.insert(
                    "measurement_variance_m2".to_owned(),
                    ParameterValue::Float(measurement_variance_m2),
                );
                parameters.insert(
                    "initial_velocity_variance_m2_s2".to_owned(),
                    ParameterValue::Float(initial_velocity_variance_m2_s2),
                );
                parameters.insert(
                    "confidence_window_samples".to_owned(),
                    ParameterValue::Integer(
                        i64::try_from(confidence_window_samples).unwrap_or(i64::MAX),
                    ),
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
                confidence_behavior: "preserves each input confidence exactly",
                irregular_timestamp_behavior:
                    "identity; authoritative input timestamps are preserved exactly",
                gap_behavior:
                    "identity; missing spans remain absent and are never interpolated",
                edge_behavior: "identity at every sample including clip boundaries",
                latency_behavior: "zero algorithmic delay and zero timestamp shift",
            },
            Self::MovingAverage { .. } => FilterBehavior {
                causal: false,
                confidence_behavior:
                    "output confidence is the minimum confidence of samples contributing to the centered window",
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
                confidence_behavior:
                    "output confidence is the minimum confidence of samples contributing to the local polynomial fit",
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
                confidence_behavior:
                    "output confidence is the minimum recent observed confidence over confidence_window_samples and recovers as low-confidence observations leave that window",
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
            Self::MovingAverage {
                window,
                window_s,
                max_gap_s,
            } => {
                if let Some(duration) = window_s {
                    validate_window_duration(duration)?;
                }
                validate_centered_window(window)?;
                validate_gap(max_gap_s)
            }
            Self::SavitzkyGolay {
                window,
                window_s,
                polynomial_order,
                max_gap_s,
            } => {
                if let Some(duration) = window_s {
                    validate_window_duration(duration)?;
                }
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
                acceleration_variance_m2_s4,
                measurement_variance_m2,
                initial_velocity_variance_m2_s2,
                confidence_window_samples,
                max_gap_s,
            } => {
                if !acceleration_variance_m2_s4.is_finite() || acceleration_variance_m2_s4 < 0.0 {
                    return Err(FilterError::InvalidAccelerationVariance {
                        value: acceleration_variance_m2_s4,
                    });
                }
                if !measurement_variance_m2.is_finite() || measurement_variance_m2 <= 0.0 {
                    return Err(FilterError::InvalidMeasurementVariance {
                        value: measurement_variance_m2,
                    });
                }
                if !initial_velocity_variance_m2_s2.is_finite()
                    || initial_velocity_variance_m2_s2 <= 0.0
                {
                    return Err(FilterError::InvalidInitialVelocityVariance {
                        value: initial_velocity_variance_m2_s2,
                    });
                }
                if confidence_window_samples == 0 {
                    return Err(FilterError::InvalidConfidenceWindow {
                        value: confidence_window_samples,
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

    let segment_count = match config {
        FilterConfig::Raw => usize::from(!samples.is_empty()),
        FilterConfig::MovingAverage { max_gap_s, .. }
        | FilterConfig::SavitzkyGolay { max_gap_s, .. }
        | FilterConfig::Kalman { max_gap_s, .. } => segment_ranges(samples, max_gap_s).len(),
    };

    let filtered = match config {
        FilterConfig::Raw => samples.to_vec(),
        FilterConfig::MovingAverage {
            window, max_gap_s, ..
        } => moving_average_segmented(samples, window, max_gap_s),
        FilterConfig::SavitzkyGolay {
            window,
            polynomial_order,
            max_gap_s,
            ..
        } => savitzky_golay_segmented(samples, window, polynomial_order, max_gap_s)?,
        FilterConfig::Kalman {
            acceleration_variance_m2_s4,
            measurement_variance_m2,
            initial_velocity_variance_m2_s2,
            confidence_window_samples,
            max_gap_s,
        } => kalman_segmented(
            samples,
            acceleration_variance_m2_s4,
            measurement_variance_m2,
            initial_velocity_variance_m2_s2,
            confidence_window_samples,
            max_gap_s,
        ),
    };

    Ok(FilterRun {
        trajectory: FilteredTrajectory {
            filter: config.provenance(),
            samples: filtered,
        },
        behavior: config.behavior(),
        segment_count,
    })
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
    if window.is_multiple_of(2) {
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
    let time_scale_s = samples
        .iter()
        .map(|sample| (sample.timestamp_s - target_timestamp).abs())
        .fold(0.0_f64, f64::max);
    let time_scale_s = if time_scale_s > 0.0 {
        time_scale_s
    } else {
        1.0
    };

    for sample in samples {
        // Scale local time to roughly [-1, 1]. The fitted value at dt=0 is unchanged,
        // while high-order normal equations remain well-conditioned at 120/240+ fps.
        let dt = (sample.timestamp_s - target_timestamp) / time_scale_s;
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
        for value in matrix[pivot].iter_mut().skip(pivot) {
            *value /= pivot_value;
        }
        rhs[pivot] /= pivot_value;

        let pivot_row = matrix[pivot].clone();
        for (row, values) in matrix.iter_mut().enumerate() {
            if row == pivot {
                continue;
            }
            let factor = values[pivot];
            if factor == 0.0 {
                continue;
            }
            for (column, value) in values.iter_mut().enumerate().skip(pivot) {
                *value -= factor * pivot_row[column];
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
    fn new(
        position: f64,
        measurement_variance_m2: f64,
        initial_velocity_variance_m2_s2: f64,
    ) -> Self {
        Self {
            position,
            velocity: 0.0,
            p00: measurement_variance_m2,
            p01: 0.0,
            p11: initial_velocity_variance_m2_s2,
        }
    }

    fn update(
        &mut self,
        measurement: f64,
        dt: f64,
        acceleration_variance_m2_s4: f64,
        measurement_variance_m2: f64,
    ) {
        self.position += self.velocity * dt;

        let dt2 = dt * dt;
        let dt3 = dt2 * dt;
        let dt4 = dt2 * dt2;
        let predicted_p00 = self.p00
            + 2.0 * dt * self.p01
            + dt2 * self.p11
            + acceleration_variance_m2_s4 * dt4 / 4.0;
        let predicted_p01 = self.p01 + dt * self.p11 + acceleration_variance_m2_s4 * dt3 / 2.0;
        let predicted_p11 = self.p11 + acceleration_variance_m2_s4 * dt2;

        let innovation = measurement - self.position;
        let innovation_variance = predicted_p00 + measurement_variance_m2;
        let k0 = predicted_p00 / innovation_variance;
        let k1 = predicted_p01 / innovation_variance;

        self.position += k0 * innovation;
        self.velocity += k1 * innovation;
        self.p00 = (1.0 - k0) * predicted_p00;
        self.p01 = (1.0 - k0) * predicted_p01;
        self.p11 = predicted_p11 - k1 * predicted_p01;
    }
}

fn minimum_confidence(history: &VecDeque<f32>) -> f32 {
    history.iter().copied().fold(1.0_f32, f32::min)
}

fn kalman_segmented(
    samples: &[MetricPositionSample],
    acceleration_variance_m2_s4: f64,
    measurement_variance_m2: f64,
    initial_velocity_variance_m2_s2: f64,
    confidence_window_samples: usize,
    max_gap_s: f64,
) -> Vec<MetricPositionSample> {
    if samples.is_empty() {
        return Vec::new();
    }

    let mut output = Vec::with_capacity(samples.len());
    let mut x_state = KalmanAxis::new(
        samples[0].x_m,
        measurement_variance_m2,
        initial_velocity_variance_m2_s2,
    );
    let mut y_state = KalmanAxis::new(
        samples[0].y_m,
        measurement_variance_m2,
        initial_velocity_variance_m2_s2,
    );
    let mut confidence_history = VecDeque::with_capacity(confidence_window_samples);
    confidence_history.push_back(samples[0].confidence);
    output.push(samples[0]);

    for index in 1..samples.len() {
        let sample = samples[index];
        let dt = sample.timestamp_s - samples[index - 1].timestamp_s;
        if dt > max_gap_s {
            x_state = KalmanAxis::new(
                sample.x_m,
                measurement_variance_m2,
                initial_velocity_variance_m2_s2,
            );
            y_state = KalmanAxis::new(
                sample.y_m,
                measurement_variance_m2,
                initial_velocity_variance_m2_s2,
            );
            confidence_history.clear();
            confidence_history.push_back(sample.confidence);
            output.push(sample);
            continue;
        }

        x_state.update(
            sample.x_m,
            dt,
            acceleration_variance_m2_s4,
            measurement_variance_m2,
        );
        y_state.update(
            sample.y_m,
            dt,
            acceleration_variance_m2_s4,
            measurement_variance_m2,
        );

        confidence_history.push_back(sample.confidence);
        while confidence_history.len() > confidence_window_samples {
            confidence_history.pop_front();
        }

        output.push(MetricPositionSample {
            timestamp_s: sample.timestamp_s,
            x_m: x_state.position,
            y_m: y_state.position,
            confidence: minimum_confidence(&confidence_history),
        });
    }
    output
}

pub mod butterworth_experimental;
pub use butterworth_experimental::{
    apply_butterworth_filter, assess_timestamp_regularity, ButterworthExperimentalConfig,
    TimestampRegularity,
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duration_window_examples() {
        for (duration, fps, expected) in [
            (0.15, 60.0, 9),
            (0.15, 30.0, 5),
            (0.3, 30.0, 9),
            (0.1, 60.0, 7),
            (0.25, 12.0, 3),
        ] {
            assert_eq!(resolve_window_samples(duration, fps, 1), Ok(expected));
        }
    }

    #[test]
    fn duration_window_covers_same_timestamp_span_at_30_and_60_fps() {
        for (lo, hi) in [(30.0, 60.0), (29.97, 59.94)] {
            for duration in [0.1, 0.15, 0.2, 0.3] {
                let a = resolve_window_samples(duration, lo, 1).unwrap();
                let b = resolve_window_samples(duration, hi, 1).unwrap();
                let a_span_s = (a - 1) as f64 / lo;
                let b_span_s = (b - 1) as f64 / hi;
                assert!((a_span_s - b_span_s).abs() <= 1.0 / lo + f64::EPSILON);
            }
        }
    }

    #[test]
    fn duration_window_clamps_within_tolerance() {
        assert_eq!(resolve_window_samples(0.02, 30.0, 1), Ok(1));
        assert_eq!(resolve_window_samples(0.1, 30.0, 3), Ok(3));
        assert_eq!(resolve_window_samples(0.1, 60.0 - 1e-10, 1), Ok(7));
    }

    #[test]
    fn duration_window_rejects_clamp_beyond_tolerance() {
        assert!(matches!(
            FilterConfig::savitzky_golay_for_duration(0.05, 30.0, 3, 0.2),
            Err(FilterError::WindowDurationUnresolvable {
                resolved_window: 5,
                ..
            })
        ));
    }

    #[test]
    fn duration_window_rejects_invalid_inputs() {
        for duration in [0.0, -0.1, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(matches!(
                resolve_window_samples(duration, 30.0, 1),
                Err(FilterError::InvalidWindowDuration { .. })
            ));
        }
        for fps in [0.0, -1.0, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(matches!(
                resolve_window_samples(0.1, fps, 1),
                Err(FilterError::InvalidMeasuredFps { .. })
            ));
        }
        for (duration, fps) in [(f64::MAX, 30.0), (1e18, 30.0), (1.0, (1_u64 << 53) as f64)] {
            assert!(resolve_window_samples(duration, fps, 1).is_err());
        }
        assert!(resolve_window_samples(0.1, 30.0, 0).is_err());
        assert!(resolve_window_samples(0.1, 30.0, 2).is_err());
    }

    #[test]
    fn duration_window_rejects_minimum_outside_exact_integer_range() {
        if let Ok(min_window) = usize::try_from((1_u64 << 53) + 1) {
            assert!(matches!(
                resolve_window_samples(1.0, ((1_u64 << 53) - 1) as f64, min_window),
                Err(FilterError::WindowDurationOutOfRange { .. })
            ));
        }
    }

    #[test]
    fn provenance_records_window_s_only_when_requested() {
        for (fixed, duration) in [
            (
                FilterConfig::MovingAverage {
                    window: 9,
                    window_s: None,
                    max_gap_s: 0.2,
                },
                FilterConfig::moving_average_for_duration(0.15, 60.0, 0.2).unwrap(),
            ),
            (
                FilterConfig::SavitzkyGolay {
                    window: 9,
                    window_s: None,
                    polynomial_order: 2,
                    max_gap_s: 0.2,
                },
                FilterConfig::savitzky_golay_for_duration(0.15, 60.0, 2, 0.2).unwrap(),
            ),
        ] {
            let mut expected = fixed.provenance();
            assert!(!expected.parameters.contains_key("window_s"));
            expected
                .parameters
                .insert("window_s".to_owned(), ParameterValue::Float(0.15));
            assert_eq!(duration.provenance(), expected);
        }
    }

    #[test]
    fn validate_rejects_non_finite_window_s() {
        for duration in [0.0, -0.1, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            for config in [
                FilterConfig::MovingAverage {
                    window: 3,
                    window_s: Some(duration),
                    max_gap_s: 0.2,
                },
                FilterConfig::SavitzkyGolay {
                    window: 3,
                    window_s: Some(duration),
                    polynomial_order: 2,
                    max_gap_s: 0.2,
                },
            ] {
                assert!(matches!(
                    apply_filter(&[], config),
                    Err(FilterError::InvalidWindowDuration { .. })
                ));
            }
        }
    }
    use crate::test_utils::sample_metric_position;

    fn sample(timestamp_s: f64, x_m: f64, y_m: f64) -> MetricPositionSample {
        sample_metric_position(timestamp_s, x_m, y_m, 1.0)
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

        let run = apply_filter(
            &input,
            FilterConfig::MovingAverage {
                window_s: None,
                window: 3,
                max_gap_s: 1.0,
            },
        )
        .unwrap();
        let output = run.trajectory.samples;
        assert_eq!(output.len(), input.len());
        assert_eq!(output[1].timestamp_s, 0.5);
        assert_eq!(output[1].x_m, 3.0);
        assert_eq!(output[1].y_m, 6.0);
        assert_eq!(output[0].confidence, 0.5);
        assert_eq!(output[1].confidence, 0.5);
    }

    #[test]
    fn moving_average_handles_empty_samples() {
        let run = apply_filter(
            &[],
            FilterConfig::MovingAverage {
                window_s: None,
                window: 3,
                max_gap_s: 0.1,
            },
        )
        .unwrap();
        assert!(run.trajectory.samples.is_empty());
        assert_eq!(run.segment_count, 0);
    }

    #[test]
    fn moving_average_zero_window_returns_error() {
        let input = [sample(0.0, 1.0, 2.0)];

        assert_eq!(
            apply_filter(
                &[],
                FilterConfig::MovingAverage {
                    window_s: None,
                    window: 0,
                    max_gap_s: 0.1,
                },
            ),
            Err(FilterError::InvalidWindow { window: 0 })
        );
        assert_eq!(
            apply_filter(
                &input,
                FilterConfig::MovingAverage {
                    window_s: None,
                    window: 0,
                    max_gap_s: 0.1,
                },
            ),
            Err(FilterError::InvalidWindow { window: 0 })
        );
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
                window_s: None,
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
                window_s: None,
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
                window_s: None,
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
            acceleration_variance_m2_s4: 1.0,
            measurement_variance_m2: 0.01,
            initial_velocity_variance_m2_s2: 1.0,
            confidence_window_samples: 3,
            max_gap_s: 0.1,
        };
        let first = apply_filter(&input, config).unwrap();
        let second = apply_filter(&input, config).unwrap();

        assert_eq!(first, second);
        assert_eq!(first.trajectory.samples[3], input[3]);
        assert_eq!(first.segment_count, 2);
    }

    #[test]
    fn savitzky_golay_is_conditioned_for_high_frame_rate_high_order_windows() {
        let input = (0..21)
            .map(|index| {
                let t = index as f64 / 240.0;
                let x = 0.2 + 0.3 * t - 0.4 * t.powi(2) + 0.2 * t.powi(3) - 0.1 * t.powi(4)
                    + 0.05 * t.powi(5);
                sample(t, x, -0.1 + 0.8 * t)
            })
            .collect::<Vec<_>>();

        for (window, polynomial_order) in [(9, 4), (11, 5)] {
            let run = apply_filter(
                &input,
                FilterConfig::SavitzkyGolay {
                    window,
                    window_s: None,
                    polynomial_order,
                    max_gap_s: 0.02,
                },
            )
            .unwrap();

            for (actual, expected) in run.trajectory.samples.iter().zip(&input) {
                assert!((actual.x_m - expected.x_m).abs() < 1.0e-8);
                assert!((actual.y_m - expected.y_m).abs() < 1.0e-8);
            }
        }
    }

    #[test]
    fn savitzky_golay_shifted_edge_windows_and_confidence_are_explicit() {
        let mut input = (0..7)
            .map(|index| {
                let t = index as f64 * 0.02;
                sample(t, 0.5 + 0.2 * t + 0.3 * t * t, 0.1 + t)
            })
            .collect::<Vec<_>>();
        input[1].confidence = 0.3;

        let run = apply_filter(
            &input,
            FilterConfig::SavitzkyGolay {
                window_s: None,
                window: 5,
                polynomial_order: 2,
                max_gap_s: 0.1,
            },
        )
        .unwrap();

        assert!((run.trajectory.samples[0].x_m - input[0].x_m).abs() < 1.0e-9);
        assert!((run.trajectory.samples[6].x_m - input[6].x_m).abs() < 1.0e-9);
        assert_eq!(run.trajectory.samples[0].confidence, 0.3);
        assert_eq!(run.trajectory.samples[3].confidence, 0.3);
        assert_eq!(run.trajectory.samples[6].confidence, 1.0);
    }

    #[test]
    fn kalman_converges_on_noiseless_constant_velocity() {
        let input = (0..60)
            .map(|index| {
                let t = index as f64 / 60.0;
                sample(t, 0.25 + 0.4 * t, -0.1 + 0.8 * t)
            })
            .collect::<Vec<_>>();
        let run = apply_filter(
            &input,
            FilterConfig::Kalman {
                acceleration_variance_m2_s4: 0.0,
                measurement_variance_m2: 1.0e-12,
                initial_velocity_variance_m2_s2: 100.0,
                confidence_window_samples: 3,
                max_gap_s: 0.05,
            },
        )
        .unwrap();

        let actual = run.trajectory.samples.last().unwrap();
        let expected = input.last().unwrap();
        assert!((actual.x_m - expected.x_m).abs() < 1.0e-6);
        assert!((actual.y_m - expected.y_m).abs() < 1.0e-6);
    }

    #[test]
    fn kalman_confidence_recovers_after_low_confidence_leaves_recent_window() {
        let mut input = (0..6)
            .map(|index| {
                let t = index as f64 * 0.02;
                sample(t, t, 2.0 * t)
            })
            .collect::<Vec<_>>();
        input[1].confidence = 0.2;

        let run = apply_filter(
            &input,
            FilterConfig::Kalman {
                acceleration_variance_m2_s4: 1.0,
                measurement_variance_m2: 1.0e-4,
                initial_velocity_variance_m2_s2: 1.0,
                confidence_window_samples: 3,
                max_gap_s: 0.1,
            },
        )
        .unwrap();

        assert_eq!(run.trajectory.samples[1].confidence, 0.2);
        assert_eq!(run.trajectory.samples[3].confidence, 0.2);
        assert_eq!(run.trajectory.samples[4].confidence, 1.0);
        assert_eq!(run.trajectory.samples[5].confidence, 1.0);
    }

    #[test]
    fn effective_parameters_are_persisted_in_filter_provenance() {
        let config = FilterConfig::SavitzkyGolay {
            window_s: None,
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

        let kalman = FilterConfig::Kalman {
            acceleration_variance_m2_s4: 2.0,
            measurement_variance_m2: 4.0e-6,
            initial_velocity_variance_m2_s2: 1.0,
            confidence_window_samples: 3,
            max_gap_s: 0.05,
        }
        .provenance();
        assert_eq!(
            kalman.parameters.get("measurement_variance_m2"),
            Some(&ParameterValue::Float(4.0e-6))
        );
        assert_eq!(
            kalman.parameters.get("confidence_window_samples"),
            Some(&ParameterValue::Integer(3))
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
                    window_s: None,
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
                    acceleration_variance_m2_s4: 1.0,
                    measurement_variance_m2: 0.0,
                    initial_velocity_variance_m2_s2: 1.0,
                    confidence_window_samples: 3,
                    max_gap_s: 0.1,
                }
            ),
            Err(FilterError::InvalidMeasurementVariance { .. })
        ));
        assert!(matches!(
            apply_filter(
                &valid,
                FilterConfig::Kalman {
                    acceleration_variance_m2_s4: 1.0,
                    measurement_variance_m2: 1.0e-4,
                    initial_velocity_variance_m2_s2: 1.0,
                    confidence_window_samples: 0,
                    max_gap_s: 0.1,
                }
            ),
            Err(FilterError::InvalidConfidenceWindow { .. })
        ));
    }
}
