"""Regression checks for default-window relative-registration ambiguity."""

import importlib.util
import math
from pathlib import Path
import unittest

import numpy as np


SPEC = importlib.util.spec_from_file_location("bar_path_vision", Path(__file__).parents[1] / "vision.py")
VISION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VISION)


class DefaultWindowAmbiguityTests(unittest.TestCase):
    def test_periodic_texture_with_multiple_supported_shifts_fails_closed(self):
        yy, xx = np.mgrid[:64, :64]
        previous = 125 + 30 * np.cos(2 * np.pi * xx / 8) + 30 * np.cos(2 * np.pi * yy / 8)
        current = np.roll(previous, 1, axis=1)

        result = VISION.relative_shift(previous, current)

        self.assertIsNone(result["delta_px"])
        self.assertIsNone(result["confidence"])
        self.assertEqual(result["diagnostics"]["loss_reason"], "ambiguous_real_overlap")
        competitor = result["diagnostics"]["best_competing_real_overlap"]
        self.assertIsNotNone(competitor)
        self.assertGreaterEqual(competitor["aligned_correlation"], VISION.REGISTRATION_CONFIG["min_correlation"])
        self.assertGreaterEqual(competitor["overlap_fraction"], VISION.REGISTRATION_CONFIG["min_overlap_fraction"])
        phase = result["diagnostics"]["phase_delta_px"]
        separation = math.hypot(competitor["delta_px"]["x_px"] - phase["x_px"],
                                competitor["delta_px"]["y_px"] - phase["y_px"])
        self.assertGreater(separation, VISION.PHASE_MAIN_LOBE_RADIUS_PX)

    def test_unique_finite_crop_still_measures_default_window_translation(self):
        rng = np.random.default_rng(23017)
        field = rng.integers(0, 256, size=(112, 112), dtype=np.uint8).astype(float)
        dx, dy = 3, -2
        previous = field[24:88, 24:88]
        current = field[24 - dy:88 - dy, 24 - dx:88 - dx]

        result = VISION.relative_shift(previous, current)

        self.assertIsNotNone(result["delta_px"], result["diagnostics"])
        self.assertAlmostEqual(result["delta_px"]["x_px"], dx, delta=0.05)
        self.assertAlmostEqual(result["delta_px"]["y_px"], dy, delta=0.05)
        competitor = result["diagnostics"]["best_competing_real_overlap"]
        if competitor is not None:
            self.assertTrue(
                competitor["aligned_correlation"] < VISION.REGISTRATION_CONFIG["min_correlation"]
                or competitor["overlap_fraction"] < VISION.REGISTRATION_CONFIG["min_overlap_fraction"]
            )


if __name__ == "__main__":
    unittest.main()
