//! CLI requests whose sample window can only be resolved after decoding.
use super::{parse_f64, parse_usize};
use crate::cli_error::{CliError, CliResult};
use openbar_core::filtering::{apply_filter, FilterConfig};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Copy, PartialEq)]
pub(super) enum FilterRequest {
    Fixed(FilterConfig),
    MovingAverageDuration {
        window_s: f64,
        max_gap_s: f64,
    },
    SavitzkyGolayDuration {
        window_s: f64,
        polynomial_order: usize,
        max_gap_s: f64,
    },
}

impl FilterRequest {
    pub(super) fn resolve(self, measured_fps: Option<f64>) -> CliResult<FilterConfig> {
        if let Self::Fixed(config) = self {
            return Ok(config);
        }
        let fps = measured_fps.ok_or_else(|| {
            CliError::invalid_input(
                "--filter-window-s needs at least two selected frames with increasing timestamps",
            )
        })?;
        let result = match self {
            Self::MovingAverageDuration {
                window_s,
                max_gap_s,
            } => FilterConfig::moving_average_for_duration(window_s, fps, max_gap_s),
            Self::SavitzkyGolayDuration {
                window_s,
                polynomial_order,
                max_gap_s,
            } => FilterConfig::savitzky_golay_for_duration(
                window_s,
                fps,
                polynomial_order,
                max_gap_s,
            ),
            Self::Fixed(_) => unreachable!(),
        };
        result.map_err(|error| {
            CliError::invalid_input(format!("invalid --filter-window-s configuration: {error}"))
        })
    }

    // Validate non-duration parameters before media I/O, using a minimal valid
    // sample window. No nominal frame rate or floating-point division is needed.
    fn validation_config(self) -> FilterConfig {
        match self {
            Self::Fixed(config) => config,
            Self::MovingAverageDuration {
                window_s,
                max_gap_s,
            } => FilterConfig::MovingAverage {
                window: 1,
                window_s: Some(window_s),
                max_gap_s,
            },
            Self::SavitzkyGolayDuration {
                window_s,
                polynomial_order,
                max_gap_s,
            } => FilterConfig::SavitzkyGolay {
                // Saturate invalid huge orders; core validation rejects them.
                window: polynomial_order
                    .saturating_add(1)
                    .saturating_add(polynomial_order % 2),
                window_s: Some(window_s),
                polynomial_order,
                max_gap_s,
            },
        }
    }
}

fn parse_duration(value: &str) -> CliResult<f64> {
    let duration = parse_f64(value, "--filter-window-s")?;
    if !duration.is_finite() || duration <= 0.0 {
        return Err(CliError::invalid_input(
            "--filter-window-s must be finite and positive",
        ));
    }
    Ok(duration)
}

pub(super) fn parse_filter_config(
    filter_name: &str,
    values: &mut BTreeMap<String, String>,
) -> CliResult<FilterRequest> {
    let window = take_raw(values, "--filter-window");
    let window_s = take_raw(values, "--filter-window-s");
    let order = take_raw(values, "--filter-polynomial-order");
    let max_gap = take_raw(values, "--filter-max-gap-s");
    let acceleration = take_raw(values, "--filter-acceleration-variance-m2-s4");
    let measurement = take_raw(values, "--filter-measurement-variance-m2");
    let initial_velocity = take_raw(values, "--filter-initial-velocity-variance-m2-s2");
    let confidence_window = take_raw(values, "--filter-confidence-window-samples");

    let config = match filter_name {
        "raw" => {
            reject_present_filter_options(&[
                ("--filter-window", &window),
                ("--filter-window-s", &window_s),
                ("--filter-polynomial-order", &order),
                ("--filter-max-gap-s", &max_gap),
                ("--filter-acceleration-variance-m2-s4", &acceleration),
                ("--filter-measurement-variance-m2", &measurement),
                (
                    "--filter-initial-velocity-variance-m2-s2",
                    &initial_velocity,
                ),
                ("--filter-confidence-window-samples", &confidence_window),
            ])?;
            FilterRequest::Fixed(FilterConfig::Raw)
        }
        "moving-average" => {
            reject_present_filter_options(&[
                ("--filter-polynomial-order", &order),
                ("--filter-acceleration-variance-m2-s4", &acceleration),
                ("--filter-measurement-variance-m2", &measurement),
                (
                    "--filter-initial-velocity-variance-m2-s2",
                    &initial_velocity,
                ),
                ("--filter-confidence-window-samples", &confidence_window),
            ])?;
            let max_gap_s = parse_f64(
                &required_raw(max_gap, "--filter-max-gap-s")?,
                "--filter-max-gap-s",
            )?;
            match (window, window_s) {
                (Some(value), None) => FilterRequest::Fixed(FilterConfig::MovingAverage {
                    window: parse_usize(&value, "--filter-window")?,
                    window_s: None,
                    max_gap_s,
                }),
                (None, Some(value)) => FilterRequest::MovingAverageDuration {
                    window_s: parse_duration(&value)?,
                    max_gap_s,
                },
                _ => return Err(CliError::invalid_input(
                    "selected filter requires exactly one of --filter-window and --filter-window-s",
                )),
            }
        }
        "savitzky-golay" => {
            reject_present_filter_options(&[
                ("--filter-acceleration-variance-m2-s4", &acceleration),
                ("--filter-measurement-variance-m2", &measurement),
                (
                    "--filter-initial-velocity-variance-m2-s2",
                    &initial_velocity,
                ),
                ("--filter-confidence-window-samples", &confidence_window),
            ])?;
            let max_gap_s = parse_f64(
                &required_raw(max_gap, "--filter-max-gap-s")?,
                "--filter-max-gap-s",
            )?;
            let polynomial_order = parse_usize(
                &required_raw(order, "--filter-polynomial-order")?,
                "--filter-polynomial-order",
            )?;
            match (window, window_s) {
                (Some(value), None) => FilterRequest::Fixed(FilterConfig::SavitzkyGolay {
                    window: parse_usize(&value, "--filter-window")?,
                    window_s: None,
                    max_gap_s,
                    polynomial_order,
                }),
                (None, Some(value)) => FilterRequest::SavitzkyGolayDuration {
                    window_s: parse_duration(&value)?,
                    max_gap_s,
                    polynomial_order,
                },
                _ => return Err(CliError::invalid_input(
                    "selected filter requires exactly one of --filter-window and --filter-window-s",
                )),
            }
        }
        "kalman" => {
            reject_present_filter_options(&[
                ("--filter-window", &window),
                ("--filter-window-s", &window_s),
                ("--filter-polynomial-order", &order),
            ])?;
            FilterRequest::Fixed(FilterConfig::Kalman {
                acceleration_variance_m2_s4: parse_f64(
                    &required_raw(acceleration, "--filter-acceleration-variance-m2-s4")?,
                    "--filter-acceleration-variance-m2-s4",
                )?,
                measurement_variance_m2: parse_f64(
                    &required_raw(measurement, "--filter-measurement-variance-m2")?,
                    "--filter-measurement-variance-m2",
                )?,
                initial_velocity_variance_m2_s2: parse_f64(
                    &required_raw(initial_velocity, "--filter-initial-velocity-variance-m2-s2")?,
                    "--filter-initial-velocity-variance-m2-s2",
                )?,
                confidence_window_samples: parse_usize(
                    &required_raw(confidence_window, "--filter-confidence-window-samples")?,
                    "--filter-confidence-window-samples",
                )?,
                max_gap_s: parse_f64(
                    &required_raw(max_gap, "--filter-max-gap-s")?,
                    "--filter-max-gap-s",
                )?,
            })
        }
        value => {
            return Err(CliError::invalid_input(format!(
                "--filter must be raw, moving-average, savitzky-golay, or kalman, got '{value}'"
            )));
        }
    };
    apply_filter(&[], config.validation_config())
        .map_err(|error| CliError::invalid_input(format!("invalid filter config: {error}")))?;
    Ok(config)
}

fn reject_present_filter_options(options: &[(&str, &Option<String>)]) -> CliResult<()> {
    if let Some((flag, _)) = options.iter().find(|(_, value)| value.is_some()) {
        return Err(CliError::invalid_input(format!(
            "{flag} is not valid for the selected filter"
        )));
    }
    Ok(())
}

fn take_raw(values: &mut BTreeMap<String, String>, flag: &str) -> Option<String> {
    values.remove(flag)
}

fn required_raw(value: Option<String>, flag: &str) -> CliResult<String> {
    value.ok_or_else(|| CliError::invalid_input(format!("selected filter requires {flag}")))
}
