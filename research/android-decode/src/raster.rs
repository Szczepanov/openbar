//! Host-testable luma repacking: crop, de-stride, rotate to display orientation, expand range.
//!
//! MediaCodec hands back a padded Y plane (`stride` >= width, `slice_height` >= height) plus a
//! crop rectangle. The ADR-0006 contract wants a tightly packed, display-oriented, full-range
//! 8-bit raster with `(0, 0)` at the centre of the top-left pixel (ADR-0007). Everything here is
//! pure so it can be unit-tested off-device.

use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RasterError {
    ZeroStride,
    NegativeCrop {
        coordinates: [i32; 4],
    },
    /// Crop rectangle is inverted or falls outside the stride/slice-height plane.
    InvalidCrop {
        crop: CropRect,
        stride: usize,
        slice_height: usize,
    },
    /// Buffer is shorter than the bytes the crop rectangle addresses.
    TruncatedPlane {
        needed: usize,
        actual: usize,
    },
    /// Rotation metadata is not a multiple of 90 degrees.
    UnsupportedRotation {
        degrees: i32,
    },
}

impl fmt::Display for RasterError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ZeroStride => write!(f, "luma plane stride must be positive"),
            Self::NegativeCrop { coordinates } => {
                write!(
                    f,
                    "decoder crop {coordinates:?} contains a negative coordinate"
                )
            }
            Self::InvalidCrop {
                crop,
                stride,
                slice_height,
            } => write!(
                f,
                "crop {crop:?} is inverted or outside the {stride}x{slice_height} luma plane"
            ),
            Self::TruncatedPlane { needed, actual } => write!(
                f,
                "luma plane has {actual} bytes but the crop rectangle needs {needed}"
            ),
            Self::UnsupportedRotation { degrees } => {
                write!(f, "rotation {degrees} deg is not a multiple of 90")
            }
        }
    }
}

impl std::error::Error for RasterError {}

/// Android crop rectangle; `right` and `bottom` are inclusive (MediaFormat `crop-*` keys).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CropRect {
    pub left: usize,
    pub top: usize,
    pub right: usize,
    pub bottom: usize,
}

impl CropRect {
    pub fn try_from_android(coordinates: [i32; 4]) -> Result<Self, RasterError> {
        if coordinates.iter().any(|&value| value < 0) {
            return Err(RasterError::NegativeCrop { coordinates });
        }
        let [left, top, right, bottom] = coordinates.map(|value| value as usize);
        Ok(Self {
            left,
            top,
            right,
            bottom,
        })
    }

    pub fn full(width_px: usize, height_px: usize) -> Self {
        Self {
            left: 0,
            top: 0,
            right: width_px.saturating_sub(1),
            bottom: height_px.saturating_sub(1),
        }
    }

    pub fn width_px(&self) -> usize {
        self.right - self.left + 1
    }

    pub fn height_px(&self) -> usize {
        self.bottom - self.top + 1
    }
}

/// Geometry of the decoder's Y plane inside its output buffer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PlaneLayout {
    pub stride: usize,
    pub slice_height: usize,
    pub crop: CropRect,
}

impl PlaneLayout {
    /// Bytes from the plane start through the last pixel the crop addresses.
    pub fn extent(&self) -> usize {
        self.crop.bottom * self.stride + self.crop.right + 1
    }

    fn validate(&self, buffer_len: usize) -> Result<(), RasterError> {
        if self.stride == 0 {
            return Err(RasterError::ZeroStride);
        }
        let crop = self.crop;
        if crop.right < crop.left
            || crop.bottom < crop.top
            || crop.right >= self.stride
            || crop.bottom >= self.slice_height
        {
            return Err(RasterError::InvalidCrop {
                crop,
                stride: self.stride,
                slice_height: self.slice_height,
            });
        }
        let needed = self.extent();
        if buffer_len < needed {
            return Err(RasterError::TruncatedPlane {
                needed,
                actual: buffer_len,
            });
        }
        Ok(())
    }
}

/// Clockwise rotation needed to show the coded raster upright (Android `rotation-degrees`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Rotation {
    Cw0,
    Cw90,
    Cw180,
    Cw270,
}

impl Rotation {
    /// Android reports the clockwise angle the coded frame must be rotated for display.
    pub fn from_android_cw(degrees: i32) -> Result<Self, RasterError> {
        if degrees % 90 != 0 {
            return Err(RasterError::UnsupportedRotation { degrees });
        }
        Ok(match degrees.rem_euclid(360) {
            0 => Self::Cw0,
            90 => Self::Cw90,
            180 => Self::Cw180,
            _ => Self::Cw270,
        })
    }

    pub fn clockwise_deg(self) -> u16 {
        match self {
            Self::Cw0 => 0,
            Self::Cw90 => 90,
            Self::Cw180 => 180,
            Self::Cw270 => 270,
        }
    }

    /// Same rotation in the counter-clockwise display-matrix convention `probe.rs` records.
    pub fn ffmpeg_ccw_deg(self) -> u16 {
        (360 - self.clockwise_deg()) % 360
    }
}

/// Tightly packed, display-oriented 8-bit luma (same layout as FFmpeg `-f rawvideo -pix_fmt gray`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LumaFrame {
    pub width_px: usize,
    pub height_px: usize,
    pub pixels: Vec<u8>,
}

const QUARTER_TURN_TILE: usize = 64;

/// Crop, remove stride padding and rotate the Y plane into display orientation.
pub fn repack_luma(
    plane: &[u8],
    layout: &PlaneLayout,
    rotation: Rotation,
) -> Result<LumaFrame, RasterError> {
    layout.validate(plane.len())?;
    let crop = layout.crop;
    let (src_w, src_h) = (crop.width_px(), crop.height_px());
    let src = |x: usize, y: usize| plane[(crop.top + y) * layout.stride + crop.left + x];

    let (width_px, height_px) = match rotation {
        Rotation::Cw0 | Rotation::Cw180 => (src_w, src_h),
        Rotation::Cw90 | Rotation::Cw270 => (src_h, src_w),
    };
    let mut pixels = Vec::with_capacity(width_px * height_px);
    match rotation {
        Rotation::Cw0 => {
            for y in 0..src_h {
                let row = (crop.top + y) * layout.stride + crop.left;
                pixels.extend_from_slice(&plane[row..row + src_w]);
            }
        }
        Rotation::Cw180 => {
            for y in 0..height_px {
                pixels.extend((0..width_px).map(|x| src(src_w - 1 - x, src_h - 1 - y)));
            }
        }
        // Quarter turns walk the source in square tiles so reads and writes both stay in cache.
        // Cw90:  out(x, y) = in(y, H-1-x): the coded bottom-left pixel becomes the display top-left.
        // Cw270: out(x, y) = in(W-1-y, x): the coded top-right pixel becomes the display top-left.
        Rotation::Cw90 | Rotation::Cw270 => {
            pixels.resize(width_px * height_px, 0);
            let rows: Vec<&[u8]> = (0..src_h)
                .map(|sy| {
                    let start = (crop.top + sy) * layout.stride + crop.left;
                    &plane[start..start + src_w]
                })
                .collect();
            let clockwise = rotation == Rotation::Cw90;
            for tile_x in (0..src_w).step_by(QUARTER_TURN_TILE) {
                let tile_x_end = (tile_x + QUARTER_TURN_TILE).min(src_w);
                for tile_y in (0..src_h).step_by(QUARTER_TURN_TILE) {
                    let tile_rows = &rows[tile_y..(tile_y + QUARTER_TURN_TILE).min(src_h)];
                    for sx in tile_x..tile_x_end {
                        // Source column sx becomes display row sx (Cw90) or W-1-sx (Cw270).
                        let oy = if clockwise { sx } else { src_w - 1 - sx };
                        let out_row = &mut pixels[oy * width_px..(oy + 1) * width_px];
                        for (k, row) in tile_rows.iter().enumerate() {
                            let sy = tile_y + k;
                            let ox = if clockwise { src_h - 1 - sy } else { sy };
                            out_row[ox] = row[sx];
                        }
                    }
                }
            }
        }
    }
    Ok(LumaFrame {
        width_px,
        height_px,
        pixels,
    })
}

/// Colour range of the decoded luma, as reported by the decoder output format.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LumaRange {
    Full,
    Limited,
}

impl LumaRange {
    /// MediaFormat `color-range`: 1 = full, 2 = limited. Absent/0 is unspecified.
    pub fn from_android(value: Option<i32>) -> Option<Self> {
        match value {
            Some(1) => Some(Self::Full),
            Some(2) => Some(Self::Limited),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Full => "full",
            Self::Limited => "limited",
        }
    }
}

/// Expand limited-range (16-235) luma to full range (0-255), clipping out-of-range codes.
///
/// Matches ADR-0006 grayscale semantics: `round((Y - 16) * 255 / 219)`, clamped. Rounding is
/// half-up in integer arithmetic so the result is deterministic on every platform.
pub fn expand_limited_range(pixels: &[u8]) -> Vec<u8> {
    pixels
        .iter()
        .map(|&y| LIMITED_TO_FULL[usize::from(y)])
        .collect()
}

const LIMITED_TO_FULL: [u8; 256] = build_limited_to_full();

const fn build_limited_to_full() -> [u8; 256] {
    let mut table = [0u8; 256];
    let mut code = 0;
    while code < 256 {
        let shifted = code as i32 - 16;
        let value = if shifted <= 0 {
            0
        } else {
            (shifted * 255 + 109) / 219
        };
        table[code] = if value > 255 { 255 } else { value as u8 };
        code += 1;
    }
    table
}

/// Bring a decoded Y plane to the ADR-0006 raster: repack, then expand range when limited.
pub fn to_contract_luma(
    plane: &[u8],
    layout: &PlaneLayout,
    rotation: Rotation,
    range: LumaRange,
) -> Result<LumaFrame, RasterError> {
    let frame = repack_luma(plane, layout, rotation)?;
    Ok(match range {
        LumaRange::Full => frame,
        LumaRange::Limited => LumaFrame {
            pixels: expand_limited_range(&frame.pixels),
            ..frame
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    // 3x2 coded raster inside a 4-wide, 3-tall padded plane (padding = 0xEE):
    //   1 2 3
    //   4 5 6
    const PLANE: [u8; 12] = [1, 2, 3, 0xEE, 4, 5, 6, 0xEE, 0xEE, 0xEE, 0xEE, 0xEE];

    fn layout() -> PlaneLayout {
        PlaneLayout {
            stride: 4,
            slice_height: 3,
            crop: CropRect::full(3, 2),
        }
    }

    fn frame(width_px: usize, height_px: usize, pixels: &[u8]) -> LumaFrame {
        LumaFrame {
            width_px,
            height_px,
            pixels: pixels.to_vec(),
        }
    }

    #[test]
    fn unrotated_repack_drops_stride_and_slice_padding() {
        let out = repack_luma(&PLANE, &layout(), Rotation::Cw0).unwrap();
        assert_eq!(out, frame(3, 2, &[1, 2, 3, 4, 5, 6]));
    }

    #[test]
    fn clockwise_90_puts_coded_bottom_left_at_display_top_left() {
        let out = repack_luma(&PLANE, &layout(), Rotation::Cw90).unwrap();
        // 4 1
        // 5 2
        // 6 3
        assert_eq!(out, frame(2, 3, &[4, 1, 5, 2, 6, 3]));
    }

    #[test]
    fn rotation_180_reverses_both_axes() {
        let out = repack_luma(&PLANE, &layout(), Rotation::Cw180).unwrap();
        assert_eq!(out, frame(3, 2, &[6, 5, 4, 3, 2, 1]));
    }

    #[test]
    fn clockwise_270_puts_coded_top_right_at_display_top_left() {
        let out = repack_luma(&PLANE, &layout(), Rotation::Cw270).unwrap();
        // 3 6
        // 2 5
        // 1 4
        assert_eq!(out, frame(2, 3, &[3, 6, 2, 5, 1, 4]));
    }

    #[test]
    fn four_quarter_turns_are_identity() {
        let once = repack_luma(&PLANE, &layout(), Rotation::Cw90).unwrap();
        let mut current = once;
        for _ in 0..3 {
            let flat = PlaneLayout {
                stride: current.width_px,
                slice_height: current.height_px,
                crop: CropRect::full(current.width_px, current.height_px),
            };
            current = repack_luma(&current.pixels, &flat, Rotation::Cw90).unwrap();
        }
        assert_eq!(current, frame(3, 2, &[1, 2, 3, 4, 5, 6]));
    }

    #[test]
    fn tiled_quarter_turns_match_naive_rotation_across_tile_edges() {
        // 70x45 visible inside a 75-wide, 50-tall padded plane, offset crop: spans several tiles.
        let (stride, slice_height) = (75, 50);
        let plane: Vec<u8> = (0..stride * slice_height)
            .map(|i| (i * 7 % 251) as u8)
            .collect();
        let crop = CropRect {
            left: 3,
            top: 2,
            right: 72,
            bottom: 46,
        };
        let layout = PlaneLayout {
            stride,
            slice_height,
            crop,
        };
        let (w, h) = (crop.width_px(), crop.height_px());
        let at = |x: usize, y: usize| plane[(crop.top + y) * stride + crop.left + x];
        let naive_cw90: Vec<u8> = (0..w)
            .flat_map(|y| (0..h).map(move |x| (y, x)))
            .map(|(y, x)| at(y, h - 1 - x))
            .collect();
        let naive_cw270: Vec<u8> = (0..w)
            .flat_map(|y| (0..h).map(move |x| (y, x)))
            .map(|(y, x)| at(w - 1 - y, x))
            .collect();
        assert_eq!(
            repack_luma(&plane, &layout, Rotation::Cw90).unwrap(),
            LumaFrame {
                width_px: h,
                height_px: w,
                pixels: naive_cw90
            }
        );
        assert_eq!(
            repack_luma(&plane, &layout, Rotation::Cw270).unwrap(),
            LumaFrame {
                width_px: h,
                height_px: w,
                pixels: naive_cw270
            }
        );
    }

    #[test]
    fn crop_offset_selects_interior_window() {
        let crop = CropRect {
            left: 1,
            top: 0,
            right: 2,
            bottom: 1,
        };
        let out = repack_luma(&PLANE, &PlaneLayout { crop, ..layout() }, Rotation::Cw0).unwrap();
        assert_eq!(out, frame(2, 2, &[2, 3, 5, 6]));
    }

    #[test]
    fn rejects_zero_stride_bad_crop_and_truncated_plane() {
        let zero = PlaneLayout {
            stride: 0,
            ..layout()
        };
        assert_eq!(
            repack_luma(&PLANE, &zero, Rotation::Cw0),
            Err(RasterError::ZeroStride)
        );

        let wide = PlaneLayout {
            crop: CropRect::full(5, 2),
            ..layout()
        };
        assert!(matches!(
            repack_luma(&PLANE, &wide, Rotation::Cw0),
            Err(RasterError::InvalidCrop { .. })
        ));

        let inverted = PlaneLayout {
            crop: CropRect {
                left: 2,
                top: 0,
                right: 1,
                bottom: 1,
            },
            ..layout()
        };
        assert!(matches!(
            repack_luma(&PLANE, &inverted, Rotation::Cw0),
            Err(RasterError::InvalidCrop { .. })
        ));

        assert_eq!(
            repack_luma(&PLANE[..6], &layout(), Rotation::Cw0),
            Err(RasterError::TruncatedPlane {
                needed: 7,
                actual: 6
            })
        );
    }

    #[test]
    fn android_crop_rejects_each_negative_coordinate() {
        for coordinates in [[-1, 0, 1, 1], [0, -1, 1, 1], [0, 0, -1, 1], [0, 0, 1, -1]] {
            assert!(CropRect::try_from_android(coordinates).is_err());
        }
        assert_eq!(
            CropRect::try_from_android([0, 0, 3, 1]).unwrap(),
            CropRect::full(4, 2)
        );
    }

    #[test]
    fn android_clockwise_maps_to_ffmpeg_counter_clockwise() {
        let mapped: Vec<(u16, u16)> = [0, 90, 180, 270, -90, 450]
            .iter()
            .map(|&deg| {
                let rotation = Rotation::from_android_cw(deg).unwrap();
                (rotation.clockwise_deg(), rotation.ffmpeg_ccw_deg())
            })
            .collect();
        assert_eq!(
            mapped,
            vec![
                (0, 0),
                (90, 270),
                (180, 180),
                (270, 90),
                (270, 90),
                (90, 270)
            ]
        );
        assert_eq!(
            Rotation::from_android_cw(45),
            Err(RasterError::UnsupportedRotation { degrees: 45 })
        );
    }

    #[test]
    fn limited_range_expansion_hits_anchor_codes_and_clips() {
        let expanded = expand_limited_range(&[0, 15, 16, 17, 126, 235, 236, 255]);
        assert_eq!(expanded, vec![0, 0, 0, 1, 128, 255, 255, 255]);
    }

    #[test]
    fn limited_range_expansion_is_monotonic() {
        let all: Vec<u8> = (0..=255).collect();
        let expanded = expand_limited_range(&all);
        assert!(expanded.windows(2).all(|pair| pair[0] <= pair[1]));
    }

    #[test]
    fn color_range_mapping_and_contract_luma() {
        assert_eq!(LumaRange::from_android(Some(1)), Some(LumaRange::Full));
        assert_eq!(LumaRange::from_android(Some(2)), Some(LumaRange::Limited));
        assert_eq!(LumaRange::from_android(Some(0)), None);
        assert_eq!(LumaRange::from_android(None), None);

        let plane = [16, 235, 126, 0];
        let flat = PlaneLayout {
            stride: 2,
            slice_height: 2,
            crop: CropRect::full(2, 2),
        };
        let full = to_contract_luma(&plane, &flat, Rotation::Cw0, LumaRange::Full).unwrap();
        assert_eq!(full, frame(2, 2, &[16, 235, 126, 0]));
        let limited = to_contract_luma(&plane, &flat, Rotation::Cw0, LumaRange::Limited).unwrap();
        assert_eq!(limited, frame(2, 2, &[0, 255, 128, 0]));
    }
}
