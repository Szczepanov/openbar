//! CSRT-class grayscale tracker `csrt-gray-v1` (work in progress).
//!
//! Slice S1 provides only the deterministic numeric core: a radix-2 complex 2D FFT
//! ([`fft`]) and transcendental-free helpers ([`detmath`]) for twiddle factors, the Hann
//! window and the exponential. Everything here is built from IEEE-754 correctly-rounded
//! operations (`+ - * /` and square root) in a fixed evaluation order, never from platform
//! libm transcendentals and never with fused multiply-add. Pinned digests prove the results
//! bit-identical on the x86_64 CI platforms (Ubuntu, Windows); aarch64 is argued by
//! construction (same correctly-rounded, unfused operations), not proven. Implemented from the
//! published literature (Cooley and Tukey, 1965; Cody and Waite, 1980); no third-party source
//! was consulted or ported.

mod detmath;
mod fft;
mod filter;
mod patch;
mod peak;
mod tracker;

pub use tracker::{CsrtGrayConfig, CsrtGrayTracker};

use std::fmt;
use std::ops::{Add, Mul, Sub};

/// Complex number on `f64` with plain (non-fused) arithmetic.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct Complex {
    pub(crate) re: f64,
    pub(crate) im: f64,
}

impl Complex {
    pub(crate) const ZERO: Self = Self { re: 0.0, im: 0.0 };
    pub(crate) const ONE: Self = Self { re: 1.0, im: 0.0 };

    pub(crate) const fn new(re: f64, im: f64) -> Self {
        Self { re, im }
    }

    /// Complex conjugate. `0.0 - im` keeps a zero imaginary part as `+0.0`.
    pub(crate) fn conj(self) -> Self {
        Self::new(self.re, 0.0 - self.im)
    }

    pub(crate) fn scale(self, factor: f64) -> Self {
        Self::new(self.re * factor, self.im * factor)
    }

    pub(crate) fn norm_sqr(self) -> f64 {
        self.re * self.re + self.im * self.im
    }

    pub(crate) fn is_finite(self) -> bool {
        self.re.is_finite() && self.im.is_finite()
    }
}

impl Add for Complex {
    type Output = Self;
    fn add(self, rhs: Self) -> Self {
        Self::new(self.re + rhs.re, self.im + rhs.im)
    }
}

impl Sub for Complex {
    type Output = Self;
    fn sub(self, rhs: Self) -> Self {
        Self::new(self.re - rhs.re, self.im - rhs.im)
    }
}

impl Mul for Complex {
    type Output = Self;
    fn mul(self, rhs: Self) -> Self {
        Self::new(
            self.re * rhs.re - self.im * rhs.im,
            self.re * rhs.im + self.im * rhs.re,
        )
    }
}

/// Errors from the deterministic numeric core.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) enum NumericError {
    /// A 1D length that is zero or not a power of two.
    InvalidLength {
        len: usize,
    },
    /// A 1D length above `max` (`detmath::MAX_TRANSFORM_LEN`).
    LengthTooLarge {
        len: usize,
        max: usize,
    },
    /// A 2D FFT size whose width or height is zero or not a power of two.
    InvalidFftSize {
        width: usize,
        height: usize,
    },
    /// A 2D FFT size whose width or height exceeds `max` (`detmath::MAX_TRANSFORM_LEN`).
    FftSizeTooLarge {
        width: usize,
        height: usize,
        max: usize,
    },
    BufferLengthMismatch {
        expected: usize,
        actual: usize,
    },
    NonFiniteInput {
        index: usize,
    },
    NonFiniteOutput {
        index: usize,
    },
    NonFiniteExpArgument {
        value: f64,
    },
    ExpOverflow {
        value: f64,
    },
    /// A target radius that is not finite or not positive.
    InvalidRadius {
        value: f64,
    },
    /// A target radius whose patch would be wider than `max_side` (`patch::MAX_PATCH_SIDE`).
    RadiusTooLarge {
        radius_px: f64,
        max_side: usize,
    },
    /// A support radius that violates `4 * radius + 1 <= side` for the given patch side.
    RadiusExceedsPatch {
        radius_px: f64,
        side: usize,
    },
    /// A patch size that is not square, not a power of two, or outside `patch::MIN_PATCH_SIDE`
    /// to `patch::MAX_PATCH_SIDE`. Raised before any allocation sized from the transform.
    UnsupportedPatchSide {
        width: usize,
        height: usize,
    },
    /// A Gaussian width that is not finite and positive, or whose variance term or farthest
    /// exponent argument is not finite (the variance underflows to zero or overflows).
    InvalidSigma {
        value: f64,
    },
    /// A training configuration parameter that is invalid or out of range.
    InvalidTrainingParameter {
        field: &'static str,
    },
    /// A mask entry that is not finite or not exactly 0.0 or 1.0.
    InvalidMask {
        index: usize,
    },
    /// A spatial mask with no active (1.0) entries.
    EmptySupport,
    /// A filter transform size whose side is not a power of two or outside 1..=512.
    UnsupportedFilterSize {
        width: usize,
        height: usize,
    },
    /// A spectrum entry violating Hermitian symmetry for a real signal.
    NonRealSpectrum {
        index: usize,
    },
    /// An inverse transform entry having a non-negligible imaginary residual.
    NonRealInverse {
        index: usize,
    },
    /// A denominator in filter training that is not finite and positive.
    InvalidDenominator {
        index: usize,
    },
}

impl fmt::Display for NumericError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidLength { len } => {
                write!(f, "length must be a non-zero power of two, got {len}")
            }
            Self::LengthTooLarge { len, max } => {
                write!(f, "length must be at most {max}, got {len}")
            }
            Self::InvalidFftSize { width, height } => write!(
                f,
                "FFT width and height must be non-zero powers of two, got {width}x{height}"
            ),
            Self::FftSizeTooLarge { width, height, max } => write!(
                f,
                "FFT width and height must each be at most {max}, got {width}x{height}"
            ),
            Self::BufferLengthMismatch { expected, actual } => {
                write!(f, "buffer length must be {expected}, got {actual}")
            }
            Self::NonFiniteInput { index } => {
                write!(f, "input value at index {index} is not finite")
            }
            Self::NonFiniteOutput { index } => {
                write!(f, "output value at index {index} is not finite")
            }
            Self::NonFiniteExpArgument { value } => {
                write!(f, "exponential argument must be finite, got {value}")
            }
            Self::ExpOverflow { value } => {
                write!(f, "exponential of {value} overflows f64")
            }
            Self::InvalidRadius { value } => {
                write!(f, "radius must be finite and positive, got {value}")
            }
            Self::RadiusTooLarge {
                radius_px,
                max_side,
            } => write!(
                f,
                "radius {radius_px} needs a patch wider than the supported {max_side} px"
            ),
            Self::RadiusExceedsPatch { radius_px, side } => {
                write!(
                    f,
                    "radius {radius_px} does not fit a {side} px patch (needs 4 * radius + 1 <= side)"
                )
            }
            Self::UnsupportedPatchSide { width, height } => write!(
                f,
                "patch must be square with a supported power-of-two side, got {width}x{height}"
            ),
            Self::InvalidSigma { value } => {
                write!(
                    f,
                    "sigma must be finite and positive with a finite variance term, got {value}"
                )
            }
            Self::InvalidTrainingParameter { field } => {
                write!(f, "training parameter {field} is invalid")
            }
            Self::InvalidMask { index } => {
                write!(f, "mask value at index {index} must be 0 or 1")
            }
            Self::EmptySupport => write!(f, "mask support is empty"),
            Self::UnsupportedFilterSize { width, height } => write!(
                f,
                "filter transform size must be power-of-two side in 1..=512, got {width}x{height}"
            ),
            Self::NonRealSpectrum { index } => write!(
                f,
                "spectrum at index {index} violates Hermitian symmetry for a real signal"
            ),
            Self::NonRealInverse { index } => write!(
                f,
                "inverse transform at index {index} has non-negligible imaginary residual"
            ),
            Self::InvalidDenominator { index } => write!(
                f,
                "ADMM denominator at index {index} is not finite and positive"
            ),
        }
    }
}

impl std::error::Error for NumericError {}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every file in `src/csrt`; `every_csrt_file_is_scanned` keeps this list complete.
    const SOURCES: [(&str, &str); 7] = [
        ("detmath.rs", include_str!("detmath.rs")),
        ("fft.rs", include_str!("fft.rs")),
        ("filter.rs", include_str!("filter.rs")),
        ("mod.rs", include_str!("mod.rs")),
        ("patch.rs", include_str!("patch.rs")),
        ("peak.rs", include_str!("peak.rs")),
        ("tracker.rs", include_str!("tracker.rs")),
    ];

    /// Identifiers banned outside test modules: libm transcendentals, `powi` (its precision is
    /// documented as unspecified) and fused multiply-add. Matched as whole identifiers, so
    /// method calls, `f64::name(x)` UFCS and `.map(f64::name)` paths are all caught. The `libm`
    /// crate and any identifier starting with `FORBIDDEN_PREFIX` (logarithms) are banned too.
    const FORBIDDEN_IDENTIFIERS: [&str; 27] = [
        "sin", "cos", "tan", "sin_cos", "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh",
        "asinh", "acosh", "atanh", "exp", "exp2", "exp10", "exp_m1", "powf", "powi", "ln", "ln_1p",
        "gamma", "ln_gamma", "cbrt", "hypot", "mul_add",
    ];
    const FORBIDDEN_CRATE: &str = "libm";
    const FORBIDDEN_PREFIX: &str = "log";
    /// Assembled from pieces so this file's own source contains it exactly once.
    const TEST_MODULE_MARKER: &str = concat!("#[cfg", "(test)]\nmod tests");

    /// Fowler-Noll-Vo FNV-1a 64-bit digest over the little-endian `to_bits` bytes of `values`.
    pub(super) fn fnv1a_64(values: impl IntoIterator<Item = f64>) -> u64 {
        values
            .into_iter()
            .flat_map(|value| value.to_bits().to_le_bytes())
            .fold(0xcbf2_9ce4_8422_2325, |hash, byte| {
                (hash ^ u64::from(byte)).wrapping_mul(0x0000_0100_0000_01b3)
            })
    }

    /// Source before the single `#[cfg(test)] mod tests` marker, line endings normalised and
    /// line comments removed (a name in a comment is not a call).
    fn production_code(source: &str) -> Result<String, String> {
        let normalized = source.replace("\r\n", "\n");
        let count = normalized.matches(TEST_MODULE_MARKER).count();
        let end = match (count, normalized.find(TEST_MODULE_MARKER)) {
            (1, Some(end)) => end,
            _ => return Err(format!("expected one test-module marker, found {count}")),
        };
        let lines = normalized[..end]
            .lines()
            .map(|line| line.find("//").map_or(line, |start| &line[..start]));
        Ok(lines.collect::<Vec<_>>().join("\n"))
    }

    /// Sorted, de-duplicated forbidden identifiers in the production part of `source`.
    fn forbidden_hits(source: &str) -> Result<Vec<String>, String> {
        let code = production_code(source)?;
        let mut hits: Vec<String> = code
            .split(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
            .filter(|token| {
                FORBIDDEN_IDENTIFIERS.contains(token)
                    || *token == FORBIDDEN_CRATE
                    || token.starts_with(FORBIDDEN_PREFIX)
            })
            .map(str::to_owned)
            .collect();
        hits.sort();
        hits.dedup();
        Ok(hits)
    }

    fn planted(body: &str) -> String {
        format!("fn f(x: f64) -> f64 {{ {body} }}\n{TEST_MODULE_MARKER} {{}}\n")
    }

    fn names(parts: &[&str]) -> Vec<String> {
        parts.iter().map(|part| part.to_string()).collect()
    }

    #[test]
    fn csrt_production_code_has_no_forbidden_math() {
        for (name, source) in SOURCES {
            let code = production_code(source).unwrap_or_else(|error| panic!("{name}: {error}"));
            assert!(code.contains("fn "), "{name} scan is empty");
            assert_eq!(forbidden_hits(source), Ok(Vec::new()), "{name}");
        }
    }

    #[test]
    fn every_csrt_file_is_scanned() {
        let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/src/csrt");
        let mut on_disk: Vec<String> = std::fs::read_dir(dir)
            .expect("read src/csrt")
            .map(|entry| {
                entry
                    .expect("entry")
                    .file_name()
                    .into_string()
                    .expect("utf-8")
            })
            .collect();
        on_disk.sort();
        let scanned: Vec<&str> = SOURCES.iter().map(|(name, _)| *name).collect();
        assert_eq!(on_disk, scanned);
    }

    #[test]
    fn source_scan_catches_method_ufcs_path_and_crate_forms() {
        let log2 = format!("{FORBIDDEN_PREFIX}2");
        let cases = [
            (planted(&format!("x.{}()", "cos")), names(&["cos"])),
            (planted(&format!("f64::{}(x)", "sin")), names(&["sin"])),
            (
                planted(&format!("[x].map(f64::{}).sum()", "exp")),
                names(&["exp"]),
            ),
            (planted(&format!("x.{}(2)", "powi")), names(&["powi"])),
            (
                planted(&format!("{FORBIDDEN_CRATE}::{}(x)", "exp")),
                names(&["exp", FORBIDDEN_CRATE]),
            ),
            (
                planted(&format!("x.{log2}() + x.{}()", "atanh")),
                names(&["atanh", &log2]),
            ),
            (planted(&format!("x.{}()", "ln_1p")), names(&["ln_1p"])),
        ];
        for (source, expected) in cases {
            assert_eq!(forbidden_hits(&source), Ok(expected), "{source}");
        }
    }

    #[test]
    fn source_scan_ignores_comments_lookalikes_and_test_module() {
        let comment = planted(&format!("x // {} {} {FORBIDDEN_PREFIX}", "sin", "cos"));
        assert_eq!(forbidden_hits(&comment), Ok(Vec::new()));
        let lookalikes = planted("let half_sin = x; let exponent = half_sin; exponent");
        assert_eq!(forbidden_hits(&lookalikes), Ok(Vec::new()));
        let in_tests = format!(
            "fn f() {{}}\n{TEST_MODULE_MARKER} {{ fn g(x: f64) {{ x.{}(); }} }}\n",
            "sin"
        );
        assert_eq!(forbidden_hits(&in_tests), Ok(Vec::new()));
        let crlf = planted(&format!("x.{}()", "cos")).replace('\n', "\r\n");
        assert_eq!(forbidden_hits(&crlf), Ok(names(&["cos"])));
    }

    #[test]
    fn source_scan_requires_exactly_one_test_module_marker() {
        assert!(forbidden_hits("fn f() {}\n").is_err());
        let twice = format!("fn f() {{}}\n{TEST_MODULE_MARKER} {{}}\n{TEST_MODULE_MARKER} {{}}\n");
        assert!(forbidden_hits(&twice).is_err());
    }

    #[test]
    fn complex_arithmetic_is_plain_and_exact_on_small_integers() {
        let a = Complex::new(1.0, 2.0);
        let b = Complex::new(3.0, -4.0);
        assert_eq!(a + b, Complex::new(4.0, -2.0));
        assert_eq!(a - b, Complex::new(-2.0, 6.0));
        assert_eq!(a * b, Complex::new(11.0, 2.0));
        assert_eq!(a.conj(), Complex::new(1.0, -2.0));
        assert_eq!(Complex::ONE.conj().im.to_bits(), 0.0f64.to_bits());
        assert_eq!(a.scale(0.5), Complex::new(0.5, 1.0));
        assert_eq!(b.norm_sqr(), 25.0);
        assert!(a.is_finite() && Complex::ZERO.is_finite());
        assert!(!Complex::new(f64::NAN, 0.0).is_finite());
        assert!(!Complex::new(0.0, f64::INFINITY).is_finite());
    }

    #[test]
    fn numeric_error_display_names_the_offending_values() {
        let cases = [
            (NumericError::InvalidLength { len: 6 }, "got 6"),
            (
                NumericError::LengthTooLarge {
                    len: 8192,
                    max: 4096,
                },
                "at most 4096, got 8192",
            ),
            (
                NumericError::InvalidFftSize {
                    width: 8,
                    height: 12,
                },
                "got 8x12",
            ),
            (
                NumericError::FftSizeTooLarge {
                    width: 8192,
                    height: 2,
                    max: 4096,
                },
                "at most 4096, got 8192x2",
            ),
            (
                NumericError::BufferLengthMismatch {
                    expected: 4,
                    actual: 3,
                },
                "must be 4, got 3",
            ),
            (NumericError::NonFiniteInput { index: 2 }, "index 2"),
            (NumericError::NonFiniteOutput { index: 5 }, "index 5"),
            (
                NumericError::NonFiniteExpArgument { value: f64::NAN },
                "got NaN",
            ),
            (NumericError::ExpOverflow { value: 710.0 }, "710"),
            (NumericError::InvalidRadius { value: -1.0 }, "got -1"),
            (
                NumericError::RadiusTooLarge {
                    radius_px: 200.0,
                    max_side: 512,
                },
                "radius 200 needs a patch wider than the supported 512",
            ),
            (
                NumericError::RadiusExceedsPatch {
                    radius_px: 9.0,
                    side: 16,
                },
                "radius 9 does not fit a 16 px patch",
            ),
            (
                NumericError::UnsupportedPatchSide {
                    width: 8,
                    height: 16,
                },
                "got 8x16",
            ),
            (NumericError::InvalidSigma { value: 0.0 }, "got 0"),
            (
                NumericError::InvalidTrainingParameter { field: "lambda" },
                "lambda",
            ),
            (NumericError::InvalidMask { index: 4 }, "index 4"),
            (NumericError::EmptySupport, "empty"),
            (
                NumericError::UnsupportedFilterSize {
                    width: 1024,
                    height: 1024,
                },
                "got 1024x1024",
            ),
            (NumericError::NonRealSpectrum { index: 3 }, "index 3"),
            (NumericError::NonRealInverse { index: 7 }, "index 7"),
            (NumericError::InvalidDenominator { index: 2 }, "index 2"),
        ];
        for (error, needle) in cases {
            let message = error.to_string();
            assert!(message.contains(needle), "{message}");
            let _: &dyn std::error::Error = &error;
        }
    }
}
