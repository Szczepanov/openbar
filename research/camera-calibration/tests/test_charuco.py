"""Research-only checks; use research/opencv-tracking's pinned environment."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from unittest import mock

import cv2
import numpy as np

MODULE = Path(__file__).resolve().parents[1] / "charuco.py"
spec = importlib.util.spec_from_file_location("charuco", MODULE)
cal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cal)


def board_config():
    return dict(squares_x=7, squares_y=9, square_length_m=0.04,
                marker_length_m=0.02, dictionary="DICT_6X6_250",
                measured_square_length_m=0.04, square_length_tolerance_m=0.0001,
                flatness_verified=True, print_scale_verified=True)


def camera_config():
    return dict(device="synthetic", lens="main", width_px=1280, height_px=960,
                orientation="display", zoom=1.0, focus="locked", stabilization="off",
                mode="still")


def manifest():
    return dict(schema_version=1, board=board_config(), camera=camera_config(),
                views=[], references=[], preregistration=dict(
                    frozen_before_capture=True, protocol_id="synthetic-protocol",
                    minimum_relative_improvement=0.2,
                    minimum_absolute_improvement=0.005,
                    maximum_position_regression=0.005))


def projected_views(board, distortion):
    objects = board.getChessboardCorners()
    camera = np.array([[900., 0., 640.], [0., 920., 480.], [0., 0., 1.]])
    views = []
    for i in range(15):
        rotation = np.array([(-0.5 + (i % 5) * 0.25), -0.35 + (i % 3) * 0.35, 0.05])
        translation = np.array([-0.44 + (i % 5) * 0.15, -0.35 + (i // 5) * 0.18, 0.65])
        points = cv2.projectPoints(objects, rotation, translation, camera, distortion)[0].reshape(-1, 2)
        valid = (points[:, 0] >= 0) & (points[:, 0] < 1280) & (points[:, 1] >= 0) & (points[:, 1] < 960)
        views.append(dict(id=f"view-{i}", role="fit", object_points_m=objects[valid].tolist(),
                          image_points_px=points[valid].tolist()))
    return camera, views


class CharucoTests(unittest.TestCase):
    def test_invalid_board_and_manifest_values_fail_closed(self):
        for field, value in [("square_length_m", 0), ("marker_length_m", float("nan")),
                             ("squares_x", True), ("flatness_verified", False),
                             ("measured_square_length_m", 0.05), ("dictionary", "unknown")]:
            config = board_config()
            config[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                cal.make_board(config)
        for mutate in [lambda m: m.update(schema_version=2),
                       lambda m: m.update(extra=1),
                       lambda m: m["camera"].update(zoom=float("inf")),
                       lambda m: m["camera"].update(zoom=10**1000),
                       lambda m: m["preregistration"].update(frozen_before_capture=False)]:
            value = manifest()
            mutate(value)
            with self.assertRaises(ValueError):
                cal.validate_manifest(value)

    def test_rendered_board_detection_and_missing_board(self):
        board = cal.make_board(board_config())
        image = board.generateImage((700, 900), marginSize=30)
        observation = cal.detect_view(image, board, "board", "fit")
        self.assertGreaterEqual(len(observation["image_points_px"]), 12)
        missing = cal.detect_view(np.full((900, 700), 255, np.uint8), board, "missing", "fit")
        self.assertEqual(missing["rejection_reason"], "no_charuco_corners")

    def test_known_projection_recovers_intrinsics_and_distortion(self):
        board = cal.make_board(board_config())
        distortion = np.array([-0.12, 0.03, 0.001, -0.002, 0.0])
        expected, views = projected_views(board, distortion)
        fit = cal.fit_calibration(views, (1280, 960))
        self.assertEqual(fit["status"], "accepted", fit.get("rejection_reasons"))
        np.testing.assert_allclose(fit["camera_matrix"], expected, atol=0.04)
        np.testing.assert_allclose(np.ravel(fit["distortion"]), distortion, atol=0.002)
        self.assertLess(fit["rms_px"], 0.001)
        again = cal.fit_calibration(views, (1280, 960))
        self.assertEqual(json.dumps(fit, sort_keys=True, allow_nan=False),
                         json.dumps(again, sort_keys=True, allow_nan=False))

    def test_degenerate_fit_is_rejected_and_held_out_never_fitted(self):
        board = cal.make_board(board_config())
        _, views = projected_views(board, np.zeros(5))
        held_out = copy.deepcopy(views[0])
        held_out.update(id="held", role="held_out", image_points_px=[[float("nan"), 0]])
        fit = cal.fit_calibration(views + [held_out], (1280, 960))
        self.assertEqual(fit["status"], "accepted")
        self.assertNotIn("held", fit["fit_view_ids"])
        self.assertEqual(cal.fit_calibration([], (1280, 960))["status"], "rejected")
        duplicate = [dict(views[0], id=f"duplicate-{i}") for i in range(10)]
        self.assertEqual(cal.fit_calibration(duplicate, (1280, 960))["status"], "rejected")

    def test_zero_distortion_identity_and_reference_uncertainty(self):
        camera = [[900., 0., 640.], [0., 920., 480.], [0., 0., 1.]]
        points = [[100., 200.], [100., 400.]]
        np.testing.assert_allclose(cal.correct_points(points, camera, [0] * 5), points, atol=1e-10)
        anchor = dict(id="anchor", role="anchor", position="centre", point_a_px=[640, 300],
                      point_b_px=[640, 500], known_length_m=1., length_tolerance_m=0.001,
                      endpoint_tolerance_px=0.5, plane_verified=True)
        check = dict(anchor, id="check", role="check", point_a_px=[100, 300], point_b_px=[100, 500])
        comparison = cal.compare_references([anchor, check], camera, [0] * 5, manifest()["preregistration"])
        self.assertEqual(comparison["status"], "defer_missing_captures")
        row = comparison["checks"][0]
        self.assertEqual(row["raw_endpoints_px"], [[100, 300], [100, 500]])
        np.testing.assert_allclose(row["corrected_endpoints_px"], row["raw_endpoints_px"])
        self.assertAlmostEqual(row["raw"]["estimated_length_m"], 1.)
        self.assertAlmostEqual(row["corrected"]["estimated_length_m"], 1.)
        self.assertGreater(row["corrected"]["absolute_relative_error_interval"][1], 0.)

    def test_distortion_correction_improves_independent_lengths(self):
        camera = np.array([[900., 0., 640.], [0., 900., 480.], [0., 0., 1.]])
        distortion = np.array([-0.2, 0.02, 0., 0., 0.])
        references = []
        for i, (position, x, y) in enumerate([("centre", 0, 0), ("left", -0.55, 0),
                                             ("right", 0.55, 0), ("top", 0, -0.35),
                                             ("bottom", 0, 0.35)]):
            for repeat in range(2):
                geometry = [[x, y - 0.08, 1.], [x, y + 0.08, 1.]] if repeat else [[x-0.08, y, 1.], [x+0.08, y, 1.]]
                points = cv2.projectPoints(np.array(geometry),
                                           np.zeros(3), np.zeros(3), camera, distortion)[0].reshape(-1, 2)
                references.append(dict(id=f"check-{i}-{repeat}", role="check", position=position,
                                       point_a_px=points[0].tolist(), point_b_px=points[1].tolist(),
                                       known_length_m=0.16, length_tolerance_m=0.00001,
                                       endpoint_tolerance_px=0.001, plane_verified=True))
        anchor = dict(references[0], id="anchor", role="anchor")
        result = cal.compare_references([anchor] + references, camera, distortion, manifest()["preregistration"])
        self.assertEqual(result["status"], "use_for_tested_setup", result)
        self.assertLess(result["corrected_median_absolute_relative_error"], 1e-5)
        self.assertGreater(result["raw_median_absolute_relative_error"], 0.02)
        uncertain = copy.deepcopy([anchor] + references)
        for reference in uncertain:
            reference["endpoint_tolerance_px"] = 8.
        result = cal.compare_references(uncertain, camera, distortion, manifest()["preregistration"])
        self.assertEqual(result["status"], "defer_no_useful_improvement")
        self.assertLessEqual(result["conservative_improvement"], 0.)
        result = cal.compare_references([anchor] + references, camera, np.zeros(5), manifest()["preregistration"])
        self.assertEqual(result["status"], "defer_no_useful_improvement")
        missing_direction = [anchor] + [r for r in references if r["id"] != "check-0-0"]
        result = cal.compare_references(missing_direction, camera, distortion, manifest()["preregistration"])
        self.assertEqual(result["status"], "defer_missing_captures")
        self.assertIn("horizontal_and_vertical_checks_at_centre", result["missing_inputs"])
        regressed = copy.deepcopy([anchor] + references)
        successful = cal.compare_references(regressed, camera, distortion, manifest()["preregistration"])
        for reference in regressed:
            if reference["role"] == "check" and reference["position"] == "left":
                row = next(r for r in successful["checks"] if r["id"] == reference["id"])
                reference["known_length_m"] = row["raw"]["estimated_length_m"]
        result = cal.compare_references(regressed, camera, distortion, manifest()["preregistration"])
        self.assertEqual(result["status"], "defer_no_useful_improvement")

    def test_run_missing_captures_and_hash_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest()), encoding="utf-8")
            first = cal.run(path)
            self.assertEqual(first["recommendation"], "defer_missing_captures")
            self.assertEqual(first, cal.run(path))
            self.assertEqual(first["provenance"]["manifest_sha256"], cal.digest(path))
            self.assertEqual(first["provenance"]["source_sha256"], cal.digest(MODULE))
            with mock.patch.object(cal.importlib.metadata, "version", return_value="wrong-version"):
                with self.assertRaisesRegex(ValueError, "pinned"):
                    cal.run(path)
            image = Path(directory) / "board.png"
            cv2.imwrite(str(image), cal.make_board(board_config()).generateImage((700, 900)))
            value = manifest()
            value["views"] = [dict(id="one", path="board.png", sha256="0" * 64,
                                   role="fit", camera=camera_config())]
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash"):
                cal.run(path)

    def test_camera_mismatch_and_overlapping_fit_holdout_fail(self):
        value = manifest()
        view = dict(id="one", path="one.png", sha256="0" * 64, role="fit", camera=camera_config())
        value["views"] = [view, dict(view, id="two", role="held_out")]
        with self.assertRaisesRegex(ValueError, "overlap|duplicate"):
            cal.validate_manifest(value)
        value["views"] = [view]
        view["camera"]["focus"] = "auto"
        with self.assertRaisesRegex(ValueError, "camera"):
            cal.validate_manifest(value)
        view["camera"] = camera_config()
        for path in ("../outside.png", str(MODULE.resolve())):
            view["path"] = path
            with self.assertRaisesRegex(ValueError, "relative"):
                cal.validate_manifest(value)

    def test_cli_board_bootstrap_report_determinism_and_input_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = board_config()
            config.update(flatness_verified=False, print_scale_verified=False)
            config_path = root / "board.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            args = [sys.executable, str(MODULE), "board", "--config", str(config_path), "--output", str(root / "board.png")]
            subprocess.run(args, check=True, capture_output=True)
            self.assertTrue((root / "board.png").is_file())
            source = root / "manifest.json"
            source.write_text(json.dumps(manifest()), encoding="utf-8")
            for name in ("first.json", "second.json"):
                subprocess.run([sys.executable, str(MODULE), "run", "--manifest", str(source),
                                "--output", str(root / name)], check=True, capture_output=True)
            self.assertEqual((root / "first.json").read_bytes(), (root / "second.json").read_bytes())
            original = source.read_bytes()
            result = subprocess.run([sys.executable, str(MODULE), "run", "--manifest", str(source),
                                     "--output", str(source)], capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(original, source.read_bytes())
            invalid = manifest()
            invalid["camera"]["zoom"] = 10**1000
            source.write_text(json.dumps(invalid), encoding="utf-8")
            result = subprocess.run([sys.executable, str(MODULE), "run", "--manifest", str(source),
                                     "--output", str(root / "bad-report.json")], capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn(b"Traceback", result.stderr)

    def test_reference_validation_and_strict_json(self):
        value = manifest()
        reference = dict(id="reference", role="anchor", position="centre", path="reference.png", sha256="1"*64,
                         camera=camera_config(), point_a_px=[640, 300], point_b_px=[640, 500], known_length_m=1.,
                         length_tolerance_m=0.001, endpoint_tolerance_px=0.5, plane_verified=True)
        value["references"] = [reference]
        cal.validate_manifest(value)
        for field, invalid in [("known_length_m", float("nan")), ("plane_verified", False),
                               ("length_tolerance_m", 1.), ("point_a_px", [-1, 300]),
                               ("point_b_px", [640, 300]), ("position", "left")]:
            broken = copy.deepcopy(value)
            broken["references"][0][field] = invalid
            with self.subTest(field=field), self.assertRaises(ValueError):
                cal.validate_manifest(broken)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for content in ('{"one":1,"one":2}', '{"one":NaN}'):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    cal.load_json(path)

    def test_held_out_quality_and_missing_or_rejected_run(self):
        board = cal.make_board(board_config())
        _, views = projected_views(board, np.array([-0.12, 0.03, 0.001, -0.002, 0.]))
        fit = cal.fit_calibration(views, (1280, 960))
        held = [dict(views[i], role="held_out") for i in range(3)]
        diagnostics = cal.held_out_diagnostics(held, fit)
        self.assertTrue(all(v["rms_px"] < 0.001 for v in diagnostics))
        held[0]["image_points_px"] = (np.array(held[0]["image_points_px"]) +
                                      np.random.default_rng(0).normal(0, 10, np.array(held[0]["image_points_px"]).shape)).tolist()
        self.assertGreater(cal.held_out_diagnostics(held, fit)[0]["rms_px"], 2.)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "blank.png"
            cv2.imwrite(str(image), np.full((960, 1280), 255, np.uint8))
            value = manifest()
            value["views"] = [dict(id="blank", role="fit", path=image.name,
                                   sha256=cal.digest(image), camera=camera_config())]
            path = root / "manifest.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            report = cal.run(path)
            self.assertEqual(report["recommendation"], "defer_calibration_rejected")
            self.assertEqual(report["observations"][0]["rejection_reason"], "no_charuco_corners")
            cv2.imwrite(str(image), np.full((100, 100), 255, np.uint8))
            value["views"][0]["sha256"] = cal.digest(image)
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "resolution"):
                cal.run(path)

    def test_retained_rendered_views_end_to_end(self):
        board = cal.make_board(board_config())
        pattern = board.generateImage((700, 900))
        camera = np.array([[900., 0., 640.], [0., 920., 480.], [0., 0., 1.]])
        physical = np.array([[0., 0., 0.], [0.28, 0., 0.], [0.28, 0.36, 0.], [0., 0.36, 0.]])
        source_corners = np.array([[0, 0], [699, 0], [699, 899], [0, 899]], np.float32)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = manifest()
            for i in range(18):
                pose_index = i if i < 15 else i - 10
                rotation = np.array([-0.5 + (pose_index % 5)*0.25, -0.35 + (pose_index % 3)*0.35, 0.05 + i*0.001])
                translation = np.array([-0.44 + (pose_index % 5)*0.15, -0.35 + (pose_index // 5)*0.18, 0.65])
                projected = cv2.projectPoints(physical, rotation, translation, camera, np.zeros(5))[0].reshape(-1, 2).astype(np.float32)
                image = cv2.warpPerspective(pattern, cv2.getPerspectiveTransform(source_corners, projected),
                                            (1280, 960), borderValue=255)
                path = root / f"view-{i:02}.png"
                cv2.imwrite(str(path), image)
                value["views"].append(dict(id=f"view-{i:02}", path=path.name, sha256=cal.digest(path),
                                           role="fit" if i < 15 else "held_out", camera=camera_config()))
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(value), encoding="utf-8")
            first = cal.run(manifest_path)
            self.assertEqual(first["calibration"]["status"], "accepted", first["calibration"])
            self.assertEqual(first["recommendation"], "defer_missing_captures")
            self.assertEqual(len(first["held_out_diagnostics"]), 3)
            self.assertTrue(all("rms_px" in item for item in first["held_out_diagnostics"]), first["held_out_diagnostics"])
            self.assertEqual(first, cal.run(manifest_path))


if __name__ == "__main__":
    unittest.main()
