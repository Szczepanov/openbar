use crate::trajectory::{KinematicSample, MetricPositionSample};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KinematicsError {
    NonIncreasingTimestamp,
}

pub fn derive_velocity(
    samples: &[MetricPositionSample],
) -> Result<Vec<KinematicSample>, KinematicsError> {
    if samples.is_empty() {
        return Ok(Vec::new());
    }

    let mut output = Vec::with_capacity(samples.len());
    output.push(KinematicSample {
        timestamp_s: samples[0].timestamp_s,
        x_m: samples[0].x_m,
        y_m: samples[0].y_m,
        vx_mps: None,
        vy_mps: None,
        confidence: samples[0].confidence,
    });

    for pair in samples.windows(2) {
        let previous = pair[0];
        let current = pair[1];
        let dt = current.timestamp_s - previous.timestamp_s;

        if dt <= 0.0 {
            return Err(KinematicsError::NonIncreasingTimestamp);
        }

        output.push(KinematicSample {
            timestamp_s: current.timestamp_s,
            x_m: current.x_m,
            y_m: current.y_m,
            vx_mps: Some((current.x_m - previous.x_m) / dt),
            vy_mps: Some((current.y_m - previous.y_m) / dt),
            confidence: current.confidence.min(previous.confidence),
        });
    }

    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn derives_velocity_from_timestamps_not_nominal_fps() {
        let samples = [
            MetricPositionSample {
                timestamp_s: 0.0,
                x_m: 0.0,
                y_m: 0.0,
                confidence: 1.0,
            },
            MetricPositionSample {
                timestamp_s: 0.5,
                x_m: 1.0,
                y_m: 2.0,
                confidence: 0.9,
            },
        ];

        let result = derive_velocity(&samples).unwrap();
        assert_eq!(result[0].vx_mps, None);
        assert_eq!(result[1].vx_mps, Some(2.0));
        assert_eq!(result[1].vy_mps, Some(4.0));
        assert!((result[1].confidence - 0.9).abs() < f32::EPSILON);
    }

    #[test]
    fn rejects_non_increasing_time() {
        let samples = [
            MetricPositionSample {
                timestamp_s: 1.0,
                x_m: 0.0,
                y_m: 0.0,
                confidence: 1.0,
            },
            MetricPositionSample {
                timestamp_s: 1.0,
                x_m: 1.0,
                y_m: 1.0,
                confidence: 1.0,
            },
        ];

        assert_eq!(
            derive_velocity(&samples),
            Err(KinematicsError::NonIncreasingTimestamp)
        );
    }
}
