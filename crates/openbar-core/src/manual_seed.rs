use serde::{Deserialize, Serialize};
use std::fmt;

pub const MANUAL_TARGET_SEED_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PixelCoordinateSpace {
    DisplayTopLeft,
}

pub trait SpatialFrameReference {
    fn timestamp_s(&self) -> f64;
    fn frame_index(&self) -> Option<u64>;
    fn coordinate_space(&self) -> PixelCoordinateSpace;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PixelAxis {
    X,
    Y,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PixelPoint {
    x_px: f64,
    y_px: f64,
}

impl PixelPoint {
    pub const fn new(x_px: f64, y_px: f64) -> Self {
        Self { x_px, y_px }
    }

    pub const fn x_px(self) -> f64 {
        self.x_px
    }

    pub const fn y_px(self) -> f64 {
        self.y_px
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PixelBoundingBox {
    pub left_px: f64,
    pub top_px: f64,
    pub width_px: f64,
    pub height_px: f64,
}

impl PixelBoundingBox {
    pub fn right_px(self) -> f64 {
        self.left_px + self.width_px
    }

    pub fn bottom_px(self) -> f64 {
        self.top_px + self.height_px
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlateTarget {
    center: PixelPoint,
    radius_px: f64,
}

impl PlateTarget {
    pub const fn new(center: PixelPoint, radius_px: f64) -> Self {
        Self { center, radius_px }
    }

    pub const fn center(self) -> PixelPoint {
        self.center
    }

    pub const fn radius_px(self) -> f64 {
        self.radius_px
    }

    pub fn diameter_px(self) -> f64 {
        self.radius_px * 2.0
    }

    pub fn bounding_box(self) -> PixelBoundingBox {
        let diameter_px = self.diameter_px();
        PixelBoundingBox {
            left_px: self.center.x_px - self.radius_px,
            top_px: self.center.y_px - self.radius_px,
            width_px: diameter_px,
            height_px: diameter_px,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SeedValidationContext {
    pub frame_width_px: u32,
    pub frame_height_px: u32,
    pub selected_range_start_s: f64,
    pub selected_range_end_s: f64,
    pub source_rotation_deg: u16,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManualTargetSeed {
    timestamp_s: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    frame_index: Option<u64>,
    target: PlateTarget,
    coordinate_space: PixelCoordinateSpace,
    source_rotation_deg: u16,
    #[serde(skip_serializing_if = "Option::is_none")]
    selection_confidence: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    notes: Option<String>,
}

impl ManualTargetSeed {
    pub fn try_new(
        timestamp_s: f64,
        frame_index: Option<u64>,
        target: PlateTarget,
        source_rotation_deg: u16,
        selection_confidence: Option<f32>,
        notes: Option<String>,
        context: SeedValidationContext,
    ) -> Result<Self, SeedValidationError> {
        let seed = Self {
            timestamp_s,
            frame_index,
            target,
            coordinate_space: PixelCoordinateSpace::DisplayTopLeft,
            source_rotation_deg,
            selection_confidence,
            notes,
        };
        seed.validate(context)?;
        Ok(seed)
    }

    pub fn validate(&self, context: SeedValidationContext) -> Result<(), SeedValidationError> {
        validate_context(context)?;

        if !self.timestamp_s.is_finite() {
            return Err(SeedValidationError::NonFiniteTimestamp {
                value: self.timestamp_s,
            });
        }
        if self.timestamp_s < 0.0 {
            return Err(SeedValidationError::NegativeTimestamp {
                value: self.timestamp_s,
            });
        }

        validate_coordinate(PixelAxis::X, self.target.center.x_px)?;
        validate_coordinate(PixelAxis::Y, self.target.center.y_px)?;

        if !self.target.radius_px.is_finite() {
            return Err(SeedValidationError::NonFiniteTargetRadius {
                value: self.target.radius_px,
            });
        }
        if self.target.radius_px <= 0.0 {
            return Err(SeedValidationError::NonPositiveTargetRadius {
                value: self.target.radius_px,
            });
        }

        if let Some(confidence) = self.selection_confidence {
            if !confidence.is_finite() {
                return Err(SeedValidationError::NonFiniteSelectionConfidence {
                    value: confidence,
                });
            }
            if !(0.0..=1.0).contains(&confidence) {
                return Err(SeedValidationError::SelectionConfidenceOutOfRange {
                    value: confidence,
                });
            }
        }

        validate_rotation(self.source_rotation_deg)?;
        if self.source_rotation_deg != context.source_rotation_deg {
            return Err(SeedValidationError::OrientationMismatch {
                seed_rotation_deg: self.source_rotation_deg,
                expected_rotation_deg: context.source_rotation_deg,
            });
        }

        if self.timestamp_s < context.selected_range_start_s
            || self.timestamp_s > context.selected_range_end_s
        {
            return Err(SeedValidationError::TimestampOutsideSelectedRange {
                timestamp_s: self.timestamp_s,
                start_s: context.selected_range_start_s,
                end_s: context.selected_range_end_s,
            });
        }

        validate_center_in_frame(self.target.center, context)?;
        validate_target_in_frame(self.target, context)?;

        Ok(())
    }

    pub const fn timestamp_s(&self) -> f64 {
        self.timestamp_s
    }

    pub const fn frame_index(&self) -> Option<u64> {
        self.frame_index
    }

    pub const fn target(&self) -> PlateTarget {
        self.target
    }

    pub const fn coordinate_space(&self) -> PixelCoordinateSpace {
        self.coordinate_space
    }

    pub const fn source_rotation_deg(&self) -> u16 {
        self.source_rotation_deg
    }

    pub const fn selection_confidence(&self) -> Option<f32> {
        self.selection_confidence
    }

    pub fn notes(&self) -> Option<&str> {
        self.notes.as_deref()
    }
}

impl SpatialFrameReference for ManualTargetSeed {
    fn timestamp_s(&self) -> f64 {
        self.timestamp_s
    }

    fn frame_index(&self) -> Option<u64> {
        self.frame_index
    }

    fn coordinate_space(&self) -> PixelCoordinateSpace {
        self.coordinate_space
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManualTargetSeedDocument {
    schema_version: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    fixture_id: Option<String>,
    seed: ManualTargetSeed,
}

impl ManualTargetSeedDocument {
    pub fn new(fixture_id: Option<String>, seed: ManualTargetSeed) -> Self {
        Self {
            schema_version: MANUAL_TARGET_SEED_SCHEMA_VERSION,
            fixture_id,
            seed,
        }
    }

    pub fn validate(&self, context: SeedValidationContext) -> Result<(), SeedValidationError> {
        if self.schema_version != MANUAL_TARGET_SEED_SCHEMA_VERSION {
            return Err(SeedValidationError::SchemaVersionMismatch {
                found: self.schema_version,
                expected: MANUAL_TARGET_SEED_SCHEMA_VERSION,
            });
        }
        if let Some(fixture_id) = self.fixture_id.as_deref() {
            if fixture_id.trim().is_empty() {
                return Err(SeedValidationError::EmptyFixtureId);
            }
            if !is_valid_fixture_id(fixture_id) {
                return Err(SeedValidationError::InvalidFixtureId {
                    value: fixture_id.to_owned(),
                });
            }
        }
        self.seed.validate(context)
    }

    pub const fn schema_version(&self) -> u32 {
        self.schema_version
    }

    pub fn fixture_id(&self) -> Option<&str> {
        self.fixture_id.as_deref()
    }

    pub const fn seed(&self) -> &ManualTargetSeed {
        &self.seed
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum SeedValidationError {
    InvalidFrameDimensions {
        width_px: u32,
        height_px: u32,
    },
    InvalidSelectedRange {
        start_s: f64,
        end_s: f64,
    },
    NonFiniteTimestamp {
        value: f64,
    },
    NegativeTimestamp {
        value: f64,
    },
    NonFiniteCoordinate {
        axis: PixelAxis,
        value: f64,
    },
    NonFiniteTargetRadius {
        value: f64,
    },
    NonPositiveTargetRadius {
        value: f64,
    },
    NonFiniteSelectionConfidence {
        value: f32,
    },
    SelectionConfidenceOutOfRange {
        value: f32,
    },
    UnsupportedRotation {
        rotation_deg: u16,
    },
    OrientationMismatch {
        seed_rotation_deg: u16,
        expected_rotation_deg: u16,
    },
    TimestampOutsideSelectedRange {
        timestamp_s: f64,
        start_s: f64,
        end_s: f64,
    },
    CoordinateOutsideFrame {
        axis: PixelAxis,
        value: f64,
        upper_exclusive: f64,
    },
    TargetExtendsOutsideFrame {
        bounds: PixelBoundingBox,
        frame_width_px: u32,
        frame_height_px: u32,
    },
    SchemaVersionMismatch {
        found: u32,
        expected: u32,
    },
    EmptyFixtureId,
    InvalidFixtureId {
        value: String,
    },
}

impl fmt::Display for SeedValidationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidFrameDimensions {
                width_px,
                height_px,
            } => write!(
                formatter,
                "frame dimensions must be positive, got {width_px}x{height_px}"
            ),
            Self::InvalidSelectedRange { start_s, end_s } => write!(
                formatter,
                "selected time range must be finite, non-negative, and ordered, got [{start_s}, {end_s}]"
            ),
            Self::NonFiniteTimestamp { value } => {
                write!(formatter, "seed timestamp must be finite, got {value}")
            }
            Self::NegativeTimestamp { value } => {
                write!(formatter, "seed timestamp must be non-negative, got {value}")
            }
            Self::NonFiniteCoordinate { axis, value } => {
                write!(formatter, "seed {axis:?} coordinate must be finite, got {value}")
            }
            Self::NonFiniteTargetRadius { value } => {
                write!(formatter, "target radius must be finite, got {value}")
            }
            Self::NonPositiveTargetRadius { value } => {
                write!(formatter, "target radius must be positive, got {value}")
            }
            Self::NonFiniteSelectionConfidence { value } => write!(
                formatter,
                "selection confidence must be finite when present, got {value}"
            ),
            Self::SelectionConfidenceOutOfRange { value } => write!(
                formatter,
                "selection confidence must be within [0, 1], got {value}"
            ),
            Self::UnsupportedRotation { rotation_deg } => write!(
                formatter,
                "source rotation must be one of 0, 90, 180, or 270 degrees, got {rotation_deg}"
            ),
            Self::OrientationMismatch {
                seed_rotation_deg,
                expected_rotation_deg,
            } => write!(
                formatter,
                "seed source rotation {seed_rotation_deg} does not match expected rotation {expected_rotation_deg}"
            ),
            Self::TimestampOutsideSelectedRange {
                timestamp_s,
                start_s,
                end_s,
            } => write!(
                formatter,
                "seed timestamp {timestamp_s} is outside selected range [{start_s}, {end_s}]"
            ),
            Self::CoordinateOutsideFrame {
                axis,
                value,
                upper_exclusive,
            } => write!(
                formatter,
                "seed {axis:?} coordinate {value} is outside [0, {upper_exclusive})"
            ),
            Self::TargetExtendsOutsideFrame {
                bounds,
                frame_width_px,
                frame_height_px,
            } => write!(
                formatter,
                "target bounds {bounds:?} extend outside frame {frame_width_px}x{frame_height_px}"
            ),
            Self::SchemaVersionMismatch { found, expected } => write!(
                formatter,
                "manual target seed schema version {found} is unsupported; expected {expected}"
            ),
            Self::EmptyFixtureId => write!(formatter, "fixture_id must not be blank when present"),
            Self::InvalidFixtureId { value } => write!(
                formatter,
                "fixture_id '{value}' must match ^[a-z0-9][a-z0-9._-]*$"
            ),
        }
    }
}

impl std::error::Error for SeedValidationError {}

fn is_valid_fixture_id(value: &str) -> bool {
    let mut chars = value.chars();
    let Some(first) = chars.next() else {
        return false;
    };

    (first.is_ascii_lowercase() || first.is_ascii_digit())
        && chars.all(|character| {
            character.is_ascii_lowercase()
                || character.is_ascii_digit()
                || matches!(character, '.' | '_' | '-')
        })
}

fn validate_context(context: SeedValidationContext) -> Result<(), SeedValidationError> {
    if context.frame_width_px == 0 || context.frame_height_px == 0 {
        return Err(SeedValidationError::InvalidFrameDimensions {
            width_px: context.frame_width_px,
            height_px: context.frame_height_px,
        });
    }

    if !context.selected_range_start_s.is_finite()
        || !context.selected_range_end_s.is_finite()
        || context.selected_range_start_s < 0.0
        || context.selected_range_end_s < context.selected_range_start_s
    {
        return Err(SeedValidationError::InvalidSelectedRange {
            start_s: context.selected_range_start_s,
            end_s: context.selected_range_end_s,
        });
    }

    validate_rotation(context.source_rotation_deg)
}

fn validate_rotation(rotation_deg: u16) -> Result<(), SeedValidationError> {
    if matches!(rotation_deg, 0 | 90 | 180 | 270) {
        Ok(())
    } else {
        Err(SeedValidationError::UnsupportedRotation { rotation_deg })
    }
}

fn validate_coordinate(axis: PixelAxis, value: f64) -> Result<(), SeedValidationError> {
    if value.is_finite() {
        Ok(())
    } else {
        Err(SeedValidationError::NonFiniteCoordinate { axis, value })
    }
}

/// Checks the v1 window `[0, width) x [0, height)`. Integer coordinates are pixel centres, so this
/// window is not the raster extent `[-0.5, width - 0.5]`; ADR-0007 records why v1 keeps it.
fn validate_center_in_frame(
    center: PixelPoint,
    context: SeedValidationContext,
) -> Result<(), SeedValidationError> {
    let frame_width = f64::from(context.frame_width_px);
    let frame_height = f64::from(context.frame_height_px);

    if center.x_px < 0.0 || center.x_px >= frame_width {
        return Err(SeedValidationError::CoordinateOutsideFrame {
            axis: PixelAxis::X,
            value: center.x_px,
            upper_exclusive: frame_width,
        });
    }
    if center.y_px < 0.0 || center.y_px >= frame_height {
        return Err(SeedValidationError::CoordinateOutsideFrame {
            axis: PixelAxis::Y,
            value: center.y_px,
            upper_exclusive: frame_height,
        });
    }
    Ok(())
}

fn validate_target_in_frame(
    target: PlateTarget,
    context: SeedValidationContext,
) -> Result<(), SeedValidationError> {
    let bounds = target.bounding_box();
    if bounds.left_px < 0.0
        || bounds.top_px < 0.0
        || bounds.right_px() > f64::from(context.frame_width_px)
        || bounds.bottom_px() > f64::from(context.frame_height_px)
    {
        return Err(SeedValidationError::TargetExtendsOutsideFrame {
            bounds,
            frame_width_px: context.frame_width_px,
            frame_height_px: context.frame_height_px,
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn context() -> SeedValidationContext {
        SeedValidationContext {
            frame_width_px: 1920,
            frame_height_px: 1080,
            selected_range_start_s: 1.0,
            selected_range_end_s: 3.0,
            source_rotation_deg: 0,
        }
    }

    fn valid_seed() -> ManualTargetSeed {
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
    fn accepts_valid_seed_and_exposes_geometry_helpers() {
        let seed = valid_seed();
        assert_eq!(seed.target().diameter_px(), 244.0);
        assert_eq!(
            seed.target().bounding_box(),
            PixelBoundingBox {
                left_px: 838.5,
                top_px: 578.25,
                width_px: 244.0,
                height_px: 244.0,
            }
        );
        assert_eq!(
            seed.coordinate_space(),
            PixelCoordinateSpace::DisplayTopLeft
        );
    }

    #[test]
    fn rejects_zero_and_negative_target_size() {
        for radius_px in [0.0, -1.0] {
            let error = ManualTargetSeed::try_new(
                1.25,
                None,
                PlateTarget::new(PixelPoint::new(960.0, 540.0), radius_px),
                0,
                None,
                None,
                context(),
            )
            .unwrap_err();
            assert!(matches!(
                error,
                SeedValidationError::NonPositiveTargetRadius { .. }
            ));
        }
    }

    #[test]
    fn rejects_non_finite_time_coordinates_size_and_confidence() {
        let mut seed = valid_seed();
        seed.timestamp_s = f64::NAN;
        assert!(matches!(
            seed.validate(context()),
            Err(SeedValidationError::NonFiniteTimestamp { .. })
        ));

        let mut seed = valid_seed();
        seed.target.center.x_px = f64::INFINITY;
        assert!(matches!(
            seed.validate(context()),
            Err(SeedValidationError::NonFiniteCoordinate {
                axis: PixelAxis::X,
                ..
            })
        ));

        let mut seed = valid_seed();
        seed.target.radius_px = f64::NAN;
        assert!(matches!(
            seed.validate(context()),
            Err(SeedValidationError::NonFiniteTargetRadius { .. })
        ));

        let mut seed = valid_seed();
        seed.selection_confidence = Some(f32::NAN);
        assert!(matches!(
            seed.validate(context()),
            Err(SeedValidationError::NonFiniteSelectionConfidence { .. })
        ));
    }

    #[test]
    fn rejects_negative_or_out_of_range_timestamp() {
        let negative = ManualTargetSeed::try_new(
            -0.01,
            None,
            PlateTarget::new(PixelPoint::new(960.0, 540.0), 100.0),
            0,
            None,
            None,
            context(),
        )
        .unwrap_err();
        assert!(matches!(
            negative,
            SeedValidationError::NegativeTimestamp { .. }
        ));

        let outside = ManualTargetSeed::try_new(
            3.01,
            None,
            PlateTarget::new(PixelPoint::new(960.0, 540.0), 100.0),
            0,
            None,
            None,
            context(),
        )
        .unwrap_err();
        assert!(matches!(
            outside,
            SeedValidationError::TimestampOutsideSelectedRange { .. }
        ));
    }

    #[test]
    fn accepts_target_touching_frame_boundary_and_rejects_outside_geometry() {
        let edge_context = SeedValidationContext {
            selected_range_start_s: 0.0,
            selected_range_end_s: 2.0,
            ..context()
        };
        let seed = ManualTargetSeed::try_new(
            1.0,
            None,
            PlateTarget::new(PixelPoint::new(100.0, 100.0), 100.0),
            0,
            None,
            None,
            edge_context,
        )
        .unwrap();
        assert_eq!(seed.target().bounding_box().left_px, 0.0);

        let error = ManualTargetSeed::try_new(
            1.0,
            None,
            PlateTarget::new(PixelPoint::new(99.0, 100.0), 100.0),
            0,
            None,
            None,
            edge_context,
        )
        .unwrap_err();
        assert!(matches!(
            error,
            SeedValidationError::TargetExtendsOutsideFrame { .. }
        ));
    }

    #[test]
    fn keeps_v1_frame_window_instead_of_pixel_centre_raster_extent() {
        // ADR-0007: integer coordinates are pixel centres, so the raster spans
        // [-0.5, width - 0.5]. v1 deliberately keeps the [0, width) point window and the
        // [0, width] bounds window; these cases sit in the half-pixel border where they differ.
        let ctx = context();
        assert!(validate_center_in_frame(PixelPoint::new(1919.75, 1079.75), ctx).is_ok());
        assert!(matches!(
            validate_center_in_frame(PixelPoint::new(-0.25, 540.0), ctx),
            Err(SeedValidationError::CoordinateOutsideFrame {
                axis: PixelAxis::X,
                ..
            })
        ));
        assert!(matches!(
            validate_center_in_frame(PixelPoint::new(960.0, -0.25), ctx),
            Err(SeedValidationError::CoordinateOutsideFrame {
                axis: PixelAxis::Y,
                ..
            })
        ));

        let right_edge = ManualTargetSeed::try_new(
            1.25,
            None,
            PlateTarget::new(PixelPoint::new(1820.0, 980.0), 100.0),
            0,
            None,
            None,
            ctx,
        )
        .unwrap();
        assert_eq!(right_edge.target().bounding_box().right_px(), 1920.0);
        assert_eq!(right_edge.target().bounding_box().bottom_px(), 1080.0);

        let left_border = ManualTargetSeed::try_new(
            1.25,
            None,
            PlateTarget::new(PixelPoint::new(99.75, 540.0), 100.0),
            0,
            None,
            None,
            ctx,
        )
        .unwrap_err();
        assert!(matches!(
            left_border,
            SeedValidationError::TargetExtendsOutsideFrame { .. }
        ));
    }

    #[test]
    fn rejects_center_outside_frame() {
        let error = ManualTargetSeed::try_new(
            1.25,
            None,
            PlateTarget::new(PixelPoint::new(1920.0, 540.0), 50.0),
            0,
            None,
            None,
            context(),
        )
        .unwrap_err();
        assert!(matches!(
            error,
            SeedValidationError::CoordinateOutsideFrame {
                axis: PixelAxis::X,
                ..
            }
        ));
    }

    #[test]
    fn validates_display_oriented_rotated_video_context() {
        let rotated_context = SeedValidationContext {
            frame_width_px: 1080,
            frame_height_px: 1920,
            selected_range_start_s: 0.0,
            selected_range_end_s: 4.0,
            source_rotation_deg: 90,
        };

        let seed = ManualTargetSeed::try_new(
            2.0,
            Some(120),
            PlateTarget::new(PixelPoint::new(540.0, 960.0), 100.0),
            90,
            None,
            None,
            rotated_context,
        )
        .unwrap();
        assert_eq!(seed.source_rotation_deg(), 90);

        let mismatch = seed
            .validate(SeedValidationContext {
                source_rotation_deg: 0,
                ..rotated_context
            })
            .unwrap_err();
        assert!(matches!(
            mismatch,
            SeedValidationError::OrientationMismatch { .. }
        ));
    }

    #[test]
    fn rejects_invalid_confidence_rotation_and_context() {
        let confidence_error = ManualTargetSeed::try_new(
            1.25,
            None,
            PlateTarget::new(PixelPoint::new(960.0, 540.0), 100.0),
            0,
            Some(1.1),
            None,
            context(),
        )
        .unwrap_err();
        assert!(matches!(
            confidence_error,
            SeedValidationError::SelectionConfidenceOutOfRange { .. }
        ));

        let rotation_error = ManualTargetSeed::try_new(
            1.25,
            None,
            PlateTarget::new(PixelPoint::new(960.0, 540.0), 100.0),
            45,
            None,
            None,
            context(),
        )
        .unwrap_err();
        assert!(matches!(
            rotation_error,
            SeedValidationError::UnsupportedRotation { .. }
                | SeedValidationError::OrientationMismatch { .. }
        ));

        let invalid_context = SeedValidationContext {
            selected_range_start_s: 2.0,
            selected_range_end_s: 1.0,
            ..context()
        };
        assert!(matches!(
            valid_seed().validate(invalid_context),
            Err(SeedValidationError::InvalidSelectedRange { .. })
        ));
    }

    #[test]
    fn seed_document_round_trips_and_preserves_fixture_reference() {
        let document =
            ManualTargetSeedDocument::new(Some("example-clean-side-60".to_owned()), valid_seed());
        let json = serde_json::to_string_pretty(&document).unwrap();
        let decoded: ManualTargetSeedDocument = serde_json::from_str(&json).unwrap();
        assert_eq!(decoded, document);
        decoded.validate(context()).unwrap();
        assert_eq!(decoded.fixture_id(), Some("example-clean-side-60"));

        let invalid_fixture_id =
            ManualTargetSeedDocument::new(Some("Bad fixture".to_owned()), valid_seed());
        assert!(matches!(
            invalid_fixture_id.validate(context()),
            Err(SeedValidationError::InvalidFixtureId { .. })
        ));
    }

    #[test]
    fn committed_example_deserializes_and_validates() {
        let document: ManualTargetSeedDocument = serde_json::from_str(include_str!(
            "../../../validation/examples/manual-target-seed.example.json"
        ))
        .unwrap();

        document
            .validate(SeedValidationContext {
                frame_width_px: 1920,
                frame_height_px: 1080,
                selected_range_start_s: 0.0,
                selected_range_end_s: 10.0,
                source_rotation_deg: 0,
            })
            .unwrap();

        assert_eq!(document.fixture_id(), Some("example-clean-side-60"));
        assert_eq!(document.seed().target().diameter_px(), 244.0);
    }
}
