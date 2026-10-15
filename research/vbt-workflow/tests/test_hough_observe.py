"""Observation invariants; synthetic pixels only, no private-media access."""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
HAVE_CV = all(importlib.util.find_spec(name) for name in ("cv2", "numpy"))
if HAVE_CV:
    import cv2
    import numpy as np
    import vbt_suggest


@unittest.skipUnless(HAVE_CV, "requires existing research OpenCV/NumPy environment")
class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("hough_observe"), "observation harness missing")
        self.observe = importlib.import_module("hough_observe").observe_plate
        self.image = np.full((240, 320, 3), 220, dtype=np.uint8)
        cv2.circle(self.image, (130, 120), 45, (15, 15, 15), -1)

    def test_actual_hough_preserves_result_pixels_and_determinism(self):
        original = self.image.copy()
        expected = vbt_suggest.suggest_plate(self.image)
        observed = self.observe(self.image)
        self.assertEqual(expected, observed["final"])
        np.testing.assert_array_equal(original, self.image)
        self.assertEqual(observed, self.observe(self.image))
        json.dumps(observed, allow_nan=False)
        self.assertEqual(observed["parameters"], expected["parameters"])
        self.assertIsNotNone(observed["selected"])
        _, features = vbt_suggest._plate_features(self.image, vbt_suggest.PlateParams())
        selected = observed["selected"]
        refinement = vbt_suggest._refine(features, (
            selected["center_x_px"], selected["center_y_px"], selected["radius_px"]),
            vbt_suggest.PlateParams())
        self.assertEqual(tuple(observed["refinement"].values()), refinement)

    def test_all_raw_outputs_truncation_fit_ties_and_raw_indices(self):
        circles = np.array([[[0, 0, 20]] + [[100, 100, 20]] * 59 + [[130, 120, 45]]], np.float32)
        with mock.patch.object(cv2, "HoughCircles", return_value=circles):
            observed = self.observe(self.image)
            expected = vbt_suggest.suggest_plate(self.image)
        self.assertEqual(observed["final"], expected)
        self.assertEqual(observed["raw_count"], 61)
        self.assertEqual(observed["post_first_60_count"], 60)
        self.assertEqual(observed["post_fit_count"], 59)
        self.assertFalse(observed["raw"][0]["fits"])
        self.assertTrue(observed["raw"][60]["fits"])
        self.assertFalse(observed["raw"][60]["retained_first_60"])
        self.assertEqual([row["raw_index"] for row in observed["ranked"]], list(range(1, 60)))
        self.assertEqual(observed["support_margin"], 0.0)
        self.assertEqual(observed["selected"], observed["ranked"][0])
        self.assertIsNotNone(observed["refinement"])

    def test_ranked_scores_and_geometry_are_exact_existing_scores(self):
        circles = np.array([[[210, 120, 30], [130, 120, 45], [100, 100, 20]]], np.float32)
        with mock.patch.object(cv2, "HoughCircles", return_value=circles):
            observed = self.observe(self.image)
        params = vbt_suggest.PlateParams()
        _, features = vbt_suggest._plate_features(self.image, params)
        expected = []
        for i, (x, y, radius) in enumerate(circles[0]):
            x, y, radius = float(x), float(y), float(radius)
            score = vbt_suggest.edge_support(features, x, y, radius, params.rank_tolerance_px, params.min_alignment)
            expected.append((score, x, y, radius, i))
        expected.sort(key=lambda row: (-row[0], *row[1:4]))
        actual = [(r["support"], r["center_x_px"], r["center_y_px"], r["radius_px"], r["raw_index"])
                  for r in observed["ranked"]]
        self.assertEqual(actual, expected)
        self.assertEqual([r["rank"] for r in observed["ranked"]], [1, 2, 3])
        self.assertEqual(observed["support_margin"], expected[0][0] - expected[1][0])

    def test_scaling_uses_display_pixel_centres(self):
        image = np.full((1000, 1600, 3), 220, dtype=np.uint8)
        with mock.patch.object(cv2, "HoughCircles", return_value=np.array([[[240.25, 300.5, 40.5]]], np.float32)):
            observed = self.observe(image)
        raw = observed["raw"][0]
        self.assertEqual(observed["scale"], 0.6)
        self.assertEqual(raw["hough_geometry"], [240.25, 300.5, 40.5])
        self.assertEqual(raw["center_x_px"], (240.25 + 0.5) / 0.6 - 0.5)
        self.assertEqual(raw["center_y_px"], (300.5 + 0.5) / 0.6 - 0.5)
        self.assertEqual(raw["radius_px"], 40.5 / 0.6)
        self.assertIsNone(observed["support_margin"])

    def test_no_candidates_preserves_failure_and_nulls(self):
        with mock.patch.object(cv2, "HoughCircles", return_value=None):
            observed = self.observe(self.image)
            expected = vbt_suggest.suggest_plate(self.image)
        self.assertEqual(observed["final"], expected)
        self.assertEqual(observed["raw"], [])
        self.assertEqual(observed["ranked"], [])
        self.assertIsNone(observed["selected"])
        self.assertIsNone(observed["refinement"])
        self.assertIsNone(observed["support_margin"])

    def test_all_out_of_bounds_preserves_failure(self):
        with mock.patch.object(cv2, "HoughCircles", return_value=np.array([[[0, 0, 20]]], np.float32)):
            observed = self.observe(self.image)
        self.assertEqual(observed["post_fit_count"], 0)
        self.assertIsNone(observed["refinement"])
        self.assertEqual(observed["final"]["status"], "failed")

    def test_final_refusal_retains_refinement_without_fake_selection(self):
        image = np.full_like(self.image, 220)
        with mock.patch.object(cv2, "HoughCircles", return_value=np.array([[[100, 100, 20]]], np.float32)):
            observed = self.observe(image)
        self.assertEqual(observed["refinement"]["support"], 0)
        self.assertEqual(observed["final"]["status"], "failed")
        self.assertIsNotNone(observed["selected"])

    def test_exception_restores_profiler_and_algorithm(self):
        original = vbt_suggest.suggest_plate
        with mock.patch.object(cv2, "HoughCircles", side_effect=RuntimeError("decode-free synthetic error")):
            with self.assertRaisesRegex(RuntimeError, "synthetic error"):
                self.observe(self.image)
        self.assertIsNone(sys.getprofile())
        self.assertIs(original, vbt_suggest.suggest_plate)

    def test_existing_profiler_is_refused_and_untouched(self):
        def profiler(frame, event, value):
            return None
        previous = sys.getprofile()
        sys.setprofile(profiler)
        try:
            with self.assertRaisesRegex(RuntimeError, "profiler"):
                self.observe(self.image)
            self.assertIs(sys.getprofile(), profiler)
        finally:
            sys.setprofile(previous)

    def test_invalid_pixels_fail_before_algorithm(self):
        for image in (np.zeros((0, 2, 3), np.uint8), np.zeros((2, 2), np.uint8),
                      np.zeros((2, 2, 3), np.float64), None):
            with self.subTest(image=type(image)):
                with self.assertRaises(ValueError):
                    self.observe(image)


if __name__ == "__main__":
    unittest.main()
