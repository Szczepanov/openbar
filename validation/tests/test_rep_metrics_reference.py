"""Consistency checks for the #180 rep/phase metrics reference cases.

The reference file pins *expected values*. Segmentation boundaries come from the pinned consumer
(`research/vbt-workflow/rep_reference_cases_consumer.mjs check`); this module checks everything that
follows from a boundary:

- the OpenBar core definitions in docs/validation/KINEMATIC_METRICS.md (backward-difference@1,
  explicit-interval mean/peak/ROM, continuity and confidence rules), evaluated over the rep's metric
  interval: the sample before the consumer window's first frame through its last frame;
- the consumer's reporting arithmetic (unweighted sample mean, endpoint ROM in cm, legacy rounding);
- the rep-level availability policy in docs/validation/REP_PHASE_METRICS.md.

The functions below are a specification oracle for the reference file. They are not a measurement
pipeline and must not be imported by product code; the Rust implementation in `openbar-core` stays
authoritative (ADR-0001, ADR-0012).
"""
from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "validation" / "reference-cases" / "rep-metrics-v1.json"

WITHHELD_REASON_ORDER = ("unsupported_segment", "spans_gap", "touches_series_edge")


def derive_velocity(samples: list[dict], config: dict) -> list[float | None]:
    """backward-difference@1 vertical velocity, aligned to samples (core derive_velocity)."""
    velocities: list[float | None] = [None]
    for previous, current in zip(samples, samples[1:]):
        dt = current["timestamp_s"] - previous["timestamp_s"]
        supported = (
            dt <= config["max_gap_s"]
            and previous["confidence"] >= config["min_confidence"]
            and current["confidence"] >= config["min_confidence"]
        )
        velocities.append(
            (current["y_m"] - previous["y_m"]) / dt if supported else None
        )
    return velocities


def series_supported(samples: list[dict], start: int, end: int, config: dict) -> bool:
    """Core series_supported: confidence of every sample and every gap in [start, end]."""
    window = samples[start : end + 1]
    if any(sample["confidence"] < config["min_confidence"] for sample in window):
        return False
    return all(
        later["timestamp_s"] - earlier["timestamp_s"] <= config["max_gap_s"]
        for earlier, later in zip(window, window[1:])
    )


def core_interval_metrics(
    samples: list[dict], config: dict, start: int, end: int
) -> dict | None:
    """Core mean/peak/ROM over [t[start], t[end]]; None where core returns unavailable."""
    if not series_supported(samples, start, end, config):
        return None
    window = samples[start : end + 1]
    duration_s = window[-1]["timestamp_s"] - window[0]["timestamp_s"]
    rise_m = window[-1]["y_m"] - window[0]["y_m"]
    # Derivation is restricted to the interval: the segment ending at the start is excluded.
    velocities = derive_velocity(window, config)[1:]
    peak = None
    for sample, velocity in zip(window[1:], velocities):
        if velocity is None:
            return None
        if peak is None or velocity > peak[0]:
            peak = (velocity, sample["timestamp_s"])
    ys = [sample["y_m"] for sample in window]
    return {
        "duration_s": duration_s,
        "rom_m": max(ys) - min(ys),
        "mean_velocity_mps": rise_m / duration_s,
        "peak_velocity_mps": peak[0],
        "peak_timestamp_s": peak[1],
        "confidence": min(sample["confidence"] for sample in window),
    }


def js_round(value: float, digits: int) -> float:
    """ECMAScript Math.round(value * 10**digits) / 10**digits (ties toward +infinity)."""
    scale = 10**digits
    return math.floor(value * scale + 0.5) / scale


def consumer_window_values(
    samples: list[dict], config: dict, start: int, end: int
) -> dict:
    """The pinned consumer's reporting arithmetic over usable frames start..end (sample indices)."""
    velocities = derive_velocity(samples, config)
    usable = [velocities[index] for index in range(start, end + 1) if velocities[index] is not None]
    mean = sum(usable) / len(usable)
    return {
        "mean_velocity_mps": mean,
        "peak_velocity_mps": max(usable),
        "rom_cm": (samples[end]["y_m"] - samples[start]["y_m"]) * 100,
    }


def withheld_reasons(core: dict | None, exclusion: str | None) -> list[str]:
    reasons = set()
    if core is None:
        reasons.add("unsupported_segment")
    if exclusion is not None:
        reasons.add(exclusion)
    return [reason for reason in WITHHELD_REASON_ORDER if reason in reasons]


class RepMetricsReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        cls.config = cls.document["kinematics"]

    def test_case_ids_are_unique_and_inputs_are_valid_samples(self) -> None:
        ids = [case["id"] for case in self.document["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        for case in self.document["cases"]:
            samples = case["samples"]
            self.assertGreaterEqual(len(samples), 2, case["id"])
            for earlier, later in zip(samples, samples[1:]):
                self.assertLess(earlier["timestamp_s"], later["timestamp_s"], case["id"])
            for sample in samples:
                self.assertTrue(math.isfinite(sample["y_m"]), case["id"])
                self.assertTrue(0.0 <= sample["confidence"] <= 1.0, case["id"])

    def test_every_consumer_rep_has_exactly_one_policy_entry(self) -> None:
        for case in self.document["cases"]:
            expected = case["expected"]
            self.assertEqual(
                [rep["rep_index"] for rep in expected["reps"]],
                [rep["index"] for rep in expected["consumer_reps"]],
                case["id"],
            )

    def test_boundaries_are_sampled_timestamps_in_range(self) -> None:
        for case in self.document["cases"]:
            samples = case["samples"]
            for rep, consumer in zip(case["expected"]["reps"], case["expected"]["consumer_reps"]):
                start, end = rep["start_sample_index"], rep["end_sample_index"]
                self.assertTrue(0 <= start < end < len(samples), case["id"])
                self.assertEqual(consumer["start_frame"], start + 1, case["id"])
                self.assertEqual(consumer["end_frame"], end + 1, case["id"])
                self.assertEqual(rep["start_timestamp_s"], samples[start]["timestamp_s"], case["id"])
                self.assertEqual(rep["end_timestamp_s"], samples[end]["timestamp_s"], case["id"])
                # The metric interval begins at the turnaround sample, one before the first upward frame.
                self.assertEqual(rep["interval_start_sample_index"], start - 1, case["id"])
                self.assertEqual(
                    rep["interval_start_timestamp_s"], samples[start - 1]["timestamp_s"], case["id"]
                )

    def test_core_values_follow_the_kinematic_metric_definitions(self) -> None:
        tolerance = self.document["tolerances"]["core_abs"]
        for case in self.document["cases"]:
            for rep in case["expected"]["reps"]:
                expected = core_interval_metrics(
                    case["samples"], self.config, rep["interval_start_sample_index"], rep["end_sample_index"]
                )
                if expected is None:
                    self.assertIsNone(rep["core"], case["id"])
                    continue
                self.assertIsNotNone(rep["core"], case["id"])
                for key, value in expected.items():
                    self.assertAlmostEqual(rep["core"][key], value, delta=tolerance, msg=f"{case['id']} {key}")

    def test_consumer_values_follow_the_consumer_arithmetic(self) -> None:
        for case in self.document["cases"]:
            for rep, consumer in zip(case["expected"]["reps"], case["expected"]["consumer_reps"]):
                raw = consumer_window_values(
                    case["samples"], self.config, rep["start_sample_index"], rep["end_sample_index"]
                )
                self.assertEqual(rep["consumer_unrounded"], raw, case["id"])
                self.assertEqual(consumer["mean_velocity_mps"], js_round(raw["mean_velocity_mps"], 3), case["id"])
                self.assertEqual(consumer["peak_velocity_mps"], js_round(raw["peak_velocity_mps"], 3), case["id"])
                self.assertEqual(consumer["rom_cm"], js_round(raw["rom_cm"], 2), case["id"])
                duration = case["samples"][rep["end_sample_index"]]["timestamp_s"] - case["samples"][
                    rep["start_sample_index"]
                ]["timestamp_s"]
                self.assertEqual(consumer["duration_s"], js_round(duration, 3), case["id"])

    def test_policy_fields_follow_the_availability_rules(self) -> None:
        for case in self.document["cases"]:
            for rep, consumer in zip(case["expected"]["reps"], case["expected"]["consumer_reps"]):
                reasons = withheld_reasons(rep["core"], consumer["exclusion"])
                self.assertEqual(rep["withheld_reasons"], reasons, case["id"])
                self.assertEqual(rep["completeness"], "complete" if consumer["complete"] else "incomplete", case["id"])

    def test_core_and_consumer_agree_on_peak_and_differ_only_where_documented(self) -> None:
        tolerance = self.document["tolerances"]["core_abs"]
        for case in self.document["cases"]:
            samples = case["samples"]
            intervals = [
                samples[rep["interval_start_sample_index"] + 1 : rep["end_sample_index"] + 1]
                for rep in case["expected"]["reps"]
            ]
            for rep, segment_samples in zip(case["expected"]["reps"], intervals):
                if rep["core"] is None:
                    self.assertIsNone(rep["mean_velocity_difference_mps"], case["id"])
                    continue
                consumer = rep["consumer_unrounded"]
                # Same set of segments, so the peak is identical by construction.
                self.assertAlmostEqual(
                    rep["core"]["peak_velocity_mps"], consumer["peak_velocity_mps"], delta=tolerance, msg=case["id"]
                )
                difference = rep["core"]["mean_velocity_mps"] - consumer["mean_velocity_mps"]
                self.assertAlmostEqual(rep["mean_velocity_difference_mps"], difference, delta=1e-12, msg=case["id"])
                steps = {
                    round(later["timestamp_s"] - earlier["timestamp_s"], 12)
                    for earlier, later in zip(
                        samples[rep["interval_start_sample_index"] : rep["end_sample_index"] + 1],
                        segment_samples,
                    )
                }
                if len(steps) == 1:
                    # Uniform sampling: time-weighted and unweighted means are the same quantity.
                    self.assertAlmostEqual(difference, 0.0, delta=tolerance, msg=case["id"])
                # The consumer's ROM omits the first rising segment that its mean includes.
                first_segment_rise_cm = (
                    samples[rep["start_sample_index"]]["y_m"] - samples[rep["interval_start_sample_index"]]["y_m"]
                ) * 100
                self.assertAlmostEqual(
                    rep["core"]["rom_m"] * 100 - consumer["rom_cm"], first_segment_rise_cm, delta=1e-9, msg=case["id"]
                )

    def test_unsupported_phase_cases_state_why(self) -> None:
        unsupported = [case for case in self.document["cases"] if case["expected"]["phase_model"] != "concentric_only"]
        self.assertTrue(unsupported, "the clean-and-jerk shape must be pinned as unsupported")
        for case in unsupported:
            self.assertEqual(case["expected"]["phase_model"], "lift_specific_required", case["id"])
            self.assertTrue(case["expected"]["phase_model_reason"], case["id"])


if __name__ == "__main__":
    unittest.main()
