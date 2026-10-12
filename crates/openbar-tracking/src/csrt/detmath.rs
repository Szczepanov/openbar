//! Deterministic, transcendental-free math for the `csrt-gray-v1` numeric core.
//!
//! Only IEEE-754 correctly-rounded operations (`+ - * /`, square root) appear here, each in a
//! fixed order and never fused. Pinned digests prove the results bit-identical on the x86_64
//! CI platforms (Ubuntu, Windows); aarch64 is argued by construction, not proven.
//!
//! * Roots of unity come from the half-angle recurrence
//!   `cos(t/2) = sqrt((1 + cos t) / 2)`, `sin(t/2) = sin t / (2 cos(t/2))`, starting from the
//!   correctly-rounded `cos(pi/4) = sin(pi/4) = sqrt(1/2)`. An angle `2 pi k / n` on a
//!   power-of-two grid is the product of the half-angle roots selected by the bits of `k`,
//!   after an exact reduction to the first octant by quadrant and octant symmetry.
//! * The Hann window is the periodic (DFT-even) form `w[i] = sin^2(pi i / n)`.
//! * [`det_exp`] uses Cody-Waite (1980) range reduction `x = k ln2 + r`, `|r| <= ln2 / 2`,
//!   with the standard split of `ln 2` into a high part with 32 significant high-word bits
//!   (so `k * LN2_HI` is exact) and a low correction. The same split is used in fdlibm; the
//!   constants are mathematical facts and no third-party source was copied. `e^r` is a fixed
//!   degree-13 Taylor polynomial in Horner form; its truncation error is at most
//!   `(ln2/2)^14 / 14! ~ 4.1e-18` absolute (relative up to `~5.9e-18`, for negative `r`).
//!   For normal results the error is within 2 ulp of the true exponential (checked against
//!   the platform exponential in tests). Subnormal results are rounded twice (the polynomial,
//!   then the final scaling onto the subnormal grid), so the 2 ulp bound does not apply there.

use super::{Complex, NumericError};

/// Upper bound on every 1D length in the numeric core: FFT sides, root tables and windows.
/// It comfortably exceeds CSRT template sizes and caps a full 2D FFT buffer at
/// `4096 * 4096 * 16 B = 256 MiB`, so oversized requests fail with a typed error instead of a
/// capacity-overflow panic or an allocation abort.
#[cfg_attr(not(test), expect(dead_code, reason = "consumed by later csrt slices"))]
pub(crate) const MAX_TRANSFORM_LEN: usize = 4096;

/// High part of `ln 2`, with its low 21 mantissa bits zero so `k * LN2_HI` is exact.
const LN2_HI: f64 = f64::from_bits(0x3fe6_2e42_fee0_0000);
/// `ln 2 - LN2_HI`, rounded to nearest.
const LN2_LO: f64 = f64::from_bits(0x3dea_39ef_3579_3c76);
/// `1 / ln 2`, rounded to nearest.
const INV_LN2: f64 = f64::from_bits(0x3ff7_1547_652b_82fe);
/// Largest argument whose exponential is finite (`ln(f64::MAX)` rounded down).
const EXP_MAX_ARG: f64 = 709.782_712_893_384;
/// Below this argument the exponential is under half the smallest subnormal and rounds to zero.
const EXP_UNDERFLOW_ARG: f64 = -746.0;
/// Taylor coefficients `1 / j!` for `j = 2..=13`; each is a single correctly-rounded division.
const EXP_TAYLOR: [f64; 12] = [
    1.0 / 2.0,
    1.0 / 6.0,
    1.0 / 24.0,
    1.0 / 120.0,
    1.0 / 720.0,
    1.0 / 5_040.0,
    1.0 / 40_320.0,
    1.0 / 362_880.0,
    1.0 / 3_628_800.0,
    1.0 / 39_916_800.0,
    1.0 / 479_001_600.0,
    1.0 / 6_227_020_800.0,
];

/// Accepts a non-zero power of two no larger than [`MAX_TRANSFORM_LEN`].
#[cfg_attr(not(test), expect(dead_code, reason = "consumed by later csrt slices"))]
pub(crate) fn validate_table_len(len: usize) -> Result<(), NumericError> {
    if !len.is_power_of_two() {
        Err(NumericError::InvalidLength { len })
    } else if len > MAX_TRANSFORM_LEN {
        Err(NumericError::LengthTooLarge {
            len,
            max: MAX_TRANSFORM_LEN,
        })
    } else {
        Ok(())
    }
}

/// Returns `cos(2 pi k / len) + i sin(2 pi k / len)` for `k` in `0..len`.
#[cfg_attr(not(test), expect(dead_code, reason = "consumed by later csrt slices"))]
pub(crate) fn unit_roots(len: usize) -> Result<Vec<Complex>, NumericError> {
    validate_table_len(len)?;
    Ok(roots_prefix(len, len))
}

/// The first `len / 2` entries of [`unit_roots`], without computing the rest.
#[cfg_attr(not(test), expect(dead_code, reason = "consumed by later csrt slices"))]
pub(crate) fn unit_roots_half(len: usize) -> Result<Vec<Complex>, NumericError> {
    validate_table_len(len)?;
    Ok(roots_prefix(len, len / 2))
}

/// Periodic Hann window `w[i] = sin^2(pi i / len) = (1 - cos(2 pi i / len)) / 2`, `i < len`.
#[cfg_attr(not(test), expect(dead_code, reason = "consumed by later csrt slices"))]
pub(crate) fn hann_window_periodic(len: usize) -> Result<Vec<f64>, NumericError> {
    validate_table_len(len)?;
    let roots = roots_prefix(len * 2, len);
    Ok(roots.into_iter().map(|root| root.im * root.im).collect())
}

/// Deterministic `e^x`. Non-finite arguments and arguments whose result would overflow are
/// rejected; arguments below the underflow threshold return `+0.0`.
#[cfg_attr(not(test), expect(dead_code, reason = "consumed by later csrt slices"))]
pub(crate) fn det_exp(x: f64) -> Result<f64, NumericError> {
    if !x.is_finite() {
        return Err(NumericError::NonFiniteExpArgument { value: x });
    }
    if x > EXP_MAX_ARG {
        return Err(NumericError::ExpOverflow { value: x });
    }
    if x < EXP_UNDERFLOW_ARG {
        return Ok(0.0);
    }
    let k = (x * INV_LN2).round();
    let r = (x - k * LN2_HI) - k * LN2_LO;
    let tail = EXP_TAYLOR
        .iter()
        .rev()
        .fold(0.0, |acc, &coefficient| coefficient + r * acc);
    let poly = 1.0 + r * (1.0 + r * tail);
    // |k| <= 1077 here, so the cast is exact.
    let result = scale_by_power_of_two(poly, k as i32);
    if result.is_finite() {
        Ok(result)
    } else {
        Err(NumericError::ExpOverflow { value: x })
    }
}

/// `value * 2^exponent` in two steps; the first step is exact for `value` near 1, so only the
/// second rounds (which matters for subnormal results only).
fn scale_by_power_of_two(value: f64, exponent: i32) -> f64 {
    let first = exponent / 2;
    value * power_of_two(first) * power_of_two(exponent - first)
}

/// Exact `2^exponent` for a normal exponent in `-1022..=1023`.
fn power_of_two(exponent: i32) -> f64 {
    debug_assert!((-1022..=1023).contains(&exponent));
    f64::from_bits(((exponent + 1023) as u64) << 52)
}

/// `cos(pi / 2^j) + i sin(pi / 2^j)` for `j = 2..=max_j`, stored at index `j - 2`. The `pi / 4`
/// entry is `sqrt(1/2)` for both parts so that octant-mirrored roots stay bit-symmetric.
fn half_angle_roots(max_j: u32) -> Vec<Complex> {
    let root_half = 0.5f64.sqrt();
    let start = vec![Complex::new(root_half, root_half)];
    (3..=max_j).fold(start, |mut table, _| {
        let previous = table[table.len() - 1];
        let half_cos = ((1.0 + previous.re) / 2.0).sqrt();
        let half_sin = previous.im / (2.0 * half_cos);
        table.push(Complex::new(half_cos, half_sin));
        table
    })
}

/// The first `count` roots `e^{2 pi i k / len}` of a power-of-two `len` (validated by callers).
fn roots_prefix(len: usize, count: usize) -> Vec<Complex> {
    // Work on a grid of at least 8 points so quadrant and octant boundaries are integers.
    let fine_len = len.max(8);
    let stride = fine_len / len;
    let grid_bits = fine_len.trailing_zeros();
    let base = half_angle_roots(grid_bits);
    (0..count)
        .map(|k| fine_grid_root(k * stride, fine_len, grid_bits, &base))
        .collect()
}

/// Root `k < fine_len` on the fine grid.
fn fine_grid_root(k: usize, fine_len: usize, grid_bits: u32, base: &[Complex]) -> Complex {
    let quarter = fine_len / 4;
    let eighth = fine_len / 8;
    let quadrant = k / quarter;
    let offset = k % quarter;
    let first = if offset <= eighth {
        octant_root(offset, grid_bits, base)
    } else {
        let mirrored = octant_root(quarter - offset, grid_bits, base);
        Complex::new(mirrored.im, mirrored.re)
    };
    // `0.0 - x` instead of `-x` keeps exact zeros as `+0.0`.
    match quadrant {
        0 => first,
        1 => Complex::new(0.0 - first.im, first.re),
        2 => Complex::new(0.0 - first.re, 0.0 - first.im),
        _ => Complex::new(first.im, 0.0 - first.re),
    }
}

/// Root for `offset <= fine_len / 8`: bit `b` of `offset` contributes `pi / 2^(grid_bits - 1 - b)`,
/// i.e. `base[grid_bits - 3 - b]`. Factors are multiplied from the lowest bit up, a fixed order.
fn octant_root(offset: usize, grid_bits: u32, base: &[Complex]) -> Complex {
    (0..=grid_bits - 3)
        .filter(|&bit| (offset >> bit) & 1 == 1)
        .fold(Complex::ONE, |acc, bit| {
            acc * base[(grid_bits - 3 - bit) as usize]
        })
}

#[cfg(test)]
mod tests {
    use super::super::tests::fnv1a_64;
    use super::*;
    use std::f64::consts::PI;

    // Pinned FNV-1a 64-bit digests over little-endian `to_bits` bytes. They pin the numeric
    // core bit-for-bit on the x86_64 CI platforms (Ubuntu, Windows); aarch64 is argued by
    // construction (only correctly-rounded, unfused IEEE operations), not proven. A change
    // changes tracker output and requires bumping the `csrt-gray-v1` tracker VERSION; never
    // update a digest just to make a test pass.
    /// `cos`, `sin` of each root in `unit_roots(64)`, then the 64 `hann_window_periodic(64)` values.
    const PINNED_DIGEST_N64: u64 = 0x363a_f040_0576_7624;
    /// `det_exp` over the sweep in `exp_sweep`.
    const PINNED_DIGEST_EXP: u64 = 0x6eeb_1301_e6b4_f61a;

    fn ulp_distance(left: f64, right: f64) -> u64 {
        assert!(left >= 0.0 && right >= 0.0, "{left} {right}");
        left.to_bits().abs_diff(right.to_bits())
    }

    #[test]
    fn pinned_digest_of_twiddles_and_hann_window_is_stable() {
        let roots = unit_roots(64).unwrap();
        let hann = hann_window_periodic(64).unwrap();
        let values = roots
            .iter()
            .flat_map(|root| [root.re, root.im])
            .chain(hann.iter().copied());
        let digest = fnv1a_64(values);
        assert_eq!(digest, PINNED_DIGEST_N64, "computed digest {digest:#018x}");
    }

    /// Covers underflow-to-zero, subnormal, normal and near-overflow results.
    fn exp_sweep() -> impl Iterator<Item = f64> {
        let grid = (0..=29_110).map(|i| -746.0 + f64::from(i) * 0.05);
        grid.chain([EXP_MAX_ARG, -0.0, 1e-300, -1e-300, 0.346_573_590_279_972_6])
    }

    #[test]
    fn pinned_digest_of_det_exp_sweep_is_stable() {
        let digest = fnv1a_64(exp_sweep().map(|x| det_exp(x).unwrap()));
        assert_eq!(digest, PINNED_DIGEST_EXP, "computed digest {digest:#018x}");
    }

    #[test]
    fn unit_roots_match_platform_trig_within_a_few_ulp() {
        for grid_bits in 0..=12 {
            let len = 1usize << grid_bits;
            let roots = unit_roots(len).unwrap();
            assert_eq!(roots.len(), len);
            for (k, root) in roots.iter().enumerate() {
                let angle = 2.0 * PI * k as f64 / len as f64;
                let error = (root.re - angle.cos())
                    .abs()
                    .max((root.im - angle.sin()).abs());
                assert!(error <= 8.0 * f64::EPSILON, "k={k} n={len}: {error:e}");
            }
        }
    }

    #[test]
    fn unit_roots_are_exact_on_axes_without_negative_zero() {
        let roots = unit_roots(64).unwrap();
        let exact = [
            (0, 1.0, 0.0),
            (16, 0.0, 1.0),
            (32, -1.0, 0.0),
            (48, 0.0, -1.0),
        ];
        for (k, re, im) in exact {
            assert_eq!(roots[k].re.to_bits(), f64::to_bits(re), "re k={k}");
            assert_eq!(roots[k].im.to_bits(), f64::to_bits(im), "im k={k}");
        }
        assert_eq!(unit_roots(1).unwrap(), vec![Complex::ONE]);
        assert_eq!(
            unit_roots(2).unwrap(),
            vec![Complex::ONE, Complex::new(-1.0, 0.0)]
        );
    }

    #[test]
    fn unit_roots_have_exact_conjugate_symmetry() {
        let roots = unit_roots(256).unwrap();
        for k in 1..256 {
            assert_eq!(roots[k].re.to_bits(), roots[256 - k].re.to_bits(), "k={k}");
            assert_eq!(roots[k].im, 0.0 - roots[256 - k].im, "k={k}");
        }
    }

    #[test]
    fn hann_window_is_symmetric_bounded_and_matches_closed_form() {
        let hann = hann_window_periodic(64).unwrap();
        assert_eq!(hann.len(), 64);
        assert_eq!(hann[0].to_bits(), 0.0f64.to_bits());
        assert_eq!(hann[32], 1.0);
        for i in 1..64 {
            assert_eq!(hann[i].to_bits(), hann[64 - i].to_bits(), "i={i}");
        }
        for (i, value) in hann.iter().enumerate() {
            assert!((0.0..=1.0).contains(value));
            let expected = 0.5 - 0.5 * (2.0 * PI * i as f64 / 64.0).cos();
            assert!((value - expected).abs() <= 4.0 * f64::EPSILON, "i={i}");
        }
        assert_eq!(hann_window_periodic(1).unwrap(), vec![0.0]);
        assert_eq!(hann_window_periodic(2).unwrap(), vec![0.0, 1.0]);
    }

    #[test]
    fn zero_and_non_power_of_two_lengths_are_rejected() {
        for len in [0, 3, 6, 12, 100] {
            let error = NumericError::InvalidLength { len };
            assert_eq!(unit_roots(len), Err(error));
            assert_eq!(unit_roots_half(len), Err(error));
            assert_eq!(hann_window_periodic(len), Err(error));
            assert_eq!(validate_table_len(len), Err(error));
        }
    }

    #[test]
    fn lengths_are_bounded_at_max_transform_len() {
        assert_eq!(MAX_TRANSFORM_LEN, 4096);
        assert_eq!(unit_roots(MAX_TRANSFORM_LEN).unwrap().len(), 4096);
        assert_eq!(unit_roots_half(MAX_TRANSFORM_LEN).unwrap().len(), 2048);
        assert_eq!(hann_window_periodic(MAX_TRANSFORM_LEN).unwrap().len(), 4096);
        let huge = 1usize << (usize::BITS - 1);
        for len in [MAX_TRANSFORM_LEN * 2, 1 << 30, huge] {
            let error = NumericError::LengthTooLarge {
                len,
                max: MAX_TRANSFORM_LEN,
            };
            assert_eq!(unit_roots(len), Err(error));
            assert_eq!(unit_roots_half(len), Err(error));
            assert_eq!(hann_window_periodic(len), Err(error));
        }
    }

    #[test]
    fn unit_roots_half_is_the_prefix_of_unit_roots() {
        for len in [1, 2, 4, 8, 64, 512] {
            let full = unit_roots(len).unwrap();
            assert_eq!(unit_roots_half(len).unwrap(), full[..len / 2].to_vec());
        }
    }

    #[test]
    fn det_exp_is_within_two_ulp_of_platform_exp() {
        let samples = (0..=140_000).map(|i| -700.0 + f64::from(i) * 0.01);
        let small = [1e-300, -1e-300, 1e-12, -1e-12, 0.5, -0.5, 0.346, -0.347];
        for x in samples.chain(small) {
            let actual = det_exp(x).unwrap();
            let distance = ulp_distance(actual, x.exp());
            assert!(distance <= 2, "x={x} actual={actual} ulp={distance}");
        }
    }

    #[test]
    fn det_exp_exact_points_and_range_limits() {
        assert_eq!(det_exp(0.0), Ok(1.0));
        assert_eq!(det_exp(-0.0), Ok(1.0));
        assert!(ulp_distance(det_exp(1.0).unwrap(), std::f64::consts::E) <= 1);
        let largest = det_exp(EXP_MAX_ARG).unwrap();
        assert!(largest.is_finite() && largest > 1.79e308);
        assert!(ulp_distance(largest, EXP_MAX_ARG.exp()) <= 2);
        let subnormal = det_exp(-740.0).unwrap();
        assert!(subnormal > 0.0 && subnormal < f64::MIN_POSITIVE);
        assert!(ulp_distance(subnormal, (-740.0f64).exp()) <= 1);
        assert_eq!(det_exp(-745.9), Ok(0.0));
        assert_eq!(det_exp(-746.5), Ok(0.0));
        assert_eq!(det_exp(-1e300), Ok(0.0));
        assert_eq!(det_exp(f64::MIN), Ok(0.0));
    }

    #[test]
    fn det_exp_rejects_non_finite_and_overflowing_arguments() {
        assert!(matches!(
            det_exp(f64::NAN),
            Err(NumericError::NonFiniteExpArgument { value }) if value.is_nan()
        ));
        for value in [f64::INFINITY, f64::NEG_INFINITY] {
            assert_eq!(
                det_exp(value),
                Err(NumericError::NonFiniteExpArgument { value })
            );
        }
        for value in [709.79, 710.0, 1e300, f64::MAX] {
            assert_eq!(det_exp(value), Err(NumericError::ExpOverflow { value }));
        }
    }

    #[test]
    fn constants_match_their_documented_values() {
        assert!((LN2_HI + LN2_LO - std::f64::consts::LN_2).abs() <= f64::EPSILON / 4.0);
        assert_eq!(LN2_HI.to_bits() & ((1 << 21) - 1), 0);
        assert_eq!(INV_LN2, std::f64::consts::LOG2_E);
        assert!(EXP_MAX_ARG.exp().is_finite());
        assert!(f64::from_bits(EXP_MAX_ARG.to_bits() + 1)
            .exp()
            .is_infinite());
        assert_eq!(power_of_two(0), 1.0);
        assert_eq!(power_of_two(-1022), f64::MIN_POSITIVE);
        assert_eq!(scale_by_power_of_two(0.75, 1024), 1.5 * 2f64.powi(1023));
        assert_eq!(scale_by_power_of_two(1.0, -1074), f64::from_bits(1));
    }
}
