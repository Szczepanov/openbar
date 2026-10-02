#!/usr/bin/env python3
"""Tests for plate-geometry refinement pure functions (#57 Phase 2).

These tests run in the research venv (not CI) and evaluate synthetic cases
without requiring any private video files.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

OPENCV_TRACKING_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPENCV_TRACKING_DIR))
import refine_circle as rc  # noqa: E402


def render_synthetic_disk(
    w: int,
    h: int,
    cx: float,
    cy: float,
    r: float,
    add_texture: bool = True,
    seed: int = 123,
    fg_val: float = 220.0,
) -> np.ndarray:
    """Render an anti-aliased disk by 8x supersampling with known sub-pixel centre."""
    scale = 8
    w_hi, h_hi = w * scale, h * scale
    cx_hi = scale * cx + (scale - 1) / 2.0
    cy_hi = scale * cy + (scale - 1) / 2.0
    r_hi = scale * r

    img_hi = np.zeros((h_hi, w_hi), dtype=np.uint8)
    cv2.circle(img_hi, (int(round(cx_hi)), int(round(cy_hi))), int(round(r_hi)), 255, -1)
    disk = cv2.resize(img_hi, (w, h), interpolation=cv2.INTER_AREA)

    alpha = disk.astype(float) / 255.0
    if add_texture:
        bg = np.random.default_rng(seed).integers(30, 70, (h, w), dtype=np.uint8)
    else:
        bg = np.full((h, w), 50, dtype=np.uint8)

    composite = (alpha * fg_val + (1.0 - alpha) * bg).astype(np.uint8)
    return cv2.cvtColor(composite, cv2.COLOR_GRAY2BGR)


class TestRefineCircle(unittest.TestCase):
    def test_01_clean_disk_accuracy(self) -> None:
        """1. Clean disk, r in {60, 130, 200}: centre error < 0.1 px, radius error < 0.2 px."""
        for r in [60.0, 130.0, 200.0]:
            cx, cy = 300.25, 300.75
            frame = render_synthetic_disk(600, 600, cx, cy, r)
            box = (cx - r, cy - r, 2 * r + 1, 2 * r + 1)
            res = rc.refine_circle(frame, box, r, r, 1.0, 0, method="circle")

            self.assertTrue(res.accepted, f"Fit failed for r={r}: {res.reject_reasons}")
            err_c = math.hypot(res.center_px[0] - cx, res.center_px[1] - cy)
            err_r = abs(res.radius_px - r)
            self.assertLess(err_c, 0.1, f"Centre error {err_c:.4f} >= 0.1 px for r={r}")
            self.assertLess(err_r, 0.2, f"Radius error {err_r:.4f} >= 0.2 px for r={r}")

    def test_02_roi_touching_frame_edges(self) -> None:
        """2. ROI touching each frame edge: the same accuracy and no indexing error."""
        r = 60.0
        # Frame 500x500; with box width 121, 0.65 * w = 78.65 px.
        cases = [
            ("left", 70.25, 250.75),    # cx - 78.65 < 0 -> x0 = 0
            ("right", 429.25, 250.75),  # cx + 78.65 > 500 -> x1 = 500
            ("top", 250.25, 70.75),     # cy - 78.65 < 0 -> y0 = 0
            ("bottom", 250.25, 429.75), # cy + 78.65 > 500 -> y1 = 500
        ]
        for edge_name, cx, cy in cases:
            frame = render_synthetic_disk(500, 500, cx, cy, r)
            box = (cx - r, cy - r, 2 * r + 1, 2 * r + 1)
            res = rc.refine_circle(frame, box, r, r, 1.0, 0, method="circle")

            self.assertTrue(res.accepted, f"Fit failed on edge {edge_name}: {res.reject_reasons}")
            err_c = math.hypot(res.center_px[0] - cx, res.center_px[1] - cy)
            err_r = abs(res.radius_px - r)
            self.assertLess(err_c, 0.1, f"Centre error {err_c:.4f} >= 0.1 px on edge {edge_name}")
            self.assertLess(err_r, 0.2, f"Radius error {err_r:.4f} >= 0.2 px on edge {edge_name}")

    def test_03_base_box_offset(self) -> None:
        """3. Base box offset by 0.2 r from the true centre: still converges."""
        cx_true, cy_true, r = 300.25, 300.75, 60.0
        frame = render_synthetic_disk(600, 600, cx_true, cy_true, r)

        offset_x = 0.2 * r
        offset_y = -0.1 * r
        cx_box = cx_true + offset_x
        cy_box = cy_true + offset_y
        box = (cx_box - r, cy_box - r, 2 * r + 1, 2 * r + 1)

        res = rc.refine_circle(frame, box, r, r, 1.0, 0, method="circle")
        self.assertTrue(res.accepted, f"Offset fit failed: {res.reject_reasons}")
        err_c = math.hypot(res.center_px[0] - cx_true, res.center_px[1] - cy_true)
        err_r = abs(res.radius_px - r)
        self.assertLess(err_c, 0.1, f"Converged centre error {err_c:.4f} >= 0.1 px")
        self.assertLess(err_r, 0.2, f"Converged radius error {err_r:.4f} >= 0.2 px")

    def test_04_occluded_arc_rejection(self) -> None:
        """4. Disk with a 60 % arc occluded: rejected on coverage, base centre emitted, conf x 0.7."""
        w, h, cx, cy, r = 600, 600, 300.25, 300.75, 60.0
        scale = 8
        w_hi, h_hi = w * scale, h * scale
        cx_hi = scale * cx + (scale - 1) / 2.0
        cy_hi = scale * cy + (scale - 1) / 2.0
        r_hi = scale * r

        # 60% occluded => 40% = 144 degrees visible
        img_hi = np.zeros((h_hi, w_hi), dtype=np.uint8)
        cv2.ellipse(img_hi, (int(round(cx_hi)), int(round(cy_hi))), (int(round(r_hi)), int(round(r_hi))), 0, 0, 144, 255, -1)
        disk = cv2.resize(img_hi, (w, h), interpolation=cv2.INTER_AREA)

        alpha = disk.astype(float) / 255.0
        bg = np.random.default_rng(123).integers(30, 70, (h, w), dtype=np.uint8)
        composite = (alpha * 220.0 + (1.0 - alpha) * bg).astype(np.uint8)
        frame = cv2.cvtColor(composite, cv2.COLOR_GRAY2BGR)

        base_box = (cx - r, cy - r, 2 * r + 1, 2 * r + 1)
        base_conf = 0.90
        res = rc.refine_circle(frame, base_box, r, r, base_conf, 0, method="circle")

        self.assertFalse(res.accepted)
        self.assertIn("coverage", res.reject_reasons)
        self.assertAlmostEqual(res.confidence, base_conf * 0.7, places=4)
        base_c = rc.box_center(base_box)
        self.assertEqual(res.center_px, base_c)

    def test_05_concentric_inner_ring(self) -> None:
        """5. Concentric inner ring at 0.5 r plus outer rim: picks the outer rim."""
        w, h, cx, cy, r = 600, 600, 300.25, 300.75, 100.0
        scale = 8
        w_hi, h_hi = w * scale, h * scale
        cx_hi = scale * cx + (scale - 1) / 2.0
        cy_hi = scale * cy + (scale - 1) / 2.0
        r_hi = scale * r
        r_inner_hi = scale * (0.5 * r)

        img_hi = np.zeros((h_hi, w_hi), dtype=np.uint8)
        cv2.circle(img_hi, (int(round(cx_hi)), int(round(cy_hi))), int(round(r_hi)), 255, -1)
        cv2.circle(img_hi, (int(round(cx_hi)), int(round(cy_hi))), int(round(r_inner_hi)), 128, -1)
        disk = cv2.resize(img_hi, (w, h), interpolation=cv2.INTER_AREA)

        bg = np.random.default_rng(123).integers(30, 50, (h, w), dtype=np.uint8)
        mask = (disk > 0).astype(float)
        composite = (disk.astype(float) + (1.0 - mask) * bg).astype(np.uint8)
        frame = cv2.cvtColor(composite, cv2.COLOR_GRAY2BGR)

        box = (cx - r, cy - r, 2 * r + 1, 2 * r + 1)
        res = rc.refine_circle(frame, box, r, r, 1.0, 0, method="circle")

        self.assertTrue(res.accepted, f"Fit failed: {res.reject_reasons}")
        self.assertLess(abs(res.radius_px - r), 1.0, f"Expected outer rim ~{r}, got {res.radius_px}")

    def test_06_radius_jump_rejection(self) -> None:
        """6. A radius jump of 15 % versus r_prev: rejected on continuity."""
        w, h, cx, cy, r_actual = 600, 600, 300.25, 300.75, 100.0
        frame = render_synthetic_disk(w, h, cx, cy, r_actual)

        r_prev = r_actual / 1.15  # 15 % discrepancy
        box = (cx - r_actual, cy - r_actual, 2 * r_actual + 1, 2 * r_actual + 1)
        res = rc.refine_circle(frame, box, r_prev, r_prev, 1.0, 0, method="circle")

        self.assertFalse(res.accepted)
        self.assertIn("frame_radius", res.reject_reasons)
        self.assertEqual(res.radius_px, r_prev)

    def test_07_vertical_motion_blur_measurement(self) -> None:
        """7. Vertical box blur of 15 px (motion-blur proxy): record the centre bias."""
        w, h, cx, cy, r = 600, 600, 300.25, 300.75, 90.0
        scale = 8
        w_hi, h_hi = w * scale, h * scale
        cx_hi = scale * cx + (scale - 1) / 2.0
        cy_hi = scale * cy + (scale - 1) / 2.0
        r_hi = scale * r

        img_hi = np.zeros((h_hi, w_hi), dtype=np.uint8)
        cv2.circle(img_hi, (int(round(cx_hi)), int(round(cy_hi))), int(round(r_hi)), 220, -1)
        disk = cv2.resize(img_hi, (w, h), interpolation=cv2.INTER_AREA)

        blurred_vert = cv2.blur(disk, (1, 15))
        frame = cv2.cvtColor(blurred_vert, cv2.COLOR_GRAY2BGR)

        box = (cx - r, cy - r, 2 * r + 1, 2 * r + 1)
        res = rc.refine_circle(frame, box, r, r, 1.0, 0, method="circle")

        bias_x = res.center_px[0] - cx
        bias_y = res.center_px[1] - cy
        bias_total = math.hypot(bias_x, bias_y)

        # Log measured bias for plan documentation
        print(f"\n[Test 7 Measurement] Vertical 15px box blur centre bias: "
              f"total={bias_total:.4f} px (dx={bias_x:.4f} px, dy={bias_y:.4f} px), "
              f"fitted_radius={res.radius_px:.4f} px")
        self.assertIsNotNone(res.center_px)

    def test_08_determinism(self) -> None:
        """8. Determinism: same frame and box twice -> identical output; independent of call order."""
        w, h, cx, cy, r = 600, 600, 300.25, 300.75, 80.0
        frame = render_synthetic_disk(w, h, cx, cy, r)
        box = (cx - r, cy - r, 2 * r + 1, 2 * r + 1)

        res1 = rc.refine_circle(frame, box, r, r, 1.0, 5, method="circle")
        res2 = rc.refine_circle(frame, box, r, r, 1.0, 5, method="circle")
        self.assertEqual(res1, res2, "Same call twice must produce identical results")

        res_f7 = rc.refine_circle(frame, box, r, r, 1.0, 7, method="circle")
        res_f3_after = rc.refine_circle(frame, box, r, r, 1.0, 3, method="circle")
        res_f3_direct = rc.refine_circle(frame, box, r, r, 1.0, 3, method="circle")
        self.assertEqual(res_f3_after, res_f3_direct, "Call order must not affect frame result")

    def test_09_hough_on_test_1(self) -> None:
        """9. Hough on test 1: centre within 1 px (Hough is quantised; record the actual value)."""
        print()
        for r in [60.0, 130.0, 200.0]:
            cx, cy = 300.0, 300.0
            frame = render_synthetic_disk(600, 600, cx, cy, r, add_texture=False)
            box = (cx - r, cy - r, 2 * r + 1, 2 * r + 1)
            res = rc.refine_circle(frame, box, r, r, 1.0, 0, method="hough")

            self.assertTrue(res.accepted, f"Hough failed for r={r}: {res.reject_reasons}")
            err_c = math.hypot(res.center_px[0] - cx, res.center_px[1] - cy)
            print(f"[Test 9 Measurement] Hough r={r}: detected={res.center_px}, error={err_c:.4f} px")
            self.assertLessEqual(err_c, 1.5, f"Hough error {err_c:.4f} > 1.5 px for r={r}")


if __name__ == "__main__":
    unittest.main()
