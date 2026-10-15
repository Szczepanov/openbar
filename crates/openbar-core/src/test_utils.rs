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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn constructs_metric_position_sample_with_expected_fields() {
        let sample = sample_metric_position(1.25, -0.5, 2.75, 0.95);
        assert_eq!(sample.timestamp_s, 1.25);
        assert_eq!(sample.x_m, -0.5);
        assert_eq!(sample.y_m, 2.75);
        assert_eq!(sample.confidence, 0.95);
    }

    #[test]
    fn constructed_sample_is_valid() {
        let sample = sample_metric_position(0.0, 0.0, 0.0, 1.0);
        assert!(sample.validate().is_ok());

        let boundary_sample = sample_metric_position(10.0, -100.0, 100.0, 0.0);
        assert!(boundary_sample.validate().is_ok());
    }
}
