//! Masked correlation-filter learning and response calculation (slice S3).
//!
//! Evaluates the circular response `inverse(conj(H) * X)` and solves the spatially
//! constrained ridge objective `||A * g - y||^2 + lambda * ||g||^2` via scaled ADMM.
//!
//! Input spectra and output responses are validated for finiteness and Hermitian
//! symmetry. No transcendentals, platform libm or fused multiply-add are used.

use super::fft::Fft2d;
use super::{Complex, NumericError};

pub(super) const MIN_FILTER_SIDE: usize = 1;
pub(super) const MAX_FILTER_SIDE: usize = 512;

/// Training configuration for the grayscale CSRT filter (S3 parameters).
#[derive(Debug, Clone, Copy, PartialEq)]
pub(super) struct FilterTrainingParams {
    pub(super) lambda: f64,
    pub(super) admm_iterations: usize,
    pub(super) mu_initial: f64,
    pub(super) mu_growth: f64,
    pub(super) mu_max: f64,
}

impl Default for FilterTrainingParams {
    fn default() -> Self {
        Self {
            lambda: 0.01,
            admm_iterations: 4,
            mu_initial: 1.0,
            mu_growth: 3.0,
            mu_max: 81.0,
        }
    }
}

impl FilterTrainingParams {
    pub(super) fn validate(self) -> Result<(), NumericError> {
        if !self.lambda.is_finite() || self.lambda <= 0.0 {
            return Err(NumericError::InvalidTrainingParameter { field: "lambda" });
        }
        if !(1..=64).contains(&self.admm_iterations) {
            return Err(NumericError::InvalidTrainingParameter {
                field: "admm_iterations",
            });
        }
        if !self.mu_initial.is_finite() || self.mu_initial <= 0.0 {
            return Err(NumericError::InvalidTrainingParameter {
                field: "mu_initial",
            });
        }
        if !self.mu_growth.is_finite() || self.mu_growth < 1.0 {
            return Err(NumericError::InvalidTrainingParameter { field: "mu_growth" });
        }
        if !self.mu_max.is_finite() || self.mu_max < self.mu_initial {
            return Err(NumericError::InvalidTrainingParameter { field: "mu_max" });
        }
        Ok(())
    }
}

fn validate_filter_fft(fft: &Fft2d) -> Result<(), NumericError> {
    let width = fft.width();
    let height = fft.height();
    if width.is_power_of_two()
        && height.is_power_of_two()
        && (MIN_FILTER_SIDE..=MAX_FILTER_SIDE).contains(&width)
        && (MIN_FILTER_SIDE..=MAX_FILTER_SIDE).contains(&height)
    {
        Ok(())
    } else {
        Err(NumericError::UnsupportedFilterSize { width, height })
    }
}

fn validate_spectrum(
    spectrum: &[Complex],
    width: usize,
    height: usize,
) -> Result<(), NumericError> {
    for (index, c) in spectrum.iter().enumerate() {
        if !c.is_finite() {
            return Err(NumericError::NonFiniteInput { index });
        }
    }
    for y in 0..height {
        let py = (height - y) % height;
        for x in 0..width {
            let px = (width - x) % width;
            let index = y * width + x;
            let paired_index = py * width + px;
            let curr = spectrum[index];
            let pair = spectrum[paired_index];

            if x == px && y == py {
                let tol = 1e-9 * 1.0f64.max(curr.re.abs());
                if curr.im.abs() > tol {
                    return Err(NumericError::NonRealSpectrum { index });
                }
            } else {
                let max_comp = 1.0f64
                    .max(curr.re.abs())
                    .max(pair.re.abs())
                    .max(curr.im.abs())
                    .max(pair.im.abs());
                let tol = 1e-9 * max_comp;
                if (curr.re - pair.re).abs() > tol || (curr.im + pair.im).abs() > tol {
                    return Err(NumericError::NonRealSpectrum { index });
                }
            }
        }
    }
    Ok(())
}

fn validate_and_extract_real(spatial: &[Complex]) -> Result<Vec<f64>, NumericError> {
    let mut max_abs_real = 1.0f64;
    for (index, c) in spatial.iter().enumerate() {
        if !c.is_finite() {
            return Err(NumericError::NonFiniteOutput { index });
        }
        let abs_re = c.re.abs();
        if abs_re > max_abs_real {
            max_abs_real = abs_re;
        }
    }
    let threshold = 1e-9 * max_abs_real;
    let mut result = Vec::with_capacity(spatial.len());
    for (index, c) in spatial.iter().enumerate() {
        if c.im.abs() > threshold {
            return Err(NumericError::NonRealInverse { index });
        }
        result.push(c.re);
    }
    Ok(result)
}

/// Trains the constrained spatial filter via scaled ADMM and returns its unnormalized FFT spectrum `H = FFT(g)`.
pub(super) fn train_filter(
    fft: &Fft2d,
    features_fft: &[Complex],
    label_fft: &[Complex],
    mask: &[f64],
    config: &FilterTrainingParams,
) -> Result<Vec<Complex>, NumericError> {
    validate_filter_fft(fft)?;
    config.validate()?;

    let expected_len = fft.width() * fft.height();
    if features_fft.len() != expected_len {
        return Err(NumericError::BufferLengthMismatch {
            expected: expected_len,
            actual: features_fft.len(),
        });
    }
    if label_fft.len() != expected_len {
        return Err(NumericError::BufferLengthMismatch {
            expected: expected_len,
            actual: label_fft.len(),
        });
    }
    if mask.len() != expected_len {
        return Err(NumericError::BufferLengthMismatch {
            expected: expected_len,
            actual: mask.len(),
        });
    }

    let mut has_support = false;
    for (index, &m) in mask.iter().enumerate() {
        if !m.is_finite() || (m != 0.0 && m != 1.0) {
            return Err(NumericError::InvalidMask { index });
        }
        if m == 1.0 {
            has_support = true;
        }
    }
    if !has_support {
        return Err(NumericError::EmptySupport);
    }

    validate_spectrum(features_fft, fft.width(), fft.height())?;
    validate_spectrum(label_fft, fft.width(), fft.height())?;

    let mut xy_conj = Vec::with_capacity(expected_len);
    let mut x_norm_sqr = Vec::with_capacity(expected_len);
    for (index, (&x, &y)) in features_fft.iter().zip(label_fft.iter()).enumerate() {
        let prod = x * y.conj();
        if !prod.is_finite() {
            return Err(NumericError::NonFiniteOutput { index });
        }
        xy_conj.push(prod);

        let norm_sq = x.norm_sqr();
        if !norm_sq.is_finite() {
            return Err(NumericError::NonFiniteOutput { index });
        }
        x_norm_sqr.push(norm_sq);
    }

    let mut g = vec![0.0f64; expected_len];
    let mut u = vec![0.0f64; expected_len];
    let mut mu = config.mu_initial;

    for _ in 0..config.admm_iterations {
        // Step 1: GminusU = FFT(g - u)
        let mut g_minus_u_spatial = Vec::with_capacity(expected_len);
        for index in 0..expected_len {
            let diff = g[index] - u[index];
            if !diff.is_finite() {
                return Err(NumericError::NonFiniteOutput { index });
            }
            g_minus_u_spatial.push(Complex::new(diff, 0.0));
        }
        let g_minus_u_fft = fft.forward(&g_minus_u_spatial)?;

        // Step 2: H = (X * conj(Y) + mu * GminusU) / (|X|^2 + mu)
        let mut h_fft = Vec::with_capacity(expected_len);
        for index in 0..expected_len {
            let num = xy_conj[index] + g_minus_u_fft[index].scale(mu);
            if !num.is_finite() {
                return Err(NumericError::NonFiniteOutput { index });
            }
            let denom = x_norm_sqr[index] + mu;
            if !denom.is_finite() || denom <= 0.0 {
                return Err(NumericError::InvalidDenominator { index });
            }
            let h_val = Complex::new(num.re / denom, num.im / denom);
            if !h_val.is_finite() {
                return Err(NumericError::NonFiniteOutput { index });
            }
            h_fft.push(h_val);
        }

        // Step 3: Inverse H -> h; validate imaginary residual before taking real components
        let h_spatial_complex = fft.inverse(&h_fft)?;
        let h = validate_and_extract_real(&h_spatial_complex)?;

        // Step 4: g = m * (mu / (lambda + mu)) * (h + u); unsupported coefficients are exactly zero
        let lambda_plus_mu = config.lambda + mu;
        if !lambda_plus_mu.is_finite() || lambda_plus_mu <= 0.0 {
            return Err(NumericError::InvalidDenominator { index: 0 });
        }
        let factor = mu / lambda_plus_mu;
        if !factor.is_finite() {
            return Err(NumericError::NonFiniteOutput { index: 0 });
        }
        for index in 0..expected_len {
            if mask[index] == 0.0 {
                g[index] = 0.0;
            } else {
                let sum_hu = h[index] + u[index];
                if !sum_hu.is_finite() {
                    return Err(NumericError::NonFiniteOutput { index });
                }
                let g_val = factor * sum_hu;
                if !g_val.is_finite() {
                    return Err(NumericError::NonFiniteOutput { index });
                }
                g[index] = g_val;
            }
        }

        // Step 5: u = u + h - g
        for index in 0..expected_len {
            let next_u = u[index] + h[index] - g[index];
            if !next_u.is_finite() {
                return Err(NumericError::NonFiniteOutput { index });
            }
            u[index] = next_u;
        }

        // Step 6: Update mu: if mu >= mu_max / mu_growth, assign mu_max; otherwise multiply by mu_growth
        if mu >= config.mu_max / config.mu_growth {
            mu = config.mu_max;
        } else {
            mu *= config.mu_growth;
        }
    }

    // Return FFT(g), not H from the last unconstrained step
    let g_complex: Vec<Complex> = g.into_iter().map(|val| Complex::new(val, 0.0)).collect();
    fft.forward(&g_complex)
}

/// Computes the real correlation response `inverse(conj(filter_fft) * features_fft)`.
pub(super) fn response(
    fft: &Fft2d,
    filter_fft: &[Complex],
    features_fft: &[Complex],
) -> Result<Vec<f64>, NumericError> {
    validate_filter_fft(fft)?;
    let expected_len = fft.width() * fft.height();
    if filter_fft.len() != expected_len {
        return Err(NumericError::BufferLengthMismatch {
            expected: expected_len,
            actual: filter_fft.len(),
        });
    }
    if features_fft.len() != expected_len {
        return Err(NumericError::BufferLengthMismatch {
            expected: expected_len,
            actual: features_fft.len(),
        });
    }
    validate_spectrum(filter_fft, fft.width(), fft.height())?;
    validate_spectrum(features_fft, fft.width(), fft.height())?;

    let mut product = Vec::with_capacity(expected_len);
    for (index, (&h, &x)) in filter_fft.iter().zip(features_fft.iter()).enumerate() {
        let s = h.conj() * x;
        if !s.is_finite() {
            return Err(NumericError::NonFiniteOutput { index });
        }
        product.push(s);
    }

    let spatial = fft.inverse(&product)?;
    validate_and_extract_real(&spatial)
}

/// Blends an existing filter spectrum with a candidate filter spectrum:
/// `H_next = (1 - learning_rate) * H_old + learning_rate * H_candidate`.
pub(super) fn blend_filters(
    old: &[Complex],
    candidate: &[Complex],
    learning_rate: f64,
) -> Result<Vec<Complex>, NumericError> {
    if !learning_rate.is_finite() || !(0.0..=1.0).contains(&learning_rate) {
        return Err(NumericError::InvalidTrainingParameter {
            field: "learning_rate",
        });
    }
    if old.len() != candidate.len() {
        return Err(NumericError::BufferLengthMismatch {
            expected: old.len(),
            actual: candidate.len(),
        });
    }
    for (index, (&o, &c)) in old.iter().zip(candidate.iter()).enumerate() {
        if !o.is_finite() || !c.is_finite() {
            return Err(NumericError::NonFiniteInput { index });
        }
    }
    if learning_rate == 0.0 {
        return Ok(old.to_vec());
    }
    if learning_rate == 1.0 {
        return Ok(candidate.to_vec());
    }
    let one_minus_lr = 1.0 - learning_rate;
    let mut blended = Vec::with_capacity(old.len());
    for (index, (&o, &c)) in old.iter().zip(candidate.iter()).enumerate() {
        let b = o.scale(one_minus_lr) + c.scale(learning_rate);
        if !b.is_finite() {
            return Err(NumericError::NonFiniteOutput { index });
        }
        blended.push(b);
    }
    Ok(blended)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn response_matches_direct_correlation() {
        let width = 8;
        let height = 8;
        let mut g_spatial = Vec::with_capacity(64);
        let mut x_spatial = Vec::with_capacity(64);
        for y in 0..height {
            for x in 0..width {
                g_spatial.push(Complex::new((y * 8 + x + 1) as f64, 0.0));
                x_spatial.push(Complex::new(((7 - y) * 3 + x * 2 + 5) as f64, 0.0));
            }
        }

        let mut direct = vec![0.0f64; 64];
        for ty in 0..height {
            for tx in 0..width {
                let mut sum = 0.0f64;
                for py in 0..height {
                    for px in 0..width {
                        let g_val = g_spatial[py * width + px].re;
                        let x_val =
                            x_spatial[((py + ty) % height) * width + ((px + tx) % width)].re;
                        sum += g_val * x_val;
                    }
                }
                direct[ty * width + tx] = sum;
            }
        }

        let fft = Fft2d::try_new(width, height).expect("valid fft size");
        let g_fft = fft.forward(&g_spatial).expect("forward g");
        let x_fft = fft.forward(&x_spatial).expect("forward x");
        let resp = response(&fft, &g_fft, &x_fft).expect("response");

        for i in 0..64 {
            let diff = (resp[i] - direct[i]).abs();
            assert!(
                diff <= 1e-9,
                "mismatch at index {i}: fft response {} vs direct {} (diff {})",
                resp[i],
                direct[i],
                diff
            );
        }
    }

    #[test]
    fn response_shift_signs_are_correct() {
        let shifts = [(1, 0), (-1, 0), (0, 1), (0, -1), (2, -1)];

        // Square 8x8 case
        let (width, height) = (8, 8);
        let mut g_spatial = Vec::with_capacity(width * height);
        for y in 0..height {
            for x in 0..width {
                g_spatial.push(Complex::new((y * 8 + x + 1) as f64, 0.0));
            }
        }
        let fft = Fft2d::try_new(width, height).expect("valid fft");
        let g_fft = fft.forward(&g_spatial).expect("forward g");

        for (dx, dy) in shifts {
            let mut x_spatial = vec![Complex::ZERO; width * height];
            for y in 0..height {
                for x in 0..width {
                    let src_y = ((y as isize - dy).rem_euclid(height as isize)) as usize;
                    let src_x = ((x as isize - dx).rem_euclid(width as isize)) as usize;
                    x_spatial[y * width + x] = g_spatial[src_y * width + src_x];
                }
            }
            let x_fft = fft.forward(&x_spatial).expect("forward x");
            let resp = response(&fft, &g_fft, &x_fft).expect("response");

            let target_x = (dx.rem_euclid(width as isize)) as usize;
            let target_y = (dy.rem_euclid(height as isize)) as usize;
            let expected_peak_idx = target_y * width + target_x;

            let peak_val = resp[expected_peak_idx];
            for (idx, &val) in resp.iter().enumerate() {
                if idx != expected_peak_idx {
                    assert!(
                        peak_val > val,
                        "expected unique peak at ({target_x}, {target_y}) idx {expected_peak_idx} with val {peak_val}, but found {val} at {idx}"
                    );
                }
            }
        }

        // Rectangular 8x4 case to expose width/height swaps
        let (width, height) = (8, 4);
        let mut g_spatial = Vec::with_capacity(width * height);
        for y in 0..height {
            for x in 0..width {
                g_spatial.push(Complex::new((y * 7 + x * 3 + 1) as f64, 0.0));
            }
        }
        let fft = Fft2d::try_new(width, height).expect("valid fft");
        let g_fft = fft.forward(&g_spatial).expect("forward g");

        let (dx, dy) = (2, -1);
        let mut x_spatial = vec![Complex::ZERO; width * height];
        for y in 0..height {
            for x in 0..width {
                let src_y = ((y as isize - dy).rem_euclid(height as isize)) as usize;
                let src_x = ((x as isize - dx).rem_euclid(width as isize)) as usize;
                x_spatial[y * width + x] = g_spatial[src_y * width + src_x];
            }
        }
        let x_fft = fft.forward(&x_spatial).expect("forward x");
        let resp = response(&fft, &g_fft, &x_fft).expect("response");

        let target_x = (dx.rem_euclid(width as isize)) as usize;
        let target_y = (dy.rem_euclid(height as isize)) as usize;
        let expected_peak_idx = target_y * width + target_x;

        let peak_val = resp[expected_peak_idx];
        for (idx, &val) in resp.iter().enumerate() {
            if idx != expected_peak_idx {
                assert!(
                    peak_val > val,
                    "expected unique peak at ({target_x}, {target_y}) idx {expected_peak_idx} with val {peak_val}, but found {val} at {idx}"
                );
            }
        }
    }

    #[test]
    fn response_rejects_invalid_inputs() {
        let fft = Fft2d::try_new(8, 8).expect("valid fft");
        let valid_spatial: Vec<Complex> = (0..64).map(|i| Complex::new(i as f64, 0.0)).collect();
        let valid_fft = fft.forward(&valid_spatial).expect("forward");

        // Wrong buffer lengths
        assert_eq!(
            response(&fft, &valid_fft[..63], &valid_fft),
            Err(NumericError::BufferLengthMismatch {
                expected: 64,
                actual: 63
            })
        );
        assert_eq!(
            response(&fft, &valid_fft, &valid_fft[..63]),
            Err(NumericError::BufferLengthMismatch {
                expected: 64,
                actual: 63
            })
        );

        // NaN / Inf
        let mut nan_fft = valid_fft.clone();
        nan_fft[5] = Complex::new(f64::NAN, 0.0);
        assert_eq!(
            response(&fft, &nan_fft, &valid_fft),
            Err(NumericError::NonFiniteInput { index: 5 })
        );

        let mut inf_fft = valid_fft.clone();
        inf_fft[12] = Complex::new(0.0, f64::INFINITY);
        assert_eq!(
            response(&fft, &valid_fft, &inf_fft),
            Err(NumericError::NonFiniteInput { index: 12 })
        );

        // Deliberately non-Hermitian spectrum
        let mut non_hermitian = valid_fft.clone();
        non_hermitian[1] = Complex::new(non_hermitian[1].re, non_hermitian[1].im + 1.0);
        assert_eq!(
            response(&fft, &non_hermitian, &valid_fft),
            Err(NumericError::NonRealSpectrum { index: 1 })
        );

        let mut non_hermitian_dc = valid_fft.clone();
        non_hermitian_dc[0] = Complex::new(non_hermitian_dc[0].re, 0.5);
        assert_eq!(
            response(&fft, &valid_fft, &non_hermitian_dc),
            Err(NumericError::NonRealSpectrum { index: 0 })
        );

        // Overflowing product
        let huge_val = 1e200;
        let mut huge_filter = vec![Complex::ZERO; 64];
        huge_filter[0] = Complex::new(huge_val, 0.0);
        let mut huge_features = vec![Complex::ZERO; 64];
        huge_features[0] = Complex::new(huge_val, 0.0);
        assert_eq!(
            response(&fft, &huge_filter, &huge_features),
            Err(NumericError::NonFiniteOutput { index: 0 })
        );

        // FFT side > 512
        let huge_fft = Fft2d::try_new(1024, 1024).expect("fft2d creates 1024");
        let dummy = vec![Complex::ZERO; 1024 * 1024];
        assert_eq!(
            response(&huge_fft, &dummy, &dummy),
            Err(NumericError::UnsupportedFilterSize {
                width: 1024,
                height: 1024
            })
        );

        // Zero filter/features is valid and yields finite zero response
        let zero_fft = vec![Complex::ZERO; 64];
        let resp = response(&fft, &zero_fft, &valid_fft).expect("valid zero filter");
        assert_eq!(resp, vec![0.0; 64]);
        let resp2 = response(&fft, &valid_fft, &zero_fft).expect("valid zero features");
        assert_eq!(resp2, vec![0.0; 64]);
    }

    #[test]
    fn training_config_validation() {
        let default_cfg = FilterTrainingParams::default();
        assert_eq!(default_cfg.lambda, 0.01);
        assert_eq!(default_cfg.admm_iterations, 4);
        assert_eq!(default_cfg.mu_initial, 1.0);
        assert_eq!(default_cfg.mu_growth, 3.0);
        assert_eq!(default_cfg.mu_max, 81.0);
        assert!(default_cfg.validate().is_ok());

        // Invalid lambda
        for bad_lambda in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 0.0, -1.0] {
            let mut cfg = default_cfg;
            cfg.lambda = bad_lambda;
            assert_eq!(
                cfg.validate(),
                Err(NumericError::InvalidTrainingParameter { field: "lambda" })
            );
        }

        // Invalid iterations
        for bad_iter in [0, 65] {
            let mut cfg = default_cfg;
            cfg.admm_iterations = bad_iter;
            assert_eq!(
                cfg.validate(),
                Err(NumericError::InvalidTrainingParameter {
                    field: "admm_iterations"
                })
            );
        }

        // Invalid mu_initial
        for bad_mu in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 0.0, -0.5] {
            let mut cfg = default_cfg;
            cfg.mu_initial = bad_mu;
            assert_eq!(
                cfg.validate(),
                Err(NumericError::InvalidTrainingParameter {
                    field: "mu_initial"
                })
            );
        }

        // Invalid mu_growth (< 1.0 or non-finite)
        for bad_growth in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 0.0, 0.999, -2.0] {
            let mut cfg = default_cfg;
            cfg.mu_growth = bad_growth;
            assert_eq!(
                cfg.validate(),
                Err(NumericError::InvalidTrainingParameter { field: "mu_growth" })
            );
        }

        // Invalid mu_max (< mu_initial or non-finite)
        for bad_max in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 0.5, -1.0] {
            let mut cfg = default_cfg;
            cfg.mu_max = bad_max;
            assert_eq!(
                cfg.validate(),
                Err(NumericError::InvalidTrainingParameter { field: "mu_max" })
            );
        }

        // Saturated safe schedule with huge growth
        let saturated_cfg = FilterTrainingParams {
            lambda: 0.01,
            admm_iterations: 4,
            mu_initial: 1.0,
            mu_growth: 1e300,
            mu_max: 1e10,
        };
        assert!(saturated_cfg.validate().is_ok());
    }

    #[test]
    fn full_support_matches_ridge() {
        let (width, height) = (4, 4);
        let fft = Fft2d::try_new(width, height).expect("valid fft");

        // 4x4 real impulse x with x[0] = 1, x[others] = 0
        let mut x_spatial = vec![Complex::ZERO; 16];
        x_spatial[0] = Complex::new(1.0, 0.0);

        // Asymmetric small y
        let mut y_spatial = Vec::with_capacity(16);
        for j in 0..16 {
            y_spatial.push(Complex::new(((j * 7 + 3) % 11) as f64 / 10.0, 0.0));
        }

        let x_fft = fft.forward(&x_spatial).expect("forward x");
        let y_fft = fft.forward(&y_spatial).expect("forward y");
        let all_one_mask = vec![1.0; 16];

        let lambda = 0.25;
        let config = FilterTrainingParams {
            lambda,
            admm_iterations: 64,
            mu_initial: 1.0,
            mu_growth: 1.0,
            mu_max: 1.0,
        };

        // Closed-form ridge solution: H = X * conj(Y) / (|X|^2 + lambda)
        let mut expected_h = Vec::with_capacity(16);
        for i in 0..16 {
            let num = x_fft[i] * y_fft[i].conj();
            let denom = x_fft[i].norm_sqr() + lambda;
            expected_h.push(Complex::new(num.re / denom, num.im / denom));
        }
        let expected_spatial = fft.inverse(&expected_h).expect("inverse expected H");

        let trained_fft =
            train_filter(&fft, &x_fft, &y_fft, &all_one_mask, &config).expect("train filter");
        let trained_spatial = fft.inverse(&trained_fft).expect("inverse trained H");

        for i in 0..16 {
            let diff = (trained_spatial[i].re - expected_spatial[i].re).abs();
            assert!(
                diff <= 1e-6,
                "ridge discrepancy at index {i}: trained {} vs expected {} (diff {})",
                trained_spatial[i].re,
                expected_spatial[i].re,
                diff
            );
            assert!(
                trained_spatial[i].im.abs() <= 1e-9,
                "imaginary residual too large at index {i}: {}",
                trained_spatial[i].im
            );
        }
    }

    /// Test-only Gaussian elimination with partial pivoting and deterministic tie-breaking.
    #[allow(clippy::needless_range_loop)]
    fn solve_dense_linear_system(mut a: Vec<Vec<f64>>, mut b: Vec<f64>) -> Vec<f64> {
        let n = b.len();
        for col in 0..n {
            // Pivot row: find row >= col with maximum |a[row][col]|, breaking ties by smaller row index
            let mut pivot_row = col;
            let mut max_abs = a[col][col].abs();
            for row in (col + 1)..n {
                let abs_val = a[row][col].abs();
                if abs_val > max_abs {
                    max_abs = abs_val;
                    pivot_row = row;
                }
            }
            assert!(max_abs > 1e-14, "matrix must be non-singular");
            if pivot_row != col {
                a.swap(col, pivot_row);
                b.swap(col, pivot_row);
            }
            let pivot_val = a[col][col];
            for row in (col + 1)..n {
                let factor = a[row][col] / pivot_val;
                for c in col..n {
                    a[row][c] -= factor * a[col][c];
                }
                b[row] -= factor * b[col];
            }
        }
        let mut x = vec![0.0f64; n];
        for row in (0..n).rev() {
            let mut sum = b[row];
            for c in (row + 1)..n {
                sum -= a[row][c] * x[c];
            }
            x[row] = sum / a[row][row];
        }
        x
    }

    #[test]
    #[allow(clippy::needless_range_loop)]
    fn masked_solution_matches_dense_spatial_ridge() {
        let (width, height) = (4, 4);
        let fft = Fft2d::try_new(width, height).expect("valid fft");

        // Fixed nonempty sparse mask with 5 active coefficients out of 16
        let active_indices = [0, 2, 5, 10, 15];
        let mut mask = vec![0.0f64; 16];
        for &idx in &active_indices {
            mask[idx] = 1.0;
        }

        let mut y_spatial = Vec::with_capacity(16);
        for j in 0..16 {
            y_spatial.push(((j * 7 + 3) % 11) as f64 / 10.0);
        }
        let y_complex: Vec<Complex> = y_spatial.iter().map(|&v| Complex::new(v, 0.0)).collect();
        let y_fft = fft.forward(&y_complex).expect("forward y");

        let lambda = 0.25;
        let config = FilterTrainingParams {
            lambda,
            admm_iterations: 64,
            mu_initial: 1.0,
            mu_growth: 1.0,
            mu_max: 1.0,
        };

        // Test with x[0] = 1.0 (impulse), then repeat with x[1] = 0.25 added
        for add_x1 in [false, true] {
            let mut x_spatial = [0.0f64; 16];
            x_spatial[0] = 1.0;
            if add_x1 {
                x_spatial[1] = 0.25;
            }
            let x_complex: Vec<Complex> = x_spatial.iter().map(|&v| Complex::new(v, 0.0)).collect();
            let x_fft = fft.forward(&x_complex).expect("forward x");

            // Independently construct design matrix A[t, col]:
            // For target t in 0..16: r[t] = sum_{p in active} g[p] * x[(py + ty)%4, (px + tx)%4]
            let k = active_indices.len();
            let mut a_mat = vec![vec![0.0f64; k]; 16];
            for t in 0..16 {
                let ty = t / width;
                let tx = t % width;
                for (col, &p) in active_indices.iter().enumerate() {
                    let py = p / width;
                    let px = p % width;
                    let sample_y = (py + ty) % height;
                    let sample_x = (px + tx) % width;
                    a_mat[t][col] = x_spatial[sample_y * width + sample_x];
                }
            }

            // Build Q = A^T * A + lambda * I, b = A^T * y
            let mut q_mat = vec![vec![0.0f64; k]; k];
            let mut b_vec = vec![0.0f64; k];
            for r in 0..k {
                for c in 0..k {
                    let mut sum = 0.0f64;
                    for t in 0..16 {
                        sum += a_mat[t][r] * a_mat[t][c];
                    }
                    if r == c {
                        sum += lambda;
                    }
                    q_mat[r][c] = sum;
                }
                let mut sum_y = 0.0f64;
                for t in 0..16 {
                    sum_y += a_mat[t][r] * y_spatial[t];
                }
                b_vec[r] = sum_y;
            }

            let g_sol = solve_dense_linear_system(q_mat, b_vec);
            let mut expected_spatial_g = [0.0f64; 16];
            for (col, &p) in active_indices.iter().enumerate() {
                expected_spatial_g[p] = g_sol[col];
            }

            let trained_fft =
                train_filter(&fft, &x_fft, &y_fft, &mask, &config).expect("train filter");
            let trained_spatial = fft.inverse(&trained_fft).expect("inverse trained");

            for i in 0..16 {
                let diff = (trained_spatial[i].re - expected_spatial_g[i]).abs();
                assert!(
                    diff <= 1e-6,
                    "masked ridge mismatch (add_x1={add_x1}) at {i}: trained {} vs expected {} (diff {})",
                    trained_spatial[i].re,
                    expected_spatial_g[i],
                    diff
                );
                assert!(
                    trained_spatial[i].im.abs() <= 1e-9,
                    "imaginary residual too large at {i}: {}",
                    trained_spatial[i].im
                );
            }
        }
    }

    #[test]
    fn trained_support_is_enforced() {
        let (width, height) = (8, 8);
        let fft = Fft2d::try_new(width, height).expect("valid fft");

        let mut x_spatial = Vec::with_capacity(64);
        let mut y_spatial = Vec::with_capacity(64);
        for i in 0..64 {
            x_spatial.push(Complex::new((i % 7 + 1) as f64, 0.0));
            y_spatial.push(Complex::new(((i * 3 + 2) % 9) as f64, 0.0));
        }
        let x_fft = fft.forward(&x_spatial).expect("forward x");
        let y_fft = fft.forward(&y_spatial).expect("forward y");

        // 1. Irregular support mask
        let mut irregular_mask = vec![0.0f64; 64];
        for i in [3, 7, 12, 19, 25, 33, 48, 55] {
            irregular_mask[i] = 1.0;
        }

        let config = FilterTrainingParams::default();
        let trained_irregular = train_filter(&fft, &x_fft, &y_fft, &irregular_mask, &config)
            .expect("train with irregular mask");
        let spatial_irregular = fft
            .inverse(&trained_irregular)
            .expect("inverse irregular trained");

        for i in 0..64 {
            if irregular_mask[i] == 0.0 {
                assert!(
                    spatial_irregular[i].re.abs() <= 1e-9,
                    "unsupported coefficient at {i} must be zero, got {}",
                    spatial_irregular[i].re
                );
            }
        }

        // 2. S2 disk support mask
        let disk_mask = crate::csrt::patch::support_mask(8, 1.0).expect("disk mask");
        let trained_disk =
            train_filter(&fft, &x_fft, &y_fft, &disk_mask, &config).expect("train with disk mask");
        let spatial_disk = fft.inverse(&trained_disk).expect("inverse disk trained");

        for i in 0..64 {
            if disk_mask[i] == 0.0 {
                assert!(
                    spatial_disk[i].re.abs() <= 1e-9,
                    "unsupported disk coefficient at {i} must be zero, got {}",
                    spatial_disk[i].re
                );
            }
        }
    }

    #[test]
    fn four_iterations_are_fixed_and_repeatable() {
        let (width, height) = (8, 8);
        let fft = Fft2d::try_new(width, height).expect("valid fft");

        let mut x_spatial = Vec::with_capacity(64);
        let mut y_spatial = Vec::with_capacity(64);
        for i in 0..64 {
            x_spatial.push(Complex::new((i % 5 + 1) as f64, 0.0));
            y_spatial.push(Complex::new(((i * 2 + 1) % 7) as f64, 0.0));
        }
        let x_fft = fft.forward(&x_spatial).expect("forward x");
        let y_fft = fft.forward(&y_spatial).expect("forward y");
        let mask = crate::csrt::patch::support_mask(8, 1.0).expect("disk mask");
        let config = FilterTrainingParams::default();

        let out1 = train_filter(&fft, &x_fft, &y_fft, &mask, &config).expect("out1");
        let out2 = train_filter(&fft, &x_fft, &y_fft, &mask, &config).expect("out2");

        for i in 0..64 {
            assert_eq!(
                out1[i].re.to_bits(),
                out2[i].re.to_bits(),
                "re mismatch at {i}"
            );
            assert_eq!(
                out1[i].im.to_bits(),
                out2[i].im.to_bits(),
                "im mismatch at {i}"
            );
        }

        // Confirm 4 iterations vs 3 iterations are distinct
        let mut config3 = config;
        config3.admm_iterations = 3;
        let out_iter3 = train_filter(&fft, &x_fft, &y_fft, &mask, &config3).expect("out iter3");
        let is_different = out1
            .iter()
            .zip(out_iter3.iter())
            .any(|(a, b)| a.re.to_bits() != b.re.to_bits() || a.im.to_bits() != b.im.to_bits());
        assert!(is_different, "iteration 3 and 4 outputs must be distinct");

        // Zero-feature with nonzero-label and valid mask yields finite zero solution
        let zero_features = vec![Complex::ZERO; 64];
        let zero_sol = train_filter(&fft, &zero_features, &y_fft, &mask, &config)
            .expect("train with zero features");
        for (i, &c) in zero_sol.iter().enumerate() {
            assert_eq!(
                c,
                Complex::ZERO,
                "zero features must yield zero filter at {i}"
            );
        }
    }

    #[test]
    fn training_rejects_mismatched_buffer_lengths() {
        let fft = Fft2d::try_new(8, 8).expect("valid fft");
        let valid_spatial: Vec<Complex> = (0..64).map(|i| Complex::new(i as f64, 0.0)).collect();
        let valid_fft = fft.forward(&valid_spatial).expect("forward");
        let valid_mask = vec![1.0; 64];
        let valid_cfg = FilterTrainingParams::default();

        assert_eq!(
            train_filter(&fft, &valid_fft[..63], &valid_fft, &valid_mask, &valid_cfg),
            Err(NumericError::BufferLengthMismatch {
                expected: 64,
                actual: 63
            })
        );
        assert_eq!(
            train_filter(&fft, &valid_fft, &valid_fft[..63], &valid_mask, &valid_cfg),
            Err(NumericError::BufferLengthMismatch {
                expected: 64,
                actual: 63
            })
        );
        assert_eq!(
            train_filter(&fft, &valid_fft, &valid_fft, &valid_mask[..63], &valid_cfg),
            Err(NumericError::BufferLengthMismatch {
                expected: 64,
                actual: 63
            })
        );
    }

    #[test]
    fn training_rejects_non_finite_and_non_hermitian_spectra() {
        let fft = Fft2d::try_new(8, 8).expect("valid fft");
        let valid_spatial: Vec<Complex> = (0..64).map(|i| Complex::new(i as f64, 0.0)).collect();
        let valid_fft = fft.forward(&valid_spatial).expect("forward");
        let valid_mask = vec![1.0; 64];
        let valid_cfg = FilterTrainingParams::default();

        // Nonfinite spectra
        let mut nan_features = valid_fft.clone();
        nan_features[7] = Complex::new(f64::NAN, 0.0);
        assert_eq!(
            train_filter(&fft, &nan_features, &valid_fft, &valid_mask, &valid_cfg),
            Err(NumericError::NonFiniteInput { index: 7 })
        );

        let mut inf_label = valid_fft.clone();
        inf_label[11] = Complex::new(0.0, f64::INFINITY);
        assert_eq!(
            train_filter(&fft, &valid_fft, &inf_label, &valid_mask, &valid_cfg),
            Err(NumericError::NonFiniteInput { index: 11 })
        );

        // Non-Hermitian spectra
        let mut non_herm_features = valid_fft.clone();
        non_herm_features[2] = Complex::new(non_herm_features[2].re, non_herm_features[2].im + 1.0);
        assert_eq!(
            train_filter(
                &fft,
                &non_herm_features,
                &valid_fft,
                &valid_mask,
                &valid_cfg
            ),
            Err(NumericError::NonRealSpectrum { index: 2 })
        );
    }

    #[test]
    fn training_rejects_invalid_and_empty_masks() {
        let fft = Fft2d::try_new(8, 8).expect("valid fft");
        let valid_spatial: Vec<Complex> = (0..64).map(|i| Complex::new(i as f64, 0.0)).collect();
        let valid_fft = fft.forward(&valid_spatial).expect("forward");
        let valid_mask = vec![1.0; 64];
        let valid_cfg = FilterTrainingParams::default();

        // Mask invalid: fractional
        let mut frac_mask = valid_mask.clone();
        frac_mask[4] = 0.5;
        assert_eq!(
            train_filter(&fft, &valid_fft, &valid_fft, &frac_mask, &valid_cfg),
            Err(NumericError::InvalidMask { index: 4 })
        );

        // Mask invalid: negative
        let mut neg_mask = valid_mask.clone();
        neg_mask[9] = -1.0;
        assert_eq!(
            train_filter(&fft, &valid_fft, &valid_fft, &neg_mask, &valid_cfg),
            Err(NumericError::InvalidMask { index: 9 })
        );

        // Mask invalid: NaN / Inf
        let mut nan_mask = valid_mask.clone();
        nan_mask[3] = f64::NAN;
        assert_eq!(
            train_filter(&fft, &valid_fft, &valid_fft, &nan_mask, &valid_cfg),
            Err(NumericError::InvalidMask { index: 3 })
        );

        // Mask all-zero
        let all_zero_mask = vec![0.0; 64];
        assert_eq!(
            train_filter(&fft, &valid_fft, &valid_fft, &all_zero_mask, &valid_cfg),
            Err(NumericError::EmptySupport)
        );
    }

    #[test]
    fn training_rejects_unsupported_filter_size_and_overflow() {
        let fft = Fft2d::try_new(8, 8).expect("valid fft");
        let valid_mask = vec![1.0; 64];
        let valid_cfg = FilterTrainingParams::default();

        // Unsupported FFT size (> 512)
        let huge_fft = Fft2d::try_new(1024, 1024).expect("fft 1024");
        let dummy = vec![Complex::ZERO; 1024 * 1024];
        let dummy_mask = vec![1.0; 1024 * 1024];
        assert_eq!(
            train_filter(&huge_fft, &dummy, &dummy, &dummy_mask, &valid_cfg),
            Err(NumericError::UnsupportedFilterSize {
                width: 1024,
                height: 1024
            })
        );

        // Overflowing intermediate: huge features whose norm_sqr overflows
        let mut huge_features = vec![Complex::ZERO; 64];
        huge_features[0] = Complex::new(1e200, 0.0);
        let mut huge_label = vec![Complex::ZERO; 64];
        huge_label[0] = Complex::new(1.0, 0.0);
        assert_eq!(
            train_filter(&fft, &huge_features, &huge_label, &valid_mask, &valid_cfg),
            Err(NumericError::NonFiniteOutput { index: 0 })
        );
    }

    #[test]
    fn s2_features_train_and_respond_without_double_fft() {
        use crate::csrt::patch::{gaussian_label, normalized_patch, support_mask, Patch};
        use crate::GrayFrame;

        let mut pixels = vec![40u8; 32 * 32];
        for (dy, dx, val) in [
            (0, 0, 220u8),
            (0, 1, 180),
            (0, -1, 150),
            (1, 0, 200),
            (-1, 0, 130),
            (1, 1, 190),
            (-1, 1, 170),
            (1, -1, 160),
            (-1, -1, 120),
        ] {
            let py = (16 + dy) as usize;
            let px = (16 + dx) as usize;
            pixels[py * 32 + px] = val;
        }
        let frame = GrayFrame::try_new(32, 32, pixels).expect("valid 32x32 frame");

        let fft8 = Fft2d::try_new(8, 8).expect("valid fft8");
        let patch = normalized_patch(&frame, (16, 16), &fft8).expect("normalized patch");
        let features_spatial = match patch {
            Patch::Available(spatial) => spatial,
            _ => panic!("expected available patch"),
        };

        // Forward transformed ONCE
        let features_fft = fft8.forward(&features_spatial).expect("forward features");
        let label_spatial = gaussian_label(&fft8, 2.0).expect("gaussian label");
        let label_fft = fft8.forward(&label_spatial).expect("forward label");
        let mask = support_mask(8, 1.0).expect("support mask");

        let config = FilterTrainingParams::default();
        let filter_fft1 =
            train_filter(&fft8, &features_fft, &label_fft, &mask, &config).expect("train filter 1");
        let resp1 = response(&fft8, &filter_fft1, &features_fft).expect("response 1");

        let filter_fft2 =
            train_filter(&fft8, &features_fft, &label_fft, &mask, &config).expect("train filter 2");
        let resp2 = response(&fft8, &filter_fft2, &features_fft).expect("response 2");

        // Repeatable bit-for-bit
        for (a, b) in resp1.iter().zip(resp2.iter()) {
            assert_eq!(a.to_bits(), b.to_bits());
        }

        // Finite and unique peak at origin (0, 0) (index 0)
        assert!(resp1[0].is_finite());
        let peak_val = resp1[0];
        for (i, &val) in resp1.iter().enumerate().skip(1) {
            assert!(
                peak_val > val,
                "peak must be strictly maximum at origin (0,0), but index {i} has {val} >= peak {peak_val}"
            );
        }
    }

    #[test]
    fn filter_exercises_minimum_and_maximum_side_smoke() {
        // Exercise S2 minimum side 8 and maximum side 512 with a one-iteration valid training pass
        let config = FilterTrainingParams {
            lambda: 0.01,
            admm_iterations: 1,
            mu_initial: 1.0,
            mu_growth: 1.0,
            mu_max: 1.0,
        };

        for side in [8, 512] {
            let fft = Fft2d::try_new(side, side).expect("valid fft");
            let len = side * side;

            let mut spatial_x = vec![Complex::ZERO; len];
            spatial_x[0] = Complex::new(1.0, 0.0);
            spatial_x[side / 2 * side + side / 2] = Complex::new(0.5, 0.0);

            let mut spatial_y = vec![Complex::ZERO; len];
            spatial_y[0] = Complex::new(1.0, 0.0);

            let features_fft = fft.forward(&spatial_x).expect("forward features");
            let label_fft = fft.forward(&spatial_y).expect("forward label");
            let mask = vec![1.0f64; len];

            let trained = train_filter(&fft, &features_fft, &label_fft, &mask, &config)
                .expect("train filter");
            let resp = response(&fft, &trained, &features_fft).expect("response");

            assert_eq!(resp.len(), len);
            assert!(resp[0].is_finite());
        }
    }

    #[test]
    fn pinned_digest_of_learned_filter_and_response_is_stable() {
        use crate::csrt::patch::{gaussian_label, normalized_patch, support_mask, Patch};
        use crate::csrt::tests::fnv1a_64;
        use crate::GrayFrame;

        let mut pixels = vec![40u8; 32 * 32];
        for (dy, dx, val) in [
            (0, 0, 220u8),
            (0, 1, 180),
            (0, -1, 150),
            (1, 0, 200),
            (-1, 0, 130),
            (1, 1, 190),
            (-1, 1, 170),
            (1, -1, 160),
            (-1, -1, 120),
        ] {
            let py = (16 + dy) as usize;
            let px = (16 + dx) as usize;
            pixels[py * 32 + px] = val;
        }
        let frame = GrayFrame::try_new(32, 32, pixels).expect("valid frame");
        let fft8 = Fft2d::try_new(8, 8).expect("fft8");
        let patch = match normalized_patch(&frame, (16, 16), &fft8).expect("patch") {
            Patch::Available(spatial) => spatial,
            _ => panic!("patch unavailable"),
        };
        let features_fft = fft8.forward(&patch).expect("forward features");
        let label_fft = fft8
            .forward(&gaussian_label(&fft8, 2.0).expect("label"))
            .expect("forward label");
        let mask = support_mask(8, 1.0).expect("mask");

        let filter_fft = train_filter(
            &fft8,
            &features_fft,
            &label_fft,
            &mask,
            &FilterTrainingParams::default(),
        )
        .expect("train");
        let resp = response(&fft8, &filter_fft, &features_fft).expect("response");

        let values = filter_fft.iter().flat_map(|c| [c.re, c.im]).chain(resp);
        let digest = fnv1a_64(values);

        // Expected digest pinned for deterministic reproducible verification
        const EXPECTED_DIGEST: u64 = 0x4045_6d96_94a9_7f6a;
        assert_eq!(
            digest, EXPECTED_DIGEST,
            "learned filter and response digest must remain bit-stable: got 0x{digest:016x}"
        );
    }

    #[test]
    fn blend_filters_properties_and_errors() {
        let (width, height) = (8, 8);
        let fft = Fft2d::try_new(width, height).expect("fft");
        let spatial1: Vec<Complex> = (0..64)
            .map(|i| Complex::new((i * 3 + 1) as f64, 0.0))
            .collect();
        let spatial2: Vec<Complex> = (0..64)
            .map(|i| Complex::new(((63 - i) * 2 + 5) as f64, 0.0))
            .collect();
        let old = fft.forward(&spatial1).expect("forward 1");
        let candidate = fft.forward(&spatial2).expect("forward 2");

        // lr = 0 returns old bit-for-bit
        let at_zero = blend_filters(&old, &candidate, 0.0).expect("lr 0");
        for (a, b) in at_zero.iter().zip(old.iter()) {
            assert_eq!(a.re.to_bits(), b.re.to_bits());
            assert_eq!(a.im.to_bits(), b.im.to_bits());
        }

        // lr = 1 returns candidate bit-for-bit
        let at_one = blend_filters(&old, &candidate, 1.0).expect("lr 1");
        for (a, b) in at_one.iter().zip(candidate.iter()) {
            assert_eq!(a.re.to_bits(), b.re.to_bits());
            assert_eq!(a.im.to_bits(), b.im.to_bits());
        }

        // Hermitian symmetry preserved for intermediate lr
        let blended = blend_filters(&old, &candidate, 0.02).expect("lr 0.02");
        assert!(validate_spectrum(&blended, width, height).is_ok());

        // Mismatched buffer lengths
        assert_eq!(
            blend_filters(&old[..63], &candidate, 0.02),
            Err(NumericError::BufferLengthMismatch {
                expected: 63,
                actual: 64
            })
        );

        // Invalid learning rates
        for bad_lr in [f64::NAN, -0.01, 1.01, f64::INFINITY] {
            assert_eq!(
                blend_filters(&old, &candidate, bad_lr),
                Err(NumericError::InvalidTrainingParameter {
                    field: "learning_rate"
                })
            );
        }

        // Non-finite input
        let mut nan_old = old.clone();
        nan_old[5] = Complex::new(f64::NAN, 0.0);
        assert_eq!(
            blend_filters(&nan_old, &candidate, 0.5),
            Err(NumericError::NonFiniteInput { index: 5 })
        );
    }
}
