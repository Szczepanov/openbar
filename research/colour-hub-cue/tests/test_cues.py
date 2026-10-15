"""Synthetic-only tests for the colour gate and the hub cue."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cues  # noqa: E402
import synthetic  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bar-path-measurement"))
import vision  # noqa: E402

CENTRE = (240.0, 160.0)


def admit_everything():
    return cues.PlateColour((0.0, 0.0, 0.0), (1e9, 1e9, 1e9))


class ColourModelTests(unittest.TestCase):
    def test_model_matches_the_plate_face_and_respects_the_scale_floor(self):
        model = cues.fit_plate_colour(synthetic.render(*CENTRE), CENTRE, synthetic.RADIUS)
        lab = cues._lab(np.full((8, 8, 3), synthetic.PLATE, np.uint8))[0, 0]
        for fitted, expected in zip(model.median, lab):
            self.assertAlmostEqual(fitted, expected, delta=2.0)
        self.assertTrue(all(s >= cues.SCALE_FLOOR for s in model.scale))

    def test_annulus_outside_the_image_is_an_error(self):
        with self.assertRaises(ValueError):
            cues.fit_plate_colour(synthetic.render(*CENTRE), (5.0, 5.0), 2000.0)

    def test_invalid_frame_is_an_error(self):
        with self.assertRaises(ValueError):
            cues.fit_plate_colour(np.zeros((4, 4, 3), np.uint8), (2.0, 2.0), 10.0)


class GatedRadialTests(unittest.TestCase):
    def setUp(self):
        self.plain = synthetic.render(*CENTRE)
        self.ringed = synthetic.render(*CENTRE, ring=True)
        self.model = cues.fit_plate_colour(self.plain, CENTRE, synthetic.RADIUS)

    def test_ungated_sampler_is_fooled_by_the_stronger_outer_edge_and_the_gate_is_not(self):
        ungated = vision.radial_center(self.ringed, CENTRE, synthetic.RADIUS)
        gated = cues.radial_center_gated(self.ringed, CENTRE, synthetic.RADIUS, self.model)
        self.assertIsNotNone(ungated["center_px"])
        self.assertAlmostEqual(ungated["diagnostics"]["radius_px"], 1.12 * synthetic.RADIUS, delta=1.0)
        self.assertIsNotNone(gated["center_px"])
        self.assertAlmostEqual(gated["diagnostics"]["radius_px"], synthetic.RADIUS, delta=1.0)
        self.assertAlmostEqual(gated["center_px"]["x_px"], CENTRE[0], delta=0.5)
        self.assertAlmostEqual(gated["center_px"]["y_px"], CENTRE[1], delta=0.5)

    def test_a_model_that_admits_every_pixel_reproduces_the_original_estimator(self):
        for image in (self.plain, self.ringed):
            original = vision.radial_center(image, CENTRE, synthetic.RADIUS)
            gated = cues.radial_center_gated(image, CENTRE, synthetic.RADIUS, admit_everything())
            self.assertEqual(gated["center_px"], original["center_px"])
            self.assertEqual(gated["confidence"], original["confidence"])
            self.assertEqual(gated["diagnostics"]["rays"], original["diagnostics"]["rays"])
            self.assertEqual(gated["diagnostics"]["rays_without_eligible_edge"], 0)

    def test_no_eligible_edge_means_lost_and_no_coordinate(self):
        wrong = cues.PlateColour((255.0, 128.0, 128.0), (1.0, 1.0, 1.0))
        result = cues.radial_center_gated(self.plain, CENTRE, synthetic.RADIUS, wrong)
        self.assertIsNone(result["center_px"])
        self.assertIsNone(result["confidence"])
        self.assertEqual(result["diagnostics"]["loss_reason"], "insufficient_rim_support")
        self.assertEqual(result["diagnostics"]["rays_without_eligible_edge"], 72)

    def test_result_is_deterministic(self):
        first = cues.radial_center_gated(self.ringed, CENTRE, synthetic.RADIUS, self.model)
        second = cues.radial_center_gated(self.ringed, CENTRE, synthetic.RADIUS, self.model)
        self.assertEqual(first, second)

    def test_invalid_inputs_are_lost_or_rejected(self):
        self.assertEqual(cues.radial_center_gated(np.zeros((4, 4), np.uint8), CENTRE, 60.0, self.model)
                         ["diagnostics"]["loss_reason"], "invalid_bgr_image")
        self.assertEqual(cues.radial_center_gated(self.plain, (-5.0, 10.0), 60.0, self.model)
                         ["diagnostics"]["loss_reason"], "coarse_center_outside_image")
        with self.assertRaises(ValueError):
            cues.radial_center_gated(self.plain, (float("nan"), 10.0), 60.0, self.model)


class HubTests(unittest.TestCase):
    def test_template_is_fitted_from_the_seed_frame(self):
        template = cues.fit_hub(synthetic.render(*CENTRE), CENTRE, synthetic.RADIUS)
        self.assertIsNotNone(template)
        self.assertEqual(template.patch.shape, (2 * template.half + 1,) * 2)
        self.assertGreaterEqual(template.half, 3)

    def test_flat_or_off_image_hub_is_unsupported(self):
        flat = np.full((*synthetic.SIZE, 3), synthetic.BACKGROUND, np.uint8)
        self.assertIsNone(cues.fit_hub(flat, CENTRE, synthetic.RADIUS))
        self.assertIsNone(cues.fit_hub(synthetic.render(*CENTRE), (3.0, 3.0), synthetic.RADIUS))

    def test_hub_is_found_at_the_shifted_position(self):
        template = cues.fit_hub(synthetic.render(*CENTRE, seed=1), CENTRE, synthetic.RADIUS)
        moved = (CENTRE[0] + 7.0, CENTRE[1] + 4.0)
        result = cues.locate_hub(synthetic.render(*moved, seed=2), template, moved, synthetic.RADIUS)
        self.assertAlmostEqual(result["center_px"]["x_px"], moved[0], delta=0.5)
        self.assertAlmostEqual(result["center_px"]["y_px"], moved[1], delta=0.5)
        self.assertGreaterEqual(result["confidence"], cues.HUB_NCC_MIN)

    def test_hub_is_found_from_a_nearby_but_wrong_coarse_centre(self):
        template = cues.fit_hub(synthetic.render(*CENTRE, seed=1), CENTRE, synthetic.RADIUS)
        moved = (CENTRE[0] + 10.0, CENTRE[1] - 6.0)
        result = cues.locate_hub(synthetic.render(*moved, seed=2), template, CENTRE, synthetic.RADIUS)
        self.assertAlmostEqual(result["center_px"]["x_px"], moved[0], delta=0.5)

    def test_missing_hub_is_lost_with_no_coordinate(self):
        template = cues.fit_hub(synthetic.render(*CENTRE, seed=1), CENTRE, synthetic.RADIUS)
        without = synthetic.render(*CENTRE, hub=False, noise=6.0, seed=3)
        result = cues.locate_hub(without, template, CENTRE, synthetic.RADIUS)
        self.assertIsNone(result["center_px"])
        self.assertIsNone(result["confidence"])
        self.assertEqual(result["diagnostics"]["loss_reason"], "hub_ncc_below_threshold")

    def test_window_outside_the_image_is_lost(self):
        template = cues.fit_hub(synthetic.render(*CENTRE), CENTRE, synthetic.RADIUS)
        result = cues.locate_hub(synthetic.render(*CENTRE), template, (500.0, 100.0), synthetic.RADIUS)
        self.assertEqual(result["diagnostics"]["loss_reason"], "coarse_center_outside_image")


if __name__ == "__main__":
    unittest.main()
