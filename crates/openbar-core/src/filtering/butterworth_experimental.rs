use serde::{Deserialize, Serialize};

use crate::analysis::{Configuration, ImplementationProvenance, ParameterValue};
use crate::trajectory::MetricPositionSample;

use super::{
    segment_ranges, validate_gap, validate_input, FilterBehavior, FilterError, FilterRun,
    FilteredTrajectory,
};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TimestampRegularity {
    pub sample_count: usize,
    pub interval_count: usize,
    pub mean_dt_s: f64,
    pub median_dt_s: f64,
    pub min_dt_s: f64,
    pub max_dt_s: f64,
    pub max_abs_dev_from_median_s: f64,
    pub max_rel_dev_from_median: f64,
    pub std_dev_dt_s: f64,
    pub coefficient_of_variation: f64,
}

pub fn assess_timestamp_regularity(timestamps: &[f64]) -> Result<TimestampRegularity, FilterError> {
    if timestamps.len() < 2 {
        return Err(FilterError::InvalidSample {
            index: 0,
            reason: "at least two timestamps are required to assess regularity".to_owned(),
        });
    }

    let mut deltas = Vec::with_capacity(timestamps.len() - 1);
    for i in 0..timestamps.len() {
        if !timestamps[i].is_finite() {
            return Err(FilterError::InvalidSample {
                index: i,
                reason: "timestamp is non-finite".to_owned(),
            });
        }
        if i > 0 {
            if timestamps[i] <= timestamps[i - 1] {
                return Err(FilterError::NonIncreasingTimestamp {
                    previous_index: i - 1,
                    index: i,
                });
            }
            deltas.push(timestamps[i] - timestamps[i - 1]);
        }
    }

    let interval_count = deltas.len();
    let mean_dt_s = deltas.iter().sum::<f64>() / interval_count as f64;

    let mut sorted_deltas = deltas.clone();
    sorted_deltas.sort_by(|a, b| a.total_cmp(b));
    let min_dt_s = sorted_deltas[0];
    let max_dt_s = sorted_deltas[interval_count - 1];
    let median_dt_s = if interval_count % 2 == 1 {
        sorted_deltas[interval_count / 2]
    } else {
        (sorted_deltas[interval_count / 2 - 1] + sorted_deltas[interval_count / 2]) / 2.0
    };

    let max_abs_dev_from_median_s = deltas
        .iter()
        .map(|&dt| (dt - median_dt_s).abs())
        .fold(0.0, f64::max);
    let max_rel_dev_from_median = if median_dt_s > 0.0 {
        max_abs_dev_from_median_s / median_dt_s
    } else {
        0.0
    };

    let variance = deltas
        .iter()
        .map(|&dt| (dt - mean_dt_s).powi(2))
        .sum::<f64>()
        / interval_count as f64;
    let std_dev_dt_s = variance.sqrt();
    let coefficient_of_variation = if mean_dt_s > 0.0 {
        std_dev_dt_s / mean_dt_s
    } else {
        0.0
    };

    Ok(TimestampRegularity {
        sample_count: timestamps.len(),
        interval_count,
        mean_dt_s,
        median_dt_s,
        min_dt_s,
        max_dt_s,
        max_abs_dev_from_median_s,
        max_rel_dev_from_median,
        std_dev_dt_s,
        coefficient_of_variation,
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct ButterworthExperimentalConfig {
    pub design_order: usize,
    pub cutoff_hz: f64,
    pub cutoff_correction: bool,
    pub max_gap_s: f64,
    pub max_relative_jitter: f64,
    pub max_coefficient_of_variation: f64,
}

impl ButterworthExperimentalConfig {
    pub fn default_research_4th_effective(cutoff_hz: f64) -> Self {
        Self {
            design_order: 2,
            cutoff_hz,
            cutoff_correction: true,
            max_gap_s: 0.05,
            max_relative_jitter: 0.01,
            max_coefficient_of_variation: 0.005,
        }
    }

    pub fn candidate_provenance(&self) -> ImplementationProvenance {
        let mut parameters = Configuration::new();
        parameters.insert(
            "design_order".to_owned(),
            ParameterValue::Integer(self.design_order as i64),
        );
        parameters.insert(
            "effective_order".to_owned(),
            ParameterValue::Integer((self.design_order * 2) as i64),
        );
        parameters.insert(
            "cutoff_hz".to_owned(),
            ParameterValue::Float(self.cutoff_hz),
        );
        parameters.insert(
            "cutoff_convention".to_owned(),
            ParameterValue::Text(if self.cutoff_correction {
                "winter_double_pass_corrected".to_owned()
            } else {
                "uncorrected_single_pass".to_owned()
            }),
        );
        parameters.insert(
            "max_gap_s".to_owned(),
            ParameterValue::Float(self.max_gap_s),
        );
        parameters.insert(
            "max_relative_jitter".to_owned(),
            ParameterValue::Float(self.max_relative_jitter),
        );
        parameters.insert(
            "max_coefficient_of_variation".to_owned(),
            ParameterValue::Float(self.max_coefficient_of_variation),
        );

        ImplementationProvenance {
            implementation: "zero-phase-butterworth-research".to_owned(),
            version: "1".to_owned(),
            parameters,
        }
    }

    pub fn provenance(&self, derived_sampling_interval_s: f64) -> ImplementationProvenance {
        let mut prov = self.candidate_provenance();
        prov.parameters.insert(
            "derived_sampling_interval_s".to_owned(),
            ParameterValue::Float(derived_sampling_interval_s),
        );
        prov
    }

    pub fn behavior(&self) -> FilterBehavior {
        FilterBehavior {
            causal: false,
            confidence_behavior:
                "output confidence is the minimum confidence across contributing segment samples",
            irregular_timestamp_behavior:
                "rejected if relative deviation > max_relative_jitter or CV > max_cv; derived sampling interval used",
            gap_behavior:
                "windows never cross a timestamp gap larger than max_gap_s; segments are filtered independently",
            edge_behavior:
                "reflected endpoint padding; segments shorter than order+1 pass through unchanged",
            latency_behavior:
                "zero phase delay via forward-backward passes",
        }
    }
}

struct BiquadSection {
    b0: f64,
    b1: f64,
    b2: f64,
    a1: f64,
    a2: f64,
}

impl BiquadSection {
    fn new(w: f64, q: f64) -> Self {
        let w2 = w * w;
        let d = 1.0 + (w / q) + w2;
        let b0 = w2 / d;
        let b1 = 2.0 * b0;
        let b2 = b0;
        let a1 = 2.0 * (w2 - 1.0) / d;
        let a2 = (1.0 - (w / q) + w2) / d;
        Self { b0, b1, b2, a1, a2 }
    }

    fn filter_forward(&self, input: &[f64], output: &mut [f64]) {
        if input.is_empty() {
            return;
        }
        let mut x1 = input[0];
        let mut x2 = input[0];
        let mut y1 = input[0];
        let mut y2 = input[0];
        for (i, &x0) in input.iter().enumerate() {
            let y0 = self.b0 * x0 + self.b1 * x1 + self.b2 * x2 - self.a1 * y1 - self.a2 * y2;
            x2 = x1;
            x1 = x0;
            y2 = y1;
            y1 = y0;
            output[i] = y0;
        }
    }
}

fn filtfilt_butterworth(
    signal: &[f64],
    sections: &[BiquadSection],
    design_order: usize,
) -> Vec<f64> {
    let len = signal.len();
    if len < design_order + 1 {
        return signal.to_vec();
    }

    let n_pad = (3 * design_order).min(len - 1);
    let mut padded = Vec::with_capacity(len + 2 * n_pad);
    for k in (1..=n_pad).rev() {
        padded.push(2.0 * signal[0] - signal[k]);
    }
    padded.extend_from_slice(signal);
    for k in 1..=n_pad {
        padded.push(2.0 * signal[len - 1] - signal[len - 1 - k]);
    }

    let mut buf_a = padded;
    let mut buf_b = vec![0.0; buf_a.len()];

    for section in sections {
        section.filter_forward(&buf_a, &mut buf_b);
        std::mem::swap(&mut buf_a, &mut buf_b);
    }

    buf_a.reverse();

    for section in sections {
        section.filter_forward(&buf_a, &mut buf_b);
        std::mem::swap(&mut buf_a, &mut buf_b);
    }

    buf_a.reverse();

    buf_a[n_pad..n_pad + len].to_vec()
}

pub fn apply_butterworth_filter(
    samples: &[MetricPositionSample],
    config: &ButterworthExperimentalConfig,
) -> Result<FilterRun, FilterError> {
    validate_input(samples)?;

    if config.design_order != 2 && config.design_order != 4 {
        return Err(FilterError::UnsupportedButterworthOrder {
            order: config.design_order,
        });
    }
    if !config.cutoff_hz.is_finite() || config.cutoff_hz <= 0.0 {
        return Err(FilterError::InvalidCutoffFrequency {
            value: config.cutoff_hz,
            reason: "cutoff frequency must be finite and positive",
        });
    }
    validate_gap(config.max_gap_s)?;

    if samples.is_empty() {
        return Ok(FilterRun {
            trajectory: FilteredTrajectory {
                filter: config.provenance(0.0),
                samples: Vec::new(),
            },
            behavior: config.behavior(),
            segment_count: 0,
        });
    }

    let ranges = segment_ranges(samples, config.max_gap_s);
    let mut filtered_samples = Vec::with_capacity(samples.len());
    let mut overall_sampling_interval_s = 0.0;

    for &(start, end) in &ranges {
        let seg = &samples[start..end];
        if seg.len() < 2 {
            filtered_samples.extend_from_slice(seg);
            continue;
        }

        let timestamps: Vec<f64> = seg.iter().map(|s| s.timestamp_s).collect();
        let regularity = assess_timestamp_regularity(&timestamps)?;

        if regularity.max_dt_s >= 1.5 * regularity.median_dt_s
            || regularity.min_dt_s <= 0.5 * regularity.median_dt_s
        {
            return Err(FilterError::UnsupportedTimestampJitter {
                max_rel_dev: regularity.max_rel_dev_from_median,
                max_allowed: config.max_relative_jitter,
            });
        }
        if regularity.max_rel_dev_from_median > config.max_relative_jitter {
            return Err(FilterError::UnsupportedTimestampJitter {
                max_rel_dev: regularity.max_rel_dev_from_median,
                max_allowed: config.max_relative_jitter,
            });
        }
        if regularity.coefficient_of_variation > config.max_coefficient_of_variation {
            return Err(FilterError::UnsupportedTimestampJitter {
                max_rel_dev: regularity.coefficient_of_variation,
                max_allowed: config.max_coefficient_of_variation,
            });
        }

        let dt_s = regularity.median_dt_s;
        overall_sampling_interval_s = dt_s;
        let fs = 1.0 / dt_s;
        let f_nyquist = 0.5 * fs;

        if config.cutoff_hz >= f_nyquist {
            return Err(FilterError::InvalidCutoffFrequency {
                value: config.cutoff_hz,
                reason: "cutoff frequency must be strictly below Nyquist",
            });
        }

        // Winter correction for m = 2 passes (forward and backward):
        // C = (2^(1/m) - 1)^(1 / (2 * n)) = (2^(0.5) - 1)^(1 / (2 * design_order))
        let c_factor = if config.cutoff_correction {
            let exponent = 1.0 / (2.0 * config.design_order as f64);
            (2.0f64.powf(0.5) - 1.0).powf(exponent)
        } else {
            1.0
        };

        let f_design = config.cutoff_hz / c_factor;
        if f_design >= f_nyquist {
            return Err(FilterError::InvalidCutoffFrequency {
                value: config.cutoff_hz,
                reason: "cutoff frequency with pass correction reaches or exceeds Nyquist",
            });
        }

        let w = (std::f64::consts::PI * f_design / fs).tan();
        let sections = if config.design_order == 2 {
            vec![BiquadSection::new(w, std::f64::consts::FRAC_1_SQRT_2)]
        } else {
            let q1 = 1.0 / (2.0 * (std::f64::consts::PI / 8.0).cos());
            let q2 = 1.0 / (2.0 * (3.0 * std::f64::consts::PI / 8.0).cos());
            vec![BiquadSection::new(w, q1), BiquadSection::new(w, q2)]
        };

        let seg_min_conf = seg.iter().map(|s| s.confidence).fold(1.0f32, f32::min);

        let x_vals: Vec<f64> = seg.iter().map(|s| s.x_m).collect();
        let y_vals: Vec<f64> = seg.iter().map(|s| s.y_m).collect();

        let filtered_x = filtfilt_butterworth(&x_vals, &sections, config.design_order);
        let filtered_y = filtfilt_butterworth(&y_vals, &sections, config.design_order);

        for i in 0..seg.len() {
            filtered_samples.push(MetricPositionSample {
                timestamp_s: seg[i].timestamp_s,
                x_m: filtered_x[i],
                y_m: filtered_y[i],
                confidence: seg_min_conf.min(seg[i].confidence),
            });
        }
    }

    Ok(FilterRun {
        trajectory: FilteredTrajectory {
            filter: config.provenance(overall_sampling_interval_s),
            samples: filtered_samples,
        },
        behavior: config.behavior(),
        segment_count: ranges.len(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_utils::sample_metric_position;

    fn sample(timestamp_s: f64, x_m: f64, y_m: f64) -> MetricPositionSample {
        sample_metric_position(timestamp_s, x_m, y_m, 1.0)
    }

    #[test]
    fn timestamp_regularity_strictly_evaluates_distribution_and_jitter() {
        let timestamps = (0..61).map(|i| i as f64 / 60.0).collect::<Vec<_>>();
        let result = assess_timestamp_regularity(&timestamps).unwrap();
        assert_eq!(result.sample_count, 61);
        assert_eq!(result.interval_count, 60);
        assert!((result.median_dt_s - 1.0 / 60.0).abs() < 1.0e-12);
        assert!((result.mean_dt_s - 1.0 / 60.0).abs() < 1.0e-12);
        assert!(result.max_rel_dev_from_median < 1.0e-12);
        assert!(result.coefficient_of_variation < 1.0e-12);

        assert!(matches!(
            assess_timestamp_regularity(&[0.0]),
            Err(FilterError::InvalidSample { .. })
        ));

        assert!(matches!(
            assess_timestamp_regularity(&[0.0, 0.05, 0.04]),
            Err(FilterError::NonIncreasingTimestamp { .. })
        ));

        assert!(matches!(
            assess_timestamp_regularity(&[0.0, f64::NAN]),
            Err(FilterError::InvalidSample { .. })
        ));

        let mut low_jitter = Vec::new();
        let mut t = 0.0;
        for i in 0..100 {
            low_jitter.push(t);
            let dt = if i % 2 == 0 { 0.0166 } else { 0.0167 };
            t += dt;
        }
        let lj_res = assess_timestamp_regularity(&low_jitter).unwrap();
        assert!(lj_res.max_rel_dev_from_median < 0.01);
        assert!(lj_res.coefficient_of_variation < 0.005);

        let vfr = vec![0.0, 0.011, 0.032, 0.046, 0.065, 0.076];
        let vfr_res = assess_timestamp_regularity(&vfr).unwrap();
        assert!(vfr_res.max_rel_dev_from_median > 0.15);
    }

    #[test]
    fn butterworth_rejects_invalid_cutoff_and_orders() {
        let valid = (0..20)
            .map(|i| sample(i as f64 / 60.0, 1.0, 2.0))
            .collect::<Vec<_>>();
        let mut config = ButterworthExperimentalConfig::default_research_4th_effective(8.0);

        config.design_order = 3;
        assert!(matches!(
            apply_butterworth_filter(&valid, &config),
            Err(FilterError::UnsupportedButterworthOrder { .. })
        ));
        config.design_order = 2;

        config.cutoff_hz = 0.0;
        assert!(matches!(
            apply_butterworth_filter(&valid, &config),
            Err(FilterError::InvalidCutoffFrequency { .. })
        ));

        config.cutoff_hz = -5.0;
        assert!(matches!(
            apply_butterworth_filter(&valid, &config),
            Err(FilterError::InvalidCutoffFrequency { .. })
        ));

        config.cutoff_hz = 30.0;
        assert!(matches!(
            apply_butterworth_filter(&valid, &config),
            Err(FilterError::InvalidCutoffFrequency { .. })
        ));

        config.cutoff_hz = 26.0;
        assert!(matches!(
            apply_butterworth_filter(&valid, &config),
            Err(FilterError::InvalidCutoffFrequency { .. })
        ));
    }

    #[test]
    fn butterworth_rejects_irregular_timestamps_and_dropped_frames() {
        let vfr_samples = [0.0, 0.011, 0.032, 0.046, 0.065, 0.076]
            .into_iter()
            .map(|t| sample(t, 1.0, 2.0))
            .collect::<Vec<_>>();

        let config = ButterworthExperimentalConfig::default_research_4th_effective(8.0);
        assert!(matches!(
            apply_butterworth_filter(&vfr_samples, &config),
            Err(FilterError::UnsupportedTimestampJitter { .. })
        ));

        let mut dropped_frame_samples = (0..10)
            .map(|i| sample(i as f64 / 60.0, 1.0, 2.0))
            .collect::<Vec<_>>();
        dropped_frame_samples.push(sample(10.0 / 60.0 + 2.0 / 60.0, 1.0, 2.0));
        assert!(matches!(
            apply_butterworth_filter(&dropped_frame_samples, &config),
            Err(FilterError::UnsupportedTimestampJitter { .. })
        ));
    }

    #[test]
    fn butterworth_preserves_constant_input_with_zero_drift() {
        for fs in [30.0, 60.0, 120.0, 240.0] {
            let input = (0..50)
                .map(|i| sample(i as f64 / fs, 2.75, -1.82))
                .collect::<Vec<_>>();

            for order in [2, 4] {
                let mut config = ButterworthExperimentalConfig::default_research_4th_effective(6.0);
                config.design_order = order;

                let run = apply_butterworth_filter(&input, &config).unwrap();
                assert_eq!(run.trajectory.samples.len(), input.len());
                for (actual, expected) in run.trajectory.samples.iter().zip(&input) {
                    assert_eq!(actual.timestamp_s, expected.timestamp_s);
                    assert!((actual.x_m - expected.x_m).abs() < 1.0e-10);
                    assert!((actual.y_m - expected.y_m).abs() < 1.0e-10);
                }
            }
        }
    }

    #[test]
    fn butterworth_zero_phase_has_no_peak_timing_shift() {
        let fs = 60.0;
        let count = 121;
        let peak_index = 60;
        let peak_time = peak_index as f64 / fs;

        let input = (0..count)
            .map(|i| {
                let t = i as f64 / fs;
                let dt = (t - peak_time) / 0.1;
                let y = (-dt * dt).exp();
                sample(t, 0.0, y)
            })
            .collect::<Vec<_>>();

        let config = ButterworthExperimentalConfig::default_research_4th_effective(8.0);
        let run = apply_butterworth_filter(&input, &config).unwrap();

        let mut max_filtered_y = -f64::INFINITY;
        let mut max_filtered_time = 0.0;
        for s in &run.trajectory.samples {
            if s.y_m > max_filtered_y {
                max_filtered_y = s.y_m;
                max_filtered_time = s.timestamp_s;
            }
        }

        assert_eq!(max_filtered_time, peak_time);
        assert!(max_filtered_y > 0.9);
    }

    #[test]
    fn butterworth_handles_empty_short_segments_and_gaps() {
        let config = ButterworthExperimentalConfig::default_research_4th_effective(8.0);

        let empty_run = apply_butterworth_filter(&[], &config).unwrap();
        assert!(empty_run.trajectory.samples.is_empty());
        assert_eq!(empty_run.segment_count, 0);

        let single = [sample(0.0, 1.0, 2.0)];
        let single_run = apply_butterworth_filter(&single, &config).unwrap();
        assert_eq!(single_run.trajectory.samples, single);

        let two = [sample(0.0, 1.0, 2.0), sample(1.0 / 60.0, 1.05, 2.05)];
        let two_run = apply_butterworth_filter(&two, &config).unwrap();
        assert_eq!(two_run.trajectory.samples, two);

        let mut gapped = (0..15)
            .map(|i| sample(i as f64 / 60.0, 1.0, 2.0))
            .collect::<Vec<_>>();
        for i in 0..15 {
            gapped.push(sample(1.0 + i as f64 / 60.0, 3.0, 4.0));
        }

        let run = apply_butterworth_filter(&gapped, &config).unwrap();
        assert_eq!(run.segment_count, 2);
        assert_eq!(run.trajectory.samples.len(), 30);
        assert_eq!(run.trajectory.samples[15].timestamp_s, 1.0);
    }

    #[test]
    fn butterworth_is_deterministic_and_records_provenance() {
        let input = (0..30)
            .map(|i| sample(i as f64 / 60.0, (i as f64).sin(), (i as f64).cos()))
            .collect::<Vec<_>>();
        let config = ButterworthExperimentalConfig::default_research_4th_effective(8.0);

        let run1 = apply_butterworth_filter(&input, &config).unwrap();
        let run2 = apply_butterworth_filter(&input, &config).unwrap();
        assert_eq!(run1, run2);

        let prov = &run1.trajectory.filter;
        assert_eq!(prov.implementation, "zero-phase-butterworth-research");
        assert_eq!(prov.version, "1");
        assert_eq!(
            prov.parameters.get("effective_order").unwrap(),
            &ParameterValue::Integer(4)
        );
        assert_eq!(
            prov.parameters.get("cutoff_convention").unwrap(),
            &ParameterValue::Text("winter_double_pass_corrected".to_owned())
        );
    }

    #[test]
    fn butterworth_frequency_response_gain_at_cutoff() {
        // Sample a pure sine wave at f = f_c with sampling rate f_s >> f_c.
        // For a zero-phase forward-backward filter:
        // - with Winter cutoff correction: overall gain at f_c must be -3 dB (1 / sqrt(2) ≈ 0.7071)
        // - without correction: overall gain at f_c is (-3 dB)^2 = -6 dB (0.5000)
        let fs = 1000.0;
        let fc = 10.0;
        let count = 2000; // 20 full cycles
        let input = (0..count)
            .map(|i| {
                let t = i as f64 / fs;
                let y = (2.0 * std::f64::consts::PI * fc * t).sin();
                sample(t, 0.0, y)
            })
            .collect::<Vec<_>>();

        // Test design order 2 (effective 4th-order) with Winter correction:
        let config_order2_corrected = ButterworthExperimentalConfig {
            design_order: 2,
            cutoff_hz: fc,
            cutoff_correction: true,
            max_gap_s: 0.05,
            max_relative_jitter: 0.01,
            max_coefficient_of_variation: 0.005,
        };
        let run2_corr = apply_butterworth_filter(&input, &config_order2_corrected).unwrap();
        // Measure steady-state amplitude in central cycles (indices 500..1500)
        let steady_amp_2_corr = run2_corr.trajectory.samples[500..1500]
            .iter()
            .map(|s| s.y_m.abs())
            .fold(0.0f64, f64::max);
        let expected_3db = std::f64::consts::FRAC_1_SQRT_2; // ≈ 0.70710678
        assert!(
            (steady_amp_2_corr - expected_3db).abs() < 0.015,
            "order 2 corrected steady amplitude {steady_amp_2_corr} expected ~{expected_3db} (-3 dB)"
        );

        // Test design order 4 (effective 8th-order) with Winter correction:
        let config_order4_corrected = ButterworthExperimentalConfig {
            design_order: 4,
            cutoff_hz: fc,
            cutoff_correction: true,
            max_gap_s: 0.05,
            max_relative_jitter: 0.01,
            max_coefficient_of_variation: 0.005,
        };
        let run4_corr = apply_butterworth_filter(&input, &config_order4_corrected).unwrap();
        let steady_amp_4_corr = run4_corr.trajectory.samples[500..1500]
            .iter()
            .map(|s| s.y_m.abs())
            .fold(0.0f64, f64::max);
        assert!(
            (steady_amp_4_corr - expected_3db).abs() < 0.015,
            "order 4 corrected steady amplitude {steady_amp_4_corr} expected ~{expected_3db} (-3 dB)"
        );

        // Test uncorrected single-pass design (dual pass yields -6 dB = 0.5000):
        let config_order2_uncorrected = ButterworthExperimentalConfig {
            design_order: 2,
            cutoff_hz: fc,
            cutoff_correction: false,
            max_gap_s: 0.05,
            max_relative_jitter: 0.01,
            max_coefficient_of_variation: 0.005,
        };
        let run2_uncorr = apply_butterworth_filter(&input, &config_order2_uncorrected).unwrap();
        let steady_amp_2_uncorr = run2_uncorr.trajectory.samples[500..1500]
            .iter()
            .map(|s| s.y_m.abs())
            .fold(0.0f64, f64::max);
        let expected_6db = 0.5000;
        assert!(
            (steady_amp_2_uncorr - expected_6db).abs() < 0.015,
            "order 2 uncorrected steady amplitude {steady_amp_2_uncorr} expected ~{expected_6db} (-6 dB)"
        );
    }
}
