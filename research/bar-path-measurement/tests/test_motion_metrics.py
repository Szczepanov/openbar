from __future__ import annotations

import copy
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import motion_metrics as metrics
import study_io as io


def labels(count=5):
    return [{"timestamp_s": i / 10, "frame_index": i, "annotation_state": "labelled",
             "center_px": {"x_px": 50 + i, "y_px": 100 - i}} for i in range(count)]


def observations(reference, dx=0, dy=0):
    return [{"timestamp_s": r["timestamp_s"], "state": "tracked", "confidence": .7,
             "center_px": {"x_px": r["center_px"]["x_px"] + dx,
                           "y_px": r["center_px"]["y_px"] + dy}} for r in reference]


class MotionMetricsTests(unittest.TestCase):
    def test_constant_bias_leaves_displacement_unchanged_and_raw_preserved(self):
        ref = labels()
        pred = observations(ref, 3, 4)
        before = copy.deepcopy(pred)
        result = metrics.evaluate(ref, pred)
        self.assertEqual(result["center_error_px"]["mae"], 5)
        self.assertEqual(result["delta_position_error_px"]["mae"], 0)
        self.assertEqual(result["debiased_center_error_px"]["mae"], 0)
        self.assertEqual(result["constant_offset_px"], {"x_px": 3, "y_px": 4})
        self.assertEqual(pred, before)

    def test_jitter_doubles_alternating_displacement_error(self):
        ref = labels()
        pred = observations(ref)
        for i, row in enumerate(pred):
            row["center_px"]["x_px"] += (-1) ** i
        result = metrics.evaluate(ref, pred)
        self.assertEqual(result["delta_axis_error_px"]["x_px"]["mae"], 2)
        self.assertAlmostEqual(result["intervals"][0]["delta_error_px"]["x_px"] / .1, -20)

    def test_interior_loss_breaks_sparse_intervals(self):
        ref = labels()
        pred = observations(ref)
        pred[1] = {"timestamp_s": .1, "state": "lost"}
        result = metrics.evaluate(ref[::2], pred)
        self.assertEqual(result["available_intervals"], 1)
        self.assertEqual(result["unavailable_intervals"], [{"start_s": 0., "end_s": .2, "reason": "loss_in_interval"}])
        self.assertEqual(result["intervals"][0]["frame_interval"], 2)

    def test_unlabelled_observation_breaks_instead_of_bridging(self):
        ref = labels()
        pred = observations(ref)
        ref[2] = {"timestamp_s": .2, "annotation_state": "unlabelable"}
        result = metrics.evaluate(ref, pred)
        self.assertEqual(result["available_intervals"], 2)
        self.assertEqual(len(result["unavailable_intervals"]), 2)

    def test_exact_matching_never_nearest_frame(self):
        ref = labels()
        pred = observations(ref)
        pred[1]["timestamp_s"] += .000001
        result = metrics.evaluate(ref, pred)
        self.assertEqual(result["matched_tracked_samples"], 4)
        self.assertEqual(result["available_intervals"], 2)

    def test_sampling_gap_and_empty_metrics(self):
        ref = labels(2)
        ref[1]["timestamp_s"] = 1
        result = metrics.evaluate(ref, observations(ref))
        self.assertEqual(result["unavailable_intervals"][0]["reason"], "observation_gap")
        empty = metrics.evaluate([], observations(ref))
        self.assertIsNone(empty["center_error_px"]["mae"])
        self.assertIsNone(empty["availability"])

    def test_seed_exclusion(self):
        ref = labels()
        result = metrics.evaluate(ref, observations(ref), seed_timestamp_s=0)
        self.assertEqual(result["matched_tracked_samples"], 4)
        self.assertEqual(result["available_intervals"], 3)

    def test_sparse_labels_cannot_claim_stationary_jitter(self):
        ref = labels()
        pred = observations(ref)
        result = metrics.evaluate(ref[::2], pred, stationary_windows=[[0, .4]])
        self.assertEqual(result["stationary_jitter"][0]["unsupported_reason"], "dense_labels_required")

    def test_sparse_predictions_do_not_prove_dense_jitter(self):
        ref = labels(3)
        for index, row in enumerate(ref):
            row.update(frame_index=index * 10, center_px={"x_px": 50., "y_px": 100.})
        result = metrics.evaluate(ref, observations(ref), stationary_windows=[[0., .2]])
        self.assertEqual(result["stationary_jitter"][0]["unsupported_reason"], "dense_labels_required")
        for row in ref:
            del row["frame_index"]
        result = metrics.evaluate(ref, observations(ref), stationary_windows=[[0., .2]])
        self.assertEqual(result["stationary_jitter"][0]["unsupported_reason"], "decoder_frame_coverage_required")

    def test_stationary_dense_jitter_and_moving_rejection(self):
        ref = labels()
        for row in ref:
            row["center_px"] = {"x_px": 50, "y_px": 100}
        pred = observations(ref)
        for i, row in enumerate(pred):
            row["center_px"]["x_px"] += (-1) ** i
        result = metrics.evaluate(ref, pred, stationary_windows=[[0, .4]])
        self.assertAlmostEqual(result["stationary_jitter"][0]["error_sd_px"]["x_px"], math.sqrt(.96))
        result = metrics.evaluate(labels(), observations(labels()), stationary_windows=[[0, .4]])
        self.assertEqual(result["stationary_jitter"][0]["unsupported_reason"], "labels_not_stationary")

    def test_paired_comparison_uses_common_support(self):
        ref = labels()
        baseline = observations(ref, 3, 0)
        candidate = observations(ref, 2, 0)
        candidate[1] = {"timestamp_s": .1, "state": "lost"}
        result = metrics.paired(metrics.evaluate(ref, candidate), metrics.evaluate(ref, baseline))
        self.assertEqual(result["center"]["common_count"], 4)
        self.assertEqual(result["center"]["candidate_minus_baseline_px"]["bias"], -1)
        self.assertEqual(result["delta"]["common_count"], 2)

    def test_invalid_numbers_loss_and_times_fail_closed(self):
        ref = labels()
        for mutate in (
            lambda p: p[0].update(confidence=1.1),
            lambda p: p[0].update(confidence=True),
            lambda p: p[0]["center_px"].update(x_px=math.nan),
            lambda p: p[0].update(state="lost"),
            lambda p: p[1].update(timestamp_s=0),
        ):
            with self.subTest(mutate=mutate):
                pred = observations(ref)
                mutate(pred)
                with self.assertRaises(ValueError):
                    metrics.evaluate(ref, pred)
        with self.assertRaises(ValueError):
            metrics.evaluate(ref, observations(ref), max_gap_s=0)

    def test_json_is_stable_strict_and_lf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            io.write(path, {"z": 1, "a": 2})
            first = path.read_bytes()
            io.write(path, {"a": 2, "z": 1})
            self.assertEqual(first, path.read_bytes())
            self.assertNotIn(b"\r", first)
            for text in ('{"x": NaN}', '{"x": 1, "x": 2}', '{"x": 1e999}'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    io.load(path)

    def test_relative_drift_accumulates_without_absolute_anchors(self):
        ref = labels()
        pred = observations(ref)
        edges = [{"previous_timestamp_s": ref[i - 1]["timestamp_s"], "timestamp_s": ref[i]["timestamp_s"],
                  "delta_px": {"x_px": 1.25, "y_px": -1}, "confidence": .8} for i in range(1, len(ref))]
        result = metrics.relative_diagnostics(ref, pred, edges)
        self.assertEqual(result["delta_axis_error_px"]["x_px"]["mae"], .25)
        self.assertEqual(result["cumulative_drift_px"]["maximum"], 1.)
        edges[1].update(delta_px=None, confidence=None)
        result = metrics.relative_diagnostics(ref[::2], pred, edges)
        self.assertEqual(result["available_intervals"], 1)
        self.assertEqual(result["unavailable_intervals"], 1)
        self.assertEqual(result["cumulative_drift_px"]["maximum"], .5)

    def test_relative_missing_wrong_endpoints_and_nonfinite_fail_closed(self):
        ref = labels()
        pred = observations(ref)
        for edge in ({"previous_timestamp_s": 0., "timestamp_s": .2, "delta_px": None},
                     {"previous_timestamp_s": 0., "timestamp_s": .1, "delta_px": None, "confidence": .2},
                     {"previous_timestamp_s": 0., "timestamp_s": .1, "delta_px": {"x_px": math.inf, "y_px": 0}, "confidence": .2}):
            with self.assertRaises(ValueError):
                metrics.relative_diagnostics(ref, pred, [edge])


if __name__ == "__main__":
    unittest.main()
