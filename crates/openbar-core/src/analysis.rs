use crate::calibration::{CalibrationProvenance, PlateDiameterCalibration};
use crate::manual_seed::{
    ManualTargetSeed, PixelBoundingBox, SeedValidationContext,
};
use crate::trajectory::{
    KinematicSample, MetricPositionSample, PixelObservation, TrajectoryValidationError,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fmt;

pub const ANALYSIS_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(try_from = "AnalysisRepr", into = "AnalysisRepr")]
pub struct Analysis {
    schema_version: u32,
    identity: AnalysisIdentity,
    video: VideoMetadata,
    manual_seed: ManualTargetSeed,
    calibration: PlateDiameterCalibration,
    raw_observations: Vec<RawObservation>,
    derived: DerivedData,
    provenance: AnalysisProvenance,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct AnalysisRepr {
    schema_version: u32,
    identity: AnalysisIdentity,
    video: VideoMetadata,
    manual_seed: ManualTargetSeed,
    calibration: PlateDiameterCalibration,
    raw_observations: Vec<RawObservation>,
    derived: DerivedData,
    provenance: AnalysisProvenance,
}

impl Analysis {
    #[allow(clippy::too_many_arguments)]
    pub fn try_new(
        identity: AnalysisIdentity,
        video: VideoMetadata,
        manual_seed: ManualTargetSeed,
        calibration: PlateDiameterCalibration,
        raw_observations: Vec<RawObservation>,
        derived: DerivedData,
        provenance: AnalysisProvenance,
    ) -> Result<Self, AnalysisValidationError> {
        let analysis = Self {
            schema_version: ANALYSIS_SCHEMA_VERSION,
            identity,
            video,
            manual_seed,
            calibration,
            raw_observations,
            derived,
            provenance,
        };
        analysis.validate()?;
        Ok(analysis)
    }

    pub fn validate(&self) -> Result<(), AnalysisValidationError> {
        if self.schema_version != ANALYSIS_SCHEMA_VERSION {
            return Err(invalid(format!(
                "analysis schema version {} is unsupported; expected {}",
                self.schema_version, ANALYSIS_SCHEMA_VERSION
            )));
        }

        self.identity.validate()?;
        self.video.validate()?;
        self.provenance.validate()?;

        let seed_context = SeedValidationContext {
            frame_width_px: self.video.display_width_px,
            frame_height_px: self.video.display_height_px,
            selected_range_start_s: self.video.trim.start_s,
            selected_range_end_s: self.video.trim.end_s,
            source_rotation_deg: self.video.source_rotation_deg,
        };
        self.manual_seed
            .validate(seed_context)
            .map_err(|error| invalid(format!("manual seed: {error}")))?;
        self.calibration
            .validate()
            .map_err(|error| invalid(format!("calibration: {error}")))?;
        self.validate_calibration_context()?;
        self.validate_raw_observations()?;
        self.validate_derived_data()?;

        Ok(())
    }

    pub const fn schema_version(&self) -> u32 {
        self.schema_version
    }

    pub const fn identity(&self) -> &AnalysisIdentity {
        &self.identity
    }

    pub const fn video(&self) -> &VideoMetadata {
        &self.video
    }

    pub const fn manual_seed(&self) -> &ManualTargetSeed {
        &self.manual_seed
    }

    pub const fn calibration(&self) -> &PlateDiameterCalibration {
        &self.calibration
    }

    pub fn raw_observations(&self) -> &[RawObservation] {
        &self.raw_observations
    }

    pub const fn derived(&self) -> &DerivedData {
        &self.derived
    }

    pub const fn provenance(&self) -> &AnalysisProvenance {
        &self.provenance
    }

    pub fn to_json_pretty(&self) -> Result<String, AnalysisJsonError> {
        self.validate().map_err(AnalysisJsonError::Validation)?;
        serde_json::to_string_pretty(self)
            .map_err(|error| AnalysisJsonError::Json(error.to_string()))
    }

    pub fn from_json(input: &str) -> Result<Self, AnalysisJsonError> {
        serde_json::from_str(input).map_err(|error| AnalysisJsonError::Json(error.to_string()))
    }

    fn validate_calibration_context(&self) -> Result<(), AnalysisValidationError> {
        let reference = self.calibration.reference();
        if reference.source_rotation_deg() != self.video.source_rotation_deg {
            return Err(invalid(
                "calibration source rotation does not match video source rotation",
            ));
        }
        if !self.video.trim.contains(reference.timestamp_s()) {
            return Err(invalid(
                "calibration reference timestamp lies outside the selected video range",
            ));
        }

        if matches!(reference.provenance(), CalibrationProvenance::ManualTargetSeed { .. }) {
            let seed = &self.manual_seed;
            if reference.timestamp_s() != seed.timestamp_s()
                || reference.frame_index() != seed.frame_index()
                || reference.source_rotation_deg() != seed.source_rotation_deg()
                || reference.coordinate_space() != seed.coordinate_space()
                || reference.geometry().center_px() != seed.target().center()
                || reference.geometry().bounds_px() != seed.target().bounding_box()
            {
                return Err(invalid(
                    "manual-seed calibration reference does not match the embedded manual seed",
                ));
            }
        }

        Ok(())
    }

    fn validate_raw_observations(&self) -> Result<(), AnalysisValidationError> {
        let mut previous_timestamp = None;
        for (index, observation) in self.raw_observations.iter().enumerate() {
            observation
                .validate()
                .map_err(|error| invalid(format!("raw observation {index}: {error}")))?;

            if observation.tracker_id != self.provenance.tracker.id {
                return Err(invalid(format!(
                    "raw observation {index} references tracker_id '{}' but provenance tracker id is '{}'",
                    observation.tracker_id, self.provenance.tracker.id
                )));
            }
            if !self.video.trim.contains(observation.timestamp_s) {
                return Err(invalid(format!(
                    "raw observation {index} timestamp lies outside the selected video range"
                )));
            }
            if let Some(previous) = previous_timestamp {
                if observation.timestamp_s <= previous {
                    return Err(invalid(
                        "raw observation timestamps must be strictly increasing",
                    ));
                }
            }
            previous_timestamp = Some(observation.timestamp_s);

            if let Some(measurement) = observation.measurement {
                if measurement.x_px < 0.0
                    || measurement.x_px >= f64::from(self.video.display_width_px)
                    || measurement.y_px < 0.0
                    || measurement.y_px >= f64::from(self.video.display_height_px)
                {
                    return Err(invalid(format!(
                        "raw observation {index} measured centre lies outside the display-oriented frame"
                    )));
                }
            }
        }
        Ok(())
    }

    fn validate_derived_data(&self) -> Result<(), AnalysisValidationError> {
        let measured: Vec<PixelObservation> = self
            .raw_observations
            .iter()
            .filter_map(|observation| observation.measurement)
            .collect();
        let calibrated = &self.derived.calibrated.samples;

        if measured.len() != calibrated.len() {
            return Err(invalid(
                "calibrated trajectory must contain exactly one sample for every measured raw observation",
            ));
        }

        validate_metric_series(calibrated, "calibrated")?;
        for (index, (raw, metric)) in measured.iter().zip(calibrated).enumerate() {
            if raw.timestamp_s != metric.timestamp_s {
                return Err(invalid(format!(
                    "calibrated sample {index} timestamp does not match its raw observation"
                )));
            }
            if raw.confidence != metric.confidence {
                return Err(invalid(format!(
                    "calibrated sample {index} must preserve raw measurement confidence"
                )));
            }

            let expected = self
                .calibration
                .calibrate_observation(*raw)
                .map_err(|error| invalid(format!("calibrated sample {index}: {error}")))?;
            if !approximately_equal(expected.x_m, metric.x_m)
                || !approximately_equal(expected.y_m, metric.y_m)
            {
                return Err(invalid(format!(
                    "calibrated sample {index} is inconsistent with the recorded calibration"
                )));
            }
        }
