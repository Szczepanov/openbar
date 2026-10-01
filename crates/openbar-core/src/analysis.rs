use crate::calibration::{CalibrationProvenance, PlateDiameterCalibration};
use crate::manual_seed::{ManualTargetSeed, PixelBoundingBox, SeedValidationContext};
use crate::math::approximately_equal;
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
        let representation: AnalysisRepr = serde_json::from_str(input)
            .map_err(|error| AnalysisJsonError::Json(error.to_string()))?;
        Self::try_from(representation).map_err(AnalysisJsonError::Validation)
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

        if matches!(
            reference.provenance(),
            CalibrationProvenance::ManualTargetSeed { .. }
        ) {
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
            if let Some(bounds) = observation.target_bounds_px {
                if bounds.left_px < 0.0
                    || bounds.top_px < 0.0
                    || bounds.right_px() > f64::from(self.video.display_width_px)
                    || bounds.bottom_px() > f64::from(self.video.display_height_px)
                {
                    return Err(invalid(format!(
                        "raw observation {index} target bounds lie outside the display-oriented frame"
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

        if let Some(filtered) = &self.derived.filtered {
            filtered.filter.validate("filter")?;
            validate_metric_series(&filtered.samples, "filtered")?;
            validate_aligned_metric_series(calibrated, &filtered.samples, "filtered")?;
        }

        if let Some(kinematics) = &self.derived.kinematics {
            kinematics.method.validate("kinematics.method")?;
            validate_kinematic_series(&kinematics.samples)?;
            let input = match kinematics.input {
                KinematicsInput::Calibrated => calibrated,
                KinematicsInput::Filtered => self
                    .derived
                    .filtered
                    .as_ref()
                    .ok_or_else(|| {
                        invalid("kinematics declares filtered input but no filtered series exists")
                    })?
                    .samples
                    .as_slice(),
            };
            if input.len() != kinematics.samples.len() {
                return Err(invalid(
                    "kinematic series must stay sample-aligned with its declared position input",
                ));
            }
            for (index, (position, sample)) in input.iter().zip(&kinematics.samples).enumerate() {
                if position.timestamp_s != sample.timestamp_s
                    || !approximately_equal(position.x_m, sample.x_m)
                    || !approximately_equal(position.y_m, sample.y_m)
                {
                    return Err(invalid(format!(
                        "kinematic sample {index} does not match its declared position input"
                    )));
                }
                if sample.confidence > position.confidence {
                    return Err(invalid(format!(
                        "kinematic sample {index} confidence exceeds its position input confidence"
                    )));
                }
            }
        }

        Ok(())
    }
}

impl TryFrom<AnalysisRepr> for Analysis {
    type Error = AnalysisValidationError;

    fn try_from(value: AnalysisRepr) -> Result<Self, Self::Error> {
        let analysis = Self {
            schema_version: value.schema_version,
            identity: value.identity,
            video: value.video,
            manual_seed: value.manual_seed,
            calibration: value.calibration,
            raw_observations: value.raw_observations,
            derived: value.derived,
            provenance: value.provenance,
        };
        analysis.validate()?;
        Ok(analysis)
    }
}

impl From<Analysis> for AnalysisRepr {
    fn from(value: Analysis) -> Self {
        Self {
            schema_version: value.schema_version,
            identity: value.identity,
            video: value.video,
            manual_seed: value.manual_seed,
            calibration: value.calibration,
            raw_observations: value.raw_observations,
            derived: value.derived,
            provenance: value.provenance,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AnalysisIdentity {
    pub source_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fixture_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_sha256: Option<String>,
}

impl AnalysisIdentity {
    fn validate(&self) -> Result<(), AnalysisValidationError> {
        validate_non_blank("identity.source_id", &self.source_id)?;
        if let Some(fixture_id) = self.fixture_id.as_deref() {
            validate_identifier("identity.fixture_id", fixture_id)?;
        }
        if let Some(hash) = self.source_sha256.as_deref() {
            validate_sha256("identity.source_sha256", hash)?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TimeRange {
    pub start_s: f64,
    pub end_s: f64,
}

impl TimeRange {
    pub fn contains(self, timestamp_s: f64) -> bool {
        timestamp_s >= self.start_s && timestamp_s <= self.end_s
    }

    fn validate(self) -> Result<(), AnalysisValidationError> {
        if !self.start_s.is_finite() || !self.end_s.is_finite() {
            return Err(invalid("video trim range must be finite"));
        }
        if self.start_s < 0.0 || self.end_s < self.start_s {
            return Err(invalid("video trim range must be non-negative and ordered"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TimestampBasis {
    DecodedPresentationTimestamp,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FrameRateMetadata {
    pub timestamp_basis: TimestampBasis,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub nominal_fps: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub measured_fps: Option<f64>,
}

impl FrameRateMetadata {
    fn validate(self) -> Result<(), AnalysisValidationError> {
        for (name, value) in [
            ("video.frame_rate.nominal_fps", self.nominal_fps),
            ("video.frame_rate.measured_fps", self.measured_fps),
        ] {
            if let Some(value) = value {
                if !value.is_finite() || value <= 0.0 {
                    return Err(invalid(format!("{name} must be finite and positive")));
                }
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VideoMetadata {
    pub decoded_width_px: u32,
    pub decoded_height_px: u32,
    pub display_width_px: u32,
    pub display_height_px: u32,
    pub source_rotation_deg: u16,
    pub frame_rate: FrameRateMetadata,
    pub trim: TimeRange,
}

impl VideoMetadata {
    fn validate(&self) -> Result<(), AnalysisValidationError> {
        if self.decoded_width_px == 0
            || self.decoded_height_px == 0
            || self.display_width_px == 0
            || self.display_height_px == 0
        {
            return Err(invalid("video dimensions must be positive"));
        }
        if !matches!(self.source_rotation_deg, 0 | 90 | 180 | 270) {
            return Err(invalid(
                "video source rotation must be 0, 90, 180, or 270 degrees",
            ));
        }
        self.frame_rate.validate()?;
        self.trim.validate()?;
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TrackingState {
    Tracked,
    LowConfidence,
    Lost,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VisibilityState {
    Visible,
    PartiallyOccluded,
    Occluded,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RawObservation {
    pub timestamp_s: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub frame_index: Option<u64>,
    pub tracking_state: TrackingState,
    pub visibility: VisibilityState,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub measurement: Option<PixelObservation>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_bounds_px: Option<PixelBoundingBox>,
    pub tracker_id: String,
}

impl RawObservation {
    pub fn validate(&self) -> Result<(), AnalysisValidationError> {
        if !self.timestamp_s.is_finite() || self.timestamp_s < 0.0 {
            return Err(invalid(
                "raw observation timestamp must be finite and non-negative",
            ));
        }
        validate_non_blank("raw observation tracker_id", &self.tracker_id)?;

        match (self.tracking_state, self.measurement) {
            (TrackingState::Lost, Some(_)) => {
                return Err(invalid(
                    "lost raw observation must not contain measured coordinates",
                ));
            }
            (TrackingState::Tracked | TrackingState::LowConfidence, None) => {
                return Err(invalid(
                    "tracked/low-confidence raw observation requires measured coordinates",
                ));
            }
            _ => {}
        }

        if let Some(measurement) = self.measurement {
            measurement
                .validate()
                .map_err(|error| invalid(format!("raw measurement: {error}")))?;
            if measurement.timestamp_s != self.timestamp_s {
                return Err(invalid(
                    "raw observation timestamp must equal its measured sample timestamp",
                ));
            }
        }

        if self.tracking_state == TrackingState::Lost && self.target_bounds_px.is_some() {
            return Err(invalid(
                "lost raw observation must not retain target bounds as if they were measured",
            ));
        }
        if let Some(bounds) = self.target_bounds_px {
            validate_bounds(bounds)?;
            if let Some(measurement) = self.measurement {
                if measurement.x_px < bounds.left_px
                    || measurement.x_px > bounds.right_px()
                    || measurement.y_px < bounds.top_px
                    || measurement.y_px > bounds.bottom_px()
                {
                    return Err(invalid(
                        "raw measurement centre must lie inside recorded target bounds",
                    ));
                }
            }
        }

        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum ParameterValue {
    Boolean(bool),
    Integer(i64),
    Float(f64),
    Text(String),
}

impl ParameterValue {
    fn validate(&self, path: &str) -> Result<(), AnalysisValidationError> {
        match self {
            Self::Float(value) if !value.is_finite() => Err(invalid(format!(
                "{path} must not contain non-finite floats"
            ))),
            _ => Ok(()),
        }
    }
}

pub type Configuration = BTreeMap<String, ParameterValue>;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImplementationProvenance {
    pub implementation: String,
    pub version: String,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub parameters: Configuration,
}

impl ImplementationProvenance {
    fn validate(&self, path: &str) -> Result<(), AnalysisValidationError> {
        validate_non_blank(&format!("{path}.implementation"), &self.implementation)?;
        validate_non_blank(&format!("{path}.version"), &self.version)?;
        validate_configuration(path, &self.parameters)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TrackerProvenance {
    pub id: String,
    pub implementation: ImplementationProvenance,
}

impl TrackerProvenance {
    fn validate(&self) -> Result<(), AnalysisValidationError> {
        validate_identifier("provenance.tracker.id", &self.id)?;
        self.implementation.validate("provenance.tracker")
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PipelineProvenance {
    pub openbar_version: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub git_commit: Option<String>,
}

impl PipelineProvenance {
    fn validate(&self) -> Result<(), AnalysisValidationError> {
        validate_non_blank("provenance.pipeline.openbar_version", &self.openbar_version)?;
        if let Some(commit) = self.git_commit.as_deref() {
            if commit.len() < 7
                || commit.len() > 64
                || !commit.bytes().all(|byte| byte.is_ascii_hexdigit())
            {
                return Err(invalid(
                    "provenance.pipeline.git_commit must be a 7-64 character hexadecimal commit id",
                ));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModelProvenance {
    pub identifier: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub checksum_sha256: Option<String>,
}

impl ModelProvenance {
    fn validate(&self) -> Result<(), AnalysisValidationError> {
        validate_non_blank("provenance.model.identifier", &self.identifier)?;
        if let Some(hash) = self.checksum_sha256.as_deref() {
            validate_sha256("provenance.model.checksum_sha256", hash)?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EnvironmentProvenance {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub os: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub architecture: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub device: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub decoder: Option<String>,
}

impl EnvironmentProvenance {
    fn validate(&self) -> Result<(), AnalysisValidationError> {
        for (name, value) in [
            ("provenance.environment.os", self.os.as_deref()),
            (
                "provenance.environment.architecture",
                self.architecture.as_deref(),
            ),
            ("provenance.environment.device", self.device.as_deref()),
            ("provenance.environment.decoder", self.decoder.as_deref()),
        ] {
            if let Some(value) = value {
                validate_non_blank(name, value)?;
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AnalysisProvenance {
    pub pipeline: PipelineProvenance,
    pub tracker: TrackerProvenance,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<ModelProvenance>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub environment: Option<EnvironmentProvenance>,
}

impl AnalysisProvenance {
    fn validate(&self) -> Result<(), AnalysisValidationError> {
        self.pipeline.validate()?;
        self.tracker.validate()?;
        if let Some(model) = &self.model {
            model.validate()?;
        }
        if let Some(environment) = &self.environment {
            environment.validate()?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CalibratedTrajectory {
    pub samples: Vec<MetricPositionSample>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FilteredTrajectory {
    pub filter: ImplementationProvenance,
    pub samples: Vec<MetricPositionSample>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum KinematicsInput {
    Calibrated,
    Filtered,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct KinematicTrajectory {
    pub input: KinematicsInput,
    pub method: ImplementationProvenance,
    pub samples: Vec<KinematicSample>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DerivedData {
    pub calibrated: CalibratedTrajectory,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub filtered: Option<FilteredTrajectory>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub kinematics: Option<KinematicTrajectory>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AnalysisValidationError {
    message: String,
}

impl AnalysisValidationError {
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl fmt::Display for AnalysisValidationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for AnalysisValidationError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AnalysisJsonError {
    Validation(AnalysisValidationError),
    Json(String),
}

impl fmt::Display for AnalysisJsonError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Validation(error) => write!(formatter, "analysis validation failed: {error}"),
            Self::Json(error) => write!(formatter, "analysis JSON error: {error}"),
        }
    }
}

impl std::error::Error for AnalysisJsonError {}

fn invalid(message: impl Into<String>) -> AnalysisValidationError {
    AnalysisValidationError {
        message: message.into(),
    }
}

fn validate_non_blank(path: &str, value: &str) -> Result<(), AnalysisValidationError> {
    if value.trim().is_empty() {
        return Err(invalid(format!("{path} must not be blank")));
    }
    Ok(())
}

fn validate_identifier(path: &str, value: &str) -> Result<(), AnalysisValidationError> {
    validate_non_blank(path, value)?;
    let mut bytes = value.bytes();
    let Some(first) = bytes.next() else {
        return Err(invalid(format!("{path} must not be blank")));
    };
    if !first.is_ascii_lowercase() && !first.is_ascii_digit() {
        return Err(invalid(format!(
            "{path} must start with a lowercase ASCII letter or digit"
        )));
    }
    if !bytes.all(|byte| {
        byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'.' | b'_' | b'-')
    }) {
        return Err(invalid(format!(
            "{path} may contain only lowercase ASCII letters, digits, '.', '_' or '-'"
        )));
    }
    Ok(())
}

fn validate_sha256(path: &str, value: &str) -> Result<(), AnalysisValidationError> {
    if value.len() != 64 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(invalid(format!(
            "{path} must be exactly 64 hexadecimal characters"
        )));
    }
    Ok(())
}

fn validate_bounds(bounds: PixelBoundingBox) -> Result<(), AnalysisValidationError> {
    if !bounds.left_px.is_finite()
        || !bounds.top_px.is_finite()
        || !bounds.width_px.is_finite()
        || !bounds.height_px.is_finite()
    {
        return Err(invalid("target bounds must be finite"));
    }
    if bounds.width_px <= 0.0 || bounds.height_px <= 0.0 {
        return Err(invalid("target bounds must have positive width and height"));
    }
    Ok(())
}

fn validate_configuration(
    path: &str,
    configuration: &Configuration,
) -> Result<(), AnalysisValidationError> {
    for (key, value) in configuration {
        validate_non_blank(&format!("{path}.parameters key"), key)?;
        value.validate(&format!("{path}.parameters.{key}"))?;
    }
    Ok(())
}

fn validate_metric_series(
    samples: &[MetricPositionSample],
    name: &str,
) -> Result<(), AnalysisValidationError> {
    let mut previous_timestamp = None;
    for (index, sample) in samples.iter().copied().enumerate() {
        sample
            .validate()
            .map_err(|error| trajectory_error(format!("{name} sample {index}"), error))?;
        if let Some(previous) = previous_timestamp {
            if sample.timestamp_s <= previous {
                return Err(invalid(format!(
                    "{name} sample timestamps must be strictly increasing"
                )));
            }
        }
        previous_timestamp = Some(sample.timestamp_s);
    }
    Ok(())
}

fn validate_aligned_metric_series(
    source: &[MetricPositionSample],
    derived: &[MetricPositionSample],
    name: &str,
) -> Result<(), AnalysisValidationError> {
    if source.len() != derived.len() {
        return Err(invalid(format!(
            "{name} trajectory must stay timestamp-aligned with the calibrated trajectory"
        )));
    }
    for (index, (source, derived)) in source.iter().zip(derived).enumerate() {
        if source.timestamp_s != derived.timestamp_s {
            return Err(invalid(format!(
                "{name} sample {index} timestamp does not match calibrated input"
            )));
        }
    }
    Ok(())
}

fn validate_kinematic_series(samples: &[KinematicSample]) -> Result<(), AnalysisValidationError> {
    let mut previous_timestamp = None;
    for (index, sample) in samples.iter().copied().enumerate() {
        sample
            .validate()
            .map_err(|error| trajectory_error(format!("kinematic sample {index}"), error))?;
        if let Some(previous) = previous_timestamp {
            if sample.timestamp_s <= previous {
                return Err(invalid(
                    "kinematic sample timestamps must be strictly increasing",
                ));
            }
        }
        previous_timestamp = Some(sample.timestamp_s);
    }
    Ok(())
}

fn trajectory_error(prefix: String, error: TrajectoryValidationError) -> AnalysisValidationError {
    invalid(format!("{prefix}: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::calibration::{CalibrationQuality, PlateDiameterCalibration};
    use crate::manual_seed::{PixelPoint, PlateTarget};

    fn seed(video: &VideoMetadata) -> ManualTargetSeed {
        ManualTargetSeed::try_new(
            1.0,
            Some(60),
            PlateTarget::new(PixelPoint::new(960.0, 700.0), 100.0),
            video.source_rotation_deg,
            Some(0.95),
            Some("golden seed".to_owned()),
            SeedValidationContext {
                frame_width_px: video.display_width_px,
                frame_height_px: video.display_height_px,
                selected_range_start_s: video.trim.start_s,
                selected_range_end_s: video.trim.end_s,
                source_rotation_deg: video.source_rotation_deg,
            },
        )
        .unwrap()
    }

    fn video() -> VideoMetadata {
        VideoMetadata {
            decoded_width_px: 1920,
            decoded_height_px: 1080,
            display_width_px: 1920,
            display_height_px: 1080,
            source_rotation_deg: 0,
            frame_rate: FrameRateMetadata {
                timestamp_basis: TimestampBasis::DecodedPresentationTimestamp,
                nominal_fps: Some(60.0),
                measured_fps: Some(59.94),
            },
            trim: TimeRange {
                start_s: 0.5,
                end_s: 2.0,
            },
        }
    }

    fn provenance() -> AnalysisProvenance {
        let mut tracker_parameters = BTreeMap::new();
        tracker_parameters.insert("search_radius_px".to_owned(), ParameterValue::Integer(32));
        AnalysisProvenance {
            pipeline: PipelineProvenance {
                openbar_version: "0.1.0".to_owned(),
                git_commit: Some("0123456789abcdef0123456789abcdef01234567".to_owned()),
            },
            tracker: TrackerProvenance {
                id: "tracker-primary".to_owned(),
                implementation: ImplementationProvenance {
                    implementation: "synthetic-tracker".to_owned(),
                    version: "1".to_owned(),
                    parameters: tracker_parameters,
                },
            },
            model: None,
            environment: Some(EnvironmentProvenance {
                os: Some("test-os".to_owned()),
                architecture: Some("test-arch".to_owned()),
                device: None,
                decoder: Some("synthetic-decoder@1".to_owned()),
            }),
        }
    }

    fn analysis() -> Analysis {
        let video = video();
        let seed = seed(&video);
        let calibration = PlateDiameterCalibration::try_from_manual_seed(
            0.45,
            &seed,
            CalibrationQuality::unassessed(),
        )
        .unwrap();
        let raw_a = PixelObservation {
            timestamp_s: 1.0,
            x_px: 960.0,
            y_px: 700.0,
            confidence: 0.95,
        };
        let raw_b = PixelObservation {
            timestamp_s: 1.2,
            x_px: 980.0,
            y_px: 660.0,
            confidence: 0.75,
        };
        let calibrated_a = calibration.calibrate_observation(raw_a).unwrap();
        let calibrated_b = calibration.calibrate_observation(raw_b).unwrap();
        let metric = vec![
            MetricPositionSample {
                timestamp_s: raw_a.timestamp_s,
                x_m: calibrated_a.x_m,
                y_m: calibrated_a.y_m,
                confidence: raw_a.confidence,
            },
            MetricPositionSample {
                timestamp_s: raw_b.timestamp_s,
                x_m: calibrated_b.x_m,
                y_m: calibrated_b.y_m,
                confidence: raw_b.confidence,
            },
        ];

        Analysis::try_new(
            AnalysisIdentity {
                source_id: "synthetic-clean-side-12".to_owned(),
                fixture_id: Some("synthetic-clean-side-12".to_owned()),
                source_sha256: Some("a".repeat(64)),
            },
            video,
            seed,
            calibration,
            vec![
                RawObservation {
                    timestamp_s: 1.0,
                    frame_index: Some(60),
                    tracking_state: TrackingState::Tracked,
                    visibility: VisibilityState::Visible,
                    measurement: Some(raw_a),
                    target_bounds_px: Some(PixelBoundingBox {
                        left_px: 860.0,
                        top_px: 600.0,
                        width_px: 200.0,
                        height_px: 200.0,
                    }),
                    tracker_id: "tracker-primary".to_owned(),
                },
                RawObservation {
                    timestamp_s: 1.1,
                    frame_index: Some(66),
                    tracking_state: TrackingState::Lost,
                    visibility: VisibilityState::Occluded,
                    measurement: None,
                    target_bounds_px: None,
                    tracker_id: "tracker-primary".to_owned(),
                },
                RawObservation {
                    timestamp_s: 1.2,
                    frame_index: Some(72),
                    tracking_state: TrackingState::LowConfidence,
                    visibility: VisibilityState::PartiallyOccluded,
                    measurement: Some(raw_b),
                    target_bounds_px: Some(PixelBoundingBox {
                        left_px: 880.0,
                        top_px: 560.0,
                        width_px: 200.0,
                        height_px: 200.0,
                    }),
                    tracker_id: "tracker-primary".to_owned(),
                },
            ],
            DerivedData {
                calibrated: CalibratedTrajectory {
                    samples: metric.clone(),
                },
                filtered: None,
                kinematics: None,
            },
            provenance(),
        )
        .unwrap()
    }

    #[test]
    fn canonical_analysis_round_trips_and_serializes_deterministically() {
        let analysis = analysis();
        let first = analysis.to_json_pretty().unwrap();
        let second = analysis.to_json_pretty().unwrap();
        assert_eq!(first, second);

        let decoded = Analysis::from_json(&first).unwrap();
        assert_eq!(decoded, analysis);
    }

    #[test]
    fn lost_tracking_requires_no_fake_coordinate() {
        let analysis = analysis();
        let lost = &analysis.raw_observations()[1];
        assert_eq!(lost.tracking_state, TrackingState::Lost);
        assert!(lost.measurement.is_none());
    }

    #[test]
    fn supports_irregular_timestamps_and_no_velocity_layer() {
        let analysis = analysis();
        assert_eq!(analysis.raw_observations()[1].timestamp_s, 1.1);
        assert_eq!(analysis.raw_observations()[2].timestamp_s, 1.2);
        assert!(analysis.derived().kinematics.is_none());
    }

    #[test]
    fn rejects_non_finite_and_impossible_values() {
        let mut value = serde_json::to_value(analysis()).unwrap();
        value["derived"]["calibrated"]["samples"][0]["confidence"] = serde_json::json!(1.5);
        assert!(serde_json::from_value::<Analysis>(value).is_err());

        let mut lost_with_measurement = serde_json::to_value(analysis()).unwrap();
        lost_with_measurement["raw_observations"][1]["measurement"] =
            serde_json::to_value(PixelObservation {
                timestamp_s: 1.1,
                x_px: 970.0,
                y_px: 680.0,
                confidence: 0.1,
            })
            .unwrap();
        assert!(serde_json::from_value::<Analysis>(lost_with_measurement).is_err());
    }

    #[test]
    fn rejects_non_finite_config_before_json_export() {
        let mut analysis = analysis();
        analysis
            .provenance
            .tracker
            .implementation
            .parameters
            .insert("bad".to_owned(), ParameterValue::Float(f64::NAN));
        assert!(matches!(
            analysis.to_json_pretty(),
            Err(AnalysisJsonError::Validation(_))
        ));
    }

    #[test]
    fn complete_analysis_retains_filter_kinematics_and_config_provenance() {
        let mut analysis = analysis();
        let filtered_samples = analysis.derived.calibrated.samples.clone();
        analysis.derived.filtered = Some(FilteredTrajectory {
            filter: ImplementationProvenance {
                implementation: "moving-average".to_owned(),
                version: "baseline-1".to_owned(),
                parameters: BTreeMap::from([("window".to_owned(), ParameterValue::Integer(3))]),
            },
            samples: filtered_samples.clone(),
        });
        analysis.derived.kinematics = Some(KinematicTrajectory {
            input: KinematicsInput::Filtered,
            method: ImplementationProvenance {
                implementation: "backward-difference".to_owned(),
                version: "1".to_owned(),
                parameters: BTreeMap::new(),
            },
            samples: vec![
                KinematicSample {
                    timestamp_s: filtered_samples[0].timestamp_s,
                    x_m: filtered_samples[0].x_m,
                    y_m: filtered_samples[0].y_m,
                    vx_mps: None,
                    vy_mps: None,
                    confidence: filtered_samples[0].confidence,
                },
                KinematicSample {
                    timestamp_s: filtered_samples[1].timestamp_s,
                    x_m: filtered_samples[1].x_m,
                    y_m: filtered_samples[1].y_m,
                    vx_mps: Some(0.225),
                    vy_mps: Some(0.45),
                    confidence: filtered_samples[1].confidence,
                },
            ],
        });
        analysis.validate().unwrap();

        let json = analysis.to_json_pretty().unwrap();
        let decoded = Analysis::from_json(&json).unwrap();
        assert_eq!(decoded, analysis);
        assert_eq!(
            decoded
                .derived
                .filtered
                .as_ref()
                .unwrap()
                .filter
                .parameters
                .get("window"),
            Some(&ParameterValue::Integer(3))
        );
    }

    #[test]
    fn rejects_non_finite_derived_values_before_export() {
        let mut analysis = analysis();
        analysis.derived.calibrated.samples[0].x_m = f64::NAN;
        assert!(analysis.validate().is_err());
        assert!(matches!(
            analysis.to_json_pretty(),
            Err(AnalysisJsonError::Validation(_))
        ));
    }

    #[test]
    fn rejects_unsupported_schema_version_and_unknown_fields() {
        let mut wrong_version = serde_json::to_value(analysis()).unwrap();
        wrong_version["schema_version"] = serde_json::json!(2);
        let error = serde_json::from_value::<Analysis>(wrong_version).unwrap_err();
        assert!(error
            .to_string()
            .contains("schema version 2 is unsupported"));

        let mut unknown_field = serde_json::to_value(analysis()).unwrap();
        unknown_field["unexpected"] = serde_json::json!(true);
        assert!(serde_json::from_value::<Analysis>(unknown_field).is_err());
    }

    #[test]
    fn rejects_target_bounds_outside_display_frame() {
        let mut value = serde_json::to_value(analysis()).unwrap();
        value["raw_observations"][0]["target_bounds_px"]["left_px"] = serde_json::json!(-1.0);
        assert!(serde_json::from_value::<Analysis>(value).is_err());
    }

    #[test]
    fn rejects_invalid_video_metadata_and_trim_range() {
        let mut invalid_trim = analysis();
        invalid_trim.video.trim.start_s = 5.0;
        invalid_trim.video.trim.end_s = 2.0;
        let error = invalid_trim.validate().unwrap_err();
        assert_eq!(
            error.message(),
            "video trim range must be non-negative and ordered"
        );

        let mut invalid_dim = analysis();
        invalid_dim.video.display_width_px = 0;
        let error = invalid_dim.validate().unwrap_err();
        assert_eq!(error.message(), "video dimensions must be positive");

        let mut invalid_rotation = analysis();
        invalid_rotation.video.source_rotation_deg = 45;
        let error = invalid_rotation.validate().unwrap_err();
        assert_eq!(
            error.message(),
            "video source rotation must be 0, 90, 180, or 270 degrees"
        );
    }

    #[test]
    fn golden_json_is_stable() {
        let golden = include_str!("../tests/fixtures/analysis-v1.golden.json");
        let expected = analysis();
        let serialized = expected.to_json_pretty().unwrap();
        assert_eq!(serialized, golden.trim_end());

        let decoded = Analysis::from_json(golden).unwrap();
        assert_eq!(decoded, expected);
    }

    #[test]
    fn time_range_contains_checks_inclusive_bounds() {
        let range = TimeRange {
            start_s: 1.0,
            end_s: 5.0,
        };

        assert!(range.contains(1.0));
        assert!(range.contains(3.0));
        assert!(range.contains(5.0));

        assert!(!range.contains(0.999));
        assert!(!range.contains(5.001));
        assert!(!range.contains(f64::NAN));
        assert!(!range.contains(f64::INFINITY));
        assert!(!range.contains(f64::NEG_INFINITY));
    }
}
