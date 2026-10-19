//! PTS conversion and inclusive window selection, mirroring the `openbar-cli` media contract.
//!
//! MediaCodec reports `presentationTimeUs` per output buffer. ADR-0005/0006 require timestamps
//! that are true PTS in seconds, strictly increasing, never derived from nominal FPS.

use std::fmt;

#[derive(Debug, Clone, PartialEq)]
pub enum TimingError {
    InvalidWindow {
        start_s: f64,
        end_s: f64,
    },
    PtsBeforeOrigin {
        pts_us: i64,
        origin_us: i64,
    },
    NonIncreasingPts {
        index: usize,
        previous_us: i64,
        current_us: i64,
    },
}

impl fmt::Display for TimingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidWindow { start_s, end_s } => write!(
                f,
                "window [{start_s}, {end_s}] s must be finite, non-negative and non-decreasing"
            ),
            Self::PtsBeforeOrigin { pts_us, origin_us } => {
                write!(f, "pts {pts_us} us lies before the media origin {origin_us} us")
            }
            Self::NonIncreasingPts {
                index,
                previous_us,
                current_us,
            } => write!(
                f,
                "output frame {index} has pts {current_us} us, not after the previous {previous_us} us"
            ),
        }
    }
}

impl std::error::Error for TimingError {}

/// `(pts_us - origin_us) / 1e6`, integer subtraction first and a single final division.
pub fn pts_us_to_seconds(pts_us: i64, origin_us: i64) -> Result<f64, TimingError> {
    let ticks = i128::from(pts_us) - i128::from(origin_us);
    if ticks < 0 {
        return Err(TimingError::PtsBeforeOrigin { pts_us, origin_us });
    }
    Ok(ticks as f64 / 1_000_000.0)
}

/// Inclusive time window, same semantics as `openbar-cli` `TimeRange::contains`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Window {
    pub start_s: f64,
    pub end_s: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Placement {
    Before,
    Inside,
    After,
}

impl Window {
    pub fn try_new(start_s: Option<f64>, end_s: Option<f64>) -> Result<Self, TimingError> {
        let start_s = start_s.unwrap_or(0.0);
        let end_s = end_s.unwrap_or(f64::INFINITY);
        if !start_s.is_finite() || end_s.is_nan() || start_s < 0.0 || end_s < start_s {
            return Err(TimingError::InvalidWindow { start_s, end_s });
        }
        Ok(Self { start_s, end_s })
    }

    pub fn place(&self, timestamp_s: f64) -> Placement {
        if timestamp_s < self.start_s {
            Placement::Before
        } else if timestamp_s > self.end_s {
            Placement::After
        } else {
            Placement::Inside
        }
    }

    /// Seek target for the extractor: the window start, in whole microseconds (rounded down).
    pub fn seek_us(&self) -> i64 {
        (self.start_s * 1_000_000.0).floor() as i64
    }
}

/// Appends `pts_us` to the presentation-order history, failing closed unless it is strictly
/// later than the previous output (no duplicates, no reordering).
pub fn push_strictly_increasing(history: &mut Vec<i64>, pts_us: i64) -> Result<(), TimingError> {
    if let Some(&previous_us) = history.last() {
        if pts_us <= previous_us {
            return Err(TimingError::NonIncreasingPts {
                index: history.len(),
                previous_us,
                current_us: pts_us,
            });
        }
    }
    history.push(pts_us);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_microseconds_relative_to_origin() {
        assert_eq!(pts_us_to_seconds(0, 0), Ok(0.0));
        assert_eq!(pts_us_to_seconds(33_166, 0), Ok(0.033166));
        assert_eq!(pts_us_to_seconds(1_500_000, 500_000), Ok(1.0));
        assert_eq!(
            pts_us_to_seconds(10, 20),
            Err(TimingError::PtsBeforeOrigin {
                pts_us: 10,
                origin_us: 20
            })
        );
    }

    #[test]
    fn window_is_inclusive_at_both_ends() {
        let window = Window::try_new(Some(1.0), Some(2.0)).unwrap();
        let placed: Vec<Placement> = [0.999_999, 1.0, 1.5, 2.0, 2.000_001]
            .iter()
            .map(|&t| window.place(t))
            .collect();
        assert_eq!(
            placed,
            vec![
                Placement::Before,
                Placement::Inside,
                Placement::Inside,
                Placement::Inside,
                Placement::After
            ]
        );
        assert_eq!(window.seek_us(), 1_000_000);
    }

    #[test]
    fn open_window_covers_whole_clip() {
        let window = Window::try_new(None, None).unwrap();
        assert_eq!(window.place(0.0), Placement::Inside);
        assert_eq!(window.place(1.0e6), Placement::Inside);
        assert_eq!(window.seek_us(), 0);
    }

    #[test]
    fn rejects_invalid_windows() {
        for (start, end) in [
            (Some(-0.1), None),
            (Some(2.0), Some(1.0)),
            (Some(f64::NAN), None),
            (None, Some(f64::NAN)),
            (Some(f64::INFINITY), None),
        ] {
            assert!(
                matches!(
                    Window::try_new(start, end),
                    Err(TimingError::InvalidWindow { .. })
                ),
                "{start:?}..{end:?} should be rejected"
            );
        }
    }

    #[test]
    fn strictly_increasing_history_reports_first_violation() {
        let mut history = Vec::new();
        for pts in [0, 33_000, 66_000] {
            push_strictly_increasing(&mut history, pts).unwrap();
        }
        assert_eq!(history, vec![0, 33_000, 66_000]);
        assert_eq!(
            push_strictly_increasing(&mut history, 66_000),
            Err(TimingError::NonIncreasingPts {
                index: 3,
                previous_us: 66_000,
                current_us: 66_000
            })
        );
        assert_eq!(
            push_strictly_increasing(&mut history, 10),
            Err(TimingError::NonIncreasingPts {
                index: 3,
                previous_us: 66_000,
                current_us: 10
            })
        );
        assert_eq!(history.len(), 3);
    }
}
