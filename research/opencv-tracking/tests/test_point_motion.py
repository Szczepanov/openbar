#!/usr/bin/env python3
"""Tests for point-motion tracking and circle fit pure functions (#57 Phase 3 bake-off).

Evaluates §6 tests:
  2. fit_circle_ransac on synthetic points: full circle and 50% arc with 30% outliers (< 0.1 px).
  3. point_motion on rotating/translating textured disk (< 0.2 px centre error).
  4. point_motion with occlusion (half occluded converges; < 12 inliers emits lost).
  5. point_motion determinism across runs.
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
import point_motion as pm  # noqa: E402
import refine_circle as rc  # noqa: E402


class TestFitCircleRansac(unittest.TestCase):
    def test_02_synthetic_points_accuracy(self) -> None:
        """2. fit_circle_ransac: full circle and 50 % arc with 30 % outliers recover centre < 0.1 px."""
        cx, cy, r = 300.25, 300.75, 85.5

        # Case A: full circle
        theta = np.linspace(0, 2 * np.pi, 100, endpoint=False)
        pts_full = np.column_stack([cx + r * np.cos(theta), cy + r * np.sin(theta)])
        res_a = rc.fit_circle_ransac(pts_full, r, 0)
        self.assertIsNotNone(res_a)
        cx_a, cy_a, r_a, inliers_a = res_a
        err_a = math.hypot(cx_a - cx, cy_a - cy)
        self.assertLess(err_a, 0.1, f"Full circle centre error {err_a:.4f} >= 0.1 px")
        self.assertLess(abs(r_a - r), 0.1, f"Full circle radius error {abs(r_a - r):.4f} >= 0.1 px")

        # Case B: 50 % arc (angles [0, pi]) with 30 % outliers
        rng = np.random.default_rng(42)
        theta_arc = np.linspace(0, np.pi, 100)
        inliers_b = np.column_stack([cx + r * np.cos(theta_arc), cy + r * np.sin(theta_arc)])
        outliers_b = []
        while len(outliers_b) < 43:  # 43 outliers / (100 + 43) = 30.06% outliers
            p = rng.uniform([cx - 1.5 * r, cy - 1.5 * r], [cx + 1.5 * r, cy + 1.5 * r], size=(2,))
            if abs(np.hypot(p[0] - cx, p[1] - cy) - r) > 3.0:
                outliers_b.append(p)
        pts_b = np.vstack([inliers_b, np.array(outliers_b)])
        res_b = rc.fit_circle_ransac(pts_b, r, 0)
        self.assertIsNotNone(res_b)
        cx_b, cy_b, r_b, inliers_found = res_b
        err_b = math.hypot(cx_b - cx, cy_b - cy)
        self.assertLess(err_b, 0.1, f"Arc with outliers centre error {err_b:.4f} >= 0.1 px")
        self.assertLess(abs(r_b - r), 0.1, f"Arc with outliers radius error {abs(r_b - r):.4f} >= 0.1 px")


class TestPointMotion(unittest.TestCase):
    def _create_synthetic_sequence(self, num_frames: int = 30) -> tuple[np.ndarray, list[np.ndarray], list[tuple[float, float]]]:
        """Creates a textured synthetic disk translated and rotated over num_frames."""
        h, w = 600, 600
        cx0, cy0, r = 300.25, 300.75, 80.0
        rng = np.random.default_rng(42)
        texture = rng.integers(50, 200, (h, w), dtype=np.uint8)

        base = np.full((h, w), 30, dtype=np.uint8)
        y, x = np.ogrid[:h, :w]
        mask = np.hypot(x - cx0, y - cy0) <= r
        base[mask] = texture[mask]

        frames = [base]
        true_centers = [(cx0, cy0)]

        for frame_idx in range(1, num_frames + 1):
            d_theta_deg = 1.5 * frame_idx
            dx = 0.3 * frame_idx
            dy = 0.2 * frame_idx

            m_rot = cv2.getRotationMatrix2D((cx0, cy0), d_theta_deg, 1.0)
            m_rot[0, 2] += dx
            m_rot[1, 2] += dy

            cur_frame = cv2.warpAffine(base, m_rot, (w, h), flags=cv2.INTER_LINEAR, borderValue=30)
            cur_cx = float(m_rot[0, 0] * cx0 + m_rot[0, 1] * cy0 + m_rot[0, 2])
            cur_cy = float(m_rot[1, 0] * cx0 + m_rot[1, 1] * cy0 + m_rot[1, 2])

            frames.append(cur_frame)
            true_centers.append((cur_cx, cur_cy))

        return base, frames, true_centers

    def test_03_synthetic_rotation_translation_accuracy(self) -> None:
        """3. point_motion: textured disk translated by sub-pixel and rotated by <= 10 deg gives error < 0.2 px."""
        cx0, cy0, r = 300.25, 300.75, 80.0
        base, frames, true_centers = self._create_synthetic_sequence(30)

        seed_pts = pm.extract_query_points(base, cx0, cy0, r)
        self.assertGreaterEqual(len(seed_pts), 50)

        prev_gray = frames[0]
        cur_pts = seed_pts.copy()
        surviving_idx = np.arange(len(seed_pts))

        max_err = 0.0
        for frame_idx in range(1, len(frames)):
            cur_gray = frames[frame_idx]
            next_pts, valid = pm.track_points_lk(prev_gray, cur_gray, cur_pts)
            surviving_idx = surviving_idx[valid]
            cur_pts = next_pts[valid]
            prev_gray = cur_gray

            res = pm.estimate_center_from_points(
                seed_pts[surviving_idx],
                cur_pts,
                (cx0, cy0),
                len(seed_pts),
                600,
                600,
            )
            self.assertEqual(res.state, "tracked")
            self.assertIsNotNone(res.center_px)
            true_cx, true_cy = true_centers[frame_idx]
            err = math.hypot(res.center_px[0] - true_cx, res.center_px[1] - true_cy)
            max_err = max(max_err, err)

        self.assertLess(max_err, 0.2, f"Max centre error {max_err:.4f} >= 0.2 px")

    def test_04_occlusion_and_lost_semantics(self) -> None:
        """4. point_motion with half of points occluded still converges. Below 12 inliers it emits lost."""
        cx0, cy0, r = 300.25, 300.75, 80.0
        base, frames, true_centers = self._create_synthetic_sequence(2)
        seed_pts = pm.extract_query_points(base, cx0, cy0, r)
        n = len(seed_pts)
        self.assertGreaterEqual(n, 50)

        # Track to frame 1
        next_pts, valid = pm.track_points_lk(frames[0], frames[1], seed_pts)
        surviving_seed = seed_pts[valid]
        surviving_cur = next_pts[valid]

        # Case A: occlude half of the surviving points
        half_n = len(surviving_seed) // 2
        res_half = pm.estimate_center_from_points(
            surviving_seed[:half_n],
            surviving_cur[:half_n],
            (cx0, cy0),
            n,
            600,
            600,
        )
        self.assertEqual(res_half.state, "tracked")
        self.assertIsNotNone(res_half.center_px)
        true_cx, true_cy = true_centers[1]
        err_half = math.hypot(res_half.center_px[0] - true_cx, res_half.center_px[1] - true_cy)
        self.assertLess(err_half, 0.2, f"Half-occluded error {err_half:.4f} >= 0.2 px")

        # Case B: fewer than 12 points remaining -> emits lost
        res_few = pm.estimate_center_from_points(
            surviving_seed[:11],
            surviving_cur[:11],
            (cx0, cy0),
            n,
            600,
            600,
        )
        self.assertEqual(res_few.state, "lost")
        self.assertIsNone(res_few.center_px)
        self.assertEqual(res_few.confidence, 0.0)

    def test_05_determinism(self) -> None:
        """5. point_motion determinism: two runs produce identical output."""
        cx0, cy0, r = 300.25, 300.75, 80.0
        base, frames, _ = self._create_synthetic_sequence(10)

        def run_once() -> list[pm.PointMotionResult]:
            seed_pts = pm.extract_query_points(base, cx0, cy0, r)
            prev_gray = frames[0]
            cur_pts = seed_pts.copy()
            surviving_idx = np.arange(len(seed_pts))
            results = []
            for frame_idx in range(1, len(frames)):
                cur_gray = frames[frame_idx]
                next_pts, valid = pm.track_points_lk(prev_gray, cur_gray, cur_pts)
                surviving_idx = surviving_idx[valid]
                cur_pts = next_pts[valid]
                prev_gray = cur_gray
                res = pm.estimate_center_from_points(
                    seed_pts[surviving_idx],
                    cur_pts,
                    (cx0, cy0),
                    len(seed_pts),
                    600,
                    600,
                )
                results.append(res)
            return results

        run1 = run_once()
        run2 = run_once()
        self.assertEqual(len(run1), len(run2))
        for r1, r2 in zip(run1, run2):
            self.assertEqual(r1.state, r2.state)
            self.assertEqual(r1.center_px, r2.center_px)
            self.assertEqual(r1.confidence, r2.confidence)
            self.assertEqual(r1.inlier_count, r2.inlier_count)
            self.assertEqual(r1.affine_matrix, r2.affine_matrix)


if __name__ == "__main__":
    unittest.main()
