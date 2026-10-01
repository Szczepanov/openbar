//! Common math utilities for OpenBar.

/// Checks if two floating point numbers are approximately equal.
///
/// Returns `false` if either operand is non-finite (NaN or Infinity).
/// Uses both relative and absolute tolerance scaling to safely compare values
/// near zero as well as large magnitudes.
pub(crate) fn approximately_equal(left: f64, right: f64) -> bool {
    if !left.is_finite() || !right.is_finite() {
        return false;
    }
    if left == right {
        return true;
    }

    let scale = left.abs().max(right.abs()).max(1.0);
    (left - right).abs() <= f64::EPSILON * 16.0 * scale
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_approximately_equal_identical() {
        assert!(approximately_equal(1.0, 1.0));
        assert!(approximately_equal(0.0, 0.0));
        assert!(approximately_equal(-123.456, -123.456));
    }

    #[test]
    fn test_approximately_equal_non_finite() {
        assert!(!approximately_equal(f64::NAN, 1.0));
        assert!(!approximately_equal(1.0, f64::NAN));
        assert!(!approximately_equal(f64::NAN, f64::NAN));
        assert!(!approximately_equal(f64::INFINITY, f64::INFINITY));
        assert!(!approximately_equal(f64::NEG_INFINITY, f64::NEG_INFINITY));
    }

    #[test]
    fn test_approximately_equal_close_values() {
        let small_diff = 1.0 + f64::EPSILON * 2.0;
        assert!(approximately_equal(1.0, small_diff));

        let large_diff = 1.0 + 1e-3;
        assert!(!approximately_equal(1.0, large_diff));

        assert!(approximately_equal(0.0, f64::EPSILON * 2.0));
        assert!(!approximately_equal(0.0, 1e-5));
    }
}
