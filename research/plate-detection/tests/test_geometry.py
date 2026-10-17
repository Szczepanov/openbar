"""Box and mask to circle, synthetic masks only (#233)."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geometry  # noqa: E402


def disc(height: int, width: int, cx: float, cy: float, r: float) -> np.ndarray:
    ys, xs = np.mgrid[0:height, 0:width]
    return (xs - cx) ** 2 + (ys - cy) ** 2 <= r * r


class BoxTests(unittest.TestCase):
    def test_box_centre_moves_to_pixel_centres(self) -> None:
        self.assertEqual(geometry.circle_from_box(10.0, 20.0, 30.0, 40.0),
                         geometry.Circle(19.5, 29.5, 10.0, "box"))

    def test_cut_box_keeps_larger_side(self) -> None:
        self.assertEqual(geometry.circle_from_box(0.0, 0.0, 15.0, 40.0).radius_px, 20.0)

    def test_invalid_boxes_are_refused(self) -> None:
        for box in [(5, 5, 5, 10), (5, 5, 1, 10), (0, 0, math.nan, 4), (0, 0, math.inf, 4)]:
            with self.subTest(box=box), self.assertRaises(ValueError):
                geometry.circle_from_box(*box)


class FitTests(unittest.TestCase):
    def test_exact_points_recover_circle(self) -> None:
        angles = np.linspace(0, 2 * np.pi, 50, endpoint=False)
        cx, cy, r = geometry.fit_circle(3 + 7 * np.cos(angles), -2 + 7 * np.sin(angles))
        self.assertAlmostEqual(cx, 3.0, places=9)
        self.assertAlmostEqual(cy, -2.0, places=9)
        self.assertAlmostEqual(r, 7.0, places=9)

    def test_degenerate_inputs_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            geometry.fit_circle(np.array([0.0, 1.0]), np.array([0.0, 1.0]))
        with self.assertRaises(ValueError):
            geometry.fit_circle(np.arange(5.0), np.arange(5.0))


class MaskTests(unittest.TestCase):
    def test_full_disc(self) -> None:
        circle = geometry.circle_from_mask(disc(200, 200, 100.0, 90.0, 50.0))
        self.assertEqual(circle.fit, "mask_contour")
        self.assertLess(math.hypot(circle.center_x_px - 100.0, circle.center_y_px - 90.0), 0.3)
        self.assertLess(abs(circle.radius_px - 50.0), 0.6)
        self.assertEqual(circle.arc_coverage, 1.0)

    def test_disc_cut_by_frame_edge_keeps_true_geometry(self) -> None:
        circle = geometry.circle_from_mask(disc(200, 200, 100.0, 180.0, 50.0))
        self.assertLess(math.hypot(circle.center_x_px - 100.0, circle.center_y_px - 180.0), 0.6)
        self.assertLess(abs(circle.radius_px - 50.0), 0.8)
        self.assertLess(circle.arc_coverage, 0.8)

    def test_protrusion_is_trimmed(self) -> None:
        mask = disc(240, 240, 120.0, 120.0, 60.0)
        mask[115:126, 120:235] = True  # a bar sleeve sticking out to the right
        circle = geometry.circle_from_mask(mask)
        self.assertLess(math.hypot(circle.center_x_px - 120.0, circle.center_y_px - 120.0), 2.0)
        self.assertLess(abs(circle.radius_px - 60.0), 2.0)

    def test_tiny_mask_falls_back_to_area(self) -> None:
        mask = np.zeros((10, 10), dtype=bool)
        mask[4:6, 4:6] = True
        circle = geometry.circle_from_mask(mask)
        self.assertEqual(circle.fit, "mask_area")
        self.assertEqual((circle.center_x_px, circle.center_y_px), (4.5, 4.5))
        self.assertAlmostEqual(circle.radius_px, math.sqrt(4 / math.pi))

    def test_empty_mask_is_none(self) -> None:
        self.assertIsNone(geometry.circle_from_mask(np.zeros((5, 5), dtype=bool)))

    def test_boundary_ignores_image_border(self) -> None:
        xs, ys = geometry.boundary_points(np.ones((6, 6), dtype=bool))
        self.assertEqual(xs.size, 0)


if __name__ == "__main__":
    unittest.main()
