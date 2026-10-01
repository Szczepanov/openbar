//! Evidence-bounded recording support policy (issue #15).
//!
//! This module intentionally separates what OpenBar can execute from what M0 evidence supports.
//! The current #14 package has no held-out non-synthetic accuracy evidence, so unmeasured
//! recording dimensions stay explicit instead of being converted into guessed numeric limits.

use serde::{Deserialize, Serialize};
use std::fmt;

pub const RECORDING_SUPPORT_SCHEMA_VERSION: u32 = 1;
pub const RECORDING_SUPPORT_EVIDENCE_REF: &str = "docs/validation/M0_EVIDENCE_REPORT.md";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RecordingSupportStatus {
    Supported,
    Warning,
    Unsupported,
    Unknown,
}

impl RecordingSupportStatus {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Supported => "supported",
            Self::Warning => "warning",
            Self::Unsupported => "unsupported",
            Self::Unknown => "unknown",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CameraView {
    Side,
    Oblique45,
    Front,
    Rear,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CameraMovement {
    Fixed,
    Handheld,
    Panning,
    MovingOther,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LightingCondition {
    Good,
    Mixed,
    Low,
    Backlit,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PlateVisibilityCondition {
    Clear,
    Partial,
    Intermittent,
    Poor,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OcclusionCondition {
    None,
    Minor,
    Moderate,
    Severe,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MotionBlurCondition {
    None,
    Low,
    Moderate,
    Severe,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RecordingConditions {
    pub camera_view: CameraView,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub approx_yaw_deg: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub approx_pitch_deg: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub camera_roll_deg: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub distance_m: Option<f64>,
    pub camera_movement: CameraMovement,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub nominal_fps: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub measured_fps: Option<f64>,
    pub width_px: u32,
    pub height_px: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub plate_diameter_px: Option<f64>,
    pub lighting: LightingCondition,
    pub plate_visibility: PlateVisibilityCondition,
    pub occlusion: OcclusionCondition,
    pub motion_blur: MotionBlurCondition,
}

impl RecordingConditions {
    pub fn validate(&self) -> Result<(), RecordingSupportError> {
        validate_optional_range("approx_yaw_deg", self.approx_yaw_deg, -180.0, 180.0)?;
        validate_optional_range("approx_pitch_deg", self.approx_pitch_deg, -90.0, 90.0)?;
        validate_optional_range("camera_roll_deg", self.camera_roll_deg, -180.0, 180.0)?;
        validate_optional_positive("distance_m", self.distance_m)?;
        validate_optional_positive("nominal_fps", self.nominal_fps)?;
        validate_optional_positive("measured_fps", self.measured_fps)?;
        validate_optional_positive("plate_diameter_px", self.plate_diameter_px)?;
        if self.width_px == 0 || self.height_px == 0 {
            return Err(invalid("recording width_px and height_px must be positive"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RecordingDimension {
    CameraView,
    CameraYaw,
    CameraPitchHeight,
    CameraRoll,
    CameraDistanceFraming,
    FrameRate,
    ResolutionOrientation,
    MotionBlurShutter,
    LightingContrast,
    Compression,
    PlateSize,
    PlateVisibilityOcclusion,
    CameraStability,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RecordingSupportReason {
    NoHeldOutRealValidation,
    ViewOutsideM0SideGeometry,
    ViewUnknown,
    CameraMovementChangesReferenceFrame,
    CameraMovementUnvalidated,
    YawBoundaryUnmeasured,
    PitchHeightBoundaryUnmeasured,
    RollBoundaryUnmeasured,
    DistanceFramingBoundaryUnmeasured,
    FrameRateBoundaryUnmeasured,
    ResolutionBoundaryUnmeasured,
    MotionBlurShutterBoundaryUnmeasured,
    LightingContrastBoundaryUnmeasured,
    CompressionBoundaryUnmeasured,
    PlateSizeBoundaryUnmeasured,
    OcclusionBoundaryUnmeasured,
}

impl RecordingSupportReason {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::NoHeldOutRealValidation => "no_held_out_real_validation",
            Self::ViewOutsideM0SideGeometry => "view_outside_m0_side_geometry",
            Self::ViewUnknown => "view_unknown",
            Self::CameraMovementChangesReferenceFrame => "camera_movement_changes_reference_frame",
            Self::CameraMovementUnvalidated => "camera_movement_unvalidated",
            Self::YawBoundaryUnmeasured => "yaw_boundary_unmeasured",
            Self::PitchHeightBoundaryUnmeasured => "pitch_height_boundary_unmeasured",
            Self::RollBoundaryUnmeasured => "roll_boundary_unmeasured",
            Self::DistanceFramingBoundaryUnmeasured => "distance_framing_boundary_unmeasured",
            Self::FrameRateBoundaryUnmeasured => "frame_rate_boundary_unmeasured",
            Self::ResolutionBoundaryUnmeasured => "resolution_boundary_unmeasured",
            Self::MotionBlurShutterBoundaryUnmeasured => "motion_blur_shutter_boundary_unmeasured",
            Self::LightingContrastBoundaryUnmeasured => "lighting_contrast_boundary_unmeasured",
            Self::CompressionBoundaryUnmeasured => "compression_boundary_unmeasured",
            Self::PlateSizeBoundaryUnmeasured => "plate_size_boundary_unmeasured",
            Self::OcclusionBoundaryUnmeasured => "occlusion_boundary_unmeasured",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RecordingDimensionAssessment {
    pub dimension: RecordingDimension,
    pub status: RecordingSupportStatus,
    pub reason: RecordingSupportReason,
    pub evidence_ref: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MetricSupportAssessment {
    pub horizontal_displacement: RecordingSupportStatus,
    pub vertical_displacement: RecordingSupportStatus,
    pub plate_diameter_scale: RecordingSupportStatus,
    pub velocity: RecordingSupportStatus,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RecordingSupportAssessment {
    pub schema_version: u32,
    pub status: RecordingSupportStatus,
    pub conditions: RecordingConditions,
    pub dimensions: Vec<RecordingDimensionAssessment>,
    pub metrics: MetricSupportAssessment,
}

impl RecordingSupportAssessment {
    pub fn reason_codes(&self) -> Vec<&'static str> {
        let mut result = Vec::new();
        for dimension in &self.dimensions {
            let code = dimension.reason.as_str();
            if !result.contains(&code) {
                result.push(code);
            }
        }
        result
    }

    pub fn unsupported_reason_codes(&self) -> Vec<&'static str> {
        let mut result = Vec::new();
        for dimension in &self.dimensions {
            if dimension.status == RecordingSupportStatus::Unsupported {
                let code = dimension.reason.as_str();
                if !result.contains(&code) {
                    result.push(code);
                }
            }
        }
        result
    }
}

pub fn assess_recording_support(
    conditions: &RecordingConditions,
) -> Result<RecordingSupportAssessment, RecordingSupportError> {
    conditions.validate()?;

    let camera_view = match conditions.camera_view {
        CameraView::Side => assessment(
            RecordingDimension::CameraView,
            RecordingSupportStatus::Warning,
            RecordingSupportReason::NoHeldOutRealValidation,
        ),
        CameraView::Oblique45 | CameraView::Front | CameraView::Rear => assessment(
            RecordingDimension::CameraView,
            RecordingSupportStatus::Unsupported,
            RecordingSupportReason::ViewOutsideM0SideGeometry,
        ),
        CameraView::Unknown => assessment(
            RecordingDimension::CameraView,
            RecordingSupportStatus::Unsupported,
            RecordingSupportReason::ViewUnknown,
        ),
    };

    let camera_stability = match conditions.camera_movement {
        CameraMovement::Fixed => assessment(
            RecordingDimension::CameraStability,
            RecordingSupportStatus::Warning,
            RecordingSupportReason::NoHeldOutRealValidation,
        ),
        CameraMovement::Handheld => assessment(
            RecordingDimension::CameraStability,
            RecordingSupportStatus::Unsupported,
            RecordingSupportReason::CameraMovementUnvalidated,
        ),
        CameraMovement::Panning | CameraMovement::MovingOther => assessment(
            RecordingDimension::CameraStability,
            RecordingSupportStatus::Unsupported,
            RecordingSupportReason::CameraMovementChangesReferenceFrame,
        ),
        CameraMovement::Unknown => assessment(
            RecordingDimension::CameraStability,
            RecordingSupportStatus::Unsupported,
            RecordingSupportReason::CameraMovementUnvalidated,
        ),
    };

    let dimensions = vec![
        camera_view,
        assessment(
            RecordingDimension::CameraYaw,
            RecordingSupportStatus::Unknown,
            RecordingSupportReason::YawBoundaryUnmeasured,
        ),
        assessment(
            RecordingDimension::CameraPitchHeight,
            RecordingSupportStatus::Unknown,
            RecordingSupportReason::PitchHeightBoundaryUnmeasured,
        ),
        assessment(
            RecordingDimension::CameraRoll,
            RecordingSupportStatus::Unknown,
            RecordingSupportReason::RollBoundaryUnmeasured,
        ),
        assessment(
            RecordingDimension::CameraDistanceFraming,
            RecordingSupportStatus::Unknown,
            RecordingSupportReason::DistanceFramingBoundaryUnmeasured,
        ),
        assessment(
            RecordingDimension::FrameRate,
            RecordingSupportStatus::Unknown,
            RecordingSupportReason::FrameRateBoundaryUnmeasured,
        ),
        assessment(
            RecordingDimension::ResolutionOrientation,
            RecordingSupportStatus::Unknown,
            RecordingSupportReason::ResolutionBoundaryUnmeasured,
        ),
        assessment(
            RecordingDimension::MotionBlurShutter,
            RecordingSupportStatus::Unknown,
            RecordingSupportReason::MotionBlurShutterBoundaryUnmeasured,
        ),
        assessment(
            RecordingDimension::LightingContrast,
            RecordingSupportStatus::Unknown,
            RecordingSupportReason::LightingContrastBoundaryUnmeasured,
        ),
        assessment(
            RecordingDimension::Compression,
            RecordingSupportStatus::Unknown,
            RecordingSupportReason::CompressionBoundaryUnmeasured,
        ),
        assessment(
            RecordingDimension::PlateSize,
            RecordingSupportStatus::Unknown,
            RecordingSupportReason::PlateSizeBoundaryUnmeasured,
        ),
        assessment(
            RecordingDimension::PlateVisibilityOcclusion,
            RecordingSupportStatus::Unknown,
            RecordingSupportReason::OcclusionBoundaryUnmeasured,
        ),
        camera_stability,
    ];

    let unsupported_geometry = dimensions
        .iter()
        .any(|dimension| dimension.status == RecordingSupportStatus::Unsupported);
    let status = if unsupported_geometry {
        RecordingSupportStatus::Unsupported
    } else {
        // #14 currently provides development/reproducibility evidence, not a held-out
        // non-synthetic envelope. A runnable side/fixed clip is therefore warning-only.
        RecordingSupportStatus::Warning
    };
    let metric_status = if unsupported_geometry {
        RecordingSupportStatus::Unsupported
    } else {
        RecordingSupportStatus::Warning
    };

    Ok(RecordingSupportAssessment {
        schema_version: RECORDING_SUPPORT_SCHEMA_VERSION,
        status,
        conditions: conditions.clone(),
        dimensions,
        metrics: MetricSupportAssessment {
            horizontal_displacement: metric_status,
            vertical_displacement: metric_status,
            plate_diameter_scale: metric_status,
            velocity: metric_status,
        },
    })
}

fn assessment(
    dimension: RecordingDimension,
    status: RecordingSupportStatus,
    reason: RecordingSupportReason,
) -> RecordingDimensionAssessment {
    RecordingDimensionAssessment {
        dimension,
        status,
        reason,
        evidence_ref: RECORDING_SUPPORT_EVIDENCE_REF.to_owned(),
    }
}

fn validate_optional_range(
    name: &str,
    value: Option<f64>,
    minimum: f64,
    maximum: f64,
) -> Result<(), RecordingSupportError> {
    if let Some(value) = value {
        if !value.is_finite() || value < minimum || value > maximum {
            return Err(invalid(format!(
                "{name} must be finite and within [{minimum}, {maximum}]"
            )));
        }
    }
    Ok(())
}

fn validate_optional_positive(
    name: &str,
    value: Option<f64>,
) -> Result<(), RecordingSupportError> {
    if let Some(value) = value {
        if !value.is_finite() || value <= 0.0 {
            return Err(invalid(format!("{name} must be finite and positive")));
        }
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordingSupportError {
    message: String,
}

impl fmt::Display for RecordingSupportError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for RecordingSupportError {}

fn invalid(message: impl Into<String>) -> RecordingSupportError {
    RecordingSupportError {
        message: message.into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn side_fixed() -> RecordingConditions {
        RecordingConditions {
            camera_view: CameraView::Side,
            approx_yaw_deg: None,
            approx_pitch_deg: None,
            camera_roll_deg: None,
            distance_m: None,
            camera_movement: CameraMovement::Fixed,
            nominal_fps: Some(30.0),
            measured_fps: Some(30.0),
            width_px: 1920,
            height_px: 1080,
            plate_diameter_px: Some(180.0),
            lighting: LightingCondition::Good,
            plate_visibility: PlateVisibilityCondition::Clear,
            occlusion: OcclusionCondition::None,
            motion_blur: MotionBlurCondition::None,
        }
    }

    #[test]
    fn current_side_fixed_envelope_is_warning_not_supported() {
        let result = assess_recording_support(&side_fixed()).unwrap();
        assert_eq!(result.status, RecordingSupportStatus::Warning);
        assert_eq!(
            result.metrics.horizontal_displacement,
            RecordingSupportStatus::Warning
        );
        assert!(result
            .dimensions
            .iter()
            .any(|dimension| dimension.status == RecordingSupportStatus::Unknown));
        assert!(result
            .reason_codes()
            .contains(&"no_held_out_real_validation"));
    }

    #[test]
    fn non_side_geometry_rejects_physical_metric_interpretation() {
        for view in [CameraView::Oblique45, CameraView::Front, CameraView::Rear] {
            let mut conditions = side_fixed();
            conditions.camera_view = view;
            let result = assess_recording_support(&conditions).unwrap();
            assert_eq!(result.status, RecordingSupportStatus::Unsupported);
            assert_eq!(
                result.metrics.horizontal_displacement,
                RecordingSupportStatus::Unsupported
            );
            assert!(result
                .unsupported_reason_codes()
                .contains(&"view_outside_m0_side_geometry"));
        }
    }

    #[test]
    fn unknown_view_is_rejected_instead_of_assumed_side_on() {
        let mut conditions = side_fixed();
        conditions.camera_view = CameraView::Unknown;
        let result = assess_recording_support(&conditions).unwrap();
        assert_eq!(result.status, RecordingSupportStatus::Unsupported);
        assert!(result.unsupported_reason_codes().contains(&"view_unknown"));
    }

    #[test]
    fn moving_camera_is_rejected_because_reference_frame_is_not_fixed() {
        for movement in [
            CameraMovement::Handheld,
            CameraMovement::Panning,
            CameraMovement::MovingOther,
            CameraMovement::Unknown,
        ] {
            let mut conditions = side_fixed();
            conditions.camera_movement = movement;
            let result = assess_recording_support(&conditions).unwrap();
            assert_eq!(result.status, RecordingSupportStatus::Unsupported);
        }
    }

    #[test]
    fn yaw_values_do_not_create_an_invented_numeric_cutoff() {
        for yaw in [0.0, 1.0, 15.0, 45.0] {
            let mut conditions = side_fixed();
            conditions.approx_yaw_deg = Some(yaw);
            let result = assess_recording_support(&conditions).unwrap();
            assert_eq!(result.status, RecordingSupportStatus::Warning);
            assert!(result.reason_codes().contains(&"yaw_boundary_unmeasured"));
        }
    }

    #[test]
    fn invalid_numeric_metadata_is_rejected() {
        let mut conditions = side_fixed();
        conditions.measured_fps = Some(f64::NAN);
        assert!(assess_recording_support(&conditions).is_err());

        let mut conditions = side_fixed();
        conditions.distance_m = Some(0.0);
        assert!(assess_recording_support(&conditions).is_err());
    }
}
