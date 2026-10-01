use crate::trajectory::MetricPositionSample;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FilterError {
    ZeroWindow,
}

/// Simple baseline filter for benchmarking only.
///
/// This is not the selected production filter. M0 validation must compare it with
/// alternatives such as Savitzky-Golay and state-space/Kalman approaches.
pub fn moving_average(
    samples: &[MetricPositionSample],
    window: usize,
) -> Result<Vec<MetricPositionSample>, FilterError> {
    if window == 0 {
        return Err(FilterError::ZeroWindow);
    }
    if samples.is_empty() {
        return Ok(Vec::new());
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
            .map(|value| f64::from(value.confidence))
            .sum::<f64>()
            / count;

        output.push(MetricPositionSample {
            timestamp_s: sample.timestamp_s,
            x_m,
            y_m,
            confidence: confidence as f32,
        });
    }

    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn moving_average_preserves_timestamps() {
        let input = [
            MetricPositionSample {
                timestamp_s: 0.0,
                x_m: 0.0,
                y_m: 0.0,
                confidence: 1.0,
            },
            MetricPositionSample {
                timestamp_s: 1.0,
                x_m: 3.0,
                y_m: 6.0,
                confidence: 1.0,
            },
            MetricPositionSample {
                timestamp_s: 2.0,
                x_m: 6.0,
                y_m: 12.0,
                confidence: 1.0,
            },
        ];

        let output = moving_average(&input, 3).unwrap();
        assert_eq!(output.len(), input.len());
        assert_eq!(output[1].timestamp_s, 1.0);
        assert_eq!(output[1].x_m, 3.0);
        assert_eq!(output[1].y_m, 6.0);
    }

    #[test]
    fn moving_average_handles_empty_samples() {
        let result = moving_average(&[], 3).unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn moving_average_zero_window_returns_error() {
        let input = [MetricPositionSample {
            timestamp_s: 0.0,
            x_m: 1.0,
            y_m: 2.0,
            confidence: 1.0,
        }];

        assert_eq!(moving_average(&[], 0), Err(FilterError::ZeroWindow));
        assert_eq!(moving_average(&input, 0), Err(FilterError::ZeroWindow));
    }
}
