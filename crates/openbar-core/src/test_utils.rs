use crate::trajectory::MetricPositionSample;

/// Shared test helper to construct a `MetricPositionSample`.
pub fn sample_metric_position(
    timestamp_s: f64,
    x_m: f64,
    y_m: f64,
    confidence: f32,
) -> MetricPositionSample {
    MetricPositionSample {
        timestamp_s,
        x_m,
        y_m,
        confidence,
    }
}
