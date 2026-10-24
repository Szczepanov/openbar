//! Zero-copy display-oriented luma view: rotation and range expansion applied per lookup.
//!
//! ADR-0005 lets a tracker read frames through `GrayscaleImage` (`width_px`, `height_px`,
//! `intensity(x, y)`) instead of a packed buffer. `DisplayLumaView` holds only the copied Y plane
//! (crop, stride and padding untouched) and maps each display coordinate to its coded sample, so
//! the per-frame cost before tracking is the luma copy alone. `to_packed` materialises the same
//! raster `repack_luma` + `expand_limited_range` produce, which keeps `--dump-gray` parity intact.
//!
//! The `DisplayGray` trait mirrors the ADR-0005 trait shape locally on purpose: this research crate
//! does not depend on `openbar-tracking`.

use crate::raster::{LumaFrame, LumaRange, PlaneLayout, RasterError, Rotation, LIMITED_TO_FULL};

/// Local mirror of `openbar_tracking::GrayscaleImage` (display orientation, ADR-0007 pixel grid).
pub trait DisplayGray {
    fn width_px(&self) -> u32;
    fn height_px(&self) -> u32;
    /// Full-range intensity at a display pixel; panics outside the raster, like `GrayFrame`.
    fn intensity(&self, x_px: u32, y_px: u32) -> u8;
}

impl DisplayGray for LumaFrame {
    fn width_px(&self) -> u32 {
        self.width_px as u32
    }
    fn height_px(&self) -> u32 {
        self.height_px as u32
    }
    fn intensity(&self, x_px: u32, y_px: u32) -> u8 {
        assert!((x_px as usize) < self.width_px && (y_px as usize) < self.height_px);
        self.pixels[y_px as usize * self.width_px + x_px as usize]
    }
}

const IDENTITY: [u8; 256] = build_identity();

const fn build_identity() -> [u8; 256] {
    let mut table = [0u8; 256];
    let mut code = 0;
    while code < 256 {
        table[code] = code as u8;
        code += 1;
    }
    table
}

/// Copied Y plane plus the affine map from display coordinates to plane offsets.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DisplayLumaView {
    plane: Vec<u8>,
    width_px: usize,
    height_px: usize,
    /// Plane offset of display pixel (0, 0).
    origin: isize,
    /// Plane offset change per display step in +X and +Y.
    step_x: isize,
    step_y: isize,
    lut: &'static [u8; 256],
}

impl DisplayLumaView {
    /// Takes ownership of a copied plane; `layout` is validated exactly as `repack_luma` does.
    pub fn try_new(
        plane: Vec<u8>,
        layout: &PlaneLayout,
        rotation: Rotation,
        range: LumaRange,
    ) -> Result<Self, RasterError> {
        layout.validate(plane.len())?;
        let crop = layout.crop;
        let (w, h) = (crop.width_px() as isize, crop.height_px() as isize);
        let stride = layout.stride as isize;
        let top_left = (crop.top * layout.stride + crop.left) as isize;
        // Same mappings as `repack_luma`, written as origin + x*step_x + y*step_y.
        let (origin, step_x, step_y, width_px, height_px) = match rotation {
            // out(x, y) = in(x, y)
            Rotation::Cw0 => (top_left, 1, stride, w, h),
            // out(x, y) = in(y, H-1-x)
            Rotation::Cw90 => (top_left + (h - 1) * stride, -stride, 1, h, w),
            // out(x, y) = in(W-1-x, H-1-y)
            Rotation::Cw180 => (top_left + (h - 1) * stride + (w - 1), -1, -stride, w, h),
            // out(x, y) = in(W-1-y, x)
            Rotation::Cw270 => (top_left + (w - 1), stride, -1, h, w),
        };
        Ok(Self {
            plane,
            width_px: width_px as usize,
            height_px: height_px as usize,
            origin,
            step_x,
            step_y,
            lut: match range {
                LumaRange::Full => &IDENTITY,
                LumaRange::Limited => &LIMITED_TO_FULL,
            },
        })
    }

    /// The packed display raster, byte-identical to `to_contract_luma` on the same input.
    pub fn to_packed(&self) -> LumaFrame {
        let mut pixels = Vec::with_capacity(self.width_px * self.height_px);
        for y in 0..self.height_px {
            let row = self.origin + y as isize * self.step_y;
            pixels.extend((0..self.width_px).map(|x| {
                self.lut[usize::from(self.plane[(row + x as isize * self.step_x) as usize])]
            }));
        }
        LumaFrame {
            width_px: self.width_px,
            height_px: self.height_px,
            pixels,
        }
    }
}

impl DisplayGray for DisplayLumaView {
    fn width_px(&self) -> u32 {
        self.width_px as u32
    }
    fn height_px(&self) -> u32 {
        self.height_px as u32
    }
    fn intensity(&self, x_px: u32, y_px: u32) -> u8 {
        let (x, y) = (x_px as usize, y_px as usize);
        assert!(
            x < self.width_px && y < self.height_px,
            "({x_px}, {y_px}) outside {}x{}",
            self.width_px,
            self.height_px
        );
        let offset = self.origin + x as isize * self.step_x + y as isize * self.step_y;
        self.lut[usize::from(self.plane[offset as usize])]
    }
}

/// Sum of every display pixel read through `intensity`, row-major: the worst-case consumer that
/// touches the whole frame. Used by `--frame-mode view-scan` to bound the cost moved downstream.
pub fn scan_checksum(image: &dyn DisplayGray) -> u64 {
    let mut sum = 0u64;
    for y in 0..image.height_px() {
        for x in 0..image.width_px() {
            sum += u64::from(image.intensity(x, y));
        }
    }
    sum
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::raster::{to_contract_luma, CropRect};

    const ROTATIONS: [Rotation; 4] = [
        Rotation::Cw0,
        Rotation::Cw90,
        Rotation::Cw180,
        Rotation::Cw270,
    ];

    fn padded_plane() -> (Vec<u8>, PlaneLayout) {
        // 70x45 visible inside a 75-wide, 50-tall padded plane with an offset crop.
        let (stride, slice_height) = (75, 50);
        let plane = (0..stride * slice_height)
            .map(|i| (i * 7 % 251) as u8)
            .collect();
        let crop = CropRect {
            left: 3,
            top: 2,
            right: 72,
            bottom: 46,
        };
        (
            plane,
            PlaneLayout {
                stride,
                slice_height,
                crop,
            },
        )
    }

    #[test]
    fn view_matches_materialised_raster_for_every_rotation_and_range() {
        let (plane, layout) = padded_plane();
        for rotation in ROTATIONS {
            for range in [LumaRange::Full, LumaRange::Limited] {
                let expected = to_contract_luma(&plane, &layout, rotation, range).unwrap();
                let view =
                    DisplayLumaView::try_new(plane.clone(), &layout, rotation, range).unwrap();
                assert_eq!(view.to_packed(), expected, "{rotation:?} {range:?}");
                let by_lookup: Vec<u8> = (0..view.height_px())
                    .flat_map(|y| (0..view.width_px()).map(move |x| (x, y)))
                    .map(|(x, y)| view.intensity(x, y))
                    .collect();
                assert_eq!(by_lookup, expected.pixels, "{rotation:?} {range:?}");
                assert_eq!(
                    scan_checksum(&view),
                    expected.pixels.iter().map(|&p| u64::from(p)).sum::<u64>()
                );
            }
        }
    }

    #[test]
    fn quarter_turn_swaps_dimensions_and_corner_mapping() {
        // Coded 3x2: 1 2 3 / 4 5 6 in a 4-wide plane; Cw90 shows 4 1 / 5 2 / 6 3.
        let plane = vec![1, 2, 3, 0xEE, 4, 5, 6, 0xEE];
        let layout = PlaneLayout {
            stride: 4,
            slice_height: 2,
            crop: CropRect::full(3, 2),
        };
        let view =
            DisplayLumaView::try_new(plane, &layout, Rotation::Cw90, LumaRange::Full).unwrap();
        assert_eq!((view.width_px(), view.height_px()), (2, 3));
        assert_eq!(
            [
                view.intensity(0, 0),
                view.intensity(1, 0),
                view.intensity(1, 2)
            ],
            [4, 1, 3]
        );
    }

    #[test]
    fn limited_range_lut_is_applied_on_lookup() {
        let layout = PlaneLayout {
            stride: 2,
            slice_height: 2,
            crop: CropRect::full(2, 2),
        };
        let view = DisplayLumaView::try_new(
            vec![16, 235, 126, 0],
            &layout,
            Rotation::Cw0,
            LumaRange::Limited,
        )
        .unwrap();
        assert_eq!(view.to_packed().pixels, vec![0, 255, 128, 0]);
    }

    #[test]
    fn construction_validates_like_repack() {
        let layout = PlaneLayout {
            stride: 4,
            slice_height: 3,
            crop: CropRect::full(3, 2),
        };
        assert_eq!(
            DisplayLumaView::try_new(vec![0; 6], &layout, Rotation::Cw0, LumaRange::Full),
            Err(RasterError::TruncatedPlane {
                needed: 7,
                actual: 6
            })
        );
        let zero = PlaneLayout {
            stride: 0,
            ..layout
        };
        assert_eq!(
            DisplayLumaView::try_new(vec![0; 12], &zero, Rotation::Cw0, LumaRange::Full),
            Err(RasterError::ZeroStride)
        );
    }

    #[test]
    #[should_panic(expected = "outside 2x3")]
    fn lookup_outside_the_raster_panics() {
        let layout = PlaneLayout {
            stride: 3,
            slice_height: 2,
            crop: CropRect::full(3, 2),
        };
        let view = DisplayLumaView::try_new(vec![0; 6], &layout, Rotation::Cw270, LumaRange::Full)
            .unwrap();
        view.intensity(2, 0);
    }
}
