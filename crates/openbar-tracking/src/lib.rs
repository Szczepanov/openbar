use openbar_core::benchmark::{TrackerPrediction, TrackerPredictionState};
use openbar_core::manual_seed::{ManualTargetSeed, PixelBoundingBox, PixelPoint};
use std::collections::BTreeMap;
use std::fmt;

mod contrast;
mod template;

pub use contrast::{LocalContrastConfig, LocalContrastTracker};
pub use template::{TemplateMatchConfig, TemplateMatchTracker};

#[derive(Debug, Clone, PartialEq)]
pub struct GrayFrame {
    width_px: u32,
    height_px: u32,
    pixels: Vec<u8>,
}

impl GrayFrame {
    pub fn try_new(width_px: u32, height_px: u32, pixels: Vec<u8>) -> Result<Self, TrackerError> {
        if width_px == 0
            || height_px == 0
            || width_px > i32::MAX as u32
            || height_px > i32::MAX as u32
        {
            return Err(TrackerError::InvalidFrameDimensions {
                width_px,
                height_px,
            });
        }
        let expected = (width_px as usize)
            .checked_mul(height_px as usize)
            .ok_or(TrackerError::FrameBufferSizeOverflow)?;
        if pixels.len() != expected {
            return Err(TrackerError::InvalidFrameBufferLength {
                expected,
                actual: pixels.len(),
            });
        }
        Ok(Self {
            width_px,
            height_px,
            pixels,
        })
    }
}

pub trait GrayscaleImage {
    fn width_px(&self) -> u32;
    fn height_px(&self) -> u32;
    fn intensity(&self, x_px: u32, y_px: u32) -> u8;
}

impl GrayscaleImage for GrayFrame {
    fn width_px(&self) -> u32 {
        self.width_px
    }
    fn height_px(&self) -> u32 {
        self.height_px
    }

    fn intensity(&self, x_px: u32, y_px: u32) -> u8 {
        self.pixels[y_px as usize * self.width_px as usize + x_px as usize]
    }
}

#[derive(Clone, Copy)]
pub struct FrameSample<'a> {
    pub timestamp_s: f64,
    pub frame_index: Option<u64>,
    pub image: &'a dyn GrayscaleImage,
}

#[derive(Debug, Clone, PartialEq)]
pub struct TrackerIdentity {
    pub id: String,
    pub implementation: String,
    pub version: String,
    pub config: BTreeMap<String, String>,
    pub confidence_semantics: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrackerLossReason {
    PoorMatch,
    InsufficientContrast,
    TargetOutsideFrame,
    NoCandidate,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrackerVisibilityState {
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum TrackerObservationState {
    Tracked { center: PixelPoint, confidence: f32 },
    LowConfidence { center: PixelPoint, confidence: f32 },
    Lost { reason: TrackerLossReason },
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TrackerDiagnostics {
    pub quality_score: Option<f64>,
    pub displacement_px: Option<f64>,
    pub reacquired_after_loss: bool,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TrackerObservation {
    pub timestamp_s: f64,
    pub frame_index: Option<u64>,
    pub state: TrackerObservationState,
    pub visibility: TrackerVisibilityState,
    pub target_bounds_px: Option<PixelBoundingBox>,
    pub diagnostics: TrackerDiagnostics,
}

#[derive(Debug, Clone, PartialEq)]
pub struct TrackerRun {
    pub tracker: TrackerIdentity,
    pub seed_timestamp_s: f64,
    pub observations: Vec<TrackerObservation>,
}

impl TrackerRun {
    pub fn benchmark_predictions(&self) -> Vec<TrackerPrediction> {
        self.observations
            .iter()
            .map(|observation| TrackerPrediction {
                timestamp_s: observation.timestamp_s,
                state: match observation.state {
                    TrackerObservationState::Tracked { center, confidence }
                    | TrackerObservationState::LowConfidence { center, confidence } => {
                        TrackerPredictionState::Tracked { center, confidence }
                    }
                    TrackerObservationState::Lost { .. } => TrackerPredictionState::Lost,
                },
            })
            .collect()
    }
}

pub trait ManualSeedTracker {
    fn identity(&self) -> TrackerIdentity;
    fn track(
        &self,
        frames: &[FrameSample<'_>],
        seed: &ManualTargetSeed,
    ) -> Result<TrackerRun, TrackerError>;
}

#[derive(Debug, Clone, PartialEq)]
pub enum TrackerError {
    InvalidFrameDimensions {
        width_px: u32,
        height_px: u32,
    },
    FrameBufferSizeOverflow,
    InvalidFrameBufferLength {
        expected: usize,
        actual: usize,
    },
    FrameDimensionsChanged {
        index: usize,
        expected_width_px: u32,
        expected_height_px: u32,
        actual_width_px: u32,
        actual_height_px: u32,
    },
    InvalidConfiguration {
        field: &'static str,
    },
    EmptySequence,
    InvalidTimestamp {
        index: usize,
        value: f64,
    },
    NonIncreasingTimestamps {
        previous_index: usize,
        index: usize,
    },
    SeedFrameNotFound,
    SeedTargetOutsideFrame,
    InvalidSeedRadius {
        value: f64,
    },
    InsufficientSeedContrast {
        contrast: f64,
    },
}

impl fmt::Display for TrackerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidFrameDimensions { width_px, height_px } =>
                write!(f, "frame dimensions must be positive and fit signed pixel indexing, got {width_px}x{height_px}"),
            Self::FrameBufferSizeOverflow => write!(f, "frame buffer size overflow"),
            Self::InvalidFrameBufferLength { expected, actual } =>
                write!(f, "frame buffer length must be {expected}, got {actual}"),
            Self::FrameDimensionsChanged {
                index,
                expected_width_px,
                expected_height_px,
                actual_width_px,
                actual_height_px,
            } => write!(
                f,
                "frame dimensions changed at index {index}: expected {expected_width_px}x{expected_height_px}, got {actual_width_px}x{actual_height_px}"
            ),
            Self::InvalidConfiguration { field } => write!(f, "invalid tracker configuration field '{field}'"),
            Self::EmptySequence => write!(f, "tracker requires at least one frame"),
            Self::InvalidTimestamp { index, value } =>
                write!(f, "frame timestamp at index {index} must be finite and non-negative, got {value}"),
            Self::NonIncreasingTimestamps { previous_index, index } =>
                write!(f, "frame timestamps must be strictly increasing (indices {previous_index} and {index})"),
            Self::SeedFrameNotFound => write!(f, "no frame matched the manual seed timestamp within tolerance"),
            Self::SeedTargetOutsideFrame => write!(f, "manual seed target does not fit inside the seed frame"),
            Self::InvalidSeedRadius { value } => write!(f, "manual seed radius must round to a positive pixel radius, got {value}"),
            Self::InsufficientSeedContrast { contrast } =>
                write!(f, "manual seed target/background contrast {contrast:.3} is insufficient for the contrast tracker"),
        }
    }
}

impl std::error::Error for TrackerError {}

pub(crate) fn validate_search_radius(value: u32) -> Result<(), TrackerError> {
    if value == 0 || value > i32::MAX as u32 {
        Err(TrackerError::InvalidConfiguration {
            field: "search_radius_px",
        })
    } else {
        Ok(())
    }
}

pub(crate) fn validate_seed_tolerance(value: f64) -> Result<(), TrackerError> {
    if !value.is_finite() || value < 0.0 {
        Err(TrackerError::InvalidConfiguration {
            field: "seed_timestamp_tolerance_s",
        })
    } else {
        Ok(())
    }
}

pub(crate) fn validate_sequence_and_seed(
    frames: &[FrameSample<'_>],
    seed: &ManualTargetSeed,
    tolerance_s: f64,
) -> Result<usize, TrackerError> {
    if frames.is_empty() {
        return Err(TrackerError::EmptySequence);
    }
    let expected_width_px = frames[0].image.width_px();
    let expected_height_px = frames[0].image.height_px();
    if expected_width_px == 0
        || expected_height_px == 0
        || expected_width_px > i32::MAX as u32
        || expected_height_px > i32::MAX as u32
    {
        return Err(TrackerError::InvalidFrameDimensions {
            width_px: expected_width_px,
            height_px: expected_height_px,
        });
    }

    let mut best: Option<(usize, f64)> = None;
    for (index, frame) in frames.iter().enumerate() {
        let width_px = frame.image.width_px();
        let height_px = frame.image.height_px();
        if width_px == 0
            || height_px == 0
            || width_px > i32::MAX as u32
            || height_px > i32::MAX as u32
        {
            return Err(TrackerError::InvalidFrameDimensions {
                width_px,
                height_px,
            });
        }
        if width_px != expected_width_px || height_px != expected_height_px {
            return Err(TrackerError::FrameDimensionsChanged {
                index,
                expected_width_px,
                expected_height_px,
                actual_width_px: width_px,
                actual_height_px: height_px,
            });
        }
        if !frame.timestamp_s.is_finite() || frame.timestamp_s < 0.0 {
            return Err(TrackerError::InvalidTimestamp {
                index,
                value: frame.timestamp_s,
            });
        }
        if index > 0 && frames[index - 1].timestamp_s >= frame.timestamp_s {
            return Err(TrackerError::NonIncreasingTimestamps {
                previous_index: index - 1,
                index,
            });
        }
        let distance = (frame.timestamp_s - seed.timestamp_s()).abs();
        if distance <= tolerance_s && best.is_none_or(|(_, current)| distance < current) {
            best = Some((index, distance));
        }
    }
    best.map(|(index, _)| index)
        .ok_or(TrackerError::SeedFrameNotFound)
}

pub(crate) fn rounded_radius(seed: &ManualTargetSeed) -> Result<i32, TrackerError> {
    let value = seed.target().radius_px();
    let radius = value.round();
    if !radius.is_finite() || radius < 1.0 || radius > f64::from(i32::MAX) {
        return Err(TrackerError::InvalidSeedRadius { value });
    }
    Ok(radius as i32)
}

pub(crate) fn rounded_point(point: PixelPoint) -> (i32, i32) {
    (point.x_px().round() as i32, point.y_px().round() as i32)
}

pub(crate) fn contains(frame: &dyn GrayscaleImage, x: i32, y: i32) -> bool {
    x >= 0 && y >= 0 && x < frame.width_px() as i32 && y < frame.height_px() as i32
}

pub(crate) fn pixel(frame: &dyn GrayscaleImage, x: i32, y: i32) -> u8 {
    frame.intensity(x as u32, y as u32)
}

pub(crate) fn patch_fits(frame: &dyn GrayscaleImage, center: (i32, i32), radius: i32) -> bool {
    contains(frame, center.0 - radius, center.1 - radius)
        && contains(frame, center.0 + radius, center.1 + radius)
}

pub(crate) fn normalized_mean_absolute_difference(
    template: &dyn GrayscaleImage,
    template_center: (i32, i32),
    candidate: &dyn GrayscaleImage,
    candidate_center: (i32, i32),
    radius: i32,
) -> f64 {
    let mut total = 0u64;
    let mut count = 0u64;
    for dy in -radius..=radius {
        for dx in -radius..=radius {
            let left = i32::from(pixel(
                template,
                template_center.0 + dx,
                template_center.1 + dy,
            ));
            let right = i32::from(pixel(
                candidate,
                candidate_center.0 + dx,
                candidate_center.1 + dy,
            ));
            total += left.abs_diff(right) as u64;
            count += 1;
        }
    }
    total as f64 / (count as f64 * 255.0)
}

pub(crate) fn target_and_ring_means(
    frame: &dyn GrayscaleImage,
    center: (i32, i32),
    radius: i32,
) -> Option<(f64, f64)> {
    let ring_radius = radius.saturating_mul(2);
    if !patch_fits(frame, center, ring_radius) {
        return None;
    }
    let target_r2 = radius * radius;
    let ring_r2 = ring_radius * ring_radius;
    let mut target_sum = 0u64;
    let mut target_count = 0u64;
    let mut ring_sum = 0u64;
    let mut ring_count = 0u64;
    for dy in -ring_radius..=ring_radius {
        for dx in -ring_radius..=ring_radius {
            let d2 = dx * dx + dy * dy;
            if d2 <= target_r2 {
                target_sum += u64::from(pixel(frame, center.0 + dx, center.1 + dy));
                target_count += 1;
            } else if d2 <= ring_r2 {
                ring_sum += u64::from(pixel(frame, center.0 + dx, center.1 + dy));
                ring_count += 1;
            }
        }
    }
    if target_count == 0 || ring_count == 0 {
        None
    } else {
        Some((
            target_sum as f64 / target_count as f64,
            ring_sum as f64 / ring_count as f64,
        ))
    }
}

pub(crate) fn contrast_mass_in_target(
    frame: &dyn GrayscaleImage,
    center: (i32, i32),
    radius: i32,
    threshold: f64,
    dark_target: bool,
) -> (f64, f64, f64) {
    let r2 = radius * radius;
    let mut mass = 0.0;
    let mut weighted_x = 0.0;
    let mut weighted_y = 0.0;
    for dy in -radius..=radius {
        for dx in -radius..=radius {
            if dx * dx + dy * dy > r2 || !contains(frame, center.0 + dx, center.1 + dy) {
                continue;
            }
            let value = f64::from(pixel(frame, center.0 + dx, center.1 + dy));
            let weight = if dark_target {
                (threshold - value).max(0.0)
            } else {
                (value - threshold).max(0.0)
            };
            if weight > 0.0 {
                mass += weight;
                weighted_x += weight * f64::from(center.0 + dx);
                weighted_y += weight * f64::from(center.1 + dy);
            }
        }
    }
    (mass, weighted_x, weighted_y)
}

pub(crate) fn bounds_for_center(center: PixelPoint, radius: f64) -> PixelBoundingBox {
    PixelBoundingBox {
        left_px: center.x_px() - radius,
        top_px: center.y_px() - radius,
        width_px: radius * 2.0,
        height_px: radius * 2.0,
    }
}

pub(crate) fn bounds_fit(frame: &dyn GrayscaleImage, bounds: PixelBoundingBox) -> bool {
    bounds.left_px >= 0.0
        && bounds.top_px >= 0.0
        && bounds.right_px() <= f64::from(frame.width_px())
        && bounds.bottom_px() <= f64::from(frame.height_px())
}

pub(crate) fn lost_observation(
    frame: &FrameSample<'_>,
    reason: TrackerLossReason,
    quality_score: Option<f64>,
) -> TrackerObservation {
    TrackerObservation {
        timestamp_s: frame.timestamp_s,
        frame_index: frame.frame_index,
        state: TrackerObservationState::Lost { reason },
        visibility: TrackerVisibilityState::Unknown,
        target_bounds_px: None,
        diagnostics: TrackerDiagnostics {
            quality_score,
            displacement_px: None,
            reacquired_after_loss: false,
        },
    }
}

pub(crate) fn distance(left: PixelPoint, right: PixelPoint) -> f64 {
    let dx = left.x_px() - right.x_px();
    let dy = left.y_px() - right.y_px();
    dx.hypot(dy)
}
