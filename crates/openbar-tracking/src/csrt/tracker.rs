//! Grayscale CSRT-class correlation tracker `csrt-gray-v1` (slices S4 and S5).
//!
//! A single-channel, fixed-support, fixed-scale correlation-filter tracker implementing
//! [`ManualSeedTracker`]. Uses:
//! - Exact integer crops and normalisation with periodic Hann window ([`super::patch`]).
//! - Spatially constrained ADMM filter learning ([`super::filter`]).
//! - Circular response peak location and PSR scoring ([`super::peak`]).
//! - Conservative adaptation: only updates model on `Tracked` observations; freezes
//!   anchor and model on loss; skips update if candidate crop is unavailable.

use super::fft::Fft2d;
use super::filter::{blend_filters, response, train_filter, FilterTrainingParams};
use super::patch::{gaussian_label, normalized_patch, patch_side, support_mask, Patch};
use super::peak::{locate_peak, PeakOutcome};
use super::{Complex, NumericError};
use crate::{
    bounds_fit, bounds_for_center, lost_observation, rounded_point, validate_seed_tolerance,
    validate_sequence_and_seed, FrameSample, ManualSeedTracker, TrackerDiagnostics, TrackerError,
    TrackerIdentity, TrackerLossReason, TrackerObservation, TrackerObservationState, TrackerRun,
    TrackerVisibilityState,
};
use openbar_core::manual_seed::{ManualTargetSeed, PixelPoint};
use std::collections::BTreeMap;

pub const VERSION: &str = "1";

/// Configuration for the grayscale CSRT tracker.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CsrtGrayConfig {
    pub lambda: f64,
    pub admm_iterations: usize,
    pub mu_initial: f64,
    pub mu_growth: f64,
    pub mu_max: f64,
    pub learning_rate: f64,
    pub label_sigma_px: f64,
    pub lost_psr: f64,
    pub tracked_psr: f64,
    pub psr_exclusion_radius_px: usize,
    pub seed_timestamp_tolerance_s: f64,
}

impl Default for CsrtGrayConfig {
    fn default() -> Self {
        Self {
            lambda: 0.01,
            admm_iterations: 4,
            mu_initial: 1.0,
            mu_growth: 3.0,
            mu_max: 81.0,
            learning_rate: 0.02,
            label_sigma_px: 2.0,
            lost_psr: 5.0,
            tracked_psr: 8.0,
            psr_exclusion_radius_px: 2,
            seed_timestamp_tolerance_s: 0.0005,
        }
    }
}

impl CsrtGrayConfig {
    pub(super) fn training_params(&self) -> FilterTrainingParams {
        FilterTrainingParams {
            lambda: self.lambda,
            admm_iterations: self.admm_iterations,
            mu_initial: self.mu_initial,
            mu_growth: self.mu_growth,
            mu_max: self.mu_max,
        }
    }

    pub fn validate(&self) -> Result<(), TrackerError> {
        self.training_params().validate().map_err(|err| match err {
            NumericError::InvalidTrainingParameter { field } => {
                TrackerError::InvalidConfiguration { field }
            }
            _ => TrackerError::InvalidConfiguration {
                field: "training_params",
            },
        })?;

        if !self.learning_rate.is_finite() || !(0.0..=1.0).contains(&self.learning_rate) {
            return Err(TrackerError::InvalidConfiguration {
                field: "learning_rate",
            });
        }

        if !self.label_sigma_px.is_finite() || self.label_sigma_px <= 0.0 {
            return Err(TrackerError::InvalidConfiguration {
                field: "label_sigma_px",
            });
        }

        validate_seed_tolerance(self.seed_timestamp_tolerance_s)?;

        if !self.lost_psr.is_finite() || self.lost_psr <= 0.0 {
            return Err(TrackerError::InvalidConfiguration { field: "lost_psr" });
        }

        if !self.tracked_psr.is_finite() || self.tracked_psr <= self.lost_psr {
            return Err(TrackerError::InvalidConfiguration {
                field: "tracked_psr",
            });
        }

        if !(1..=3).contains(&self.psr_exclusion_radius_px) {
            return Err(TrackerError::InvalidConfiguration {
                field: "psr_exclusion_radius_px",
            });
        }

        Ok(())
    }
}

/// Grayscale CSRT tracker `csrt-gray-v1`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CsrtGrayTracker {
    config: CsrtGrayConfig,
}

impl CsrtGrayTracker {
    pub fn try_new(config: CsrtGrayConfig) -> Result<Self, TrackerError> {
        config.validate()?;
        Ok(Self { config })
    }

    pub fn config(&self) -> &CsrtGrayConfig {
        &self.config
    }
}

impl Default for CsrtGrayTracker {
    fn default() -> Self {
        Self::try_new(CsrtGrayConfig::default()).expect("default CSRT config is valid")
    }
}

/// Private internal state for driving and testing tracking steps.
#[derive(Debug, Clone, PartialEq)]
pub(super) struct TrackState {
    pub(super) anchor: (i32, i32),
    pub(super) filter_fft: Vec<Complex>,
    pub(super) was_lost: bool,
}

fn map_numeric_error(err: NumericError, radius_px: f64) -> TrackerError {
    match err {
        NumericError::InvalidRadius { .. } | NumericError::RadiusTooLarge { .. } => {
            TrackerError::InvalidSeedRadius { value: radius_px }
        }
        other => TrackerError::CsrtNumerical {
            reason: other.to_string(),
        },
    }
}

fn psr_confidence(psr: f64, tracked_psr: f64) -> f32 {
    let non_neg = psr.max(0.0);
    (non_neg / (non_neg + tracked_psr)) as f32
}

impl ManualSeedTracker for CsrtGrayTracker {
    fn identity(&self) -> TrackerIdentity {
        let mut config = BTreeMap::new();
        config.insert(
            "admm_iterations".to_owned(),
            self.config.admm_iterations.to_string(),
        );
        config.insert(
            "feature".to_owned(),
            "mean-subtracted unit-RMS periodic Hann".to_owned(),
        );
        config.insert(
            "flat_patch_threshold_intensity_levels".to_owned(),
            "1".to_owned(),
        );
        config.insert(
            "label_sigma_px".to_owned(),
            self.config.label_sigma_px.to_string(),
        );
        config.insert("lambda".to_owned(), self.config.lambda.to_string());
        config.insert(
            "learning_rate".to_owned(),
            self.config.learning_rate.to_string(),
        );
        config.insert("lost_psr".to_owned(), self.config.lost_psr.to_string());
        config.insert("mu_growth".to_owned(), self.config.mu_growth.to_string());
        config.insert("mu_initial".to_owned(), self.config.mu_initial.to_string());
        config.insert("mu_max".to_owned(), self.config.mu_max.to_string());
        config.insert("patch_max_side".to_owned(), "512".to_owned());
        config.insert("patch_min_side".to_owned(), "8".to_owned());
        config.insert(
            "patch_side_rule".to_owned(),
            "next power of two of ceil(4 * radius + 1), clamped to [8, 512]".to_owned(),
        );
        config.insert(
            "psr_exclusion_radius_px".to_owned(),
            self.config.psr_exclusion_radius_px.to_string(),
        );
        config.insert("search_limit".to_owned(), "side / 4".to_owned());
        config.insert(
            "seed_timestamp_tolerance_s".to_owned(),
            self.config.seed_timestamp_tolerance_s.to_string(),
        );
        config.insert("support".to_owned(), "disk of seed radius".to_owned());
        config.insert(
            "tracked_psr".to_owned(),
            self.config.tracked_psr.to_string(),
        );
        config.insert(
            "tracking_mode".to_owned(),
            "integer-pixel, fixed-scale".to_owned(),
        );

        TrackerIdentity {
            id: "csrt-gray-v1".to_owned(),
            implementation: "csrt-gray".to_owned(),
            version: VERSION.to_owned(),
            config,
            confidence_semantics:
                "max(psr,0)/(max(psr,0)+tracked_psr); algorithm-specific, not a calibrated probability across trackers"
                    .to_owned(),
        }
    }

    fn track(
        &self,
        frames: &[FrameSample<'_>],
        seed: &ManualTargetSeed,
    ) -> Result<TrackerRun, TrackerError> {
        let seed_index =
            validate_sequence_and_seed(frames, seed, self.config.seed_timestamp_tolerance_s)?;
        let radius_px = seed.target().radius_px();
        let side = patch_side(radius_px).map_err(|e| map_numeric_error(e, radius_px))?;
        let fft = Fft2d::try_new(side, side).map_err(|e| map_numeric_error(e, radius_px))?;
        let label_spatial = gaussian_label(&fft, self.config.label_sigma_px)
            .map_err(|e| map_numeric_error(e, radius_px))?;
        let label_fft = fft
            .forward(&label_spatial)
            .map_err(|e| map_numeric_error(e, radius_px))?;
        let mask = support_mask(side, radius_px).map_err(|e| map_numeric_error(e, radius_px))?;

        let seed_center = rounded_point(seed.target().center());
        let seed_frame = frames[seed_index];
        let seed_patch = normalized_patch(seed_frame.image, seed_center, &fft)
            .map_err(|e| map_numeric_error(e, radius_px))?;

        let seed_spatial = match seed_patch {
            Patch::OutsideFrame => return Err(TrackerError::SeedTargetOutsideFrame),
            Patch::InsufficientContrast { rms } => {
                return Err(TrackerError::InsufficientSeedPatchContrast { rms_levels: rms });
            }
            Patch::Available(spatial) => spatial,
        };

        let seed_features_fft = fft
            .forward(&seed_spatial)
            .map_err(|e| map_numeric_error(e, radius_px))?;
        let training_params = self.config.training_params();
        let seed_filter_fft = train_filter(
            &fft,
            &seed_features_fft,
            &label_fft,
            &mask,
            &training_params,
        )
        .map_err(|e| map_numeric_error(e, radius_px))?;

        let self_response = response(&fft, &seed_filter_fft, &seed_features_fft)
            .map_err(|e| map_numeric_error(e, radius_px))?;
        let self_peak = locate_peak(&self_response, side, self.config.psr_exclusion_radius_px)
            .map_err(|e| map_numeric_error(e, radius_px))?;

        let seed_psr = match self_peak {
            PeakOutcome::Peak { dx, dy, psr, .. } => {
                if dx != 0 || dy != 0 || psr < self.config.lost_psr {
                    return Err(TrackerError::SeedNotTrackable { psr: Some(psr) });
                }
                psr
            }
            PeakOutcome::Ambiguous | PeakOutcome::FlatSidelobes => {
                return Err(TrackerError::SeedNotTrackable { psr: None });
            }
        };

        let seed_confidence = psr_confidence(seed_psr, self.config.tracked_psr);
        let mut observations = Vec::with_capacity(frames.len() - seed_index);
        observations.push(TrackerObservation {
            timestamp_s: seed_frame.timestamp_s,
            frame_index: seed_frame.frame_index,
            state: TrackerObservationState::Tracked {
                center: seed.target().center(),
                confidence: seed_confidence,
            },
            visibility: TrackerVisibilityState::Unknown,
            target_bounds_px: Some(bounds_for_center(seed.target().center(), radius_px)),
            diagnostics: TrackerDiagnostics {
                quality_score: Some(seed_psr),
                displacement_px: Some(0.0),
                reacquired_after_loss: false,
            },
        });

        let mut state = TrackState {
            anchor: seed_center,
            filter_fft: seed_filter_fft,
            was_lost: false,
        };

        for frame in frames.iter().skip(seed_index + 1) {
            let patch = normalized_patch(frame.image, state.anchor, &fft)
                .map_err(|e| map_numeric_error(e, radius_px))?;
            let spatial = match patch {
                Patch::OutsideFrame => {
                    observations.push(lost_observation(
                        frame,
                        TrackerLossReason::NoCandidate,
                        None,
                    ));
                    state.was_lost = true;
                    continue;
                }
                Patch::InsufficientContrast { rms } => {
                    observations.push(lost_observation(
                        frame,
                        TrackerLossReason::InsufficientContrast,
                        Some(rms),
                    ));
                    state.was_lost = true;
                    continue;
                }
                Patch::Available(s) => s,
            };

            let features_fft = fft
                .forward(&spatial)
                .map_err(|e| map_numeric_error(e, radius_px))?;
            let resp = response(&fft, &state.filter_fft, &features_fft)
                .map_err(|e| map_numeric_error(e, radius_px))?;
            let peak_outcome = locate_peak(&resp, side, self.config.psr_exclusion_radius_px)
                .map_err(|e| map_numeric_error(e, radius_px))?;

            let (dx, dy, psr) = match peak_outcome {
                PeakOutcome::Ambiguous | PeakOutcome::FlatSidelobes => {
                    observations.push(lost_observation(frame, TrackerLossReason::PoorMatch, None));
                    state.was_lost = true;
                    continue;
                }
                PeakOutcome::Peak { dx, dy, psr, .. } => (dx, dy, psr),
            };

            if psr < self.config.lost_psr {
                observations.push(lost_observation(
                    frame,
                    TrackerLossReason::PoorMatch,
                    Some(psr),
                ));
                state.was_lost = true;
                continue;
            }

            let next_anchor_x = match state.anchor.0.checked_add(dx) {
                Some(v) => v,
                None => {
                    observations.push(lost_observation(
                        frame,
                        TrackerLossReason::TargetOutsideFrame,
                        Some(psr),
                    ));
                    state.was_lost = true;
                    continue;
                }
            };
            let next_anchor_y = match state.anchor.1.checked_add(dy) {
                Some(v) => v,
                None => {
                    observations.push(lost_observation(
                        frame,
                        TrackerLossReason::TargetOutsideFrame,
                        Some(psr),
                    ));
                    state.was_lost = true;
                    continue;
                }
            };
            let candidate_anchor = (next_anchor_x, next_anchor_y);
            let center =
                PixelPoint::new(f64::from(candidate_anchor.0), f64::from(candidate_anchor.1));
            let bounds = bounds_for_center(center, radius_px);

            if !bounds_fit(frame.image, bounds) {
                observations.push(lost_observation(
                    frame,
                    TrackerLossReason::TargetOutsideFrame,
                    Some(psr),
                ));
                state.was_lost = true;
                continue;
            }

            let is_tracked = psr >= self.config.tracked_psr;
            let confidence = psr_confidence(psr, self.config.tracked_psr);
            let obs_state = if is_tracked {
                TrackerObservationState::Tracked { center, confidence }
            } else {
                TrackerObservationState::LowConfidence { center, confidence }
            };

            let displacement_px = ((dx * dx + dy * dy) as f64).sqrt();
            let reacquired = state.was_lost;
            observations.push(TrackerObservation {
                timestamp_s: frame.timestamp_s,
                frame_index: frame.frame_index,
                state: obs_state,
                visibility: TrackerVisibilityState::Unknown,
                target_bounds_px: Some(bounds),
                diagnostics: TrackerDiagnostics {
                    quality_score: Some(psr),
                    displacement_px: Some(displacement_px),
                    reacquired_after_loss: reacquired,
                },
            });

            state.anchor = candidate_anchor;
            state.was_lost = false;

            // Only adapt filter if Tracked and candidate crop is available (F5)
            if is_tracked {
                if let Ok(Patch::Available(new_spatial)) =
                    normalized_patch(frame.image, state.anchor, &fft)
                {
                    let new_features_fft = fft
                        .forward(&new_spatial)
                        .map_err(|e| map_numeric_error(e, radius_px))?;
                    let candidate_filter =
                        train_filter(&fft, &new_features_fft, &label_fft, &mask, &training_params)
                            .map_err(|e| map_numeric_error(e, radius_px))?;
                    state.filter_fft = blend_filters(
                        &state.filter_fft,
                        &candidate_filter,
                        self.config.learning_rate,
                    )
                    .map_err(|e| map_numeric_error(e, radius_px))?;
                }
            }
        }

        Ok(TrackerRun {
            tracker: self.identity(),
            seed_timestamp_s: seed.timestamp_s(),
            observations,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::GrayFrame;
    use openbar_core::manual_seed::{PlateTarget, SeedValidationContext};

    fn test_image(width: u32, height: u32, fill: u8) -> GrayFrame {
        GrayFrame::try_new(width, height, vec![fill; (width * height) as usize]).unwrap()
    }

    fn test_seed(timestamp_s: f64, cx: f64, cy: f64, r: f64) -> ManualTargetSeed {
        ManualTargetSeed::try_new(
            timestamp_s,
            Some(0),
            PlateTarget::new(PixelPoint::new(cx, cy), r),
            0,
            Some(1.0),
            None,
            SeedValidationContext {
                frame_width_px: 2000,
                frame_height_px: 2000,
                selected_range_start_s: 0.0,
                selected_range_end_s: 100.0,
                source_rotation_deg: 0,
            },
        )
        .unwrap()
    }

    #[test]
    fn config_validation_covers_every_field() {
        let default_cfg = CsrtGrayConfig::default();
        assert!(default_cfg.validate().is_ok());

        // learning_rate
        for bad_lr in [f64::NAN, -0.1, 1.1, f64::INFINITY] {
            let mut cfg = default_cfg;
            cfg.learning_rate = bad_lr;
            assert_eq!(
                cfg.validate(),
                Err(TrackerError::InvalidConfiguration {
                    field: "learning_rate"
                })
            );
        }

        // label_sigma_px
        for bad_sigma in [f64::NAN, 0.0, -1.0, f64::INFINITY] {
            let mut cfg = default_cfg;
            cfg.label_sigma_px = bad_sigma;
            assert_eq!(
                cfg.validate(),
                Err(TrackerError::InvalidConfiguration {
                    field: "label_sigma_px"
                })
            );
        }

        // seed_timestamp_tolerance_s
        for bad_tol in [f64::NAN, -1e-5, f64::INFINITY] {
            let mut cfg = default_cfg;
            cfg.seed_timestamp_tolerance_s = bad_tol;
            assert_eq!(
                cfg.validate(),
                Err(TrackerError::InvalidConfiguration {
                    field: "seed_timestamp_tolerance_s"
                })
            );
        }

        // lost_psr
        for bad_lost in [f64::NAN, 0.0, -2.0, f64::INFINITY] {
            let mut cfg = default_cfg;
            cfg.lost_psr = bad_lost;
            assert_eq!(
                cfg.validate(),
                Err(TrackerError::InvalidConfiguration { field: "lost_psr" })
            );
        }

        // tracked_psr <= lost_psr
        for bad_tracked in [f64::NAN, 5.0, 4.0, -1.0, f64::INFINITY] {
            let mut cfg = default_cfg;
            cfg.tracked_psr = bad_tracked;
            assert_eq!(
                cfg.validate(),
                Err(TrackerError::InvalidConfiguration {
                    field: "tracked_psr"
                })
            );
        }

        // psr_exclusion_radius_px
        for bad_r in [0, 4, 10] {
            let mut cfg = default_cfg;
            cfg.psr_exclusion_radius_px = bad_r;
            assert_eq!(
                cfg.validate(),
                Err(TrackerError::InvalidConfiguration {
                    field: "psr_exclusion_radius_px"
                })
            );
        }

        // Delegated training params: lambda
        let mut cfg = default_cfg;
        cfg.lambda = -1.0;
        assert_eq!(
            cfg.validate(),
            Err(TrackerError::InvalidConfiguration { field: "lambda" })
        );
    }

    #[test]
    fn identity_maps_all_keys_and_is_byte_stable() {
        let tracker = CsrtGrayTracker::default();
        let id = tracker.identity();
        assert_eq!(id.id, "csrt-gray-v1");
        assert_eq!(id.implementation, "csrt-gray");
        assert_eq!(id.version, "1");

        let keys: Vec<&str> = id.config.keys().map(String::as_str).collect();
        let expected_keys = [
            "admm_iterations",
            "feature",
            "flat_patch_threshold_intensity_levels",
            "label_sigma_px",
            "lambda",
            "learning_rate",
            "lost_psr",
            "mu_growth",
            "mu_initial",
            "mu_max",
            "patch_max_side",
            "patch_min_side",
            "patch_side_rule",
            "psr_exclusion_radius_px",
            "search_limit",
            "seed_timestamp_tolerance_s",
            "support",
            "tracked_psr",
            "tracking_mode",
        ];
        assert_eq!(keys, expected_keys);
    }

    #[test]
    fn new_tracker_errors_display() {
        let cases = [
            (
                TrackerError::InsufficientSeedPatchContrast { rms_levels: 0.5 },
                "manual seed patch contrast 0.500 intensity levels is insufficient for the grayscale CSRT tracker",
            ),
            (
                TrackerError::SeedNotTrackable { psr: Some(3.2) },
                "manual seed target self-response PSR 3.200 is not trackable",
            ),
            (
                TrackerError::SeedNotTrackable { psr: None },
                "manual seed target self-response is not trackable",
            ),
            (
                TrackerError::CsrtNumerical {
                    reason: "test math error".to_owned(),
                },
                "CSRT numerical error: test math error",
            ),
        ];
        for (err, needle) in cases {
            assert_eq!(err.to_string(), needle);
        }
    }

    #[test]
    fn shared_validation_rejects_malformed_sequences_and_seeds() {
        let tracker = CsrtGrayTracker::default();
        let seed = test_seed(0.0, 32.0, 32.0, 3.0);
        let frame0 = test_image(64, 64, 100);

        // Empty sequence
        assert_eq!(tracker.track(&[], &seed), Err(TrackerError::EmptySequence));

        // Non-increasing timestamps
        let s0 = FrameSample {
            timestamp_s: 0.1,
            frame_index: Some(0),
            image: &frame0,
        };
        let s1 = FrameSample {
            timestamp_s: 0.05,
            frame_index: Some(1),
            image: &frame0,
        };
        assert_eq!(
            tracker.track(&[s0, s1], &seed),
            Err(TrackerError::NonIncreasingTimestamps {
                previous_index: 0,
                index: 1
            })
        );

        // Dimension changed
        let frame_diff = test_image(32, 64, 100);
        let s_diff = FrameSample {
            timestamp_s: 0.2,
            frame_index: Some(1),
            image: &frame_diff,
        };
        assert_eq!(
            tracker.track(&[s0, s_diff], &seed),
            Err(TrackerError::FrameDimensionsChanged {
                index: 1,
                expected_width_px: 64,
                expected_height_px: 64,
                actual_width_px: 32,
                actual_height_px: 64
            })
        );

        // Seed timestamp not found
        let s_far = FrameSample {
            timestamp_s: 1.0,
            frame_index: Some(0),
            image: &frame0,
        };
        assert_eq!(
            tracker.track(&[s_far], &seed),
            Err(TrackerError::SeedFrameNotFound)
        );

        // Flat seed patch -> InsufficientSeedPatchContrast
        let s_match = FrameSample {
            timestamp_s: 0.0,
            frame_index: Some(0),
            image: &frame0,
        };
        assert!(matches!(
            tracker.track(&[s_match], &seed),
            Err(TrackerError::InsufficientSeedPatchContrast { .. })
        ));

        // Seed outside frame (fits in validation context 2000x2000, but crop extends outside 64x64 frame)
        let seed_outside = test_seed(0.0, 60.0, 60.0, 3.0);
        assert_eq!(
            tracker.track(&[s_match], &seed_outside),
            Err(TrackerError::SeedTargetOutsideFrame)
        );

        // Invalid radius: too large (> 127 px -> side > 512)
        let seed_huge = test_seed(0.0, 500.0, 500.0, 150.0);
        assert_eq!(
            tracker.track(&[s_match], &seed_huge),
            Err(TrackerError::InvalidSeedRadius { value: 150.0 })
        );
    }

    fn textured_disk_frame(
        width: u32,
        height: u32,
        center: Option<(i32, i32)>,
        radius: i32,
    ) -> GrayFrame {
        let mut pixels = vec![40u8; (width * height) as usize];
        if let Some((cx, cy)) = center {
            for y in (cy - radius)..=(cy + radius) {
                for x in (cx - radius)..=(cx + radius) {
                    if x >= 0 && y >= 0 && x < width as i32 && y < height as i32 {
                        let dx = x - cx;
                        let dy = y - cy;
                        if dx * dx + dy * dy <= radius * radius {
                            let val = 180 + ((dx * 13 + dy * 29).rem_euclid(60)) as u8;
                            pixels[y as usize * width as usize + x as usize] = val;
                        }
                    }
                }
            }
        }
        GrayFrame::try_new(width, height, pixels).unwrap()
    }

    #[test]
    fn tracking_loop_preserves_seed_and_tracks_stationary() {
        let tracker = CsrtGrayTracker::default();
        let seed = test_seed(0.0, 48.0, 48.0, 3.0);
        let f0 = textured_disk_frame(96, 96, Some((48, 48)), 3);
        let f1 = textured_disk_frame(96, 96, Some((48, 48)), 3);

        let samples = [
            FrameSample {
                timestamp_s: 0.0,
                frame_index: Some(0),
                image: &f0,
            },
            FrameSample {
                timestamp_s: 0.05,
                frame_index: Some(1),
                image: &f1,
            },
        ];

        let run = tracker.track(&samples, &seed).expect("track run");
        assert_eq!(run.observations.len(), 2);

        // Seed observation preserves authored center and bounds exactly
        let seed_obs = &run.observations[0];
        assert_eq!(seed_obs.timestamp_s, 0.0);
        assert_eq!(seed_obs.frame_index, Some(0));
        match seed_obs.state {
            TrackerObservationState::Tracked { center, confidence } => {
                assert_eq!(center, PixelPoint::new(48.0, 48.0));
                assert!(confidence > 0.5);
            }
            _ => panic!("seed not tracked"),
        }
        assert_eq!(
            seed_obs.target_bounds_px,
            Some(bounds_for_center(PixelPoint::new(48.0, 48.0), 3.0))
        );
        assert_eq!(seed_obs.diagnostics.displacement_px, Some(0.0));
        assert!(!seed_obs.diagnostics.reacquired_after_loss);

        // Frame 1 observation
        let obs1 = &run.observations[1];
        assert_eq!(obs1.timestamp_s, 0.05);
        assert_eq!(obs1.frame_index, Some(1));
        match obs1.state {
            TrackerObservationState::Tracked { center, confidence } => {
                assert_eq!(center, PixelPoint::new(48.0, 48.0));
                assert!(confidence > 0.5);
            }
            _ => panic!("frame 1 not tracked"),
        }
        assert_eq!(obs1.diagnostics.displacement_px, Some(0.0));
    }

    #[test]
    fn tracking_loop_signed_translations() {
        let tracker = CsrtGrayTracker::default();
        let seed = test_seed(0.0, 48.0, 48.0, 3.0);
        let f0 = textured_disk_frame(96, 96, Some((48, 48)), 3);

        let shifts = [
            (1, 0, 49.0, 48.0),
            (-1, 0, 47.0, 48.0),
            (0, 1, 48.0, 49.0),
            (0, -1, 48.0, 47.0),
            (2, 2, 50.0, 50.0),
        ];

        for (dx, dy, want_x, want_y) in shifts {
            let f1 = textured_disk_frame(96, 96, Some((48 + dx, 48 + dy)), 3);
            let samples = [
                FrameSample {
                    timestamp_s: 0.0,
                    frame_index: Some(0),
                    image: &f0,
                },
                FrameSample {
                    timestamp_s: 0.05,
                    frame_index: Some(1),
                    image: &f1,
                },
            ];
            let run = tracker.track(&samples, &seed).expect("track shift");
            match run.observations[1].state {
                TrackerObservationState::Tracked { center, .. } => {
                    assert_eq!(center.x_px(), want_x, "shift ({dx}, {dy}) x");
                    assert_eq!(center.y_px(), want_y, "shift ({dx}, {dy}) y");
                }
                other => panic!("shift ({dx}, {dy}) not tracked: {other:?}"),
            }
        }
    }

    #[test]
    fn loss_freezes_state_and_reacquisition_marks_flag() {
        let tracker = CsrtGrayTracker::default();
        let seed = test_seed(0.0, 48.0, 48.0, 3.0);
        let f_present = textured_disk_frame(96, 96, Some((48, 48)), 3);
        let f_absent = test_image(96, 96, 40); // flat frame -> InsufficientContrast

        let samples = [
            FrameSample {
                timestamp_s: 0.0,
                frame_index: Some(0),
                image: &f_present,
            },
            FrameSample {
                timestamp_s: 0.05,
                frame_index: Some(1),
                image: &f_absent,
            },
            FrameSample {
                timestamp_s: 0.10,
                frame_index: Some(2),
                image: &f_present,
            },
        ];

        let run = tracker.track(&samples, &seed).expect("run");
        assert_eq!(run.observations.len(), 3);

        // Frame 1 is lost
        assert!(matches!(
            run.observations[1].state,
            TrackerObservationState::Lost {
                reason: TrackerLossReason::InsufficientContrast
            }
        ));
        assert_eq!(run.observations[1].target_bounds_px, None);
        assert!(!run.observations[1].diagnostics.reacquired_after_loss);

        // Frame 2 is reacquired
        match run.observations[2].state {
            TrackerObservationState::Tracked { center, .. } => {
                assert_eq!(center, PixelPoint::new(48.0, 48.0));
            }
            other => panic!("expected reacquired tracked, got {other:?}"),
        }
        assert!(run.observations[2].diagnostics.reacquired_after_loss);
    }

    #[test]
    fn one_frame_and_offset_seed_frames() {
        let tracker = CsrtGrayTracker::default();
        let f = textured_disk_frame(96, 96, Some((48, 48)), 3);

        // One-frame sequence
        let seed = test_seed(0.0, 48.0, 48.0, 3.0);
        let run1 = tracker
            .track(
                &[FrameSample {
                    timestamp_s: 0.0,
                    frame_index: Some(0),
                    image: &f,
                }],
                &seed,
            )
            .expect("one frame");
        assert_eq!(run1.observations.len(), 1);

        // Seed not on first frame: earlier frames ignored
        let seed2 = test_seed(0.10, 48.0, 48.0, 3.0);
        let samples = [
            FrameSample {
                timestamp_s: 0.0,
                frame_index: Some(0),
                image: &f,
            },
            FrameSample {
                timestamp_s: 0.05,
                frame_index: Some(1),
                image: &f,
            },
            FrameSample {
                timestamp_s: 0.10,
                frame_index: Some(2),
                image: &f,
            },
            FrameSample {
                timestamp_s: 0.15,
                frame_index: Some(3),
                image: &f,
            },
        ];
        let run2 = tracker.track(&samples, &seed2).expect("offset seed");
        assert_eq!(run2.observations.len(), 2);
        assert_eq!(run2.observations[0].timestamp_s, 0.10);
        assert_eq!(run2.observations[1].timestamp_s, 0.15);
    }
}
