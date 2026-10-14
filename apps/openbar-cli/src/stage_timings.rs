//! Opt-in stage-timing sidecar for `analyze` (issue #179).
//!
//! Timings are non-deterministic environment evidence. They are never part of the canonical
//! `Analysis` or recording-support JSON and use only the monotonic `Instant` clock.

use crate::cli_error::{CliError, CliResult};
use serde::Serialize;
use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::time::{Duration, Instant};

pub const SCHEMA_VERSION: u32 = 1;
pub const TIMINGS_VERSION: &str = "analyze-stage-timings-v1";
pub const COMMAND: &str = "analyze";

/// Accumulates monotonic per-stage durations. Stage durations are kept as `Duration`, so their
/// exact sum never exceeds the total measured from the same `Instant` origin.
#[derive(Debug)]
pub struct StageRecorder {
    start: Instant,
    stages: BTreeMap<&'static str, Duration>,
    total: Option<Duration>,
}

impl StageRecorder {
    pub fn start() -> Self {
        Self {
            start: Instant::now(),
            stages: BTreeMap::new(),
            total: None,
        }
    }

    /// Runs `work`, adding its elapsed time to `stage` (repeated calls accumulate).
    pub fn time<T>(&mut self, stage: &'static str, work: impl FnOnce() -> T) -> T {
        let began = Instant::now();
        let value = work();
        self.add(stage, began.elapsed());
        value
    }

    /// Adds an already-measured, non-overlapping duration to `stage`.
    pub fn add(&mut self, stage: &'static str, elapsed: Duration) {
        *self.stages.entry(stage).or_default() += elapsed;
    }

    /// Freezes the total at the end of timings-eligible work.
    pub fn finish(&mut self) {
        self.total = Some(self.start.elapsed());
    }

    pub fn report(&self) -> CliResult<StageTimingsReport> {
        let total = self
            .total
            .ok_or_else(|| CliError::internal("stage timings were reported before finish()"))?;
        StageTimingsReport::try_new(
            self.stages
                .iter()
                .map(|(stage, elapsed)| ((*stage).to_owned(), elapsed.as_secs_f64()))
                .collect(),
            total.as_secs_f64(),
        )
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct StageTimingsReport {
    schema_version: u32,
    timings_version: &'static str,
    command: &'static str,
    stages_s: BTreeMap<String, f64>,
    total_s: f64,
}

impl StageTimingsReport {
    pub fn try_new(stages_s: BTreeMap<String, f64>, total_s: f64) -> CliResult<Self> {
        validate_seconds("total_s", total_s)?;
        for (stage, seconds) in &stages_s {
            validate_seconds(&format!("stages_s.{stage}"), *seconds)?;
        }
        Ok(Self {
            schema_version: SCHEMA_VERSION,
            timings_version: TIMINGS_VERSION,
            command: COMMAND,
            stages_s,
            total_s,
        })
    }

    pub fn to_json_pretty(&self) -> CliResult<String> {
        serde_json::to_string_pretty(self).map_err(|error| {
            CliError::output(format!("failed to serialize stage timings: {error}"))
        })
    }
}

fn validate_seconds(name: &str, value: f64) -> CliResult<()> {
    if value.is_finite() && value >= 0.0 {
        Ok(())
    } else {
        Err(CliError::internal(format!(
            "stage timing {name} must be finite and non-negative, got {value}"
        )))
    }
}

/// Writes the sidecar with the same create/overwrite policy as the other analyze outputs.
pub fn write_timings(path: &Path, report: &StageTimingsReport, force: bool) -> CliResult<()> {
    let serialized = report.to_json_pretty()?;
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).map_err(|error| {
                CliError::output(format!(
                    "failed to create timings output directory '{}': {error}",
                    parent.display()
                ))
            })?;
        }
    }
    let mut options = OpenOptions::new();
    options.write(true);
    if force {
        options.create(true).truncate(true);
    } else {
        options.create_new(true);
    }
    let mut file = options.open(path).map_err(|error| {
        let hint = if !force && error.kind() == std::io::ErrorKind::AlreadyExists {
            " (use --force to replace it)"
        } else {
            ""
        };
        CliError::output(format!(
            "failed to open timings output '{}': {error}{hint}",
            path.display()
        ))
    })?;
    file.write_all(serialized.as_bytes())
        .and_then(|()| file.write_all(b"\n"))
        .map_err(|error| {
            CliError::output(format!(
                "failed to write timings output '{}': {error}",
                path.display()
            ))
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stages(pairs: &[(&str, f64)]) -> BTreeMap<String, f64> {
        pairs
            .iter()
            .map(|(name, value)| ((*name).to_owned(), *value))
            .collect()
    }

    #[test]
    fn serializes_exact_pretty_json_with_sorted_stages() {
        let report =
            StageTimingsReport::try_new(stages(&[("tracking", 0.5), ("decode", 0.25)]), 1.0)
                .unwrap();
        let expected = "{\n  \"schema_version\": 1,\n  \"timings_version\": \"analyze-stage-timings-v1\",\n  \"command\": \"analyze\",\n  \"stages_s\": {\n    \"decode\": 0.25,\n    \"tracking\": 0.5\n  },\n  \"total_s\": 1.0\n}";
        assert_eq!(report.to_json_pretty().unwrap(), expected);
    }

    #[test]
    fn rejects_non_finite_and_negative_values() {
        for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, -0.001] {
            assert!(StageTimingsReport::try_new(stages(&[("decode", bad)]), 1.0).is_err());
            assert!(StageTimingsReport::try_new(stages(&[("decode", 0.1)]), bad).is_err());
        }
    }

    #[test]
    fn recorder_accumulates_and_keeps_sum_within_total() {
        let mut recorder = StageRecorder::start();
        assert_eq!(recorder.time("a", || 7), 7);
        recorder.time("a", || ());
        recorder.time("b", || ());
        assert!(recorder.report().is_err(), "report requires finish()");
        recorder.finish();
        let report = recorder.report().unwrap();
        assert_eq!(
            report
                .stages_s
                .keys()
                .map(String::as_str)
                .collect::<Vec<_>>(),
            ["a", "b"]
        );
        assert!(report.stages_s.values().sum::<f64>() <= report.total_s + 1e-12);
    }

    #[test]
    fn write_refuses_overwrite_without_force() {
        let path = std::env::temp_dir().join(format!(
            "openbar-stage-timings-{}-write.json",
            std::process::id()
        ));
        let _ = fs::remove_file(&path);
        let report = StageTimingsReport::try_new(stages(&[("decode", 0.1)]), 0.2).unwrap();
        write_timings(&path, &report, false).unwrap();
        let text = fs::read_to_string(&path).unwrap();
        assert!(text.ends_with("}\n"));
        let error = write_timings(&path, &report, false).unwrap_err();
        assert!(error.to_string().contains("--force"));
        write_timings(&path, &report, true).unwrap();
        let _ = fs::remove_file(path);
    }
}
