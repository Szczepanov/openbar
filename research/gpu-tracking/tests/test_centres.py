#!/usr/bin/env python3
"""Tests for GPU tracking centres and coordinate conversion pure functions (#57 Phase 3 bake-off).

Evaluates §6 tests:
  6. centres: sub-pixel disk (centroid and circle < 0.1 px), bar across 30% (circle < 0.5 px, record centroid bias), empty/oversized masks lost.
  7. seed-disk rasterisation follows pixel-centre rule (symmetric about half-integer centre).
  8. coordinate conversion round-trip within 1e-9 px.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np

GPU_TRACKING_DIR = Path(__file__).resolve().parents[1]
if str(GPU_TRACKING_DIR) not in sys.path:
    sys.path.insert(0, str(GPU_TRACKING_DIR))

import centres  # noqa: E402


class TestCentres(unittest.TestCase):
    def test_06_centres_accuracy_and_occlusion(self) -> None:
        """6. centres: clean disk error < 0.1 px; 30% bar occluder circle error < 0.5 px; empty/oversized lost."""
        w, h = 600, 600
        cx, cy, r = 300.25, 300.75, 80.0

        # Sub-pixel disk
        mask = centres.rasterize_seed_disk(cx, cy, r, w, h)
        seed_area = int(np.count_nonzero(mask))

        # Centroid on clean disk
        centroid = centres.extract_mask_centroid(mask, w, h)
        self.assertIsNotNone(centroid)
        err_centroid = math.hypot(centroid[0] - cx, centroid[1] - cy)
        self.assertLess(err_centroid, 0.1, f"Clean centroid error {err_centroid:.4f} >= 0.1 px")

        # Circle fit on clean disk
        res_circle = centres.fit_circle_from_mask(mask, r, 0, w, h)
        self.assertTrue(res_circle.accepted)
        self.assertIsNotNone(res_circle.center_px)
        err_circle = math.hypot(res_circle.center_px[0] - cx, res_circle.center_px[1] - cy)
        self.assertLess(err_circle, 0.1, f"Clean circle fit error {err_circle:.4f} >= 0.1 px")

        # Disk with bar across 30 % of the disk (e.g. occluding col in [cx - 0.3*r, cx])
        occluded_mask = mask.copy()
        col_indices = np.arange(w)
        bar_cols = (col_indices >= cx - 0.3 * r) & (col_indices <= cx)
        occluded_mask[:, bar_cols] = 0

        # Centroid has bias
        occluded_centroid = centres.extract_mask_centroid(occluded_mask, w, h)
        self.assertIsNotNone(occluded_centroid)
        bias_centroid = math.hypot(occluded_centroid[0] - cx, occluded_centroid[1] - cy)
        print(f"\n[Test 6 Measurement] Centroid bias with 30% bar occluder: {bias_centroid:.4f} px "
              f"(dx={occluded_centroid[0]-cx:.4f}, dy={occluded_centroid[1]-cy:.4f})")
        self.assertGreater(bias_centroid, 0.5, "Expected noticeable centroid bias due to bar occluder")

        # Circle fit should recover the un-occluded circular arc within 0.5 px
        res_occ_circle = centres.fit_circle_from_mask(occluded_mask, r, 0, w, h)
        self.assertTrue(res_occ_circle.accepted, f"Occluded circle fit rejected: {res_occ_circle.reject_reasons}")
        self.assertIsNotNone(res_occ_circle.center_px)
        err_occ_circle = math.hypot(res_occ_circle.center_px[0] - cx, res_occ_circle.center_px[1] - cy)
        print(f"[Test 6 Measurement] Circle fit error with 30% bar occluder: {err_occ_circle:.4f} px")
        self.assertLess(err_occ_circle, 0.5, f"Occluded circle fit error {err_occ_circle:.4f} >= 0.5 px")

        # Lost semantics: empty mask or area outside [0.5, 1.5]
        empty_mask = np.zeros((h, w), dtype=np.uint8)
        self.assertFalse(centres.validate_mask(empty_mask, seed_area))

        oversized_mask = np.ones((h, w), dtype=np.uint8)
        self.assertFalse(centres.validate_mask(oversized_mask, seed_area))

        undersized_mask = centres.rasterize_seed_disk(cx, cy, 0.5 * r, w, h)  # area ~ 0.25 of seed
        self.assertFalse(centres.validate_mask(undersized_mask, seed_area))

        # A rejected fit still carries useful radius diagnostics; keep the fitted radius.
        larger = centres.rasterize_seed_disk(cx, cy, 1.3 * r, w, h)
        rejected = centres.fit_circle_from_mask(larger, r, 11, w, h)
        self.assertFalse(rejected.accepted)
        self.assertIn("seed_radius", rejected.reject_reasons)
        self.assertIsNotNone(rejected.radius_px)
        self.assertGreater(rejected.radius_px, 1.2 * r)

    def test_07_seed_disk_rasterisation_symmetry(self) -> None:
        """7. Seed-disk rasterisation follows pixel-centre rule (symmetric about half-integer centre)."""
        w, h = 50, 50
        cx, cy, r = 24.5, 24.5, 10.0
        mask = centres.rasterize_seed_disk(cx, cy, r, w, h)

        # Check horizontal symmetry about 24.5: col x and col 49 - x
        self.assertTrue(np.array_equal(mask, np.fliplr(mask)), "Rasterised disk must be horizontally symmetric")
        # Check vertical symmetry about 24.5: row y and row 49 - y
        self.assertTrue(np.array_equal(mask, np.flipud(mask)), "Rasterised disk must be vertically symmetric")

    def test_08_coordinate_conversion_roundtrip(self) -> None:
        """8. Coordinate conversion: round trip from frame to model resolution and back within 1e-9 px."""
        w_frame, h_frame = 1920, 1080
        w_model, h_model = 512, 512

        test_points = [
            (0.0, 0.0),
            (960.25, 540.75),
            (1919.5, 1079.5),
            (321.123456, 789.654321),
        ]

        for x_f, y_f in test_points:
            x_m, y_m = centres.convert_frame_to_model(x_f, y_f, w_frame, h_frame, w_model, h_model)
            x_back, y_back = centres.convert_model_to_frame(x_m, y_m, w_frame, h_frame, w_model, h_model)
            self.assertAlmostEqual(x_f, x_back, delta=1e-9, msg=f"X roundtrip mismatch for {x_f}")
            self.assertAlmostEqual(y_f, y_back, delta=1e-9, msg=f"Y roundtrip mismatch for {y_f}")


if __name__ == "__main__":
    unittest.main()
