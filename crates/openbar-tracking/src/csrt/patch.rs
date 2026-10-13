//! Patch geometry and feature preprocessing for the `csrt-gray-v1` tracker (slice S2).
//!
//! A patch is a square, power-of-two crop of the grayscale frame read at integer pixel
//! positions, without resizing or interpolation. The crop is mean-subtracted, scaled to unit
//! population RMS and multiplied by a separable periodic Hann window (no renormalisation
//! afterwards). A crop that does not lie fully inside the frame is unavailable: nothing is
//! padded, reflected, wrapped or clipped, so the target is never silently shifted.
//!
//! The Gaussian desired response and the binary disk support follow the correlation-filter
//! formulation of Lukezic et al., "Discriminative Correlation Filter with Channel and Spatial
//! Reliability", CVPR 2017. This variant is single-channel and uses a fixed disk as its spatial
//! support instead of the adaptive reliability map.
//!
//! All functions return spatial-domain values; the filter-learning slice transforms them once.

use super::detmath::{det_exp, hann_window_periodic};
use super::fft::Fft2d;
use super::{Complex, NumericError};
use crate::GrayscaleImage;

/// Smallest supported patch side.
pub(super) const MIN_PATCH_SIDE: usize = 8;
/// Largest supported patch side. A prototype ceiling, not a measured phone budget.
pub(super) const MAX_PATCH_SIDE: usize = 512;
/// Patches with a population RMS below one intensity level (`1/255` on the 0..1 scale) are flat.
const MIN_RMS_LEVELS: f64 = 1.0;

/// Outcome of reading one patch.
#[derive(Debug, Clone, PartialEq)]
pub(super) enum Patch {
    /// Preprocessed spatial values, row-major, imaginary parts zero.
    Available(Vec<Complex>),
    /// The crop is not fully inside the frame.
    OutsideFrame,
    /// The crop is too flat to normalise; `rms` is in original pixel-intensity units (0..255).
    InsufficientContrast { rms: f64 },
}

/// Side of the square patch for a target of radius `radius_px`: the next power of two of
/// `ceil(4 * radius_px + 1)`, at least [`MIN_PATCH_SIDE`], at most [`MAX_PATCH_SIDE`].
pub(super) fn patch_side(radius_px: f64) -> Result<usize, NumericError> {
    validate_radius(radius_px)?;
    let required = (4.0 * radius_px + 1.0).ceil();
    // Compared as f64 before any integer conversion, so no radius can overflow or allocate.
    if required > MAX_PATCH_SIDE as f64 {
        return Err(NumericError::RadiusTooLarge {
            radius_px,
            max_side: MAX_PATCH_SIDE,
        });
    }
    // `required` lies in 1..=512 here, so the cast is exact.
    Ok((required as usize).next_power_of_two().max(MIN_PATCH_SIDE))
}

fn validate_radius(radius_px: f64) -> Result<(), NumericError> {
    if radius_px.is_finite() && radius_px > 0.0 {
        Ok(())
    } else {
        Err(NumericError::InvalidRadius { value: radius_px })
    }
}

/// Accepts a square, power-of-two side within [`MIN_PATCH_SIDE`]..=[`MAX_PATCH_SIDE`].
fn validate_side(width: usize, height: usize) -> Result<usize, NumericError> {
    if width == height
        && width.is_power_of_two()
        && (MIN_PATCH_SIDE..=MAX_PATCH_SIDE).contains(&width)
    {
        Ok(width)
    } else {
        Err(NumericError::UnsupportedPatchSide { width, height })
    }
}

/// Reads and preprocesses the patch whose anchor pixel `anchor_px` sits at crop index
/// `(side / 2, side / 2)`. The side is the (square, supported) size of `fft`.
pub(super) fn normalized_patch(
    image: &dyn GrayscaleImage,
    anchor_px: (i32, i32),
    fft: &Fft2d,
) -> Result<Patch, NumericError> {
    let side = validate_side(fft.width(), fft.height())?;
    // Side is at most 512, so these conversions are exact and the sums cannot overflow i64.
    let side_i64 = side as i64;
    let origin_x = i64::from(anchor_px.0) - side_i64 / 2;
    let origin_y = i64::from(anchor_px.1) - side_i64 / 2;
    let fits = origin_x >= 0
        && origin_y >= 0
        && origin_x + side_i64 <= i64::from(image.width_px())
        && origin_y + side_i64 <= i64::from(image.height_px());
    if !fits {
        return Ok(Patch::OutsideFrame);
    }

    // The crop is inside the frame, so every coordinate below is a valid `u32`. Statistics use
    // exact integer sums (at most 512 * 512 * 255^2 < 2^35), so a flat crop has exactly zero
    // variance and the contrast threshold has no rounding error. The normalised value is
    // scale-invariant, so working in intensity levels equals working on the 0..1 scale.
    let mut intensities = Vec::with_capacity(side * side);
    for j in 0..side_i64 {
        for i in 0..side_i64 {
            intensities.push(image.intensity((origin_x + i) as u32, (origin_y + j) as u32));
        }
    }
    let count = (side * side) as u64;
    let sum: u64 = intensities.iter().map(|&value| u64::from(value)).sum();
    let sum_squares: u64 = intensities
        .iter()
        .map(|&value| u64::from(value) * u64::from(value))
        .sum();
    // count^2 * variance = count * sum_squares - sum^2, at most 2^52 and exact as f64.
    let variance_numerator = (count * sum_squares - sum * sum) as f64;
    let rms_levels = variance_numerator.sqrt() / count as f64;
    if rms_levels < MIN_RMS_LEVELS {
        return Ok(Patch::InsufficientContrast { rms: rms_levels });
    }
    let mean_levels = sum as f64 / count as f64;

    // Both axes use the same square window; each sample is ((v - mean) / rms) * w[j] * w[i].
    let window = hann_window_periodic(side)?;
    let mut patch = Vec::with_capacity(intensities.len());
    for (index, &intensity) in intensities.iter().enumerate() {
        let normalized = (f64::from(intensity) - mean_levels) / rms_levels;
        let windowed = normalized * window[index / side] * window[index % side];
        if !windowed.is_finite() {
            return Err(NumericError::NonFiniteOutput { index });
        }
        patch.push(Complex::new(windowed, 0.0));
    }
    Ok(Patch::Available(patch))
}

/// Gaussian desired response, peaking at FFT index `(0, 0)` with wrapped axis distances. The
/// transform must be a supported patch size (square, power of two, 8..=512).
pub(super) fn gaussian_label(fft: &Fft2d, sigma_px: f64) -> Result<Vec<Complex>, NumericError> {
    let side = validate_side(fft.width(), fft.height())?;
    // The variance term must be positive and finite, and the largest exponent argument (the
    // wrapped corner, at squared distance 2 * (side / 2)^2) must stay finite as well.
    let denominator = 2.0 * sigma_px * sigma_px;
    let farthest = (side / 2) as f64;
    let valid = sigma_px.is_finite()
        && sigma_px > 0.0
        && denominator.is_finite()
        && denominator > 0.0
        && (2.0 * farthest * farthest / denominator).is_finite();
    if !valid {
        return Err(NumericError::InvalidSigma { value: sigma_px });
    }
    let mut label = Vec::with_capacity(side * side);
    for j in 0..side {
        let dy = j.min(side - j) as f64;
        for i in 0..side {
            let dx = i.min(side - i) as f64;
            let distance_sq = dx * dx + dy * dy;
            label.push(Complex::new(det_exp(0.0 - distance_sq / denominator)?, 0.0));
        }
    }
    Ok(label)
}

/// Binary disk of `radius_px` centred at `(side / 2, side / 2)`, row-major, values 0 or 1.
pub(super) fn support_mask(side: usize, radius_px: f64) -> Result<Vec<f64>, NumericError> {
    validate_side(side, side)?;
    validate_radius(radius_px)?;
    // The disk must satisfy the bound patch_side enforces (4 * radius + 1 <= side), which also
    // keeps it symmetric inside the crop. `side` is at most 512, so nothing overflows.
    if radius_px * 4.0 + 1.0 > side as f64 {
        return Err(NumericError::RadiusExceedsPatch { radius_px, side });
    }
    let centre = (side / 2) as i64;
    let radius_sq = radius_px * radius_px;
    let mut mask = Vec::with_capacity(side * side);
    for j in 0..side as i64 {
        for i in 0..side as i64 {
            let (dx, dy) = (i - centre, j - centre);
            let distance_sq = (dx * dx + dy * dy) as f64;
            mask.push(if distance_sq <= radius_sq { 1.0 } else { 0.0 });
        }
    }
    Ok(mask)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{rounded_point, GrayFrame};
    use openbar_core::manual_seed::PixelPoint;
    use std::f64::consts::PI;

    const TOLERANCE: f64 = 1e-12;

    fn frame(width: u32, height: u32, pixel: impl Fn(u32, u32) -> u8) -> GrayFrame {
        let mut pixels = Vec::new();
        for y in 0..height {
            for x in 0..width {
                pixels.push(pixel(x, y));
            }
        }
        GrayFrame::try_new(width, height, pixels).expect("valid test frame")
    }

    /// Asymmetric, non-flat texture: no 8x8 window is constant and no axis swap is invisible.
    fn textured(width: u32, height: u32) -> GrayFrame {
        frame(width, height, |x, y| {
            ((x * 37 + y * 91 + (x * y) % 7 * 13 + (x % 5) * (y % 3) * 11) % 251) as u8
        })
    }

    fn fft(side: usize) -> Fft2d {
        Fft2d::try_new(side, side).expect("supported size")
    }

    fn available(patch: Patch) -> Vec<Complex> {
        match patch {
            Patch::Available(values) => values,
            other => panic!("expected an available patch, got {other:?}"),
        }
    }

    fn hann_closed_form(len: usize) -> Vec<f64> {
        (0..len)
            .map(|k| 0.5 * (1.0 - (2.0 * PI * k as f64 / len as f64).cos()))
            .collect()
    }

    /// Independent reference: crop by plain indexing, then normalise and window.
    fn reference_patch(image: &GrayFrame, anchor: (i32, i32), side: usize) -> Vec<f64> {
        let half = (side / 2) as i32;
        let mut values = Vec::new();
        for j in 0..side as i32 {
            for i in 0..side as i32 {
                let x = (anchor.0 + i - half) as u32;
                let y = (anchor.1 + j - half) as u32;
                values.push(f64::from(image.intensity(x, y)) / 255.0);
            }
        }
        let n = values.len() as f64;
        let mean = values.iter().sum::<f64>() / n;
        let rms = (values.iter().map(|v| (v - mean) * (v - mean)).sum::<f64>() / n).sqrt();
        let window = hann_closed_form(side);
        values
            .iter()
            .enumerate()
            .map(|(index, v)| (v - mean) / rms * window[index / side] * window[index % side])
            .collect()
    }

    #[test]
    fn side_is_checked_and_power_of_two() {
        assert_eq!(patch_side(24.0), Ok(128));
        assert_eq!(patch_side(1.0), Ok(8));
        assert_eq!(patch_side(0.001), Ok(8));
        assert_eq!(patch_side(1.75), Ok(8)); // ceil(8) = 8
        assert_eq!(patch_side(1.76), Ok(16)); // ceil(8.04) = 9
        assert_eq!(patch_side(15.75), Ok(64));
        assert_eq!(patch_side(15.76), Ok(128));
        assert_eq!(patch_side(127.75), Ok(512)); // ceil(512) = 512, the ceiling
        for value in [0.0, -1.0, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(
                matches!(patch_side(value), Err(NumericError::InvalidRadius { .. })),
                "{value}"
            );
        }
        for value in [127.76, 128.0, 1e6, f64::MAX] {
            assert_eq!(
                patch_side(value),
                Err(NumericError::RadiusTooLarge {
                    radius_px: value,
                    max_side: MAX_PATCH_SIDE
                }),
                "{value}"
            );
        }
    }

    #[test]
    fn integer_centres_and_axis_orientation() {
        // Odd frame dimensions; one bright pixel at anchor + (1, -2) in an otherwise dark frame.
        let (width, height, anchor) = (21, 17, (10, 8));
        let image = frame(
            width,
            height,
            |x, y| if (x, y) == (11, 6) { 200 } else { 20 },
        );
        let values = available(normalized_patch(&image, anchor, &fft(8)).unwrap());
        assert_eq!(values.len(), 64);
        // Crop index (i, j) reads image (cx + i - 4, cy + j - 4): +X is right, +Y is down.
        let (bright_i, bright_j) = (5, 2);
        let peak = values
            .iter()
            .enumerate()
            .max_by(|a, b| a.1.re.total_cmp(&b.1.re))
            .map(|(index, _)| index)
            .unwrap();
        assert_eq!(peak, bright_j * 8 + bright_i);
        assert!(values
            .iter()
            .all(|value| value.im == 0.0 && value.re.is_finite()));

        // An asymmetric texture is reproduced exactly as the independent reference reads it.
        let image = textured(width, height);
        for anchor in [(4, 4), (10, 8), (16, 12), (17, 13), (9, 5)] {
            let values = available(normalized_patch(&image, anchor, &fft(8)).unwrap());
            let expected = reference_patch(&image, anchor, 8);
            for (index, (value, want)) in values.iter().zip(&expected).enumerate() {
                assert!(
                    (value.re - want).abs() < TOLERANCE,
                    "{anchor:?} index {index}"
                );
            }
        }
    }

    #[test]
    fn fractional_seed_centres_use_the_shared_rounding() {
        let image = textured(32, 32);
        let rounded = rounded_point(PixelPoint::new(15.6, 12.4));
        assert_eq!(rounded, (16, 12));
        // Exact halves round away from zero, as everywhere else in the tracking crate.
        assert_eq!(rounded_point(PixelPoint::new(15.5, 12.5)), (16, 13));
        let from_fraction = normalized_patch(&image, rounded, &fft(16)).unwrap();
        let from_integer = normalized_patch(&image, (16, 12), &fft(16)).unwrap();
        assert_eq!(from_fraction, from_integer);
        assert_ne!(
            from_fraction,
            normalized_patch(&image, (15, 12), &fft(16)).unwrap()
        );
    }

    #[test]
    fn edge_crop_is_unavailable_without_padding() {
        let image = textured(32, 32);
        let fft = fft(8);
        // Half side is 4: the crop spans anchor - 4 ..= anchor + 3.
        for anchor in [(4, 4), (28, 28), (4, 28), (28, 4), (16, 4), (4, 16)] {
            let patch = normalized_patch(&image, anchor, &fft).unwrap();
            assert!(
                matches!(patch, Patch::Available(_)),
                "{anchor:?} fits exactly"
            );
        }
        for anchor in [
            (3, 16),
            (29, 16),
            (16, 3),
            (16, 29),
            (3, 3),
            (29, 29),
            (-100, 16),
            (16, 1_000),
            (i32::MIN, i32::MIN),
            (i32::MAX, i32::MAX),
        ] {
            assert_eq!(
                normalized_patch(&image, anchor, &fft),
                Ok(Patch::OutsideFrame),
                "{anchor:?}"
            );
        }
        // A patch larger than the frame never fits.
        assert_eq!(
            normalized_patch(&textured(16, 16), (8, 8), &self::fft(32)),
            Ok(Patch::OutsideFrame)
        );
    }

    #[test]
    fn flat_patch_is_unusable() {
        let fft = fft(8);
        let flat = frame(16, 16, |_, _| 100);
        assert_eq!(
            normalized_patch(&flat, (8, 8), &fft),
            Ok(Patch::InsufficientContrast { rms: 0.0 })
        );
        // Half the pixels one level higher: population RMS is 0.5 intensity levels.
        let faint = frame(16, 16, |x, _| if x < 8 { 100 } else { 101 });
        match normalized_patch(&faint, (8, 8), &fft).unwrap() {
            Patch::InsufficientContrast { rms } => assert!((rms - 0.5).abs() < 1e-9, "{rms}"),
            other => panic!("expected insufficient contrast, got {other:?}"),
        }
        // Two levels apart gives an RMS of exactly one level, which is the usable minimum.
        let enough = frame(16, 16, |x, _| if x < 8 { 100 } else { 102 });
        assert!(matches!(
            normalized_patch(&enough, (8, 8), &fft).unwrap(),
            Patch::Available(_)
        ));
        // The frame edge is reported before contrast is considered.
        assert_eq!(
            normalized_patch(&flat, (2, 8), &fft),
            Ok(Patch::OutsideFrame)
        );
    }

    #[test]
    fn normalization_then_hann_is_fixed_order() {
        // A 0/255 checkerboard has mean 1/2 and population RMS 1/2, so the normalised values
        // are exactly +-1 and the output is the signed outer product of the Hann window.
        let checker = frame(16, 16, |x, y| if (x + y) % 2 == 0 { 255 } else { 0 });
        let values = available(normalized_patch(&checker, (8, 8), &fft(8)).unwrap());
        let window = hann_closed_form(8);
        for j in 0..8usize {
            for i in 0..8usize {
                // Crop (i, j) reads image (4 + i, 4 + j); parity of (4 + i + 4 + j) is (i + j).
                let sign = if (i + j) % 2 == 0 { 1.0 } else { -1.0 };
                let want = sign * window[j] * window[i];
                let got = values[j * 8 + i];
                assert!((got.re - want).abs() < TOLERANCE, "({i}, {j})");
                assert_eq!(got.im, 0.0);
            }
        }
        // The window is applied after normalisation and never renormalised, so the windowed
        // output does not have unit RMS.
        let energy: f64 = values.iter().map(|v| v.re * v.re).sum::<f64>() / 64.0;
        assert!(energy < 0.5, "{energy}");
        // Window corners are exactly zero, so the first row and column vanish.
        assert!(values[..8].iter().all(|v| v.re == 0.0));
        assert!((0..8).all(|j| values[j * 8].re == 0.0));
    }

    #[test]
    fn unsupported_fft_sizes_are_rejected() {
        let image = textured(64, 64);
        for (width, height) in [(4, 4), (16, 8), (8, 16), (1024, 1024), (2, 2)] {
            let fft = Fft2d::try_new(width, height).unwrap();
            assert_eq!(
                normalized_patch(&image, (32, 32), &fft),
                Err(NumericError::UnsupportedPatchSide { width, height })
            );
        }
    }

    #[test]
    fn normalized_patch_is_repeatable_bit_for_bit() {
        let image = textured(40, 40);
        let first = normalized_patch(&image, (20, 18), &fft(16)).unwrap();
        let second = normalized_patch(&image, (20, 18), &fft(16)).unwrap();
        assert_eq!(first, second);
    }

    #[test]
    fn label_peaks_at_fft_origin() {
        let fft = fft(8);
        let label = gaussian_label(&fft, 2.0).unwrap();
        assert_eq!(label.len(), 64);
        assert_eq!(label[0], Complex::new(1.0, 0.0));
        for (index, value) in label.iter().enumerate().skip(1) {
            assert!(value.re > 0.0 && value.re < 1.0, "{index}");
            assert_eq!(value.im, 0.0);
        }
        // Wrapped distances: index i and side - i are equally far from the origin.
        for j in 0..8usize {
            for i in 0..8usize {
                assert_eq!(label[j * 8 + i], label[j * 8 + (8 - i) % 8], "({i}, {j})");
                assert_eq!(label[j * 8 + i], label[((8 - j) % 8) * 8 + i], "({i}, {j})");
                assert_eq!(
                    label[j * 8 + i],
                    label[i * 8 + j],
                    "({i}, {j}) symmetric axes"
                );
            }
        }
        let want = |distance_sq: f64| (-distance_sq / 8.0).exp();
        assert!((label[1].re - want(1.0)).abs() < 1e-14);
        assert!((label[8].re - want(1.0)).abs() < 1e-14);
        assert!((label[9].re - want(2.0)).abs() < 1e-14);
        assert!((label[4].re - want(16.0)).abs() < 1e-14); // half period: distance 4
        assert!((label[4 * 8 + 4].re - want(32.0)).abs() < 1e-14);

        assert_eq!(gaussian_label(&fft, 2.0).unwrap(), label);
    }

    #[test]
    fn label_rejects_invalid_sigma() {
        let fft = fft(8);
        for value in [
            0.0,
            -2.0,
            f64::NAN,
            f64::INFINITY,
            f64::NEG_INFINITY,
            1e-200,
            1e200,
        ] {
            assert!(
                matches!(
                    gaussian_label(&fft, value),
                    Err(NumericError::InvalidSigma { .. })
                ),
                "{value}"
            );
        }
        // A representable but tiny variance drives the far-corner exponent to -inf.
        for value in [1e-160, 1.5e-154] {
            assert_eq!(
                gaussian_label(&fft, value),
                Err(NumericError::InvalidSigma { value }),
                "{value}"
            );
        }
        // A huge but representable sigma is accepted (a nearly flat label).
        assert!(gaussian_label(&fft, 1e100).is_ok());
    }

    #[test]
    fn label_requires_a_supported_square_size() {
        for (width, height) in [(4, 4), (16, 8), (8, 16), (1024, 1024), (4096, 4096)] {
            let fft = Fft2d::try_new(width, height).unwrap();
            assert_eq!(
                gaussian_label(&fft, 2.0),
                Err(NumericError::UnsupportedPatchSide { width, height })
            );
        }
    }

    #[test]
    fn support_matches_seed_disk() {
        let mask = support_mask(16, 3.0).unwrap();
        assert_eq!(mask.len(), 256);
        assert!(mask.iter().all(|&value| value == 0.0 || value == 1.0));
        let at = |i: usize, j: usize| mask[j * 16 + i];
        assert_eq!(at(8, 8), 1.0); // centre is (side / 2, side / 2)
        assert_eq!(at(11, 8), 1.0); // rim on the +X axis (distance exactly 3)
        assert_eq!(at(5, 8), 1.0);
        assert_eq!(at(8, 11), 1.0);
        assert_eq!(at(8, 5), 1.0);
        assert_eq!(at(12, 8), 0.0);
        assert_eq!(at(4, 8), 0.0);
        assert_eq!(at(8, 12), 0.0);
        assert_eq!(at(8, 4), 0.0);
        assert_eq!(at(10, 10), 1.0); // distance^2 = 8
        assert_eq!(at(11, 10), 0.0); // distance^2 = 13
        assert_eq!(at(0, 0), 0.0);
        assert_eq!(mask.iter().sum::<f64>(), 29.0);
        // A fractional radius keeps points whose squared distance is at most radius^2.
        let fractional = support_mask(16, 2.5).unwrap();
        assert_eq!(fractional.iter().sum::<f64>(), 21.0);
        // The radius obeys the patch_side bound 4 * radius + 1 <= side, which keeps the disk
        // symmetric inside the crop.
        assert!(support_mask(16, 3.75).is_ok());
        for value in [3.76, 8.0, 8.5] {
            assert_eq!(
                support_mask(16, value),
                Err(NumericError::RadiusExceedsPatch {
                    radius_px: value,
                    side: 16
                }),
                "{value}"
            );
        }
        for radius_px in [1.0, 24.0, 100.0] {
            let side = patch_side(radius_px).unwrap();
            assert!(support_mask(side, radius_px).is_ok(), "{radius_px}");
        }
    }

    #[test]
    fn support_rejects_invalid_geometry() {
        for value in [0.0, -1.0, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(
                matches!(
                    support_mask(16, value),
                    Err(NumericError::InvalidRadius { .. })
                ),
                "{value}"
            );
        }
        for side in [0, 1, 4, 12, 1024] {
            assert_eq!(
                support_mask(side, 2.0),
                Err(NumericError::UnsupportedPatchSide {
                    width: side,
                    height: side
                }),
                "{side}"
            );
        }
    }
}
