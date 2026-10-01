use crate::manual_seed::{ManualTargetSeed, PixelBoundingBox, PixelCoordinateSpace, PixelPoint};
use crate::math::approximately_equal;
use crate::trajectory::PixelObservation;
use serde::{Deserialize, Serialize};
use std::fmt;

pub const PLATE_DIAMETER_CALIBRATION_METHOD_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(try_from = "PlateCalibrationRepr", into = "PlateCalibrationRepr")]
pub struct PlateCalibration {
    diameter_m: f64,
    diameter_px: f64,
    metres_per_pixel: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PlateCalibrationRepr {
    diameter_m: f64,
    diameter_px: f64,
    metres_per_pixel: f64,
}

impl PlateCalibration {
    pub fn try_new(diameter_m: f64, diameter_px: f64) -> Result<Self, CalibrationError> {
        validate_diameter_m(diameter_m)?;
        validate_diameter_px(diameter_px)?;

        let metres_per_pixel = diameter_m / diameter_px;
        validate_scale(metres_per_pixel)?;

        Ok(Self {
            diameter_m,
            diameter_px,
            metres_per_pixel,
        })
    }

    pub const fn diameter_m(self) -> f64 {
        self.diameter_m
    }

    pub const fn diameter_px(self) -> f64 {
        self.diameter_px
    }

    pub const fn metres_per_pixel(self) -> f64 {
        self.metres_per_pixel
    }

    pub fn pixels_to_metres(self, pixels: f64) -> Result<f64, CalibrationError> {
        if !pixels.is_finite() {
            return Err(CalibrationError::NonFinitePixelDisplacement);
        }

        let metres = pixels * self.metres_per_pixel;
        if !metres.is_finite() {
            return Err(CalibrationError::NonFiniteConvertedDistance);
        }
        Ok(if metres == 0.0 { 0.0 } else { metres })
    }
}

impl TryFrom<PlateCalibrationRepr> for PlateCalibration {
    type Error = CalibrationError;

    fn try_from(value: PlateCalibrationRepr) -> Result<Self, Self::Error> {
        let calibration = Self::try_new(value.diameter_m, value.diameter_px)?;
        if !approximately_equal(value.metres_per_pixel, calibration.metres_per_pixel) {
            return Err(CalibrationError::InconsistentDerivedScale);
        }
        Ok(calibration)
    }
}

impl From<PlateCalibration> for PlateCalibrationRepr {
    fn from(value: PlateCalibration) -> Self {
        Self {
            diameter_m: value.diameter_m,
            diameter_px: value.diameter_px,
            metres_per_pixel: value.metres_per_pixel,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CalibrationMethod {
    PlateDiameter,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MetricCoordinateConvention {
    ReferenceCentreXRightYUp,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CalibrationQualityStatus {
    Unassessed,
    Supported,
    Warning,
    Unsupported,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CalibrationWarning {
    GeometryUnassessed,
    CameraYaw,
    CameraPitchOrHeight,
    PerspectiveOrParallax,
    LensDistortion,
    PlateForeshortening,
    CameraMovement,
    ZoomChanged,
    UnsupportedGeometry,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CalibrationQuality {
    status: CalibrationQualityStatus,
    warnings: Vec<CalibrationWarning>,
}

impl CalibrationQuality {
    pub fn unassessed() -> Self {
        Self {
            status: CalibrationQualityStatus::Unassessed,
            warnings: vec![CalibrationWarning::GeometryUnassessed],
        }
    }

    pub fn new(status: CalibrationQualityStatus, warnings: Vec<CalibrationWarning>) -> Self {
        Self { status, warnings }
    }

    pub const fn status(&self) -> CalibrationQualityStatus {
        self.status
    }

    pub fn warnings(&self) -> &[CalibrationWarning] {
        &self.warnings
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CalibrationMeasurementGeometry {
    center_px: PixelPoint,
    bounds_px: PixelBoundingBox,
}

impl CalibrationMeasurementGeometry {
    pub const fn center_px(self) -> PixelPoint {
        self.center_px
    }

    pub const fn bounds_px(self) -> PixelBoundingBox {
        self.bounds_px
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "source", rename_all = "snake_case")]
pub enum CalibrationProvenance {
    ManualTargetSeed {
        #[serde(skip_serializing_if = "Option::is_none")]
        selection_confidence: Option<f32>,
        #[serde(skip_serializing_if = "Option::is_none")]
        notes: Option<String>,
    },
    ManualPlateMeasurement {
        #[serde(skip_serializing_if = "Option::is_none")]
        notes: Option<String>,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CalibrationReference {
    timestamp_s: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    frame_index: Option<u64>,
    coordinate_space: PixelCoordinateSpace,
    source_rotation_deg: u16,
    geometry: CalibrationMeasurementGeometry,
    provenance: CalibrationProvenance,
}

impl CalibrationReference {
    pub fn from_manual_seed(seed: &ManualTargetSeed) -> Self {
        Self {
            timestamp_s: seed.timestamp_s(),
            frame_index: seed.frame_index(),
            coordinate_space: seed.coordinate_space(),
            source_rotation_deg: seed.source_rotation_deg(),
            geometry: CalibrationMeasurementGeometry {
                center_px: seed.target().center(),
                bounds_px: seed.target().bounding_box(),
            },
            provenance: CalibrationProvenance::ManualTargetSeed {
                selection_confidence: seed.selection_confidence(),
                notes: seed.notes().map(ToOwned::to_owned),
            },
        }
    }

    pub const fn timestamp_s(&self) -> f64 {
        self.timestamp_s
    }

    pub const fn frame_index(&self) -> Option<u64> {
        self.frame_index
    }

    pub const fn coordinate_space(&self) -> PixelCoordinateSpace {
        self.coordinate_space
    }

    pub const fn source_rotation_deg(&self) -> u16 {
        self.source_rotation_deg
    }

    pub const fn geometry(&self) -> CalibrationMeasurementGeometry {
        self.geometry
    }

    pub const fn provenance(&self) -> &CalibrationProvenance {
        &self.provenance
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    try_from = "PlateDiameterCalibrationRepr",
    into = "PlateDiameterCalibrationRepr"
)]
pub struct PlateDiameterCalibration {
    method: CalibrationMethod,
    method_version: u32,
    scale: PlateCalibration,
    reference: CalibrationReference,
    coordinate_convention: MetricCoordinateConvention,
    quality: CalibrationQuality,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PlateDiameterCalibrationRepr {
    method: CalibrationMethod,
    method_version: u32,
    scale: PlateCalibration,
    reference: CalibrationReference,
    coordinate_convention: MetricCoordinateConvention,
    quality: CalibrationQuality,
}

impl PlateDiameterCalibration {
    pub fn try_from_manual_seed(
        diameter_m: f64,
        seed: &ManualTargetSeed,
        quality: CalibrationQuality,
    ) -> Result<Self, CalibrationError> {
        let scale = PlateCalibration::try_new(diameter_m, seed.target().diameter_px())?;
        Self::try_new(scale, CalibrationReference::from_manual_seed(seed), quality)
    }

    pub fn try_new(
        scale: PlateCalibration,
        reference: CalibrationReference,
        quality: CalibrationQuality,
    ) -> Result<Self, CalibrationError> {
        let calibration = Self {
            method: CalibrationMethod::PlateDiameter,
            method_version: PLATE_DIAMETER_CALIBRATION_METHOD_VERSION,
            scale,
            reference,
            coordinate_convention: MetricCoordinateConvention::ReferenceCentreXRightYUp,
            quality,
        };
        calibration.validate()?;
        Ok(calibration)
    }

    pub fn validate(&self) -> Result<(), CalibrationError> {
        if self.method_version != PLATE_DIAMETER_CALIBRATION_METHOD_VERSION {
            return Err(CalibrationError::UnsupportedMethodVersion);
        }

        validate_reference(&self.reference)?;
        validate_quality(&self.quality)?;

        let bounds = self.reference.geometry.bounds_px;
        if !approximately_equal(bounds.width_px, self.scale.diameter_px)
            || !approximately_equal(bounds.height_px, self.scale.diameter_px)
        {
            return Err(CalibrationError::ReferenceGeometryDiameterMismatch);
        }

        Ok(())
    }

    pub const fn method(&self) -> CalibrationMethod {
        self.method
    }

    pub const fn method_version(&self) -> u32 {
        self.method_version
    }

    pub const fn scale(&self) -> PlateCalibration {
        self.scale
    }

    pub const fn reference(&self) -> &CalibrationReference {
        &self.reference
    }

    pub const fn coordinate_convention(&self) -> MetricCoordinateConvention {
        self.coordinate_convention
    }

    pub const fn quality(&self) -> &CalibrationQuality {
        &self.quality
    }

    pub fn pixel_displacement_to_metres(
        &self,
        dx_px: f64,
        dy_px: f64,
    ) -> Result<(f64, f64), CalibrationError> {
        if !dx_px.is_finite() || !dy_px.is_finite() {
            return Err(CalibrationError::NonFinitePixelDisplacement);
        }

        let x_m = self.scale.pixels_to_metres(dx_px)?;
        let y_m = self.scale.pixels_to_metres(-dy_px)?;
        Ok((x_m, y_m))
    }

    pub fn calibrate_observation(
        &self,
        observation: PixelObservation,
    ) -> Result<CalibratedPositionSample, CalibrationError> {
        if !observation.timestamp_s.is_finite() {
            return Err(CalibrationError::NonFiniteObservationTimestamp);
        }
        if !observation.x_px.is_finite() || !observation.y_px.is_finite() {
            return Err(CalibrationError::NonFiniteObservationCoordinate);
        }

        let origin = self.reference.geometry.center_px;
        let (x_m, y_m) = self.pixel_displacement_to_metres(
            observation.x_px - origin.x_px(),
            observation.y_px - origin.y_px(),
        )?;

        Ok(CalibratedPositionSample {
            raw: observation,
            x_m,
            y_m,
        })
    }
}

impl TryFrom<PlateDiameterCalibrationRepr> for PlateDiameterCalibration {
    type Error = CalibrationError;

    fn try_from(value: PlateDiameterCalibrationRepr) -> Result<Self, Self::Error> {
        let calibration = Self {
            method: value.method,
            method_version: value.method_version,
            scale: value.scale,
            reference: value.reference,
            coordinate_convention: value.coordinate_convention,
            quality: value.quality,
        };

        if calibration.method != CalibrationMethod::PlateDiameter {
            return Err(CalibrationError::UnsupportedCalibrationMethod);
        }
        if calibration.coordinate_convention != MetricCoordinateConvention::ReferenceCentreXRightYUp
        {
            return Err(CalibrationError::UnsupportedCoordinateConvention);
        }

        calibration.validate()?;
        Ok(calibration)
    }
}

impl From<PlateDiameterCalibration> for PlateDiameterCalibrationRepr {
    fn from(value: PlateDiameterCalibration) -> Self {
        Self {
            method: value.method,
            method_version: value.method_version,
            scale: value.scale,
            reference: value.reference,
            coordinate_convention: value.coordinate_convention,
            quality: value.quality,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CalibratedPositionSample {
    pub raw: PixelObservation,
    pub x_m: f64,
    pub y_m: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CalibrationError {
    NonFiniteDiameterMetres,
    NonPositiveDiameterMetres,
    NonFiniteDiameterPixels,
    NonPositiveDiameterPixels,
    NonFiniteDerivedScale,
    NonPositiveDerivedScale,
    InconsistentDerivedScale,
    NonFinitePixelDisplacement,
    NonFiniteConvertedDistance,
    NonFiniteReferenceTimestamp,
    NegativeReferenceTimestamp,
    NonFiniteReferenceGeometry,
    NonPositiveReferenceBounds,
    UnsupportedSourceRotation,
    ReferenceGeometryCentreMismatch,
    ReferenceGeometryDiameterMismatch,
    NonFiniteSelectionConfidence,
    SelectionConfidenceOutOfRange,
    MissingQualityWarning,
    UnsupportedCalibrationMethod,
    UnsupportedMethodVersion,
    UnsupportedCoordinateConvention,
    NonFiniteObservationTimestamp,
    NonFiniteObservationCoordinate,
}

impl fmt::Display for CalibrationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = match self {
            Self::NonFiniteDiameterMetres => "plate diameter in metres must be finite",
            Self::NonPositiveDiameterMetres => "plate diameter in metres must be positive",
            Self::NonFiniteDiameterPixels => "plate diameter in pixels must be finite",
            Self::NonPositiveDiameterPixels => "plate diameter in pixels must be positive",
            Self::NonFiniteDerivedScale => "derived metres-per-pixel scale must be finite",
            Self::NonPositiveDerivedScale => "derived metres-per-pixel scale must be positive",
            Self::InconsistentDerivedScale => {
                "persisted metres-per-pixel scale does not match the recorded diameters"
            }
            Self::NonFinitePixelDisplacement => "pixel displacement must be finite",
            Self::NonFiniteConvertedDistance => "converted metric distance must be finite",
            Self::NonFiniteReferenceTimestamp => "calibration reference timestamp must be finite",
            Self::NegativeReferenceTimestamp => {
                "calibration reference timestamp must be non-negative"
            }
            Self::NonFiniteReferenceGeometry => "calibration reference geometry must be finite",
            Self::NonPositiveReferenceBounds => {
                "calibration reference bounds must have positive width and height"
            }
            Self::UnsupportedSourceRotation => {
                "calibration source rotation must be 0, 90, 180, or 270 degrees"
            }
            Self::ReferenceGeometryCentreMismatch => {
                "calibration reference bounds must be centred on the recorded reference centre"
            }
            Self::ReferenceGeometryDiameterMismatch => {
                "reference bounds must match the observed plate diameter used for calibration"
            }
            Self::NonFiniteSelectionConfidence => {
                "manual selection confidence must be finite when present"
            }
            Self::SelectionConfidenceOutOfRange => {
                "manual selection confidence must be within [0, 1]"
            }
            Self::MissingQualityWarning => {
                "warning/unsupported calibration quality requires at least one warning flag"
            }
            Self::UnsupportedCalibrationMethod => "unsupported calibration method",
            Self::UnsupportedMethodVersion => "unsupported plate-diameter calibration version",
            Self::UnsupportedCoordinateConvention => "unsupported metric coordinate convention",
            Self::NonFiniteObservationTimestamp => "observation timestamp must be finite",
            Self::NonFiniteObservationCoordinate => "observation pixel coordinates must be finite",
        };
        formatter.write_str(message)
    }
}

impl std::error::Error for CalibrationError {}

fn validate_diameter_m(value: f64) -> Result<(), CalibrationError> {
    if !value.is_finite() {
        return Err(CalibrationError::NonFiniteDiameterMetres);
    }
    if value <= 0.0 {
        return Err(CalibrationError::NonPositiveDiameterMetres);
    }
    Ok(())
}

fn validate_diameter_px(value: f64) -> Result<(), CalibrationError> {
    if !value.is_finite() {
        return Err(CalibrationError::NonFiniteDiameterPixels);
    }
    if value <= 0.0 {
        return Err(CalibrationError::NonPositiveDiameterPixels);
    }
    Ok(())
}

fn validate_scale(value: f64) -> Result<(), CalibrationError> {
    if !value.is_finite() {
        return Err(CalibrationError::NonFiniteDerivedScale);
    }
    if value <= 0.0 {
        return Err(CalibrationError::NonPositiveDerivedScale);
    }
    Ok(())
}

fn validate_reference(reference: &CalibrationReference) -> Result<(), CalibrationError> {
    if !reference.timestamp_s.is_finite() {
        return Err(CalibrationError::NonFiniteReferenceTimestamp);
    }
    if reference.timestamp_s < 0.0 {
        return Err(CalibrationError::NegativeReferenceTimestamp);
    }

    let center = reference.geometry.center_px;
    let bounds = reference.geometry.bounds_px;
    if !center.x_px().is_finite()
        || !center.y_px().is_finite()
        || !bounds.left_px.is_finite()
        || !bounds.top_px.is_finite()
        || !bounds.width_px.is_finite()
        || !bounds.height_px.is_finite()
    {
        return Err(CalibrationError::NonFiniteReferenceGeometry);
    }
    if bounds.width_px <= 0.0 || bounds.height_px <= 0.0 {
        return Err(CalibrationError::NonPositiveReferenceBounds);
    }
    if !matches!(reference.source_rotation_deg, 0 | 90 | 180 | 270) {
        return Err(CalibrationError::UnsupportedSourceRotation);
    }

    let bounds_center_x = bounds.left_px + bounds.width_px / 2.0;
    let bounds_center_y = bounds.top_px + bounds.height_px / 2.0;
    if !approximately_equal(bounds_center_x, center.x_px())
        || !approximately_equal(bounds_center_y, center.y_px())
    {
        return Err(CalibrationError::ReferenceGeometryCentreMismatch);
    }

    if let CalibrationProvenance::ManualTargetSeed {
        selection_confidence: Some(confidence),
        ..
    } = &reference.provenance
    {
        if !confidence.is_finite() {
            return Err(CalibrationError::NonFiniteSelectionConfidence);
        }
        if !(0.0..=1.0).contains(confidence) {
            return Err(CalibrationError::SelectionConfidenceOutOfRange);
        }
    }

    Ok(())
}

fn validate_quality(quality: &CalibrationQuality) -> Result<(), CalibrationError> {
    if matches!(
        quality.status,
        CalibrationQualityStatus::Warning | CalibrationQualityStatus::Unsupported
    ) && quality.warnings.is_empty()
    {
        return Err(CalibrationError::MissingQualityWarning);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::manual_seed::{PlateTarget, SeedValidationContext};

    fn context() -> SeedValidationContext {
        SeedValidationContext {
            frame_width_px: 1920,
            frame_height_px: 1080,
            selected_range_start_s: 0.0,
            selected_range_end_s: 10.0,
            source_rotation_deg: 0,
        }
    }

    fn seed() -> ManualTargetSeed {
        ManualTargetSeed::try_new(
            1.25,
            Some(75),
            PlateTarget::new(PixelPoint::new(960.5, 700.25), 122.0),
            0,
            Some(0.95),
            Some("manual plate selection".to_owned()),
            context(),
        )
        .unwrap()
    }

    #[test]
    fn converts_standard_plate_scale() {
        let calibration = PlateCalibration::try_new(0.45, 244.0).unwrap();

        assert!((calibration.metres_per_pixel() - 0.001_844_262_295).abs() < 1e-12);
        assert!((calibration.pixels_to_metres(110.0).unwrap() - 0.202_868_852_459).abs() < 1e-12);
    }

    #[test]
    fn rejects_zero_negative_and_non_finite_diameters() {
        for value in [0.0, -1.0] {
            assert_eq!(
                PlateCalibration::try_new(value, 100.0),
                Err(CalibrationError::NonPositiveDiameterMetres)
            );
            assert_eq!(
                PlateCalibration::try_new(0.45, value),
                Err(CalibrationError::NonPositiveDiameterPixels)
            );
        }

        for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert_eq!(
                PlateCalibration::try_new(value, 100.0),
                Err(CalibrationError::NonFiniteDiameterMetres)
            );
            assert_eq!(
                PlateCalibration::try_new(0.45, value),
                Err(CalibrationError::NonFiniteDiameterPixels)
            );
        }
    }

    #[test]
    fn normalizes_signed_zero_in_metric_conversion() {
        let calibration = PlateCalibration::try_new(0.45, 200.0).unwrap();

        let positive_zero = calibration.pixels_to_metres(0.0).unwrap();
        let negative_zero = calibration.pixels_to_metres(-0.0).unwrap();

        assert_eq!(positive_zero.to_bits(), 0.0_f64.to_bits());
        assert_eq!(negative_zero.to_bits(), 0.0_f64.to_bits());
    }

    #[test]
    fn accepts_small_and_large_finite_scales() {
        let small = PlateCalibration::try_new(1.0e-9, 1.0e9).unwrap();
        let large = PlateCalibration::try_new(1.0e9, 1.0e-9).unwrap();

        assert_eq!(small.metres_per_pixel(), 1.0e-18);
        assert_eq!(large.metres_per_pixel(), 1.0e18);
    }

    #[test]
    fn manual_seed_calibration_preserves_reference_and_provenance() {
        let seed = seed();
        let calibration = PlateDiameterCalibration::try_from_manual_seed(
            0.45,
            &seed,
            CalibrationQuality::unassessed(),
        )
        .unwrap();

        assert_eq!(calibration.method(), CalibrationMethod::PlateDiameter);
        assert_eq!(calibration.method_version(), 1);
        assert_eq!(
            calibration.scale().diameter_px(),
            seed.target().diameter_px()
        );
        assert_eq!(calibration.reference().timestamp_s(), seed.timestamp_s());
        assert_eq!(calibration.reference().frame_index(), seed.frame_index());
        assert_eq!(
            calibration.reference().geometry().bounds_px(),
            seed.target().bounding_box()
        );
        assert!(matches!(
            calibration.reference().provenance(),
            CalibrationProvenance::ManualTargetSeed { .. }
        ));
        assert_eq!(
            calibration.coordinate_convention(),
            MetricCoordinateConvention::ReferenceCentreXRightYUp
        );
    }

    #[test]
    fn calibrated_axes_are_reference_centred_x_right_y_up_and_raw_is_preserved() {
        let calibration = PlateDiameterCalibration::try_from_manual_seed(
            0.45,
            &seed(),
            CalibrationQuality::unassessed(),
        )
        .unwrap();
        let raw = PixelObservation {
            timestamp_s: 1.5,
            x_px: 1070.5,
            y_px: 590.25,
            confidence: 0.9,
        };

        let sample = calibration.calibrate_observation(raw).unwrap();
        let expected = 0.202_868_852_459;

        assert_eq!(sample.raw, raw);
        assert_eq!(sample.raw.timestamp_s, 1.5);
        assert!((sample.x_m - expected).abs() < 1e-12);
        assert!((sample.y_m - expected).abs() < 1e-12);
    }

    #[test]
    fn downward_pixel_motion_is_negative_metric_y() {
        let calibration = PlateDiameterCalibration::try_from_manual_seed(
            0.45,
            &seed(),
            CalibrationQuality::unassessed(),
        )
        .unwrap();

        let (x_m, y_m) = calibration
            .pixel_displacement_to_metres(10.0, 10.0)
            .unwrap();
        assert!(x_m > 0.0);
        assert!(y_m < 0.0);
    }

    #[test]
    fn rejects_non_finite_displacements_and_observations() {
        let calibration = PlateDiameterCalibration::try_from_manual_seed(
            0.45,
            &seed(),
            CalibrationQuality::unassessed(),
        )
        .unwrap();

        assert_eq!(
            calibration.pixel_displacement_to_metres(f64::NAN, 0.0),
            Err(CalibrationError::NonFinitePixelDisplacement)
        );

        let error = calibration
            .calibrate_observation(PixelObservation {
                timestamp_s: 1.5,
                x_px: f64::INFINITY,
                y_px: 590.25,
                confidence: 0.9,
            })
            .unwrap_err();
        assert_eq!(error, CalibrationError::NonFiniteObservationCoordinate);
    }

    #[test]
    fn calibration_round_trip_persists_scale_reference_quality_and_provenance() {
        let calibration = PlateDiameterCalibration::try_from_manual_seed(
            0.45,
            &seed(),
            CalibrationQuality::new(
                CalibrationQualityStatus::Warning,
                vec![CalibrationWarning::CameraYaw],
            ),
        )
        .unwrap();

        let json = serde_json::to_string_pretty(&calibration).unwrap();
        let decoded: PlateDiameterCalibration = serde_json::from_str(&json).unwrap();

        assert_eq!(decoded, calibration);
        assert!(json.contains("\"metres_per_pixel\""));
        assert!(json.contains("\"manual_target_seed\""));
        assert!(json.contains("\"camera_yaw\""));
    }

    #[test]
    fn deserialization_rejects_tampered_derived_scale() {
        let calibration = PlateCalibration::try_new(0.45, 244.0).unwrap();
        let mut value = serde_json::to_value(calibration).unwrap();
        value["metres_per_pixel"] = serde_json::json!(42.0);

        let error = serde_json::from_value::<PlateCalibration>(value).unwrap_err();
        assert!(error
            .to_string()
            .contains("persisted metres-per-pixel scale"));
    }

    #[test]
    fn deserialization_rejects_invalid_reference_rotation_and_off_centre_bounds() {
        let calibration = PlateDiameterCalibration::try_from_manual_seed(
            0.45,
            &seed(),
            CalibrationQuality::unassessed(),
        )
        .unwrap();

        let mut invalid_rotation = serde_json::to_value(&calibration).unwrap();
        invalid_rotation["reference"]["source_rotation_deg"] = serde_json::json!(45);
        let rotation_error =
            serde_json::from_value::<PlateDiameterCalibration>(invalid_rotation).unwrap_err();
        assert!(rotation_error
            .to_string()
            .contains("source rotation must be 0, 90, 180, or 270"));

        let mut off_centre = serde_json::to_value(&calibration).unwrap();
        off_centre["reference"]["geometry"]["bounds_px"]["left_px"] = serde_json::json!(0.0);
        let geometry_error =
            serde_json::from_value::<PlateDiameterCalibration>(off_centre).unwrap_err();
        assert!(geometry_error
            .to_string()
            .contains("bounds must be centred on the recorded reference centre"));
    }

    #[test]
    fn warning_and_unsupported_quality_require_a_reason() {
        for status in [
            CalibrationQualityStatus::Warning,
            CalibrationQualityStatus::Unsupported,
        ] {
            let error = PlateDiameterCalibration::try_from_manual_seed(
                0.45,
                &seed(),
                CalibrationQuality::new(status, vec![]),
            )
            .unwrap_err();
            assert_eq!(error, CalibrationError::MissingQualityWarning);
        }
    }
}
