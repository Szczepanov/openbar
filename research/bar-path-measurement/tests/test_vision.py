"""Deterministic synthetic-only measurement checks; no fixture media is read.

The generators use analytical geometry, seeded Fourier texture and independently
sampled larger-field crops. OpenCV HSV semantics were checked against its
colorspaces documentation; no third-party implementation or test data was copied.
"""

import copy
import importlib.util
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np


SPEC = importlib.util.spec_from_file_location("bar_path_vision", Path(__file__).parents[1] / "vision.py")
VISION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VISION)


def grid(size=128):
    yy, xx = np.mgrid[:size, :size]
    return xx, yy


def disk_image(center=(63.35, 62.7), radius=28, contrast=180, sector=None, outliers=False):
    xx, yy = grid()
    dx, dy = xx - center[0], yy - center[1]
    angles = np.arctan2(dy, dx)
    rim = np.full(xx.shape, float(radius))
    if outliers:
        rim[(angles > -0.3) & (angles < 0.5)] += 6
    image = 30 + contrast * (1 - np.tanh((np.hypot(dx, dy) - rim) / 0.8)) / 2
    if sector is not None:
        image[(angles >= sector[0]) & (angles <= sector[1])] = 30
    return np.repeat(np.rint(image).astype(np.uint8)[..., None], 3, axis=2)


def marker_image(center=(63.35, 62.7), radius=12, color=(0, 220, 0)):
    xx, yy = grid()
    image = np.zeros((*xx.shape, 3), dtype=np.uint8)
    image[np.hypot(xx - center[0], yy - center[1]) <= radius] = color
    return image


def texture(size=64):
    rng = np.random.default_rng(51497)
    source = rng.normal(size=(size, size))
    fy, fx = np.meshgrid(np.fft.fftfreq(size), np.fft.fftfreq(size), indexing="ij")
    smooth = np.fft.ifft2(np.fft.fft2(source) * np.exp(-35 * (fx * fx + fy * fy))).real
    return 125 + smooth * (25 / smooth.std())


def translated(image, dx, dy):
    fy, fx = np.meshgrid(np.fft.fftfreq(image.shape[0]), np.fft.fftfreq(image.shape[1]), indexing="ij")
    return np.fft.ifft2(np.fft.fft2(image) * np.exp(-2j * np.pi * (dx * fx + dy * fy))).real


def crop_texture(size=160):
    rng = np.random.default_rng(123)
    source = rng.normal(size=(size, size))
    fy, fx = np.meshgrid(np.fft.fftfreq(size), np.fft.fftfreq(size), indexing="ij")
    smooth = np.fft.ifft2(np.fft.fft2(source) * np.exp(-120 * (fx * fx + fy * fy))).real
    return 125 + smooth * (25 / smooth.std())


def sample_field(field, xx, yy):
    """Test-only bilinear sampling of a larger field, independent of the estimator."""
    ix, iy = np.floor(xx).astype(int), np.floor(yy).astype(int)
    fx, fy = xx - ix, yy - iy
    return ((1 - fx) * (1 - fy) * field[iy, ix] + fx * (1 - fy) * field[iy, ix + 1]
            + (1 - fx) * fy * field[iy + 1, ix] + fx * fy * field[iy + 1, ix + 1])


def translated_crop(dx, dy, size=64):
    """Return a finite crop shift whose newly exposed pixels come from outside the first crop."""
    field = crop_texture()
    yy, xx = np.mgrid[:size, :size]
    xx, yy = xx.astype(float) + 40.0, yy.astype(float) + 40.0
    previous = sample_field(field, xx, yy)
    current = sample_field(field, xx - dx, yy - dy)
    return previous, current


class MeasurementAssertions(unittest.TestCase):
    def assertLost(self, result, reason=None, relative=False):
        self.assertIsNone(result["delta_px" if relative else "center_px"])
        self.assertIsNone(result["confidence"])
        self.assertIn("loss_reason", result["diagnostics"])
        if reason is not None:
            self.assertEqual(result["diagnostics"]["loss_reason"], reason)
        json.dumps(result, allow_nan=False)

    def assertMeasured(self, result, expected, tolerance, relative=False):
        position = result["delta_px" if relative else "center_px"]
        self.assertIsNotNone(position, result["diagnostics"])
        self.assertLessEqual(math.hypot(position["x_px"] - expected[0], position["y_px"] - expected[1]), tolerance)
        self.assertGreater(result["confidence"], 0)
        self.assertLessEqual(result["confidence"], 1)
        self.assertNotIn("loss_reason", result["diagnostics"])
        json.dumps(result, allow_nan=False)


class RelativeShiftTests(MeasurementAssertions):
    def test_fractional_translation_signs_in_canonical_display_coordinates(self):
        previous = texture()
        for dx, dy in ((0, 0), (0.36, -0.62), (-0.74, 0.28), (3.24, 2.56), (-2.8, -3.4)):
            with self.subTest(dx=dx, dy=dy):
                current = translated(previous, dx, dy)
                self.assertMeasured(VISION.relative_shift(previous, current), (dx, dy), 0.05, relative=True)
                self.assertMeasured(VISION.relative_shift(current, previous), (-dx, -dy), 0.05, relative=True)

    def test_local_dft_grid_resolves_subpixel_without_window(self):
        previous = texture()
        for dx, dy in ((0.36, -0.62), (-4.22, 2.8)):
            result = VISION.relative_shift(previous, translated(previous, dx, dy), {"window": False})
            self.assertMeasured(result, (dx, dy), 0.031, relative=True)
            self.assertLess(result["diagnostics"]["forward_backward_px"], 1e-12)

    def test_nonperiodic_crop_translation_refines_phase_boundary_bias(self):
        for dx, dy in ((0.36, -0.62), (3.24, 2.56), (-8.3, 7.2), (11.0, 0.0)):
            with self.subTest(dx=dx, dy=dy):
                previous, current = translated_crop(dx, dy)
                result = VISION.relative_shift(previous, current)
                self.assertMeasured(result, (dx, dy), 0.05, relative=True)
                diagnostics = result["diagnostics"]
                phase = diagnostics["phase_delta_px"]
                phase_error = math.hypot(phase["x_px"] - dx, phase["y_px"] - dy)
                self.assertGreater(phase_error, 0.1)
                self.assertGreater(diagnostics["spatial_refinement_px"], 0.1)
                self.assertLessEqual(diagnostics["forward_backward_px"], VISION.REGISTRATION_CONFIG["max_forward_backward_px"])
                self.assertEqual(diagnostics["overlap_refinement_steps_px"], list(VISION.OVERLAP_REFINEMENT_STEPS_PX))

    def test_zero_shift_has_full_real_overlap(self):
        previous = texture()
        result = VISION.relative_shift(previous, previous, {"min_overlap_fraction": 1})
        self.assertMeasured(result, (0, 0), 0.001, relative=True)
        self.assertEqual(result["diagnostics"]["overlap_fraction"], 1.0)

    def test_constant_and_nearly_constant_are_lost(self):
        for previous in (np.full((64, 64), 100.0), texture() * 0.001 + 100):
            self.assertLost(VISION.relative_shift(previous, previous), "insufficient_texture", relative=True)

    def test_changed_texture_rotation_and_occlusion_do_not_emit_motion(self):
        previous = texture()
        occluded = previous.copy()
        occluded[:, 16:48] = 125
        changed = np.flipud(previous).copy()
        for current in (np.rot90(previous).copy(), occluded, changed):
            with self.subTest(kind="appearance"):
                result = VISION.relative_shift(previous, current)
                self.assertLost(result, relative=True)
                self.assertEqual(result["diagnostics"]["rotation_model"], "translation_only_correlation_gate")

    def test_repeated_texture_ambiguity(self):
        xx, yy = grid(64)
        previous = 125 + 30 * np.cos(2 * np.pi * xx / 8) + 30 * np.cos(2 * np.pi * yy / 8)
        self.assertLost(VISION.relative_shift(previous, np.roll(previous, 1, axis=1), {"window": False}),
                        "ambiguous_correlation_peak", relative=True)

    def test_shift_overlap_and_correlation_thresholds(self):
        previous = texture()
        current = translated(previous, 3.24, 2.56)
        self.assertLost(VISION.relative_shift(previous, current, {"max_shift_px": 1}), "shift_exceeds_gate", relative=True)
        self.assertLost(VISION.relative_shift(previous, current, {"min_overlap_fraction": 0.99}),
                        "insufficient_overlap", relative=True)
        self.assertLost(VISION.relative_shift(previous, current, {"min_correlation": 1}),
                        "rotation_or_appearance_change", relative=True)

    def test_forward_backward_inconsistency_explicit_gate(self):
        # Exact cross-spectrum conjugacy makes most natural phase pairs symmetric.
        # Inject a disagreement at that boundary to verify the safety branch.
        previous = texture()
        with patch.object(VISION, "_phase_translation", side_effect=[(np.array([0.2, 0.1]), 4.0),
                                                                     (np.array([-0.1, 0.1]), 4.0)]):
            result = VISION.relative_shift(previous, previous)
        self.assertLost(result, "forward_backward_gate", relative=True)
        self.assertAlmostEqual(result["diagnostics"]["phase_forward_backward_px"], math.hypot(0.1, 0.2))

    def test_registration_is_byte_stable_and_inputs_unchanged(self):
        previous, current = texture(), translated(texture(), 0.36, -0.62)
        config = copy.deepcopy(VISION.REGISTRATION_CONFIG)
        saved = (previous.copy(), current.copy(), copy.deepcopy(config))
        outputs = [json.dumps(VISION.relative_shift(previous, current, config), sort_keys=True, allow_nan=False) for _ in range(3)]
        self.assertEqual(outputs, [outputs[0]] * 3)
        np.testing.assert_array_equal(previous, saved[0])
        np.testing.assert_array_equal(current, saved[1])
        self.assertEqual(config, saved[2])

    def test_invalid_gray_patches_fail_closed(self):
        valid = texture()
        invalid = (None, [], np.zeros((0, 64)), np.zeros((8, 64)), np.zeros((64, 64, 3)),
                   np.full((64, 64), np.nan), np.full((64, 64), np.inf), np.full((64, 64), -1),
                   np.full((64, 64), 256), np.zeros((64, 64), dtype=complex), np.full((64, 64), "x"))
        for image in invalid:
            with self.subTest(dtype=getattr(image, "dtype", None)):
                self.assertLost(VISION.relative_shift(image, valid), "invalid_gray_patch", relative=True)
                self.assertLost(VISION.relative_shift(valid, image), "invalid_gray_patch", relative=True)
        self.assertLost(VISION.relative_shift(valid, texture(65)), "patch_shape_mismatch", relative=True)


class RadialCenterTests(MeasurementAssertions):
    def test_subpixel_circle_and_offcenter_coarse_prediction(self):
        for center, coarse, radius in (((63.35, 62.7), (63.35, 62.7), 28),
                                       ((63.35, 62.7), (66.8, 60.5), 28),
                                       ((65.8, 58.45), (65, 60), 20)):
            with self.subTest(center=center, coarse=coarse):
                result = VISION.radial_center(disk_image(center, radius), coarse, radius)
                self.assertMeasured(result, center, 0.15)
                self.assertAlmostEqual(result["diagnostics"]["radius_px"], radius, delta=0.2)
                self.assertGreaterEqual(result["diagnostics"]["support_fraction"], 0.6)
                self.assertTrue(all(ray["width_px"] > 0 for ray in result["diagnostics"]["rays"]))

    def test_spatial_outliers_are_excluded(self):
        center = (63.35, 62.7)
        result = VISION.radial_center(disk_image(outliers=True), (65, 61), 28)
        self.assertMeasured(result, center, 0.2)
        self.assertLess(result["diagnostics"]["inlier_count"], result["diagnostics"]["edge_count"])

    def test_moderate_occlusion_retains_geometry_and_reduces_confidence(self):
        clean = VISION.radial_center(disk_image(), (63.35, 62.7), 28)
        partial = VISION.radial_center(disk_image(sector=(-0.5, 0.5)), (63.35, 62.7), 28)
        self.assertMeasured(partial, (63.35, 62.7), 0.2)
        self.assertLess(partial["confidence"], clean["confidence"])
        self.assertLess(partial["diagnostics"]["support_fraction"], clean["diagnostics"]["support_fraction"])

    def test_severe_occlusion_and_weak_contrast_are_lost(self):
        self.assertLost(VISION.radial_center(disk_image(sector=(-math.pi / 2, math.pi / 2)), (63.35, 62.7), 28),
                        "insufficient_rim_support")
        self.assertLost(VISION.radial_center(disk_image(contrast=10), (63.35, 62.7), 28), "insufficient_rim_support")

    def test_radius_center_arc_residual_and_support_gates(self):
        image, coarse = disk_image(), (63.35, 62.7)
        for config in ({"min_radius_factor": 1.1}, {"max_residual_px": 0.001}):
            with self.subTest(config=config):
                self.assertLost(VISION.radial_center(image, coarse, 28, config))
        self.assertLost(VISION.radial_center(image, (68, 62.7), 28, {"max_center_offset_factor": 0.01}))
        partial = disk_image(sector=(-0.5, 0.5))
        for config in ({"min_arc_fraction": 1}, {"min_support_fraction": 1}):
            self.assertLost(VISION.radial_center(partial, coarse, 28, config))

    def test_empty_rim_and_narrow_band_loss_have_no_coordinates(self):
        self.assertLost(VISION.radial_center(np.zeros((128, 128, 3), np.uint8), (64, 64), 28),
                        "insufficient_rim_support")
        self.assertLost(VISION.radial_center(disk_image(), (64, 64), 28, {"radius_band_fraction": 0.01}),
                        "radius_band_too_narrow")

    def test_radial_result_is_reproducible_and_input_unchanged(self):
        image = disk_image(outliers=True)
        before = image.copy()
        results = [VISION.radial_center(image, (64, 62), 28) for _ in range(2)]
        self.assertEqual(results[0], results[1])
        np.testing.assert_array_equal(image, before)


class MarkerCenterTests(MeasurementAssertions):
    def test_color_segmented_disk_subpixel_fit_ignores_coarse_center(self):
        result = VISION.marker_center(marker_image(), (66, 61), 12)
        self.assertMeasured(result, (63.35, 62.7), 0.15)
        self.assertGreater(result["diagnostics"]["mask_purity"], 0.95)

    def test_wrong_color_dim_color_and_absent_marker_are_lost(self):
        for color in ((220, 0, 0), (0, 0, 220), (0, 30, 0), (90, 100, 90), (0, 0, 0)):
            self.assertLost(VISION.marker_center(marker_image(color=color), (64, 63), 12), "no_plausible_marker")

    def test_hue_wrap_selects_red(self):
        result = VISION.marker_center(marker_image(color=(0, 0, 220)), (64, 63), 12,
                                      {"hsv_lower": [170, 80, 60], "hsv_upper": [10, 255, 255]})
        self.assertMeasured(result, (63.35, 62.7), 0.15)

    def test_ring_rectangle_radius_offset_and_clipped_components_fail(self):
        xx, yy = grid()
        ring = marker_image()
        ring[np.hypot(xx - 63.35, yy - 62.7) < 6] = 0
        rectangle = np.zeros((128, 128, 3), np.uint8)
        rectangle[52:74, 53:75] = (0, 220, 0)
        for image, coarse, radius in ((ring, (64, 63), 12), (rectangle, (64, 63), 12),
                                     (marker_image(radius=5), (64, 63), 12),
                                     (marker_image(radius=19), (64, 63), 12),
                                     (marker_image(), (74, 63), 12),
                                     (marker_image(center=(4, 64)), (4, 64), 12)):
            with self.subTest(coarse=coarse, radius=radius):
                self.assertLost(VISION.marker_center(image, coarse, radius), "no_plausible_marker")

    def test_tiny_confounds_do_not_move_accepted_marker(self):
        clean = marker_image()
        confounded = clean.copy()
        confounded[45:48, 44:47] = (0, 220, 0)
        confounded[78:81, 79:82] = (0, 220, 0)
        self.assertEqual(VISION.marker_center(clean, (64, 63), 12),
                         VISION.marker_center(confounded, (64, 63), 12))

    def test_two_plausible_components_are_ambiguous_when_roi_allows_them(self):
        image = marker_image(center=(47, 64)) + marker_image(center=(80, 64))
        result = VISION.marker_center(image, (64, 64), 12,
                                      {"roi_radius_factor": 4, "max_center_offset_factor": 2})
        self.assertLost(result, "ambiguous_marker")
        self.assertEqual(result["diagnostics"]["accepted_count"], 2)

    def test_marker_quality_gates_and_reproducibility(self):
        image = marker_image()
        saved = image.copy()
        for config in ({"min_circularity": 1}, {"max_residual_px": 0.001}):
            self.assertLost(VISION.marker_center(image, (64, 63), 12, config), "no_plausible_marker")
        # A small central hole keeps contour/area valid but violates purity.
        punctured = image.copy()
        punctured[60:64, 61:65] = 0
        self.assertMeasured(VISION.marker_center(punctured, (64, 63), 12), (63.35, 62.7), 0.15)
        self.assertLost(VISION.marker_center(punctured, (64, 63), 12, {"min_purity": 1}), "no_plausible_marker")
        self.assertEqual(VISION.marker_center(image, (64, 63), 12), VISION.marker_center(image, (64, 63), 12))
        np.testing.assert_array_equal(image, saved)

    def test_contour_support_gate_rejects_square_despite_full_sector_coverage(self):
        image = np.zeros((128, 128, 3), np.uint8)
        image[52:74, 53:75] = (0, 220, 0)
        permissive = VISION.marker_center(image, (64, 63), 12, {"min_contour_fraction": 0.7})
        self.assertMeasured(permissive, (63.5, 62.5), 0.01)
        self.assertEqual(permissive["diagnostics"]["arc_fraction"], 1)
        self.assertLess(permissive["diagnostics"]["contour_fraction"], 0.9)
        self.assertLost(VISION.marker_center(image, (64, 63), 12), "no_plausible_marker")


class InputContractTests(MeasurementAssertions):
    def test_bad_bgr_and_outside_coarse_centers_are_loss(self):
        for estimator in (VISION.marker_center, VISION.radial_center):
            for image in (None, [], np.zeros((128, 128)), np.zeros((128, 128, 4), np.uint8),
                          np.zeros((4, 4, 3), np.uint8), np.zeros((128, 128, 3), float)):
                self.assertLost(estimator(image, (64, 64), 12), "invalid_bgr_image")
            for center in ((-1, 64), (128, 64), (64, 128)):
                self.assertLost(estimator(marker_image(), center, 12), "coarse_center_outside_image")

    def test_invalid_geometry_raises_value_error(self):
        for estimator in (VISION.marker_center, VISION.radial_center):
            for center, radius in ((None, 12), ((1,), 12), ((1, 2, 3), 12), ((True, 2), 12),
                                   ((math.nan, 2), 12), ((1, math.inf), 12), ((1, 2), 2.9),
                                   ((1, 2), math.nan), ((1, 2), "12"), ((1 + 2j, 2), 12),
                                   ((np.complex128(1 + 2j), 2), 12), ((1, 2), np.complex128(12 + 2j))):
                with self.subTest(center=center, radius=radius), self.assertRaises(ValueError):
                    estimator(marker_image(), center, radius)

    def test_unknown_wrong_type_nonfinite_and_impossible_configs_raise(self):
        for estimator, call_args, default in ((VISION.marker_center, (marker_image(), (64, 63), 12), VISION.MARKER_CONFIG),
                                               (VISION.radial_center, (disk_image(), (64, 63), 28), VISION.RADIAL_CONFIG),
                                               (VISION.relative_shift, (texture(), texture()), VISION.REGISTRATION_CONFIG)):
            first_numeric = "min_std" if estimator == VISION.relative_shift else "max_residual_px"
            invalid = [[], {"typo": 1}, {1: 1}, {first_numeric: True}, {first_numeric: 0},
                       {first_numeric: math.nan}, {first_numeric: math.inf}, {first_numeric: "1"},
                       {first_numeric: 1 + 2j}, {first_numeric: np.complex128(1 + 2j)}]
            if estimator == VISION.marker_center:
                invalid += [{"hsv_lower": [180, 80, 60]}, {"hsv_upper": [85, 256, 255]},
                            {"hsv_lower": [35, 80]}, {"hsv_lower": [True, 80, 60]},
                            {"hsv_lower": [35, 200, 60], "hsv_upper": [85, 100, 255]},
                            {"min_radius_factor": 1.5}, {"roi_radius_factor": 1}, {"min_purity": 1.01}]
            elif estimator == VISION.radial_center:
                invalid += [{"ray_count": 11}, {"ray_count": 72.0}, {"ray_count": 1025},
                            {"sample_step_px": 0.05}, {"sample_step_px": 3}, {"radius_band_fraction": 1},
                            {"min_support_fraction": 1.01}, {"min_radius_factor": 1.3}]
            else:
                invalid += [{"window": 1}, {"upsample_factor": 1}, {"upsample_factor": 201},
                            {"upsample_factor": 50.0}, {"min_peak_ratio": 0.9}, {"min_correlation": 1.01}]
            for config in invalid:
                with self.subTest(estimator=estimator.__name__, config=config), self.assertRaises(ValueError):
                    estimator(*call_args, config)


if __name__ == "__main__":
    unittest.main()
