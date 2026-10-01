use crate::{
    bounds_fit, bounds_for_center, distance, lost_observation, normalized_mean_absolute_difference,
    patch_fits, rounded_point, rounded_radius, validate_search_radius, validate_seed_tolerance,
    validate_sequence_and_seed, FrameSample, ManualSeedTracker, TrackerDiagnostics, TrackerError,
    TrackerIdentity, TrackerLossReason, TrackerObservation, TrackerObservationState, TrackerRun,
    TrackerVisibilityState,
};
use openbar_core::manual_seed::{ManualTargetSeed, PixelPoint};
use std::collections::BTreeMap;

pub const VERSION: &str = "1";

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TemplateMatchConfig {
    pub search_radius_px: u32,
    pub low_confidence_normalized_mean_absolute_difference: f64,
    pub max_normalized_mean_absolute_difference: f64,
    pub seed_timestamp_tolerance_s: f64,
}

impl Default for TemplateMatchConfig {
    fn default() -> Self {
        Self {
            search_radius_px: 12,
            low_confidence_normalized_mean_absolute_difference: 0.10,
            max_normalized_mean_absolute_difference: 0.20,
            seed_timestamp_tolerance_s: 1e-6,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TemplateMatchTracker {
    config: TemplateMatchConfig,
}

impl TemplateMatchTracker {
    pub fn try_new(config: TemplateMatchConfig) -> Result<Self, TrackerError> {
        validate_search_radius(config.search_radius_px)?;
        if !config
            .low_confidence_normalized_mean_absolute_difference
            .is_finite()
            || !(0.0..=1.0).contains(&config.low_confidence_normalized_mean_absolute_difference)
        {
            return Err(TrackerError::InvalidConfiguration {
                field: "low_confidence_normalized_mean_absolute_difference",
            });
        }
        if !config.max_normalized_mean_absolute_difference.is_finite()
            || !(0.0..=1.0).contains(&config.max_normalized_mean_absolute_difference)
            || config.low_confidence_normalized_mean_absolute_difference
                > config.max_normalized_mean_absolute_difference
        {
            return Err(TrackerError::InvalidConfiguration {
                field: "max_normalized_mean_absolute_difference",
            });
        }
        validate_seed_tolerance(config.seed_timestamp_tolerance_s)?;
        Ok(Self { config })
    }
}

impl Default for TemplateMatchTracker {
    fn default() -> Self {
        Self::try_new(TemplateMatchConfig::default()).expect("default template config is valid")
    }
}

impl ManualSeedTracker for TemplateMatchTracker {
    fn identity(&self) -> TrackerIdentity {
        let mut config = BTreeMap::new();
        config.insert(
            "search_radius_px".to_owned(),
            self.config.search_radius_px.to_string(),
        );
        config.insert(
            "low_confidence_normalized_mean_absolute_difference".to_owned(),
            self.config
                .low_confidence_normalized_mean_absolute_difference
                .to_string(),
        );
        config.insert(
            "max_normalized_mean_absolute_difference".to_owned(),
            self.config
                .max_normalized_mean_absolute_difference
                .to_string(),
        );
        config.insert(
            "seed_timestamp_tolerance_s".to_owned(),
            self.config.seed_timestamp_tolerance_s.to_string(),
        );
        TrackerIdentity {
            id: "template-sad-v1".to_owned(),
            implementation: "fixed-template normalized mean absolute difference".to_owned(),
            version: VERSION.to_owned(),
            config,
            confidence_semantics:
                "1 - normalized mean absolute pixel difference against the fixed seed template; algorithm-specific and not calibrated across trackers"
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
        let radius = rounded_radius(seed)?;
        let seed_center = rounded_point(seed.target().center());
        let seed_frame = frames[seed_index].image;
        if !patch_fits(seed_frame, seed_center, radius) {
            return Err(TrackerError::SeedTargetOutsideFrame);
        }

        let mut observations = Vec::with_capacity(frames.len() - seed_index);
        observations.push(TrackerObservation {
            timestamp_s: frames[seed_index].timestamp_s,
            frame_index: frames[seed_index].frame_index,
            state: TrackerObservationState::Tracked {
                center: seed.target().center(),
                confidence: 1.0,
            },
            visibility: TrackerVisibilityState::Unknown,
            target_bounds_px: Some(bounds_for_center(
                seed.target().center(),
                seed.target().radius_px(),
            )),
            diagnostics: TrackerDiagnostics {
                quality_score: Some(0.0),
                displacement_px: Some(0.0),
                reacquired_after_loss: false,
            },
        });

        let mut last_center = seed.target().center();
        let mut was_lost = false;
        let search_radius = i32::try_from(self.config.search_radius_px).map_err(|_| {
            TrackerError::InvalidConfiguration {
                field: "search_radius_px",
            }
        })?;

        for frame in frames.iter().skip(seed_index + 1) {
            let search_center = rounded_point(last_center);
            let mut best: Option<((i32, i32), f64)> = None;

            for y in (search_center.1 - search_radius)..=(search_center.1 + search_radius) {
                for x in (search_center.0 - search_radius)..=(search_center.0 + search_radius) {
                    let candidate = (x, y);
                    if !patch_fits(frame.image, candidate, radius) {
                        continue;
                    }
                    let score = normalized_mean_absolute_difference(
                        seed_frame,
                        seed_center,
                        frame.image,
                        candidate,
                        radius,
                    );
                    if best.is_none_or(|(_, best_score)| score < best_score) {
                        best = Some((candidate, score));
                    }
                }
            }

            let Some((candidate, score)) = best else {
                observations.push(lost_observation(
                    frame,
                    TrackerLossReason::NoCandidate,
                    None,
                ));
                was_lost = true;
                continue;
            };

            if score > self.config.max_normalized_mean_absolute_difference {
                observations.push(lost_observation(
                    frame,
                    TrackerLossReason::PoorMatch,
                    Some(score),
                ));
                was_lost = true;
                continue;
            }

            let center = PixelPoint::new(f64::from(candidate.0), f64::from(candidate.1));
            let bounds = bounds_for_center(center, seed.target().radius_px());
            if !bounds_fit(frame.image, bounds) {
                observations.push(lost_observation(
                    frame,
                    TrackerLossReason::TargetOutsideFrame,
                    Some(score),
                ));
                was_lost = true;
                continue;
            }

            let displacement_px = distance(last_center, center);
            let confidence = (1.0 - score).clamp(0.0, 1.0) as f32;
            let state = if score
                > self
                    .config
                    .low_confidence_normalized_mean_absolute_difference
            {
                TrackerObservationState::LowConfidence { center, confidence }
            } else {
                TrackerObservationState::Tracked { center, confidence }
            };
            observations.push(TrackerObservation {
                timestamp_s: frame.timestamp_s,
                frame_index: frame.frame_index,
                state,
                visibility: TrackerVisibilityState::Unknown,
                target_bounds_px: Some(bounds),
                diagnostics: TrackerDiagnostics {
                    quality_score: Some(score),
                    displacement_px: Some(displacement_px),
                    reacquired_after_loss: was_lost,
                },
            });
            last_center = center;
            was_lost = false;
        }

        Ok(TrackerRun {
            tracker: self.identity(),
            seed_timestamp_s: seed.timestamp_s(),
            observations,
        })
    }
}
