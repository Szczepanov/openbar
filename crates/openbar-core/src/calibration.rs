#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PlateCalibration {
    pub diameter_m: f64,
    pub diameter_px: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CalibrationError {
    NonPositiveDiameterMetres,
    NonPositiveDiameterPixels,
}

impl PlateCalibration {
    pub fn metres_per_pixel(self) -> Result<f64, CalibrationError> {
        if self.diameter_m <= 0.0 {
            return Err(CalibrationError::NonPositiveDiameterMetres);
        }
        if self.diameter_px <= 0.0 {
            return Err(CalibrationError::NonPositiveDiameterPixels);
        }
        Ok(self.diameter_m / self.diameter_px)
    }

    pub fn pixels_to_metres(self, pixels: f64) -> Result<f64, CalibrationError> {
        Ok(pixels * self.metres_per_pixel()?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_standard_plate_scale() {
        let calibration = PlateCalibration {
            diameter_m: 0.45,
            diameter_px: 244.0,
        };

        let scale = calibration.metres_per_pixel().unwrap();
        assert!((scale - 0.001_844_262_295).abs() < 1e-12);
        assert!((calibration.pixels_to_metres(110.0).unwrap() - 0.202_868_852_459).abs() < 1e-12);
    }

    #[test]
    fn rejects_invalid_calibration() {
        assert_eq!(
            PlateCalibration {
                diameter_m: 0.0,
                diameter_px: 100.0,
            }
            .metres_per_pixel(),
            Err(CalibrationError::NonPositiveDiameterMetres)
        );
    }
}
