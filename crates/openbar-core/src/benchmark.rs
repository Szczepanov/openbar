use crate::manual_seed::PixelPoint;
use serde::{Deserialize, Serialize};
use std::fmt;

pub const BENCHMARK_METRIC_VERSION: &str = "tracker-v1";

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct GroundTruthSample {
    pub timestamp_s: f64,
    pub center: PixelPoint,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum TrackerPredictionState {
    Tracked { center: PixelPoint, confidence: f32 },
    Lost,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TrackerPrediction {
    pub timestamp_s: f64,
    pub state: TrackerPredictionState,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BenchmarkParameters {
    pub timestamp_tolerance_s: f64,
    pub min_confidence: f32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TrackerMetrics {
    pub comparable_samples: usize,
    pub tracked_samples: usize,
    pub lost_samples: usize,
    pub tracker_declared_loss_samples: usize,
    pub low_confidence_samples: usize,
    pub timestamp_unmatched_samples: usize,
    pub plate_center_mae_px: Option<f64>,
    pub plate_center_rmse_px: Option<f64>,
    pub x_bias_px: Option<f64>,
    pub y_bias_px: Option<f64>,
    pub tracking_availability: Option<f64>,
    pub lost_frame_percentage: Option<f64>,
    pub max_consecutive_tracking_loss_samples: Option<usize>,
    pub max_consecutive_tracking_loss_duration_s: Option<f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum BenchmarkError {
    InvalidTimestampTolerance { value: f64 },
    InvalidMinimumConfidence { value: f32 },
    InvalidGroundTruthTimestamp { index: usize, value: f64 },
    InvalidPredictionTimestamp { index: usize, value: f64 },
    NonIncreasingGroundTruthTimestamps { previous_index: usize, index: usize },
    NonIncreasingPredictionTimestamps { previous_index: usize, index: usize },
    NonFiniteGroundTruthCoordinate { index: usize },
    NonFinitePredictionCoordinate { index: usize },
    InvalidPredictionConfidence { index: usize, value: f32 },
}

impl fmt::Display for BenchmarkError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidTimestampTolerance { value } => write!(
                formatter,
                "timestamp tolerance must be finite and non-negative, got {value}"
            ),
            Self::InvalidMinimumConfidence { value } => write!(
                formatter,
                "minimum confidence must be finite and within [0, 1], got {value}"
            ),
            Self::InvalidGroundTruthTimestamp { index, value } => write!(
                formatter,
                "ground-truth timestamp at index {index} must be finite and non-negative, got {value}"
            ),
            Self::InvalidPredictionTimestamp { index, value } => write!(
                formatter,
                "prediction timestamp at index {index} must be finite and non-negative, got {value}"
            ),
            Self::NonIncreasingGroundTruthTimestamps {
                previous_index,
                index,
            } => write!(
                formatter,
                "ground-truth timestamps must be strictly increasing (indices {previous_index} and {index})"
            ),
            Self::NonIncreasingPredictionTimestamps {
                previous_index,
                index,
            } => write!(
                formatter,
                "prediction timestamps must be strictly increasing (indices {previous_index} and {index})"
            ),
            Self::NonFiniteGroundTruthCoordinate { index } => write!(
                formatter,
                "ground-truth coordinate at index {index} must be finite"
            ),
            Self::NonFinitePredictionCoordinate { index } => write!(
                formatter,
                "prediction coordinate at index {index} must be finite"
            ),
            Self::InvalidPredictionConfidence { index, value } => write!(
                formatter,
                "prediction confidence at index {index} must be finite and within [0, 1], got {value}"
            ),
        }
    }
}

impl std::error::Error for BenchmarkError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum UnavailableReason {
    DeclaredLost,
    LowConfidence,
    TimestampUnmatched,
}

#[derive(Debug, Clone, Copy)]
struct EvaluatedSample {
    timestamp_s: f64,
    available: bool,
    unavailable_reason: Option<UnavailableReason>,
}

pub fn evaluate_tracker_case(
    ground_truth: &[GroundTruthSample],
    predictions: &[TrackerPrediction],
    parameters: BenchmarkParameters,
) -> Result<TrackerMetrics, BenchmarkError> {
    validate_inputs(ground_truth, predictions, parameters)?;

    if ground_truth.is_empty() {
        return Ok(empty_metrics());
    }

    let mut prediction_index = 0usize;
    let mut evaluated = Vec::with_capacity(ground_truth.len());
    let mut absolute_error_sum = 0.0;
    let mut squared_error_sum = 0.0;
    let mut x_bias_sum = 0.0;
    let mut y_bias_sum = 0.0;
    let mut tracked_samples = 0usize;
    let mut declared_loss_samples = 0usize;
    let mut low_confidence_samples = 0usize;
    let mut timestamp_unmatched_samples = 0usize;

    for truth in ground_truth {
        while prediction_index < predictions.len()
            && predictions[prediction_index].timestamp_s
                < truth.timestamp_s - parameters.timestamp_tolerance_s
        {
            prediction_index += 1;
        }

        let best_index = nearest_prediction_index(
            predictions,
            prediction_index,
            truth.timestamp_s,
            parameters.timestamp_tolerance_s,
        );

        let Some(best_index) = best_index else {
            timestamp_unmatched_samples += 1;
            evaluated.push(EvaluatedSample {
                timestamp_s: truth.timestamp_s,
                available: false,
                unavailable_reason: Some(UnavailableReason::TimestampUnmatched),
            });
            continue;
        };

        prediction_index = best_index + 1;
        match predictions[best_index].state {
            TrackerPredictionState::Lost => {
                declared_loss_samples += 1;
                evaluated.push(EvaluatedSample {
                    timestamp_s: truth.timestamp_s,
                    available: false,
                    unavailable_reason: Some(UnavailableReason::DeclaredLost),
                });
            }
            TrackerPredictionState::Tracked { center, confidence } => {
                if confidence < parameters.min_confidence {
                    low_confidence_samples += 1;
                    evaluated.push(EvaluatedSample {
                        timestamp_s: truth.timestamp_s,
                        available: false,
                        unavailable_reason: Some(UnavailableReason::LowConfidence),
                    });
                    continue;
                }

                let dx = center.x_px() - truth.center.x_px();
                let dy = center.y_px() - truth.center.y_px();
                let squared_error = dx.mul_add(dx, dy * dy);
                let error = squared_error.sqrt();

                absolute_error_sum += error;
                squared_error_sum += squared_error;
                x_bias_sum += dx;
                y_bias_sum += dy;
                tracked_samples += 1;
                evaluated.push(EvaluatedSample {
                    timestamp_s: truth.timestamp_s,
                    available: true,
                    unavailable_reason: None,
                });
            }
        }
    }

    let comparable_samples = ground_truth.len();
    let lost_samples = comparable_samples - tracked_samples;
    let tracked_denominator = tracked_samples as f64;
    let comparable_denominator = comparable_samples as f64;
    let (max_loss_samples, max_loss_duration_s) = maximum_loss_span(&evaluated);

    Ok(TrackerMetrics {
        comparable_samples,
        tracked_samples,
        lost_samples,
        tracker_declared_loss_samples: declared_loss_samples,
        low_confidence_samples,
        timestamp_unmatched_samples,
        plate_center_mae_px: (tracked_samples > 0)
            .then_some(absolute_error_sum / tracked_denominator),
        plate_center_rmse_px: (tracked_samples > 0)
            .then_some((squared_error_sum / tracked_denominator).sqrt()),
        x_bias_px: (tracked_samples > 0).then_some(x_bias_sum / tracked_denominator),
        y_bias_px: (tracked_samples > 0).then_some(y_bias_sum / tracked_denominator),
        tracking_availability: Some(tracked_denominator / comparable_denominator),
        lost_frame_percentage: Some(100.0 * lost_samples as f64 / comparable_denominator),
        max_consecutive_tracking_loss_samples: Some(max_loss_samples),
        max_consecutive_tracking_loss_duration_s: Some(max_loss_duration_s),
    })
}

pub fn aggregate_metrics(metrics: &[TrackerMetrics]) -> TrackerMetrics {
    let comparable_samples = metrics.iter().map(|value| value.comparable_samples).sum();
    let tracked_samples = metrics.iter().map(|value| value.tracked_samples).sum();
    let lost_samples = metrics.iter().map(|value| value.lost_samples).sum();
    let tracker_declared_loss_samples = metrics
        .iter()
        .map(|value| value.tracker_declared_loss_samples)
        .sum();
    let low_confidence_samples = metrics
        .iter()
        .map(|value| value.low_confidence_samples)
        .sum();
    let timestamp_unmatched_samples = metrics
        .iter()
        .map(|value| value.timestamp_unmatched_samples)
        .sum();

    let plate_center_mae_px = weighted_mean(
        metrics,
        |value| value.plate_center_mae_px,
        |value| value.tracked_samples,
    );
    let x_bias_px = weighted_mean(
        metrics,
        |value| value.x_bias_px,
        |value| value.tracked_samples,
    );
    let y_bias_px = weighted_mean(
        metrics,
        |value| value.y_bias_px,
        |value| value.tracked_samples,
    );

    let rmse_squared_mean = weighted_mean(
        metrics,
        |value| value.plate_center_rmse_px.map(|metric| metric * metric),
        |value| value.tracked_samples,
    );
    let plate_center_rmse_px = rmse_squared_mean.map(f64::sqrt);

    let max_consecutive_tracking_loss_samples = metrics
        .iter()
        .filter_map(|value| value.max_consecutive_tracking_loss_samples)
        .max();
    let max_consecutive_tracking_loss_duration_s = metrics
        .iter()
        .filter_map(|value| value.max_consecutive_tracking_loss_duration_s)
        .reduce(f64::max);

    let tracking_availability =
        (comparable_samples > 0).then_some(tracked_samples as f64 / comparable_samples as f64);
    let lost_frame_percentage =
        (comparable_samples > 0).then_some(100.0 * lost_samples as f64 / comparable_samples as f64);

    TrackerMetrics {
        comparable_samples,
        tracked_samples,
        lost_samples,
        tracker_declared_loss_samples,
        low_confidence_samples,
        timestamp_unmatched_samples,
        plate_center_mae_px,
        plate_center_rmse_px,
        x_bias_px,
        y_bias_px,
        tracking_availability,
        lost_frame_percentage,
        max_consecutive_tracking_loss_samples,
        max_consecutive_tracking_loss_duration_s,
    }
}

fn validate_inputs(
    ground_truth: &[GroundTruthSample],
    predictions: &[TrackerPrediction],
    parameters: BenchmarkParameters,
) -> Result<(), BenchmarkError> {
    if !parameters.timestamp_tolerance_s.is_finite() || parameters.timestamp_tolerance_s < 0.0 {
        return Err(BenchmarkError::InvalidTimestampTolerance {
            value: parameters.timestamp_tolerance_s,
        });
    }
    if !parameters.min_confidence.is_finite() || !(0.0..=1.0).contains(&parameters.min_confidence) {
        return Err(BenchmarkError::InvalidMinimumConfidence {
            value: parameters.min_confidence,
        });
    }

    for (index, sample) in ground_truth.iter().enumerate() {
        if !sample.timestamp_s.is_finite() || sample.timestamp_s < 0.0 {
            return Err(BenchmarkError::InvalidGroundTruthTimestamp {
                index,
                value: sample.timestamp_s,
            });
        }
        if !sample.center.x_px().is_finite() || !sample.center.y_px().is_finite() {
            return Err(BenchmarkError::NonFiniteGroundTruthCoordinate { index });
        }
        if index > 0 && ground_truth[index - 1].timestamp_s >= sample.timestamp_s {
            return Err(BenchmarkError::NonIncreasingGroundTruthTimestamps {
                previous_index: index - 1,
                index,
            });
        }
    }

    for (index, sample) in predictions.iter().enumerate() {
        if !sample.timestamp_s.is_finite() || sample.timestamp_s < 0.0 {
            return Err(BenchmarkError::InvalidPredictionTimestamp {
                index,
                value: sample.timestamp_s,
            });
        }
        if index > 0 && predictions[index - 1].timestamp_s >= sample.timestamp_s {
            return Err(BenchmarkError::NonIncreasingPredictionTimestamps {
                previous_index: index - 1,
                index,
            });
        }

        if let TrackerPredictionState::Tracked { center, confidence } = sample.state {
            if !center.x_px().is_finite() || !center.y_px().is_finite() {
                return Err(BenchmarkError::NonFinitePredictionCoordinate { index });
            }
            if !confidence.is_finite() || !(0.0..=1.0).contains(&confidence) {
                return Err(BenchmarkError::InvalidPredictionConfidence {
                    index,
                    value: confidence,
                });
            }
        }
    }

    Ok(())
}

fn nearest_prediction_index(
    predictions: &[TrackerPrediction],
    start_index: usize,
    timestamp_s: f64,
    tolerance_s: f64,
) -> Option<usize> {
    let mut best: Option<(usize, f64)> = None;

    for (index, prediction) in predictions.iter().enumerate().skip(start_index) {
        if prediction.timestamp_s > timestamp_s + tolerance_s {
            break;
        }

        let distance = (prediction.timestamp_s - timestamp_s).abs();
        if distance <= tolerance_s {
            match best {
                Some((_, best_distance)) if distance >= best_distance => {}
                _ => best = Some((index, distance)),
            }
        }
    }

    best.map(|(index, _)| index)
}

fn maximum_loss_span(samples: &[EvaluatedSample]) -> (usize, f64) {
    let mut max_samples = 0usize;
    let mut max_duration_s: f64 = 0.0;
    let mut index = 0usize;

    while index < samples.len() {
        if samples[index].available {
            index += 1;
            continue;
        }

        debug_assert!(samples[index].unavailable_reason.is_some());
        let start = index;
        while index + 1 < samples.len() && !samples[index + 1].available {
            index += 1;
        }
        let end = index;
        let count = end - start + 1;
        let duration_s = if end + 1 < samples.len() {
            samples[end + 1].timestamp_s - samples[start].timestamp_s
        } else {
            samples[end].timestamp_s - samples[start].timestamp_s
        };

        max_samples = max_samples.max(count);
        max_duration_s = max_duration_s.max(duration_s);
        index += 1;
    }

    (max_samples, max_duration_s)
}

fn weighted_mean(
    metrics: &[TrackerMetrics],
    value: impl Fn(&TrackerMetrics) -> Option<f64>,
    weight: impl Fn(&TrackerMetrics) -> usize,
) -> Option<f64> {
    let mut weighted_sum = 0.0;
    let mut total_weight = 0usize;

    for metric in metrics {
        let sample_weight = weight(metric);
        if sample_weight == 0 {
            continue;
        }
        if let Some(sample_value) = value(metric) {
            weighted_sum += sample_value * sample_weight as f64;
            total_weight += sample_weight;
        }
    }

    (total_weight > 0).then_some(weighted_sum / total_weight as f64)
}

fn empty_metrics() -> TrackerMetrics {
    TrackerMetrics {
        comparable_samples: 0,
        tracked_samples: 0,
        lost_samples: 0,
        tracker_declared_loss_samples: 0,
        low_confidence_samples: 0,
        timestamp_unmatched_samples: 0,
        plate_center_mae_px: None,
        plate_center_rmse_px: None,
        x_bias_px: None,
        y_bias_px: None,
        tracking_availability: None,
        lost_frame_percentage: None,
        max_consecutive_tracking_loss_samples: None,
        max_consecutive_tracking_loss_duration_s: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn point(x_px: f64, y_px: f64) -> PixelPoint {
        PixelPoint::new(x_px, y_px)
    }

    fn truth(timestamp_s: f64, x_px: f64, y_px: f64) -> GroundTruthSample {
        GroundTruthSample {
            timestamp_s,
            center: point(x_px, y_px),
        }
    }

    fn tracked(timestamp_s: f64, x_px: f64, y_px: f64, confidence: f32) -> TrackerPrediction {
        TrackerPrediction {
            timestamp_s,
            state: TrackerPredictionState::Tracked {
                center: point(x_px, y_px),
                confidence,
            },
        }
    }

    fn lost(timestamp_s: f64) -> TrackerPrediction {
        TrackerPrediction {
            timestamp_s,
            state: TrackerPredictionState::Lost,
        }
    }

    fn parameters() -> BenchmarkParameters {
        BenchmarkParameters {
            timestamp_tolerance_s: 0.001,
            min_confidence: 0.5,
        }
    }

    #[test]
    fn perfect_tracking_has_zero_error_and_full_availability() {
        let ground_truth = [truth(0.0, 10.0, 20.0), truth(0.1, 11.0, 21.0)];
        let predictions = [tracked(0.0, 10.0, 20.0, 1.0), tracked(0.1, 11.0, 21.0, 1.0)];

        let metrics = evaluate_tracker_case(&ground_truth, &predictions, parameters()).unwrap();

        assert_eq!(metrics.comparable_samples, 2);
        assert_eq!(metrics.tracked_samples, 2);
        assert_eq!(metrics.lost_samples, 0);
        assert_eq!(metrics.plate_center_mae_px, Some(0.0));
        assert_eq!(metrics.plate_center_rmse_px, Some(0.0));
        assert_eq!(metrics.x_bias_px, Some(0.0));
        assert_eq!(metrics.y_bias_px, Some(0.0));
        assert_eq!(metrics.tracking_availability, Some(1.0));
        assert_eq!(metrics.lost_frame_percentage, Some(0.0));
        assert_eq!(metrics.max_consecutive_tracking_loss_samples, Some(0));
        assert_eq!(metrics.max_consecutive_tracking_loss_duration_s, Some(0.0));
    }

    #[test]
    fn constant_offset_produces_hand_checkable_error_and_bias() {
        let ground_truth = [truth(0.0, 10.0, 20.0), truth(0.1, 11.0, 21.0)];
        let predictions = [tracked(0.0, 13.0, 24.0, 1.0), tracked(0.1, 14.0, 25.0, 1.0)];

        let metrics = evaluate_tracker_case(&ground_truth, &predictions, parameters()).unwrap();

        assert_eq!(metrics.plate_center_mae_px, Some(5.0));
        assert_eq!(metrics.plate_center_rmse_px, Some(5.0));
        assert_eq!(metrics.x_bias_px, Some(3.0));
        assert_eq!(metrics.y_bias_px, Some(4.0));
    }

    #[test]
    fn loss_low_confidence_and_unmatched_samples_reduce_availability_without_fake_error() {
        let ground_truth = [
            truth(0.0, 0.0, 0.0),
            truth(0.1, 1.0, 1.0),
            truth(0.2, 2.0, 2.0),
            truth(0.3, 3.0, 3.0),
        ];
        let predictions = [
            tracked(0.0, 3.0, 4.0, 1.0),
            lost(0.1),
            tracked(0.2, 2.0, 2.0, 0.25),
        ];

        let metrics = evaluate_tracker_case(&ground_truth, &predictions, parameters()).unwrap();

        assert_eq!(metrics.comparable_samples, 4);
        assert_eq!(metrics.tracked_samples, 1);
        assert_eq!(metrics.lost_samples, 3);
        assert_eq!(metrics.tracker_declared_loss_samples, 1);
        assert_eq!(metrics.low_confidence_samples, 1);
        assert_eq!(metrics.timestamp_unmatched_samples, 1);
        assert_eq!(metrics.plate_center_mae_px, Some(5.0));
        assert_eq!(metrics.plate_center_rmse_px, Some(5.0));
        assert_eq!(metrics.tracking_availability, Some(0.25));
        assert_eq!(metrics.lost_frame_percentage, Some(75.0));
        assert_eq!(metrics.max_consecutive_tracking_loss_samples, Some(3));
        assert!((metrics.max_consecutive_tracking_loss_duration_s.unwrap() - 0.2).abs() < 1e-12);
    }

    #[test]
    fn aligns_by_irregular_timestamps_with_tolerance() {
        let ground_truth = [
            truth(0.0, 1.0, 1.0),
            truth(0.107, 2.0, 2.0),
            truth(0.231, 3.0, 3.0),
        ];
        let predictions = [
            tracked(0.0004, 1.0, 1.0, 1.0),
            tracked(0.1066, 2.0, 2.0, 1.0),
            tracked(0.2314, 3.0, 3.0, 1.0),
        ];

        let metrics = evaluate_tracker_case(
            &ground_truth,
            &predictions,
            BenchmarkParameters {
                timestamp_tolerance_s: 0.0005,
                min_confidence: 0.0,
            },
        )
        .unwrap();

        assert_eq!(metrics.tracked_samples, 3);
        assert_eq!(metrics.plate_center_mae_px, Some(0.0));
    }

    #[test]
    fn multiple_loss_spans_use_actual_timestamps_for_max_duration() {
        let ground_truth = [
            truth(0.0, 0.0, 0.0),
            truth(0.1, 0.0, 0.0),
            truth(0.35, 0.0, 0.0),
            truth(0.5, 0.0, 0.0),
            truth(0.9, 0.0, 0.0),
        ];
        let predictions = [
            lost(0.0),
            tracked(0.1, 0.0, 0.0, 1.0),
            lost(0.35),
            lost(0.5),
            tracked(0.9, 0.0, 0.0, 1.0),
        ];

        let metrics = evaluate_tracker_case(&ground_truth, &predictions, parameters()).unwrap();

        assert_eq!(metrics.max_consecutive_tracking_loss_samples, Some(2));
        assert!((metrics.max_consecutive_tracking_loss_duration_s.unwrap() - 0.55).abs() < 1e-12);
    }

    #[test]
    fn empty_ground_truth_produces_null_rate_and_error_metrics() {
        let metrics = evaluate_tracker_case(&[], &[], parameters()).unwrap();
        assert_eq!(metrics.comparable_samples, 0);
        assert_eq!(metrics.tracking_availability, None);
        assert_eq!(metrics.plate_center_mae_px, None);
        assert_eq!(metrics.max_consecutive_tracking_loss_samples, None);
    }

    #[test]
    fn rejects_non_increasing_prediction_timestamps() {
        let ground_truth = [truth(0.0, 0.0, 0.0)];
        let predictions = [tracked(0.0, 0.0, 0.0, 1.0), tracked(0.0, 0.0, 0.0, 1.0)];

        let error = evaluate_tracker_case(&ground_truth, &predictions, parameters()).unwrap_err();
        assert!(matches!(
            error,
            BenchmarkError::NonIncreasingPredictionTimestamps { .. }
        ));
    }

    #[test]
    fn aggregation_weights_coordinate_metrics_by_tracked_samples() {
        let first = TrackerMetrics {
            comparable_samples: 2,
            tracked_samples: 2,
            lost_samples: 0,
            tracker_declared_loss_samples: 0,
            low_confidence_samples: 0,
            timestamp_unmatched_samples: 0,
            plate_center_mae_px: Some(2.0),
            plate_center_rmse_px: Some(2.0),
            x_bias_px: Some(1.0),
            y_bias_px: Some(0.0),
            tracking_availability: Some(1.0),
            lost_frame_percentage: Some(0.0),
            max_consecutive_tracking_loss_samples: Some(0),
            max_consecutive_tracking_loss_duration_s: Some(0.0),
        };
        let second = TrackerMetrics {
            comparable_samples: 2,
            tracked_samples: 1,
            lost_samples: 1,
            tracker_declared_loss_samples: 1,
            low_confidence_samples: 0,
            timestamp_unmatched_samples: 0,
            plate_center_mae_px: Some(5.0),
            plate_center_rmse_px: Some(5.0),
            x_bias_px: Some(4.0),
            y_bias_px: Some(0.0),
            tracking_availability: Some(0.5),
            lost_frame_percentage: Some(50.0),
            max_consecutive_tracking_loss_samples: Some(1),
            max_consecutive_tracking_loss_duration_s: Some(0.1),
        };

        let aggregate = aggregate_metrics(&[first, second]);

        assert_eq!(aggregate.comparable_samples, 4);
        assert_eq!(aggregate.tracked_samples, 3);
        assert!((aggregate.plate_center_mae_px.unwrap() - 3.0).abs() < 1e-12);
        assert!((aggregate.plate_center_rmse_px.unwrap() - 3.3166247903554).abs() < 1e-12);
        assert_eq!(aggregate.tracking_availability, Some(0.75));
        assert_eq!(aggregate.lost_frame_percentage, Some(25.0));
    }
}


// Filter/kinematic evaluation extends the tracker benchmark contract without changing
// tracker metric semantics. Filter outputs are expected to preserve the reference
// timestamps one-for-one; filters must not manufacture samples to improve metrics.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FilterMetrics {
    pub comparable_position_samples: usize,
    pub comparable_velocity_samples: usize,
    pub position_mae_m: Option<f64>,
    pub position_rmse_m: Option<f64>,
    pub max_position_error_m: Option<f64>,
    pub x_bias_m: Option<f64>,
    pub y_bias_m: Option<f64>,
    pub velocity_mae_mps: Option<f64>,
    pub velocity_rmse_mps: Option<f64>,
    pub ground_truth_peak_speed_mps: Option<f64>,
    pub filtered_peak_speed_mps: Option<f64>,
    pub peak_attenuation_mps: Option<f64>,
    pub peak_attenuation_fraction: Option<f64>,
    pub peak_timing_shift_s: Option<f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum FilterBenchmarkError {
    LengthMismatch { reference: usize, filtered: usize },
    InvalidReferenceSample { index: usize, reason: String },
    InvalidFilteredSample { index: usize, reason: String },
    TimestampMismatch {
        index: usize,
        reference_s: f64,
        filtered_s: f64,
    },
    NonIncreasingTimestamp { previous_index: usize, index: usize },
    Kinematics(String),
}

impl fmt::Display for FilterBenchmarkError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::LengthMismatch {
                reference,
                filtered,
            } => write!(
                formatter,
                "filter benchmark requires one output per observed input/reference sample; reference={reference}, filtered={filtered}"
            ),
            Self::InvalidReferenceSample { index, reason } => {
                write!(formatter, "reference sample {index} is invalid: {reason}")
            }
            Self::InvalidFilteredSample { index, reason } => {
                write!(formatter, "filtered sample {index} is invalid: {reason}")
            }
            Self::TimestampMismatch {
                index,
                reference_s,
                filtered_s,
            } => write!(
                formatter,
                "filtered timestamp at index {index} changed from authoritative reference timestamp {reference_s} to {filtered_s}"
            ),
            Self::NonIncreasingTimestamp {
                previous_index,
                index,
            } => write!(
                formatter,
                "filter benchmark timestamps must be strictly increasing (indices {previous_index} and {index})"
            ),
            Self::Kinematics(reason) => write!(formatter, "velocity derivation failed: {reason}"),
        }
    }
}

impl std::error::Error for FilterBenchmarkError {}

pub fn evaluate_filter_case(
    reference: &[crate::trajectory::MetricPositionSample],
    filtered: &[crate::trajectory::MetricPositionSample],
) -> Result<FilterMetrics, FilterBenchmarkError> {
    if reference.len() != filtered.len() {
        return Err(FilterBenchmarkError::LengthMismatch {
            reference: reference.len(),
            filtered: filtered.len(),
        });
    }

    if reference.is_empty() {
        return Ok(FilterMetrics {
            comparable_position_samples: 0,
            comparable_velocity_samples: 0,
            position_mae_m: None,
            position_rmse_m: None,
            max_position_error_m: None,
            x_bias_m: None,
            y_bias_m: None,
            velocity_mae_mps: None,
            velocity_rmse_mps: None,
            ground_truth_peak_speed_mps: None,
            filtered_peak_speed_mps: None,
            peak_attenuation_mps: None,
            peak_attenuation_fraction: None,
            peak_timing_shift_s: None,
        });
    }

    let mut position_absolute_error_sum = 0.0;
    let mut position_squared_error_sum = 0.0;
    let mut max_position_error_m = 0.0_f64;
    let mut x_bias_sum = 0.0;
    let mut y_bias_sum = 0.0;
    let mut previous_timestamp = None;

    for (index, (truth, actual)) in reference.iter().zip(filtered).enumerate() {
        truth
            .validate()
            .map_err(|error| FilterBenchmarkError::InvalidReferenceSample {
                index,
                reason: error.to_string(),
            })?;
        actual
            .validate()
            .map_err(|error| FilterBenchmarkError::InvalidFilteredSample {
                index,
                reason: error.to_string(),
            })?;

        if truth.timestamp_s != actual.timestamp_s {
            return Err(FilterBenchmarkError::TimestampMismatch {
                index,
                reference_s: truth.timestamp_s,
                filtered_s: actual.timestamp_s,
            });
        }
        if let Some(previous) = previous_timestamp {
            if truth.timestamp_s <= previous {
                return Err(FilterBenchmarkError::NonIncreasingTimestamp {
                    previous_index: index - 1,
                    index,
                });
            }
        }
        previous_timestamp = Some(truth.timestamp_s);

        let dx = actual.x_m - truth.x_m;
        let dy = actual.y_m - truth.y_m;
        let squared_error = dx.mul_add(dx, dy * dy);
        let error = squared_error.sqrt();
        position_absolute_error_sum += error;
        position_squared_error_sum += squared_error;
        max_position_error_m = max_position_error_m.max(error);
        x_bias_sum += dx;
        y_bias_sum += dy;
    }

    let reference_velocity = crate::kinematics::derive_velocity(reference)
        .map_err(|error| FilterBenchmarkError::Kinematics(format!("{error:?}")))?;
    let filtered_velocity = crate::kinematics::derive_velocity(filtered)
        .map_err(|error| FilterBenchmarkError::Kinematics(format!("{error:?}")))?;

    let mut velocity_absolute_error_sum = 0.0;
    let mut velocity_squared_error_sum = 0.0;
    let mut velocity_count = 0usize;
    let mut reference_peak: Option<(f64, f64)> = None;
    let mut filtered_peak: Option<(f64, f64)> = None;

    for (truth, actual) in reference_velocity.iter().zip(&filtered_velocity) {
        let (Some(truth_vx), Some(truth_vy), Some(actual_vx), Some(actual_vy)) =
            (truth.vx_mps, truth.vy_mps, actual.vx_mps, actual.vy_mps)
        else {
            continue;
        };

        let dvx = actual_vx - truth_vx;
        let dvy = actual_vy - truth_vy;
        let squared_error = dvx.mul_add(dvx, dvy * dvy);
        velocity_absolute_error_sum += squared_error.sqrt();
        velocity_squared_error_sum += squared_error;
        velocity_count += 1;

        let truth_speed = truth_vx.hypot(truth_vy);
        let actual_speed = actual_vx.hypot(actual_vy);
        if reference_peak
            .as_ref()
            .is_none_or(|(speed, _)| truth_speed > *speed)
        {
            reference_peak = Some((truth_speed, truth.timestamp_s));
        }
        if filtered_peak
            .as_ref()
            .is_none_or(|(speed, _)| actual_speed > *speed)
        {
            filtered_peak = Some((actual_speed, actual.timestamp_s));
        }
    }

    let position_count = reference.len() as f64;
    let velocity_denominator = velocity_count as f64;
    let ground_truth_peak_speed_mps = reference_peak.map(|value| value.0);
    let filtered_peak_speed_mps = filtered_peak.map(|value| value.0);
    let peak_attenuation_mps =
        ground_truth_peak_speed_mps.zip(filtered_peak_speed_mps).map(|(truth, actual)| truth - actual);
    let peak_attenuation_fraction = peak_attenuation_mps.zip(ground_truth_peak_speed_mps).and_then(
        |(attenuation, truth)| (truth > f64::EPSILON).then_some(attenuation / truth),
    );
    let peak_timing_shift_s = reference_peak
        .zip(filtered_peak)
        .map(|(truth, actual)| actual.1 - truth.1);

    Ok(FilterMetrics {
        comparable_position_samples: reference.len(),
        comparable_velocity_samples: velocity_count,
        position_mae_m: Some(position_absolute_error_sum / position_count),
        position_rmse_m: Some((position_squared_error_sum / position_count).sqrt()),
        max_position_error_m: Some(max_position_error_m),
        x_bias_m: Some(x_bias_sum / position_count),
        y_bias_m: Some(y_bias_sum / position_count),
        velocity_mae_mps: (velocity_count > 0)
            .then_some(velocity_absolute_error_sum / velocity_denominator),
        velocity_rmse_mps: (velocity_count > 0)
            .then_some((velocity_squared_error_sum / velocity_denominator).sqrt()),
        ground_truth_peak_speed_mps,
        filtered_peak_speed_mps,
        peak_attenuation_mps,
        peak_attenuation_fraction,
        peak_timing_shift_s,
    })
}

#[cfg(test)]
mod filter_benchmark_tests {
    use super::*;

    fn sample(timestamp_s: f64, x_m: f64) -> crate::trajectory::MetricPositionSample {
        crate::trajectory::MetricPositionSample {
            timestamp_s,
            x_m,
            y_m: 0.0,
            confidence: 1.0,
        }
    }

    #[test]
    fn filter_metrics_measure_position_velocity_peak_and_shift() {
        let reference = vec![
            sample(0.0, 0.0),
            sample(1.0, 1.0),
            sample(2.0, 2.0),
        ];
        let filtered = vec![
            sample(0.0, 0.0),
            sample(1.0, 0.5),
            sample(2.0, 1.5),
        ];

        let metrics = evaluate_filter_case(&reference, &filtered).unwrap();
        assert_eq!(metrics.comparable_position_samples, 3);
        assert_eq!(metrics.comparable_velocity_samples, 2);
        assert!((metrics.position_mae_m.unwrap() - (1.0 / 3.0)).abs() < 1.0e-12);
        assert!((metrics.velocity_mae_mps.unwrap() - 0.25).abs() < 1.0e-12);
        assert_eq!(metrics.ground_truth_peak_speed_mps, Some(1.0));
        assert_eq!(metrics.filtered_peak_speed_mps, Some(1.0));
        assert_eq!(metrics.peak_attenuation_mps, Some(0.0));
        assert_eq!(metrics.peak_timing_shift_s, Some(1.0));
    }

    #[test]
    fn filter_metrics_reject_timestamp_changes_or_synthesized_samples() {
        let reference = vec![sample(0.0, 0.0), sample(1.0, 1.0)];
        let too_many = vec![
            sample(0.0, 0.0),
            sample(0.5, 0.5),
            sample(1.0, 1.0),
        ];
        assert!(matches!(
            evaluate_filter_case(&reference, &too_many),
            Err(FilterBenchmarkError::LengthMismatch { .. })
        ));

        let shifted = vec![sample(0.0, 0.0), sample(1.1, 1.0)];
        assert!(matches!(
            evaluate_filter_case(&reference, &shifted),
            Err(FilterBenchmarkError::TimestampMismatch { .. })
        ));
    }
}
