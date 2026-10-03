#!/usr/bin/env python3
"""Seed-frame suggestions (#95) on synthetic frames. Needs OpenCV (research venv); skips without it."""
from __future__ import annotations

import importlib.util
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WORKFLOW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKFLOW_DIR))

HAVE_CV2 = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None
if HAVE_CV2:
    import cv2
    import numpy as np

    import vbt_suggest

WALL = (215, 225, 230)  # BGR, light wall
YELLOW = (20, 230, 230)  # BGR neon yellow
BLACK = (15, 15, 15)


def blank(width: int = 640, height: int = 960) -> "np.ndarray":
    image = np.zeros((height, width, 3), np.uint8)
    image[:] = WALL
    return image


def draw_plate(image: "np.ndarray", centre: tuple[float, float], radius: float) -> None:
    # cv2 draws in pixel-centre integer coordinates with `shift` sub-pixel bits.
    cv2.circle(image, (round(centre[0] * 16), round(centre[1] * 16)), round(radius * 16), BLACK, -1, cv2.LINE_AA, 4)


def draw_stick(image: "np.ndarray", x: int, top: int, bottom: int, markers: list[int]) -> None:
    cv2.rectangle(image, (x - 6, top), (x + 6, bottom), YELLOW, -1)
    for y in markers:
        cv2.rectangle(image, (x - 25, y - 10), (x + 25, y + 10), BLACK, -1)


@unittest.skipUnless(HAVE_CV2, "SKIPPED: cv2/numpy not importable; use the research venv")
class PlateSuggestionTests(unittest.TestCase):
    def test_drawn_circle_is_found(self) -> None:
        image = blank()
        draw_plate(image, (240.0, 610.0), 120.0)
        result = vbt_suggest.suggest_plate(image)
        self.assertEqual(result["status"], "suggested", result)
        self.assertLess(math.hypot(result["center_x_px"] - 240.0, result["center_y_px"] - 610.0), 2.0)
        self.assertLess(abs(result["radius_px"] - 120.0), 2.0)
        self.assertTrue(0.0 <= result["confidence"] <= 1.0)
        self.assertTrue(result["id"].startswith(vbt_suggest.PLATE_METHOD + ":"))
        self.assertEqual(result["method"], vbt_suggest.PLATE_METHOD)
        self.assertIn("min_radius_fraction", result["parameters"])

    def test_largest_plausible_plate_wins_over_clutter(self) -> None:
        image = blank()
        draw_plate(image, (240.0, 610.0), 120.0)
        cv2.circle(image, (520, 180), 14, BLACK, -1)  # below the plausible radius range
        cv2.rectangle(image, (400, 700), (600, 900), (60, 60, 200), -1)
        result = vbt_suggest.suggest_plate(image)
        self.assertLess(math.hypot(result["center_x_px"] - 240.0, result["center_y_px"] - 610.0), 2.0)

    def test_frame_without_a_plate_falls_back_to_manual(self) -> None:
        image = blank()
        cv2.rectangle(image, (100, 100), (300, 400), BLACK, -1)
        result = vbt_suggest.suggest_plate(image)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("id", result)
        self.assertTrue(result["reason"])

    def test_circle_leaving_the_frame_is_not_suggested(self) -> None:
        image = blank()
        draw_plate(image, (40.0, 500.0), 120.0)  # mostly outside on the left
        result = vbt_suggest.suggest_plate(image)
        if result["status"] == "suggested":  # a smaller circle inside may be proposed, but it must fit
            r = result["radius_px"]
            self.assertGreaterEqual(result["center_x_px"] - r, 0)

    def test_suggestion_is_deterministic(self) -> None:
        image = blank()
        draw_plate(image, (300.5, 400.25), 90.0)
        self.assertEqual(vbt_suggest.suggest_plate(image), vbt_suggest.suggest_plate(image.copy()))


@unittest.skipUnless(HAVE_CV2, "SKIPPED: cv2/numpy not importable; use the research venv")
class StickSuggestionTests(unittest.TestCase):
    def test_lowest_and_highest_markers_are_proposed(self) -> None:
        image = blank()
        draw_stick(image, 500, 150, 900, [220, 360, 500, 640, 800])
        result = vbt_suggest.suggest_stick(image)
        self.assertEqual(result["status"], "suggested", result)
        self.assertLess(math.hypot(result["low_x_px"] - 500, result["low_y_px"] - 800), 3.0)
        self.assertLess(math.hypot(result["high_x_px"] - 500, result["high_y_px"] - 220), 3.0)
        self.assertEqual(result["marker_count"], 5)
        self.assertTrue(0.0 <= result["confidence"] <= 1.0)
        self.assertTrue(result["id"].startswith(vbt_suggest.STICK_METHOD + ":"))

    def test_dark_stand_at_the_foot_is_not_a_marker(self) -> None:
        image = blank()
        draw_stick(image, 500, 150, 880, [220, 500, 760])
        cv2.ellipse(image, (500, 900), (90, 40), 0, 0, 360, BLACK, -1)  # the stand covers the stick's foot
        result = vbt_suggest.suggest_stick(image)
        self.assertEqual(result["status"], "suggested", result)
        self.assertLess(abs(result["low_y_px"] - 760), 3.0)

    def test_frame_without_a_stick_falls_back_to_manual(self) -> None:
        image = blank()
        draw_plate(image, (240.0, 610.0), 120.0)
        result = vbt_suggest.suggest_stick(image)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("id", result)

    def test_stick_with_one_marker_fails(self) -> None:
        image = blank()
        draw_stick(image, 500, 150, 900, [500])
        self.assertEqual(vbt_suggest.suggest_stick(image)["status"], "failed")


@unittest.skipUnless(HAVE_CV2, "SKIPPED: cv2/numpy not importable; use the research venv")
class FrameFileTests(unittest.TestCase):
    def test_suggest_frame_reads_a_png(self) -> None:
        image = blank()
        draw_plate(image, (240.0, 610.0), 120.0)
        draw_stick(image, 500, 150, 900, [220, 500, 800])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "frame_000000.png"
            cv2.imwrite(str(path), image)
            result = vbt_suggest.suggest_frame(path)
        self.assertEqual({result["plate"]["status"], result["stick"]["status"]}, {"suggested"})

    def test_a_raising_method_fails_only_its_item(self) -> None:
        image = blank()
        draw_plate(image, (240.0, 610.0), 120.0)
        draw_stick(image, 500, 150, 900, [220, 500, 800])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "frame_000000.png"
            cv2.imwrite(str(path), image)
            with mock.patch.object(vbt_suggest, "suggest_plate", side_effect=RuntimeError("boom")):
                result = vbt_suggest.suggest_frame(path)
        self.assertEqual(result["plate"]["status"], "failed")
        self.assertIn("boom", result["plate"]["reason"])
        self.assertEqual(result["stick"]["status"], "suggested")

    def test_id_covers_the_parameters(self) -> None:
        image = blank()
        draw_plate(image, (240.0, 610.0), 120.0)
        default = vbt_suggest.suggest_plate(image)
        other = vbt_suggest.suggest_plate(image, vbt_suggest.PlateParams(min_support=0.39))
        self.assertEqual(default["center_x_px"], other["center_x_px"])
        self.assertNotEqual(default["id"], other["id"])

    def test_environment_records_versions(self) -> None:
        self.assertEqual(vbt_suggest.environment(), {"opencv_version": cv2.__version__,
                                                     "numpy_version": np.__version__})

    def test_unreadable_frame_fails_both(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing.png"
            result = vbt_suggest.suggest_frame(path)
        self.assertEqual(result["plate"]["status"], "failed")
        self.assertEqual(result["stick"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
