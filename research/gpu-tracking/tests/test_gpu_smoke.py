#!/usr/bin/env python3
"""Smoke test for all 8 GPU tracker candidates on synthetic moving disk (#57 Phase 3 bake-off).

Evaluates §6 Test 9:
  A textured disk moving 2 px per frame over 30 frames, run through every GPU candidate.
  Mask candidates must be within 0.5 px and point candidates within 1.0 px of the truth.
  A consistent ±0.5 px offset means a convention bug, and must be fixed before scoring.
"""
from __future__ import annotations

import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
GPU_DIR = Path(__file__).resolve().parents[1]
OPENCV_DIR = ROOT / "research" / "opencv-tracking"

sys.path.insert(0, str(OPENCV_DIR))
sys.path.insert(0, str(GPU_DIR))

import track_gpu  # noqa: E402


class TestGpuSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.mkdtemp(prefix="openbar-gpu-smoke-")
        cls.work_path = Path(cls.temp_dir)

        cls.width = 512
        cls.height = 512
        cls.num_frames = 30
        cls.cx0 = 200.0
        cls.cy0 = 250.0
        cls.vx = 2.0
        cls.vy = 0.0
        cls.radius = 50.0

        cls.jpeg_paths = []
        y_grid, x_grid = np.ogrid[: cls.height, : cls.width]

        for t in range(cls.num_frames):
            c_xt = cls.cx0 + cls.vx * t
            c_yt = cls.cy0 + cls.vy * t

            img = np.full((cls.height, cls.width), 30, dtype=np.uint8)
            u = x_grid - c_xt
            v = y_grid - c_yt
            dist2 = u**2 + v**2
            mask = dist2 <= cls.radius**2

            tex = 128 + 80 * np.sin(0.25 * u) * np.sin(0.25 * v) + 40 * np.sin(0.5 * u + 0.5 * v)
            tex = np.clip(tex, 0, 255).astype(np.uint8)
            img[mask] = tex[mask]

            # 3-channel BGR
            img_bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            frame_path = cls.work_path / f"{t:06d}.jpg"
            cv2.imwrite(str(frame_path), img_bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
            cls.jpeg_paths.append(frame_path)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def _eval_samples(
        self,
        candidate_name: str,
        samples: list[dict],
        max_err_threshold: float,
        is_point: bool = False,
    ) -> None:
        self.assertEqual(len(samples), self.num_frames)

        errors = []
        dxs = []
        dys = []

        for t in range(1, self.num_frames):
            s = samples[t]
            self.assertEqual(s.get("state"), "tracked", f"Frame {t} lost in {candidate_name}")
            c = s.get("center_px")
            self.assertIsNotNone(c)
            true_x = self.cx0 + self.vx * t
            true_y = self.cy0 + self.vy * t
            dx = c["x_px"] - true_x
            dy = c["y_px"] - true_y
            err = math.hypot(dx, dy)
            errors.append(err)
            dxs.append(dx)
            dys.append(dy)

        max_err = max(errors)
        mean_err = float(np.mean(errors))
        mean_dx = float(np.mean(dxs))
        mean_dy = float(np.mean(dys))

        print(
            f"\n[GPU Smoke] {candidate_name:24s} | "
            f"Mean Err: {mean_err:6.3f} px | Max Err: {max_err:6.3f} px | "
            f"Mean dx: {mean_dx:+.3f} px | Mean dy: {mean_dy:+.3f} px"
        )

        # Check for systematic convention bug (±0.5 px)
        self.assertLess(
            abs(mean_dx),
            0.5,
            f"{candidate_name} exhibits systematic X offset ({mean_dx:+.3f} px) - convention bug!",
        )
        self.assertLess(
            abs(mean_dy),
            0.5,
            f"{candidate_name} exhibits systematic Y offset ({mean_dy:+.3f} px) - convention bug!",
        )

        self.assertLess(
            max_err,
            max_err_threshold,
            f"{candidate_name} max error {max_err:.3f} px exceeds threshold {max_err_threshold:.3f} px",
        )

    def test_09_gpu_smoke_sam2_small_centroid(self) -> None:
        samples, _ = track_gpu.track_sam2(
            "small",
            "centroid",
            self.jpeg_paths,
            0,
            self.num_frames - 1,
            self.cx0,
            self.cy0,
            self.radius,
            self.width,
            self.height,
        )
        self._eval_samples("sam2.1-small-centroid", samples, max_err_threshold=0.5)

    def test_09_gpu_smoke_sam2_small_circle(self) -> None:
        samples, _ = track_gpu.track_sam2(
            "small",
            "circle",
            self.jpeg_paths,
            0,
            self.num_frames - 1,
            self.cx0,
            self.cy0,
            self.radius,
            self.width,
            self.height,
        )
        self._eval_samples("sam2.1-small-circle", samples, max_err_threshold=0.5)

    def test_09_gpu_smoke_sam2_bplus_centroid(self) -> None:
        samples, _ = track_gpu.track_sam2(
            "bplus",
            "centroid",
            self.jpeg_paths,
            0,
            self.num_frames - 1,
            self.cx0,
            self.cy0,
            self.radius,
            self.width,
            self.height,
        )
        self._eval_samples("sam2.1-bplus-centroid", samples, max_err_threshold=0.5)

    def test_09_gpu_smoke_sam2_bplus_circle(self) -> None:
        samples, _ = track_gpu.track_sam2(
            "bplus",
            "circle",
            self.jpeg_paths,
            0,
            self.num_frames - 1,
            self.cx0,
            self.cy0,
            self.radius,
            self.width,
            self.height,
        )
        self._eval_samples("sam2.1-bplus-circle", samples, max_err_threshold=0.5)

    def test_09_gpu_smoke_cutie_centroid(self) -> None:
        samples, _ = track_gpu.track_cutie(
            "centroid",
            self.jpeg_paths,
            0,
            self.num_frames - 1,
            self.cx0,
            self.cy0,
            self.radius,
            self.width,
            self.height,
        )
        self._eval_samples("cutie-base-centroid", samples, max_err_threshold=0.5)

    def test_09_gpu_smoke_cutie_circle(self) -> None:
        samples, _ = track_gpu.track_cutie(
            "circle",
            self.jpeg_paths,
            0,
            self.num_frames - 1,
            self.cx0,
            self.cy0,
            self.radius,
            self.width,
            self.height,
        )
        self._eval_samples("cutie-base-circle", samples, max_err_threshold=0.5)

    def test_09_gpu_smoke_bootstapir_affine(self) -> None:
        samples, _, _ = track_gpu.track_bootstapir(
            self.jpeg_paths,
            0,
            self.num_frames - 1,
            self.cx0,
            self.cy0,
            self.radius,
            self.width,
            self.height,
            target_res=512,
        )
        self._eval_samples("bootstapir-affine", samples, max_err_threshold=1.0, is_point=True)

    def test_09_gpu_smoke_cotracker3_affine(self) -> None:
        samples, _ = track_gpu.track_cotracker(
            self.jpeg_paths,
            0,
            self.num_frames - 1,
            self.cx0,
            self.cy0,
            self.radius,
            self.width,
            self.height,
        )
        self._eval_samples("cotracker3-affine", samples, max_err_threshold=1.0, is_point=True)


if __name__ == "__main__":
    unittest.main()
