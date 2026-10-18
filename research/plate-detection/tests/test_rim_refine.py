from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import geometry  # noqa: E402
import rim_refine  # noqa: E402


def _synthetic_plate_image(
    width: int = 320,
    height: int = 320,
    cx: float = 158.35,
    cy: float = 162.70,
    radius: float = 84.40,
    with_hub_and_occlusion: bool = False,
) -> np.ndarray:
    """Render an anti-aliased dark plate on a lighter background in ADR-0007 pixel-centre coords."""
    yy, xx = np.mgrid[:height, :width].astype(np.float64)
    dist = np.hypot(xx - cx, cy - yy)
    # Smooth anti-aliased step across 1.2 px at the outer rim (inside=40, background=200)
    t = np.clip((dist - radius) / 0.8, -4.0, 4.0)
    alpha = 1.0 / (1.0 + np.exp(-2.0 * t))
    img = 40.0 * (1.0 - alpha) + 200.0 * alpha
    if with_hub_and_occlusion:
        # Bright metallic inner hub ring around 0.35 * radius
        hub_band = np.abs(dist - 0.35 * radius) <= 4.0
        img[hub_band] = 230.0
        # Occluding bar/hand wedge over 60 degrees on the right side
        angles = np.arctan2(yy - cy, xx - cx)
        wedge = (np.abs(angles) < 0.45) & (dist > 0.6 * radius) & (dist < 1.3 * radius)
        img[wedge] = 45.0
    return np.clip(np.round(img), 0, 255).astype(np.uint8)


class RimRefineTests(unittest.TestCase):
    def test_subpixel_refinement_recovers_true_rim_from_coarse_prior(self) -> None:
        true_cx, true_cy, true_r = 158.35, 162.70, 84.40
        img = _synthetic_plate_image(cx=true_cx, cy=true_cy, radius=true_r)
        # Coarse prior with +3.2 px centre offset and +4.6 px (5.5%) radius bias
        prior = geometry.Circle(true_cx + 3.2, true_cy - 2.4, true_r + 4.6, "mask_contour")
        res = rim_refine.refine_circle(img, prior)
        self.assertTrue(res.used_refined)
        self.assertEqual(res.reason, "accepted")
        self.assertEqual(res.refined.fit, "rim_subpixel")
        self.assertEqual(res.raw, prior)
        self.assertAlmostEqual(res.refined.center_x_px, true_cx, delta=0.20)
        self.assertAlmostEqual(res.refined.center_y_px, true_cy, delta=0.20)
        self.assertAlmostEqual(res.refined.radius_px, true_r, delta=0.20)

    def test_refinement_ignores_inner_hub_and_partial_rim_occlusion(self) -> None:
        true_cx, true_cy, true_r = 160.25, 155.60, 82.80
        img = _synthetic_plate_image(
            cx=true_cx, cy=true_cy, radius=true_r, with_hub_and_occlusion=True
        )
        bgr = np.stack([img, img, img], axis=-1)
        prior = geometry.Circle(true_cx - 2.0, true_cy + 2.5, true_r - 3.8, "box")
        res = rim_refine.refine_circle(bgr, prior)
        self.assertTrue(res.used_refined)
        self.assertAlmostEqual(res.refined.center_x_px, true_cx, delta=0.25)
        self.assertAlmostEqual(res.refined.center_y_px, true_cy, delta=0.25)
        self.assertAlmostEqual(res.refined.radius_px, true_r, delta=0.25)

    def test_low_contrast_image_falls_back_explicitly_to_raw_prior(self) -> None:
        flat = np.full((240, 240), 128, dtype=np.uint8)
        prior = geometry.Circle(120.0, 120.0, 50.0, "mask_contour", 0.8, 0.9)
        res = rim_refine.refine_circle(flat, prior)
        self.assertFalse(res.used_refined)
        self.assertEqual(res.reason, "insufficient_gradient_peaks")
        self.assertEqual(res.refined, prior)
        self.assertEqual(res.raw, prior)

    def test_refine_candidate_row_preserves_raw_circle_observation(self) -> None:
        true_cx, true_cy, true_r = 150.0, 150.0, 75.0
        img = _synthetic_plate_image(cx=true_cx, cy=true_cy, radius=true_r)
        raw_row = {
            "center_x_px": 152.5,
            "center_y_px": 148.0,
            "radius_px": 79.0,
            "score": 0.95,
            "fit": "mask_contour",
            "fit_rms_px": 1.2,
            "arc_coverage": 0.88,
        }
        out = rim_refine.refine_candidate_row(img, raw_row)
        self.assertEqual(out["raw_circle"]["center_x_px"], 152.5)
        self.assertEqual(out["raw_circle"]["radius_px"], 79.0)
        self.assertEqual(out["raw_circle"]["fit"], "mask_contour")
        self.assertTrue(out["rim_refinement"]["used_refined"])
        self.assertEqual(out["fit"], "rim_subpixel")
        self.assertAlmostEqual(out["radius_px"], true_r, delta=0.20)
        out2 = rim_refine.refine_candidate_row(img, out)
        self.assertEqual(out2, out)

    def test_non_finite_input_raises_value_error(self) -> None:
        img = np.zeros((100, 100), dtype=np.uint8)
        with self.assertRaises(ValueError):
            rim_refine.refine_circle(img, geometry.Circle(float("nan"), 50.0, 20.0, "box"))


if __name__ == "__main__":
    unittest.main()
