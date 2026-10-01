use serde::{Deserialize, Serialize};
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PixelObservation {
    pub timestamp_s: f64,
    pub x_px: f64,
    pub y_px: f64,
    pub confidence: f32,
}

impl PixelObservation {
    pub fn validate(self) -> Result<(), TrajectoryValidationError> {
        validate_timestamp(self.timestamp_s)?;
        validate_finite("x_px", self.x_px)?;
        validate_finite("y_px", self.y_px)?;
        validate_confidence(self.confidence)?;
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MetricPositionSample {
    pub timestamp_s: f64,
    pub x_m: f64,
    pub y_m: f64,
    pub confidence: f32,
}

impl MetricPositionSample {
    pub fn validate(self) -> Result<(), TrajectoryValidationError> {
        validate_timestamp(self.timestamp_s)?;
        validate_finite("x_m", self.x_m)?;
        validate_finite("y_m", self.y_m)?;
        validate_confidence(self.confidence)?;
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct KinematicSample {
    pub timestamp_s: f64,
    pub x_m: f64,
    pub y_m: f64,
    pub vx_mps: Option<f64>,
    pub vy_mps: Option<f64>,
    pub confidence: f32,
}

impl KinematicSample {
    pub fn validate(self) -> Result<(), TrajectoryValidationError> {
        validate_timestamp(self.timestamp_s)?;
        validate_finite("x_m", self.x_m)?;
        validate_finite("y_m", self.y_m)?;
        if let Some(value) = self.vx_mps {
            validate_finite("vx_mps", value)?;
        }
        if let Some(value) = self.vy_mps {
            validate_finite("vy_mps", value)?;
        }
        validate_confidence(self.confidence)?;
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum TrajectoryValidationError {
    NonFiniteTimestamp { value: f64 },
    NegativeTimestamp { value: f64 },
    NonFiniteValue { field: &'static str, value: f64 },
    NonFiniteConfidence { value: f32 },
    ConfidenceOutOfRange { value: f32 },
}

impl fmt::Display for TrajectoryValidationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NonFiniteTimestamp { value } => {
                write!(formatter, "timestamp must be finite, got {value}")
            }
            Self::NegativeTimestamp { value } => {
                write!(formatter, "timestamp must be non-negative, got {value}")
            }
            Self::NonFiniteValue { field, value } => {
                write!(formatter, "{field} must be finite, got {value}")
            }
            Self::NonFiniteConfidence { value } => {
                write!(formatter, "confidence must be finite, got {value}")
            }
            Self::ConfidenceOutOfRange { value } => {
                write!(formatter, "confidence must be within [0, 1], got {value}")
            }
        }
    }
}

impl std::error::Error for TrajectoryValidationError {}

fn validate_timestamp(value: f64) -> Result<(), TrajectoryValidationError> {
    if !value.is_finite() {
        return Err(TrajectoryValidationError::NonFiniteTimestamp { value });
    }
    if value < 0.0 {
        return Err(TrajectoryValidationError::NegativeTimestamp { value });
    }
    Ok(())
}

fn validate_finite(field: &'static str, value: f64) -> Result<(), TrajectoryValidationError> {
    if !value.is_finite() {
        return Err(TrajectoryValidationError::NonFiniteValue { field, value });
    }
    Ok(())
}

fn validate_confidence(value: f32) -> Result<(), TrajectoryValidationError> {
    if !value.is_finite() {
        return Err(TrajectoryValidationError::NonFiniteConfidence { value });
    }
    if !(0.0..=1.0).contains(&value) {
        return Err(TrajectoryValidationError::ConfidenceOutOfRange { value });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_non_finite_and_out_of_range_values() {
        assert!(PixelObservation {
            timestamp_s: 0.0,
            x_px: f64::NAN,
            y_px: 2.0,
            confidence: 0.9,
        }
        .validate()
        .is_err());

        assert!(MetricPositionSample {
            timestamp_s: 0.0,
            x_m: 0.0,
            y_m: 0.0,
            confidence: 1.1,
        }
        .validate()
        .is_err());

        assert!(KinematicSample {
            timestamp_s: 0.0,
            x_m: 0.0,
            y_m: 0.0,
            vx_mps: Some(f64::INFINITY),
            vy_mps: None,
            confidence: 1.0,
        }
        .validate()
        .is_err());
    }
}
