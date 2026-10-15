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
    fn test_sample_metric_position_construction_and_validation() {
        let sample = sample_metric_position(1.5, -2.0, 3.25, 0.95);
        assert_eq!(sample.timestamp_s, 1.5);
        assert_eq!(sample.x_m, -2.0);
        assert_eq!(sample.y_m, 3.25);
        assert_eq!(sample.confidence, 0.95);

        assert!(sample.validate().is_ok());
    }
}
