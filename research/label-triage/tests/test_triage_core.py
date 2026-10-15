"""Synthetic-only tests for voter comparison, triage selection and calibration."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import triage_core as core  # noqa: E402


def tracked(stamp, x, y=100.0):
    return {"timestamp_s": stamp, "state": "tracked", "center_px": {"x_px": x, "y_px": y}, "confidence": 0.9}


def lost(stamp):
    return {"timestamp_s": stamp, "state": "lost"}


def document(samples):
    return {"schema_version": 1, "samples": samples}


def voters(**series):
    return {name: core.load_prediction_centres(document(samples)) for name, samples in series.items()}


class LoadTests(unittest.TestCase):
    def test_lost_sample_maps_to_none_and_tracked_to_a_point(self):
        loaded = core.load_prediction_centres(document([tracked(0.0, 5.0, 6.0), lost(1.0)]))
        self.assertEqual(loaded, {0.0: (5.0, 6.0), 1.0: None})

    def test_rejects_non_finite_coordinates(self):
        for bad in (math.nan, math.inf, -math.inf):
            with self.assertRaises(core.TriageError):
                core.load_prediction_centres(document([tracked(0.0, bad)]))

    def test_rejects_a_lost_sample_that_carries_a_coordinate(self):
        sample = lost(0.0) | {"center_px": {"x_px": 1.0, "y_px": 2.0}}
        with self.assertRaises(core.TriageError):
            core.load_prediction_centres(document([sample]))

    def test_rejects_non_increasing_timestamps_and_other_versions(self):
        with self.assertRaises(core.TriageError):
            core.load_prediction_centres(document([tracked(1.0, 1.0), tracked(1.0, 2.0)]))
        with self.assertRaises(core.TriageError):
            core.load_prediction_centres({"schema_version": 2, "samples": []})

    def test_labels_keep_only_labelled_samples(self):
        doc = {"schema_version": 1, "samples": [
            {"annotation_state": "labelled", "timestamp_s": 0.5, "center_px": {"x_px": 1.0, "y_px": 2.0}},
            {"annotation_state": "unlabelable", "timestamp_s": 1.0}]}
        self.assertEqual(core.load_label_centres(doc), {0.5: (1.0, 2.0)})


class CompareTests(unittest.TestCase):
    def test_three_agreeing_voters_are_not_flagged(self):
        v = voters(a=[tracked(0.0, 100.0)], b=[tracked(0.0, 101.0)], c=[tracked(0.0, 100.5)])
        (row,) = core.compare_voters(v, [0.0])
        self.assertEqual(row["flags"], [])
        self.assertEqual(row["n_valid"], 3)
        self.assertAlmostEqual(row["spread_px"], 1.0)
        self.assertEqual(row["median_px"], (100.5, 100.0))

    def test_spread_above_limit_is_flagged(self):
        v = voters(a=[tracked(0.0, 100.0)], b=[tracked(0.0, 110.0)], c=[tracked(0.0, 100.0)])
        (row,) = core.compare_voters(v, [0.0])
        self.assertEqual(row["flags"], ["spread"])

    def test_spread_exactly_at_limit_is_not_flagged(self):
        v = voters(a=[tracked(0.0, 100.0)], b=[tracked(0.0, 103.0)], c=[tracked(0.0, 101.0)])
        (row,) = core.compare_voters(v, [0.0])
        self.assertEqual(row["flags"], [])

    def test_a_lost_voter_is_flagged_and_gets_no_coordinate(self):
        v = voters(a=[tracked(0.0, 100.0)], b=[lost(0.0)], c=[tracked(0.0, 101.0)])
        (row,) = core.compare_voters(v, [0.0])
        self.assertEqual(row["flags"], ["lost:b"])
        self.assertIsNone(row["positions"]["b"])
        self.assertEqual(row["n_valid"], 2)

    def test_a_missing_sample_is_distinct_from_lost(self):
        v = voters(a=[tracked(0.0, 100.0)], b=[tracked(5.0, 100.0)], c=[tracked(0.0, 100.0)])
        (row,) = core.compare_voters(v, [0.0])
        self.assertEqual(row["flags"], ["missing:b"])

    def test_no_median_with_fewer_than_two_coordinates(self):
        v = voters(a=[tracked(0.0, 1.0)], b=[lost(0.0)], c=[lost(0.0)])
        (row,) = core.compare_voters(v, [0.0])
        self.assertIsNone(row["median_px"])
        self.assertIsNone(row["spread_px"])

    def test_timestamps_match_within_tolerance_only(self):
        v = voters(a=[tracked(0.0004, 1.0)], b=[tracked(0.0, 1.0)], c=[tracked(0.002, 1.0)])
        (row,) = core.compare_voters(v, [0.0])
        self.assertEqual(row["flags"], ["missing:c"])

    def test_needs_three_voters(self):
        with self.assertRaises(core.TriageError):
            core.compare_voters(voters(a=[tracked(0.0, 1.0)], b=[tracked(0.0, 1.0)]), [0.0])


class SelectionTests(unittest.TestCase):
    def rows(self, count, flagged=()):
        return [{"flags": ["spread"] if i in flagged else []} for i in range(count)]

    def test_always_includes_seed_frame_and_flagged_frames(self):
        picks = core.select_frames([float(i) for i in range(20)], self.rows(20, {7, 13}), "f", 0.1, 1)
        indices = {p["index"] for p in picks}
        self.assertIn(1, indices)
        self.assertTrue({8, 14} <= indices)
        self.assertIn("seed", picks[0]["reasons"])

    def test_same_seed_is_identical_and_other_seed_differs(self):
        stamps = [float(i) for i in range(200)]
        one = core.select_frames(stamps, self.rows(200), "f", 0.2, 7)
        again = core.select_frames(stamps, self.rows(200), "f", 0.2, 7)
        other = core.select_frames(stamps, self.rows(200), "f", 0.2, 8)
        self.assertEqual(one, again)
        self.assertNotEqual([p["index"] for p in one], [p["index"] for p in other])

    def test_random_sample_size_and_it_is_drawn_from_unflagged_frames_too(self):
        stamps = [float(i) for i in range(100)]
        picks = core.select_frames(stamps, self.rows(100), "f", 0.1, 3)
        self.assertEqual(sum("random" in p["reasons"] for p in picks), 10)
        self.assertGreaterEqual(len(picks), 10)

    def test_different_fixtures_draw_different_samples(self):
        stamps = [float(i) for i in range(200)]
        a = core.select_frames(stamps, self.rows(200), "a", 0.2, 1)
        b = core.select_frames(stamps, self.rows(200), "b", 0.2, 1)
        self.assertNotEqual([p["index"] for p in a], [p["index"] for p in b])

    def test_rejects_bad_fraction_and_length_mismatch(self):
        for fraction in (0.0, 1.5, -1.0):
            with self.assertRaises(core.TriageError):
                core.select_frames([0.0], self.rows(1), "f", fraction, 1)
        with self.assertRaises(core.TriageError):
            core.select_frames([0.0, 1.0], self.rows(1), "f", 0.5, 1)

    def test_empty_clip_selects_nothing(self):
        self.assertEqual(core.select_frames([], [], "f", 0.5, 1), [])


class WilsonTests(unittest.TestCase):
    def test_known_values(self):
        low, high = core.wilson_interval(30, 30)
        self.assertAlmostEqual(low, 0.8864, places=3)
        self.assertEqual(high, 1.0)
        low, high = core.wilson_interval(50, 100)
        self.assertAlmostEqual(low, 0.4038, places=3)
        self.assertAlmostEqual(high, 0.5962, places=3)

    def test_empty_denominator_is_not_estimable(self):
        self.assertIsNone(core.wilson_interval(0, 0))


class CalibrateTests(unittest.TestCase):
    def test_counts_agreeing_and_not_agreeing_frames_against_labels(self):
        v = voters(a=[tracked(0.0, 100.0), tracked(1.0, 200.0), tracked(2.0, 300.0), tracked(3.0, 400.0)],
                   b=[tracked(0.0, 101.0), tracked(1.0, 201.0), tracked(2.0, 301.0), lost(3.0)],
                   c=[tracked(0.0, 100.0), tracked(1.0, 200.0), tracked(2.0, 330.0), tracked(3.0, 400.0)])
        rows = core.compare_voters(v, [0.0, 1.0, 2.0, 3.0])
        labels = {0.0: (100.5, 100.0), 1.0: (210.0, 100.0), 2.0: (300.0, 100.0), 3.0: (400.0, 100.0)}
        result = core.calibrate(rows, labels)
        self.assertEqual(result["n_labelled_rows"], 4)
        self.assertEqual(result["agreeing"], {"n": 2, "median_within_gate": 1, "wilson95": result["agreeing"]["wilson95"]})
        self.assertEqual(result["not_agreeing"]["n"], 2)
        self.assertEqual(result["per_voter"]["b"]["n_lost_or_missing"], 1)

    def test_rows_without_a_label_do_not_count(self):
        v = voters(a=[tracked(0.0, 1.0)], b=[tracked(0.0, 1.0)], c=[tracked(0.0, 1.0)])
        rows = core.compare_voters(v, [0.0])
        result = core.calibrate(rows, {5.0: (1.0, 100.0)})
        self.assertEqual(result["n_labelled_rows"], 0)
        self.assertIsNone(result["agreeing"]["wilson95"])


if __name__ == "__main__":
    unittest.main()
