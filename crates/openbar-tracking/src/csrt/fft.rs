//! Radix-2 decimation-in-time complex 2D FFT on `f64` (Cooley and Tukey, 1965).
//!
//! Single-threaded, power-of-two sizes only, with a fixed butterfly and summation order:
//! every row is transformed first (top to bottom), then every column (left to right). Data is
//! row-major (`index = y * width + x`). The forward transform uses `e^{-2 pi i k / n}`; the
//! inverse uses the conjugate twiddles and scales by `1 / (width * height)`, which is an exact
//! power of two. Twiddles come from [`super::detmath::unit_roots_half`], so no libm is
//! involved. Each side is capped at [`MAX_TRANSFORM_LEN`]. Pinned digests prove the output
//! bit-identical on the x86_64 CI platforms (Ubuntu, Windows); aarch64 is argued by
//! construction, not proven.

use super::detmath::{unit_roots_half, MAX_TRANSFORM_LEN};
use super::{Complex, NumericError};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Direction {
    Forward,
    Inverse,
}

/// Precomputed plan for a `width x height` complex 2D FFT.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(not(test), expect(dead_code, reason = "consumed by later csrt slices"))]
pub(crate) struct Fft2d {
    width: usize,
    height: usize,
    /// Forward twiddles `e^{-2 pi i k / width}` for `k < width / 2`.
    row_twiddles: Vec<Complex>,
    /// Forward twiddles `e^{-2 pi i k / height}` for `k < height / 2`.
    column_twiddles: Vec<Complex>,
}

impl Fft2d {
    /// Rejects zero and non-power-of-two sizes ([`NumericError::InvalidFftSize`]) and sides
    /// above [`MAX_TRANSFORM_LEN`] ([`NumericError::FftSizeTooLarge`]) before allocating.
    #[cfg_attr(not(test), expect(dead_code, reason = "consumed by later csrt slices"))]
    pub(crate) fn try_new(width: usize, height: usize) -> Result<Self, NumericError> {
        if !width.is_power_of_two() || !height.is_power_of_two() {
            return Err(NumericError::InvalidFftSize { width, height });
        }
        if width > MAX_TRANSFORM_LEN || height > MAX_TRANSFORM_LEN {
            return Err(NumericError::FftSizeTooLarge {
                width,
                height,
                max: MAX_TRANSFORM_LEN,
            });
        }
        Ok(Self {
            width,
            height,
            row_twiddles: forward_twiddles(width)?,
            column_twiddles: forward_twiddles(height)?,
        })
    }

    #[cfg_attr(not(test), expect(dead_code, reason = "consumed by later csrt slices"))]
    pub(crate) fn width(&self) -> usize {
        self.width
    }

    #[cfg_attr(not(test), expect(dead_code, reason = "consumed by later csrt slices"))]
    pub(crate) fn height(&self) -> usize {
        self.height
    }

    /// Unscaled forward DFT: `X[v][u] = sum_y sum_x x[y][x] e^{-2 pi i (u x / W + v y / H)}`.
    #[cfg_attr(not(test), expect(dead_code, reason = "consumed by later csrt slices"))]
    pub(crate) fn forward(&self, input: &[Complex]) -> Result<Vec<Complex>, NumericError> {
        self.transform(input, Direction::Forward)
    }

    /// Inverse DFT including the `1 / (width * height)` scaling, so `inverse(forward(x)) ~ x`.
    #[cfg_attr(not(test), expect(dead_code, reason = "consumed by later csrt slices"))]
    pub(crate) fn inverse(&self, input: &[Complex]) -> Result<Vec<Complex>, NumericError> {
        self.transform(input, Direction::Inverse)
    }

    fn transform(
        &self,
        input: &[Complex],
        direction: Direction,
    ) -> Result<Vec<Complex>, NumericError> {
        let len = self.width * self.height;
        if input.len() != len {
            return Err(NumericError::BufferLengthMismatch {
                expected: len,
                actual: input.len(),
            });
        }
        if let Some(index) = input.iter().position(|value| !value.is_finite()) {
            return Err(NumericError::NonFiniteInput { index });
        }
        let mut data = input.to_vec();
        for row in data.chunks_exact_mut(self.width) {
            fft_in_place(row, &self.row_twiddles, direction);
        }
        let mut column = vec![Complex::ZERO; self.height];
        for x in 0..self.width {
            for (y, slot) in column.iter_mut().enumerate() {
                *slot = data[y * self.width + x];
            }
            fft_in_place(&mut column, &self.column_twiddles, direction);
            for (y, value) in column.iter().enumerate() {
                data[y * self.width + x] = *value;
            }
        }
        let output = match direction {
            Direction::Forward => data,
            Direction::Inverse => {
                // Exact: `len` is a power of two, so its reciprocal is representable.
                let scale = 1.0 / len as f64;
                data.into_iter().map(|value| value.scale(scale)).collect()
            }
        };
        match output.iter().position(|value| !value.is_finite()) {
            Some(index) => Err(NumericError::NonFiniteOutput { index }),
            None => Ok(output),
        }
    }
}

fn forward_twiddles(len: usize) -> Result<Vec<Complex>, NumericError> {
    let roots = unit_roots_half(len)?;
    Ok(roots.into_iter().map(Complex::conj).collect())
}

/// Iterative in-place radix-2 FFT of a power-of-two `data` (length checked by the plan).
fn fft_in_place(data: &mut [Complex], forward_twiddles: &[Complex], direction: Direction) {
    let len = data.len();
    if len <= 1 {
        return;
    }
    bit_reverse_permute(data);
    let mut size = 2;
    while size <= len {
        let half = size / 2;
        let stride = len / size;
        for start in (0..len).step_by(size) {
            for offset in 0..half {
                let twiddle = forward_twiddles[offset * stride];
                let twiddle = match direction {
                    Direction::Forward => twiddle,
                    Direction::Inverse => twiddle.conj(),
                };
                let even = data[start + offset];
                let odd = data[start + offset + half] * twiddle;
                data[start + offset] = even + odd;
                data[start + offset + half] = even - odd;
            }
        }
        size *= 2;
    }
}

fn bit_reverse_permute(data: &mut [Complex]) {
    let shift = usize::BITS - data.len().trailing_zeros();
    for index in 0..data.len() {
        let reversed = index.reverse_bits() >> shift;
        if index < reversed {
            data.swap(index, reversed);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::super::detmath::unit_roots;
    use super::super::tests::fnv1a_64;
    use super::*;

    /// FNV-1a 64 over `re`, `im` of forward then inverse 32x16 FFT of `signal(512, 2026)`. Pins
    /// x86_64 CI (Ubuntu, Windows); aarch64 argued by construction, not proven. A change
    /// requires bumping the `csrt-gray-v1` tracker VERSION; never update it to pass a test.
    const PINNED_DIGEST_FFT_32X16: u64 = 0xbcb0_a3a0_1248_312d;

    const TOLERANCE: f64 = 1e-12;
    #[rustfmt::skip]
    const SIZES: [(usize, usize); 9] =
        [(1, 1), (1, 8), (8, 1), (2, 2), (4, 4), (8, 16), (16, 8), (32, 4), (64, 1)];

    /// Deterministic pseudo-random values in `[-1, 1)` from a 64-bit LCG (Knuth MMIX).
    fn signal(len: usize, seed: u64) -> Vec<Complex> {
        let mut state = seed;
        let mut next = move || {
            state = state
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            (state >> 11) as f64 / (1u64 << 52) as f64 - 1.0
        };
        (0..len).map(|_| Complex::new(next(), next())).collect()
    }

    /// Reference O(N^2) DFT using the same deterministic twiddles (no libm).
    fn naive_dft(input: &[Complex], width: usize, height: usize) -> Vec<Complex> {
        let row_roots = unit_roots(width).unwrap();
        let column_roots = unit_roots(height).unwrap();
        let mut output = Vec::with_capacity(input.len());
        for v in 0..height {
            for u in 0..width {
                let mut sum = Complex::ZERO;
                for y in 0..height {
                    for x in 0..width {
                        let phase = row_roots[(u * x) % width] * column_roots[(v * y) % height];
                        sum = sum + input[y * width + x] * phase.conj();
                    }
                }
                output.push(sum);
            }
        }
        output
    }

    fn max_error(left: &[Complex], right: &[Complex]) -> f64 {
        assert_eq!(left.len(), right.len());
        left.iter()
            .zip(right)
            .map(|(a, b)| (a.re - b.re).abs().max((a.im - b.im).abs()))
            .fold(0.0, f64::max)
    }

    #[test]
    fn forward_matches_naive_dft() {
        for (seed, (width, height)) in SIZES.into_iter().enumerate() {
            let plan = Fft2d::try_new(width, height).unwrap();
            let input = signal(width * height, seed as u64 + 1);
            let error = max_error(
                &plan.forward(&input).unwrap(),
                &naive_dft(&input, width, height),
            );
            assert!(error <= TOLERANCE, "{width}x{height}: {error:e}");
        }
    }

    #[test]
    fn inverse_round_trips() {
        for (width, height) in SIZES.into_iter().chain([(64, 64), (128, 32)]) {
            let plan = Fft2d::try_new(width, height).unwrap();
            assert_eq!((plan.width(), plan.height()), (width, height));
            let input = signal(width * height, 99);
            let spectrum = plan.forward(&input).unwrap();
            let error = max_error(&plan.inverse(&spectrum).unwrap(), &input);
            assert!(error <= TOLERANCE, "{width}x{height}: {error:e}");
        }
    }

    #[test]
    fn parseval_holds() {
        let plan = Fft2d::try_new(16, 32).unwrap();
        let input = signal(16 * 32, 7);
        let spectrum = plan.forward(&input).unwrap();
        let energy: f64 = input.iter().map(|value| value.norm_sqr()).sum();
        let spectral: f64 = spectrum.iter().map(|value| value.norm_sqr()).sum();
        let relative = (energy - spectral / (16.0 * 32.0)).abs() / energy;
        assert!(relative <= TOLERANCE, "{relative:e}");
    }

    #[test]
    fn transform_is_linear() {
        let plan = Fft2d::try_new(8, 16).unwrap();
        let left = signal(128, 3);
        let right = signal(128, 4);
        let (a, b) = (Complex::new(0.75, -1.5), Complex::new(-2.0, 0.25));
        let mixed: Vec<Complex> = left
            .iter()
            .zip(&right)
            .map(|(l, r)| a * *l + b * *r)
            .collect();
        let left_spectrum = plan.forward(&left).unwrap();
        let right_spectrum = plan.forward(&right).unwrap();
        let expected: Vec<Complex> = left_spectrum
            .iter()
            .zip(&right_spectrum)
            .map(|(l, r)| a * *l + b * *r)
            .collect();
        let error = max_error(&plan.forward(&mixed).unwrap(), &expected);
        assert!(error <= TOLERANCE, "{error:e}");
    }

    #[test]
    fn delta_at_origin_gives_exactly_flat_spectrum() {
        let plan = Fft2d::try_new(8, 16).unwrap();
        let mut input = vec![Complex::ZERO; 128];
        input[0] = Complex::ONE;
        let spectrum = plan.forward(&input).unwrap();
        assert!(spectrum.iter().all(|value| *value == Complex::ONE));
        let restored = plan.inverse(&spectrum).unwrap();
        assert!(max_error(&restored, &input) <= TOLERANCE);
    }

    #[test]
    fn constant_input_concentrates_in_dc_bin() {
        let plan = Fft2d::try_new(16, 8).unwrap();
        let input = vec![Complex::new(2.5, -1.0); 128];
        let spectrum = plan.forward(&input).unwrap();
        assert_eq!(spectrum[0], Complex::new(320.0, -128.0));
        let rest = spectrum[1..]
            .iter()
            .map(|value| value.re.abs().max(value.im.abs()));
        assert!(rest.fold(0.0, f64::max) <= TOLERANCE);
    }

    #[test]
    fn repeated_transforms_are_bit_identical() {
        let plan = Fft2d::try_new(32, 16).unwrap();
        let input = signal(512, 11);
        let first = plan.forward(&input).unwrap();
        let second = Fft2d::try_new(32, 16).unwrap().forward(&input).unwrap();
        let bits = |values: &[Complex]| -> Vec<(u64, u64)> {
            values
                .iter()
                .map(|v| (v.re.to_bits(), v.im.to_bits()))
                .collect()
        };
        assert_eq!(bits(&first), bits(&second));
    }

    #[test]
    fn zero_and_non_power_of_two_sizes_are_rejected() {
        for (width, height) in [(0, 8), (8, 0), (0, 0), (6, 8), (8, 12), (3, 3)] {
            assert_eq!(
                Fft2d::try_new(width, height),
                Err(NumericError::InvalidFftSize { width, height })
            );
        }
    }

    #[test]
    fn sides_are_bounded_at_max_transform_len() {
        for (width, height) in [(MAX_TRANSFORM_LEN, 1), (1, MAX_TRANSFORM_LEN)] {
            let plan = Fft2d::try_new(width, height).unwrap();
            let input = signal(MAX_TRANSFORM_LEN, 5);
            let error = max_error(
                &plan.inverse(&plan.forward(&input).unwrap()).unwrap(),
                &input,
            );
            assert!(error <= TOLERANCE, "{width}x{height}: {error:e}");
        }
        let past = MAX_TRANSFORM_LEN * 2;
        let huge = 1usize << (usize::BITS - 1);
        for (width, height) in [(past, 1), (1, past), (1 << 30, 1), (1 << 59, 1), (huge, 2)] {
            assert_eq!(
                Fft2d::try_new(width, height),
                Err(NumericError::FftSizeTooLarge {
                    width,
                    height,
                    max: MAX_TRANSFORM_LEN,
                })
            );
        }
    }

    #[test]
    fn pinned_digest_of_forward_and_inverse_is_stable() {
        let plan = Fft2d::try_new(32, 16).unwrap();
        let spectrum = plan.forward(&signal(512, 2026)).unwrap();
        let restored = plan.inverse(&spectrum).unwrap();
        let values = spectrum.iter().chain(&restored).flat_map(|v| [v.re, v.im]);
        let digest = fnv1a_64(values);
        assert_eq!(
            digest, PINNED_DIGEST_FFT_32X16,
            "computed digest {digest:#018x}"
        );
    }

    #[test]
    fn wrong_buffer_length_and_non_finite_input_are_rejected() {
        let plan = Fft2d::try_new(4, 2).unwrap();
        let expected = NumericError::BufferLengthMismatch {
            expected: 8,
            actual: 7,
        };
        assert_eq!(plan.forward(&[Complex::ZERO; 7]), Err(expected));
        assert_eq!(plan.inverse(&[Complex::ZERO; 7]), Err(expected));
        for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let mut input = vec![Complex::ZERO; 8];
            input[5] = Complex::new(0.0, bad);
            let error = NumericError::NonFiniteInput { index: 5 };
            assert_eq!(plan.forward(&input), Err(error));
            assert_eq!(plan.inverse(&input), Err(error));
        }
    }

    #[test]
    fn overflowing_output_is_rejected() {
        let plan = Fft2d::try_new(2, 1).unwrap();
        let input = [Complex::new(f64::MAX, 0.0); 2];
        assert_eq!(
            plan.forward(&input),
            Err(NumericError::NonFiniteOutput { index: 0 })
        );
    }
}
