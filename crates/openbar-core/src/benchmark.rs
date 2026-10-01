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
