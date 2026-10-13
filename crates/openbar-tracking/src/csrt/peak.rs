//! Peak search and peak-to-sidelobe ratio (PSR) for correlation response arrays (slice S4).
//!
//! Evaluates the response array in row-major order:
//! - Decodes bin `(x, y)` displacement: `i < side / 2 ? i : i - side`.
//! - Limits search to `|dx| <= side / 4` and `|dy| <= side / 4`.
//! - Disallows exact half-period displacement (`|dx| == side / 2` or `|dy| == side / 2`).
//! - Breaks ties by smaller `dx*dx + dy*dy`, then `(dy, dx)` lexicographical order.
//! - Checks ambiguity: any other cell in the entire response, outside the wrapped
//!   `(2r + 1)^2` exclusion square around the chosen peak, with value exactly equal to the peak.
//! - Computes PSR = `(peak - mean) / std` over sidelobe cells outside the exclusion square.
//!   Returns `FlatSidelobes` if sidelobes are empty or `std <= 1e-12`.

use super::NumericError;

/// Smallest supported response side.
pub(super) const MIN_RESPONSE_SIDE: usize = 8;
/// Largest supported response side.
pub(super) const MAX_RESPONSE_SIDE: usize = 512;

/// Outcome of peak search and PSR evaluation.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(super) enum PeakOutcome {
    Peak {
        dx: i32,
        dy: i32,
        peak: f64,
        psr: f64,
    },
    Ambiguous,
    FlatSidelobes,
}

fn validate_response_side(side: usize) -> Result<(), NumericError> {
    if side.is_power_of_two() && (MIN_RESPONSE_SIDE..=MAX_RESPONSE_SIDE).contains(&side) {
        Ok(())
    } else {
        Err(NumericError::UnsupportedPatchSide {
            width: side,
            height: side,
        })
    }
}

/// Locates the highest correlation response within `|dx|, |dy| <= side / 4` and evaluates its PSR.
pub(super) fn locate_peak(
    response: &[f64],
    side: usize,
    exclusion_radius: usize,
) -> Result<PeakOutcome, NumericError> {
    validate_response_side(side)?;
    let expected_len = side * side;
    if response.len() != expected_len {
        return Err(NumericError::BufferLengthMismatch {
            expected: expected_len,
            actual: response.len(),
        });
    }
    if exclusion_radius == 0 || exclusion_radius >= side / 2 {
        return Err(NumericError::InvalidTrainingParameter {
            field: "psr_exclusion_radius_px",
        });
    }

    for (index, &val) in response.iter().enumerate() {
        if !val.is_finite() {
            return Err(NumericError::NonFiniteInput { index });
        }
    }

    let max_disp = (side / 4) as i32;
    let half_side = (side / 2) as i32;

    let mut best: Option<(usize, usize, i32, i32, f64)> = None;
    for y in 0..side {
        let dy = if y < side / 2 {
            y as i32
        } else {
            y as i32 - side as i32
        };
        if dy.abs() > max_disp || dy.abs() == half_side {
            continue;
        }
        for x in 0..side {
            let dx = if x < side / 2 {
                x as i32
            } else {
                x as i32 - side as i32
            };
            if dx.abs() > max_disp || dx.abs() == half_side {
                continue;
            }

            let val = response[y * side + x];
            match best {
                None => {
                    best = Some((x, y, dx, dy, val));
                }
                Some((_, _, best_dx, best_dy, best_val)) => {
                    if val > best_val {
                        best = Some((x, y, dx, dy, val));
                    } else if val == best_val {
                        let dist_sq = dx * dx + dy * dy;
                        let best_dist_sq = best_dx * best_dx + best_dy * best_dy;
                        if dist_sq < best_dist_sq
                            || (dist_sq == best_dist_sq && (dy, dx) < (best_dy, best_dx))
                        {
                            best = Some((x, y, dx, dy, val));
                        }
                    }
                }
            }
        }
    }

    let (peak_x, peak_y, peak_dx, peak_dy, peak_val) = match best {
        Some(b) => b,
        None => return Ok(PeakOutcome::FlatSidelobes),
    };

    // Sidelobe statistics outside the wrapped exclusion square
    let exclusion_side = 2 * exclusion_radius + 1;
    let exclusion_count = exclusion_side * exclusion_side;
    if exclusion_count >= expected_len {
        return Ok(PeakOutcome::FlatSidelobes);
    }
    let sidelobe_count = expected_len - exclusion_count;

    // Pass 1: sidelobe mean
    let mut sum = 0.0f64;
    for y in 0..side {
        let diff_y = (y as isize - peak_y as isize).rem_euclid(side as isize) as usize;
        let dist_y = diff_y.min(side - diff_y);
        for x in 0..side {
            let diff_x = (x as isize - peak_x as isize).rem_euclid(side as isize) as usize;
            let dist_x = diff_x.min(side - diff_x);
            if dist_x > exclusion_radius || dist_y > exclusion_radius {
                sum += response[y * side + x];
            }
        }
    }
    let mean = sum / sidelobe_count as f64;

    // Pass 2: sidelobe population variance and standard deviation
    let mut variance_sum = 0.0f64;
    for y in 0..side {
        let diff_y = (y as isize - peak_y as isize).rem_euclid(side as isize) as usize;
        let dist_y = diff_y.min(side - diff_y);
        for x in 0..side {
            let diff_x = (x as isize - peak_x as isize).rem_euclid(side as isize) as usize;
            let dist_x = diff_x.min(side - diff_x);
            if dist_x > exclusion_radius || dist_y > exclusion_radius {
                let diff = response[y * side + x] - mean;
                variance_sum += diff * diff;
            }
        }
    }
    let variance = variance_sum / sidelobe_count as f64;
    let std = variance.sqrt();
    if std <= 1e-12 {
        return Ok(PeakOutcome::FlatSidelobes);
    }

    // Ambiguity check: any cell in the ENTIRE response, outside the wrapped exclusion square,
    // with value exactly equal to peak_val.
    for y in 0..side {
        let diff_y = (y as isize - peak_y as isize).rem_euclid(side as isize) as usize;
        let dist_y = diff_y.min(side - diff_y);
        for x in 0..side {
            let diff_x = (x as isize - peak_x as isize).rem_euclid(side as isize) as usize;
            let dist_x = diff_x.min(side - diff_x);
            let in_exclusion = dist_x <= exclusion_radius && dist_y <= exclusion_radius;
            if !in_exclusion && response[y * side + x] == peak_val {
                return Ok(PeakOutcome::Ambiguous);
            }
        }
    }

    let psr = (peak_val - mean) / std;
    if !psr.is_finite() {
        return Err(NumericError::NonFiniteOutput {
            index: peak_y * side + peak_x,
        });
    }

    Ok(PeakOutcome::Peak {
        dx: peak_dx,
        dy: peak_dy,
        peak: peak_val,
        psr,
    })
}

#[cfg(test)]
mod tests {
    #![allow(clippy::needless_range_loop)]
    use super::*;

    #[test]
    fn wrapped_decoding_and_signed_displacements() {
        let side = 16;
        let len = side * side;

        // Displacements (dx, dy) and their corresponding array indices (x, y)
        let test_cases = [
            (0, 0, 0, 0),
            (1, 0, 1, 0),
            (-1, 0, 15, 0),
            (0, 1, 0, 1),
            (0, -1, 0, 15),
            (2, 3, 2, 3),
            (-3, 2, 13, 2),
            (4, -4, 4, 12),
            (-4, -4, 12, 12),
        ];

        for (dx, dy, x, y) in test_cases {
            let mut response = vec![0.0f64; len];
            response[y * side + x] = 10.0;
            // Background with small noise so sidelobe std > 1e-12
            for i in 0..len {
                if i != y * side + x {
                    response[i] = (i % 7) as f64 * 0.1;
                }
            }
            let outcome = locate_peak(&response, side, 2).expect("locate peak");
            match outcome {
                PeakOutcome::Peak {
                    dx: got_dx,
                    dy: got_dy,
                    peak,
                    psr,
                } => {
                    assert_eq!(got_dx, dx, "mismatch in dx for ({x}, {y})");
                    assert_eq!(got_dy, dy, "mismatch in dy for ({x}, {y})");
                    assert_eq!(peak, 10.0);
                    assert!(psr > 5.0);
                }
                other => panic!("expected Peak for ({x}, {y}), got {other:?}"),
            }
        }
    }

    #[test]
    fn search_domain_boundary_and_half_period_exclusion() {
        let side = 16;
        let len = side * side;
        let max_disp = side / 4; // 4
        assert_eq!(max_disp, 4);

        // A peak right on boundary dx = 4 is accepted
        let mut resp = vec![0.0f64; len];
        for i in 0..len {
            resp[i] = (i % 5) as f64 * 0.1;
        }
        resp[4] = 10.0;
        match locate_peak(&resp, side, 1).expect("peak at boundary") {
            PeakOutcome::Peak { dx, dy, peak, .. } => {
                assert_eq!(dx, 4);
                assert_eq!(dy, 0);
                assert_eq!(peak, 10.0);
            }
            other => panic!("expected peak, got {other:?}"),
        }

        // A larger value at dx = 5 (outside search domain side/4) is ignored in favour of dx = 4
        resp[5] = 100.0;
        match locate_peak(&resp, side, 1).expect("peak within domain") {
            PeakOutcome::Peak { dx, dy, peak, .. } => {
                assert_eq!(dx, 4);
                assert_eq!(dy, 0);
                assert_eq!(peak, 10.0);
            }
            other => panic!("expected peak, got {other:?}"),
        }

        // Exact half-period (dx = 8) is ignored even if huge
        resp[8] = 1000.0;
        match locate_peak(&resp, side, 1).expect("half-period ignored") {
            PeakOutcome::Peak { dx, dy, peak, .. } => {
                assert_eq!(dx, 4);
                assert_eq!(dy, 0);
                assert_eq!(peak, 10.0);
            }
            other => panic!("expected peak, got {other:?}"),
        }
    }

    #[test]
    fn tie_ordering_is_independent_of_scan_order() {
        let side = 16;
        let len = side * side;
        // Exclusion radius = 2: wrapped distance <= 2 is inside the exclusion square.

        // Equal maxima at (1, 0) [x=1, y=0, dist_sq=1, dy=0, dx=1]
        // and (-1, 0) [x=15, y=0, dist_sq=1, dy=0, dx=-1].
        // Both are inside exclusion square of radius 2 (distance = 2).
        // (0, -1) < (0, 1), so (-1, 0) wins!
        let mut resp = vec![0.0f64; len];
        for i in 0..len {
            resp[i] = (i % 3) as f64 * 0.1;
        }
        resp[1] = 10.0;
        resp[15] = 10.0;
        match locate_peak(&resp, side, 2).expect("locate peak") {
            PeakOutcome::Peak { dx, dy, .. } => {
                assert_eq!(dx, -1);
                assert_eq!(dy, 0);
            }
            other => panic!("expected peak, got {other:?}"),
        }

        // Same with (0, 0) [dist_sq=0] vs (1, 0) [dist_sq=1]: smaller dist_sq wins
        resp[0] = 10.0;
        match locate_peak(&resp, side, 2).expect("locate peak") {
            PeakOutcome::Peak { dx, dy, .. } => {
                assert_eq!(dx, 0);
                assert_eq!(dy, 0);
            }
            other => panic!("expected peak, got {other:?}"),
        }

        // Tie between (0, 1) and (1, 0): both dist_sq = 1.
        // For (1, 0): dy=0, dx=1.
        // For (0, 1): dy=1, dx=0.
        // (0, 1) < (1, 0) for (dy, dx), so (1, 0) wins!
        let mut resp2 = vec![0.0f64; len];
        for i in 0..len {
            resp2[i] = (i % 3) as f64 * 0.1;
        }
        resp2[1] = 10.0; // dx=1, dy=0
        resp2[side] = 10.0; // dx=0, dy=1
        match locate_peak(&resp2, side, 2).expect("locate peak") {
            PeakOutcome::Peak { dx, dy, .. } => {
                assert_eq!(dx, 1);
                assert_eq!(dy, 0);
            }
            other => panic!("expected peak, got {other:?}"),
        }
    }

    #[test]
    fn ambiguity_detects_competing_maxima_outside_exclusion_square() {
        let side = 16;
        let len = side * side;

        let mut resp = vec![0.0f64; len];
        for i in 0..len {
            resp[i] = (i % 3) as f64 * 0.1;
        }
        resp[0] = 10.0; // peak at (0, 0)
                        // With exclusion radius = 2, exclusion square is [-2..=2] x [-2..=2].
                        // Cell (3, 3) is index 3*16 + 3 = 51, outside the exclusion square.
        resp[3 * side + 3] = 10.0;
        assert_eq!(
            locate_peak(&resp, side, 2).expect("locate peak"),
            PeakOutcome::Ambiguous
        );

        // However, equal values INSIDE the exclusion square do NOT trigger ambiguity
        let mut resp_in = vec![0.0f64; len];
        for i in 0..len {
            resp_in[i] = (i % 3) as f64 * 0.1;
        }
        resp_in[0] = 10.0;
        resp_in[1] = 10.0; // at (1, 0), inside radius 2 exclusion square
        match locate_peak(&resp_in, side, 2).expect("locate peak") {
            PeakOutcome::Peak { dx, dy, .. } => {
                assert_eq!(dx, 0);
                assert_eq!(dy, 0);
            }
            other => panic!("expected peak, got {other:?}"),
        }
    }

    #[test]
    fn flat_and_constant_sidelobes_yield_flat_outcome() {
        let side = 8;
        let len = side * side;

        // Completely flat response array
        let flat = vec![5.0; len];
        assert_eq!(
            locate_peak(&flat, side, 2).expect("flat response"),
            PeakOutcome::FlatSidelobes
        );

        // Peak at (0,0) with identical constant sidelobes (variance = 0 <= 1e-12)
        let mut const_sidelobes = vec![1.0; len];
        const_sidelobes[0] = 10.0;
        assert_eq!(
            locate_peak(&const_sidelobes, side, 2).expect("constant sidelobes"),
            PeakOutcome::FlatSidelobes
        );
    }

    #[test]
    fn psr_matches_hand_computed_reference() {
        let side = 8;
        let len = side * side; // 64
        let exclusion_radius = 2; // (2*2+1)^2 = 25 cells, 39 sidelobes

        // 38 sidelobes equal to 1.0, 1 sidelobe equal to 2.0, peak at (0, 0) equal to 10.0
        let mut resp = vec![0.0f64; len];
        // Sidelobes: outside [-2..=2] wrapped around (0,0).
        // (3, 3) is a sidelobe cell
        resp[3 * side + 3] = 2.0;
        for y in 0..side {
            let dy = y.min(side - y);
            for x in 0..side {
                let dx = x.min(side - x);
                if (dx > exclusion_radius || dy > exclusion_radius) && (x, y) != (3, 3) {
                    resp[y * side + x] = 1.0;
                }
            }
        }
        resp[0] = 10.0;

        // Mathematical derivation:
        // sum = 38 * 1.0 + 1 * 2.0 = 40.0
        // mean = 40.0 / 39.0
        // variance = (38 * (1 - 40/39)^2 + 1 * (2 - 40/39)^2) / 39 = 38 / 1521
        // std = sqrt(38) / 39
        // psr = (10 - 40/39) / (sqrt(38) / 39) = 350 / sqrt(38)
        let expected_psr = 350.0 / 38.0f64.sqrt();

        match locate_peak(&resp, side, exclusion_radius).expect("locate peak") {
            PeakOutcome::Peak { dx, dy, peak, psr } => {
                assert_eq!(dx, 0);
                assert_eq!(dy, 0);
                assert_eq!(peak, 10.0);
                assert!(
                    (psr - expected_psr).abs() < 1e-12,
                    "psr discrepancy: got {psr}, expected {expected_psr}"
                );
            }
            other => panic!("expected Peak, got {other:?}"),
        }
    }

    #[test]
    fn locate_peak_validates_inputs_and_errors() {
        let side = 8;
        let valid_resp = vec![1.0; 64];

        // Wrong buffer length
        assert_eq!(
            locate_peak(&valid_resp[..63], side, 2),
            Err(NumericError::BufferLengthMismatch {
                expected: 64,
                actual: 63
            })
        );

        // Invalid side
        for bad_side in [0, 4, 6, 12, 1024] {
            assert_eq!(
                locate_peak(&vec![0.0; bad_side * bad_side], bad_side, 1),
                Err(NumericError::UnsupportedPatchSide {
                    width: bad_side,
                    height: bad_side
                })
            );
        }

        // Invalid exclusion radius
        for bad_r in [0, 4, 5] {
            assert_eq!(
                locate_peak(&valid_resp, side, bad_r),
                Err(NumericError::InvalidTrainingParameter {
                    field: "psr_exclusion_radius_px"
                })
            );
        }

        // Non-finite value in response
        let mut nan_resp = valid_resp.clone();
        nan_resp[17] = f64::NAN;
        assert_eq!(
            locate_peak(&nan_resp, side, 2),
            Err(NumericError::NonFiniteInput { index: 17 })
        );

        let mut inf_resp = valid_resp.clone();
        inf_resp[3] = f64::INFINITY;
        assert_eq!(
            locate_peak(&inf_resp, side, 2),
            Err(NumericError::NonFiniteInput { index: 3 })
        );
    }
}
