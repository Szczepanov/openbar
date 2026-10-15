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
    fn sample_metric_position_constructs_expected_struct() {
        let timestamp_s = 1.25;
        let x_m = 0.5;
        let y_m = -1.2;
        let confidence = 0.95;

        let sample = sample_metric_position(timestamp_s, x_m, y_m, confidence);

        assert_eq!(sample.timestamp_s, timestamp_s);
        assert_eq!(sample.x_m, x_m);
        assert_eq!(sample.y_m, y_m);
        assert_eq!(sample.confidence, confidence);

        let direct = MetricPositionSample {
            timestamp_s,
            x_m,
            y_m,
            confidence,
        };
        assert_eq!(sample, direct);
    }

    #[test]
    fn sample_metric_position_handles_boundary_values() {
        let sample = sample_metric_position(0.0, 0.0, 0.0, 0.0);
        assert_eq!(sample.timestamp_s, 0.0);
        assert_eq!(sample.x_m, 0.0);
        assert_eq!(sample.y_m, 0.0);
        assert_eq!(sample.confidence, 0.0);

        let valid_boundary = sample_metric_position(100.0, -10.5, 20.25, 1.0);
        assert!(valid_boundary.validate().is_ok());
    }
}
