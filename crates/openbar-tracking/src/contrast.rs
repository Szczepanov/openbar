use crate::{
    bounds_fit, bounds_for_center, contains, contrast_mass_in_target, distance, lost_observation,
    pixel, rounded_point, rounded_radius, target_and_ring_means, validate_search_radius,
    validate_seed_tolerance, validate_sequence_and_seed, FrameSample, ManualSeedTracker,
    TrackerDiagnostics, TrackerError, TrackerIdentity, TrackerLossReason, TrackerObservation,
    TrackerObservationState, TrackerRun, TrackerVisibilityState,
};
use openbar_core::manual_seed::{ManualTargetSeed, PixelPoint};
use std::collections::BTreeMap;

pub const VERSION: &str = "1";

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LocalContrastConfig {
    pub search_radius_px: u32,
    pub min_seed_contrast: f64,
    pub min_mass_ratio: f64,
    pub seed_timestamp_tolerance_s: f64,
}

impl Default for LocalContrastConfig {
    fn default() -> Self {
        Self {
            search_radius_px: 12,
            min_seed_contrast: 12.0,
            min_mass_ratio: 0.30,
            seed_timestamp_tolerance_s: 1e-6,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LocalContrastTracker {
    config: LocalContrastConfig,
}

impl LocalContrastTracker {
    pub fn try_new(config: LocalContrastConfig) -> Result<Self, TrackerError> {
        validate_search_radius(config.search_radius_px)?;
        if !config.min_seed_contrast.is_finite() || config.min_seed_contrast <= 0.0 {
            return Err(TrackerError::InvalidConfiguration {
                field: "min_seed_contrast",
            });
        }
        if !config.min_mass_ratio.is_finite() || !(0.0..=1.0).contains(&config.min_mass_ratio) {
            return Err(TrackerError::InvalidConfiguration {
                field: "min_mass_ratio",
            });
        }
        validate_seed_tolerance(config.seed_timestamp_tolerance_s)?;
        Ok(Self { config })
    }
}

impl Default for LocalContrastTracker {
    fn default() -> Self {
        Self::try_new(LocalContrastConfig::default()).expect("default contrast config is valid")
    }
}

impl ManualSeedTracker for LocalContrastTracker {
    fn identity(&self) -> TrackerIdentity {
        let mut config = BTreeMap::new();
        config.insert("search_radius_px".to_owned(), self.config.search_radius_px.to_string());
        config.insert("min_seed_contrast".to_owned(), self.config.min_seed_contrast.to_string());
        config.insert("min_mass_ratio".to_owned(), self.config.min_mass_ratio.to_string());
        config.insert(
            "seed_timestamp_tolerance_s".to_owned(),
            self.config.seed_timestamp_tolerance_s.to_string(),
        );
        TrackerIdentity {
            id: "local-contrast-centroid-v1".to_owned(),
            implementation: "local contrast weighted centroid".to_owned(),
            version: VERSION.to_owned(),
            config,
            confidence_semantics:
                "contrast mass relative to seed-frame target mass, clamped to [0, 1]; algorithm-specific and not calibrated across trackers"
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
        let seed_frame = frames[seed_index].image;
        let radius = rounded_radius(seed)?;
        let seed_center = rounded_point(seed.target().center());

        let Some((target_mean, background_mean)) =
            target_and_ring_means(seed_frame, seed_center, radius)
        else {
            return Err(TrackerError::SeedTargetOutsideFrame);
        };
        let contrast = (target_mean - background_mean).abs();
        if contrast < self.config.min_seed_contrast {
            return Err(TrackerError::InsufficientSeedContrast { contrast });
        }

        let dark_target = target_mean < background_mean;
        let threshold = (target_mean + background_mean) / 2.0;
        let (seed_mass, _, _) =
            contrast_mass_in_target(seed_frame, seed_center, radius, threshold, dark_target);
        if seed_mass <= f64::EPSILON {
            return Err(TrackerError::InsufficientSeedContrast { contrast });
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
                quality_score: Some(1.0),
                displacement_px: Some(0.0),
                reacquired_after_loss: false,
            },
        });

        let mut last_center = seed.target().center();
        let mut was_lost = false;
        for frame in frames.iter().skip(seed_index + 1) {
            let search_center = rounded_point(last_center);
            let search_radius = i32::try_from(self.config.search_radius_px)
                .map_err(|_| TrackerError::InvalidConfiguration { field: "search_radius_px" })?;
            let half_window = radius + search_radius;
            let mut mass = 0.0;
            let mut weighted_x = 0.0;
            let mut weighted_y = 0.0;

            for y in (search_center.1 - half_window)..=(search_center.1 + half_window) {
                for x in (search_center.0 - half_window)..=(search_center.0 + half_window) {
                    if !contains(frame.image, x, y) {
                        continue;
                    }
                    let intensity = f64::from(pixel(frame.image, x, y));
                    let weight = if dark_target {
                        (threshold - intensity).max(0.0)
                    } else {
                        (intensity - threshold).max(0.0)
                    };
                    if weight > 0.0 {
                        mass += weight;
                        weighted_x += f64::from(x) * weight;
                        weighted_y += f64::from(y) * weight;
                    }
                }
            }

            let mass_ratio = mass / seed_mass;
            if mass <= f64::EPSILON || mass_ratio < self.config.min_mass_ratio {
                observations.push(lost_observation(
                    frame,
                    TrackerLossReason::InsufficientContrast,
                    Some(mass_ratio),
                ));
                was_lost = true;
                continue;
            }

            let center = PixelPoint::new(weighted_x / mass, weighted_y / mass);
            let displacement_px = distance(last_center, center);
            if displacement_px > f64::from(self.config.search_radius_px) + f64::from(radius) {
                observations.push(lost_observation(
                    frame,
                    TrackerLossReason::NoCandidate,
                    Some(mass_ratio),
                ));
                was_lost = true;
                continue;
            }

            let bounds = bounds_for_center(center, seed.target().radius_px());
            if !bounds_fit(frame.image, bounds) {
                observations.push(lost_observation(
                    frame,
                    TrackerLossReason::TargetOutsideFrame,
                    Some(mass_ratio),
                ));
                was_lost = true;
                continue;
            }

            observations.push(TrackerObservation {
                timestamp_s: frame.timestamp_s,
                frame_index: frame.frame_index,
                state: TrackerObservationState::Tracked {
                    center,
                    confidence: mass_ratio.clamp(0.0, 1.0) as f32,
                },
                visibility: TrackerVisibilityState::Unknown,
                target_bounds_px: Some(bounds),
                diagnostics: TrackerDiagnostics {
                    quality_score: Some(mass_ratio),
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
