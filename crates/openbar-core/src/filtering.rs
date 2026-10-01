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
    use crate::test_utils::sample_metric_position;

    #[test]
    fn moving_average_preserves_timestamps() {
        let input = [
            sample_metric_position(0.0, 0.0, 0.0, 1.0),
            sample_metric_position(1.0, 3.0, 6.0, 1.0),
            sample_metric_position(2.0, 6.0, 12.0, 1.0),
        ];

        let output = moving_average(&input, 3).unwrap();
        assert_eq!(output.len(), input.len());
        assert_eq!(output[1].timestamp_s, 1.0);
        assert_eq!(output[1].x_m, 3.0);
        assert_eq!(output[1].y_m, 6.0);
    }
}
