#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "validation" / "tools"
sys.path.insert(0, str(TOOLS))

import scale_reference  # noqa: E402


class SegmentMeasurementTests(unittest.TestCase):
    def test_synthetic_frame_with_drawn_segment_produces_expected_scale_and_uncertainty(self):
        width_px, height_px = 320, 240
        frame = [bytearray(width_px) for _ in range(height_px)]
        for x_px in range(10, 211):
            frame[20][x_px] = 255
        self.assertEqual(frame[20][10], 255)
        self.assertEqual(frame[20][210], 255)

        measured = scale_reference.measure_segment(
            known_length_m=1.0,
            point_a={"x_px": 10.0, "y_px": 20.0},
            point_b={"x_px": 210.0, "y_px": 20.0},
            width_px=width_px,
            height_px=height_px,
        )
        self.assertEqual(measured["length_px"], 200.0)
        self.assertEqual(measured["length_uncertainty_px"], 2.0)
        self.assertEqual(measured["reference_scale_m_per_px"], 0.005)
        self.assertAlmostEqual(measured["reference_scale_lower_m_per_px"], 1.0 / 202.0)
        self.assertAlmostEqual(measured["reference_scale_upper_m_per_px"], 1.0 / 198.0)
        self.assertAlmostEqual(
            measured["reference_scale_minus_uncertainty_m_per_px"],
            0.005 - (1.0 / 202.0),
        )
        self.assertAlmostEqual(
            measured["reference_scale_plus_uncertainty_m_per_px"],
            (1.0 / 198.0) - 0.005,
        )

    def test_zero_length_is_rejected(self):
        with self.assertRaisesRegex(scale_reference.ScaleReferenceError, "zero"):
            scale_reference.measure_segment(
                known_length_m=1.0,
                point_a={"x_px": 10.0, "y_px": 20.0},
                point_b={"x_px": 10.0, "y_px": 20.0},
                width_px=320,
                height_px=240,
            )

    def test_segment_no_longer_than_uncertainty_bound_is_rejected(self):
        with self.assertRaisesRegex(scale_reference.ScaleReferenceError, "too short"):
            scale_reference.measure_segment(
                known_length_m=1.0,
                point_a={"x_px": 10.0, "y_px": 20.0},
                point_b={"x_px": 12.0, "y_px": 20.0},
                width_px=320,
                height_px=240,
            )

    def test_non_finite_values_are_rejected(self):
        cases = [
            (math.nan, {"x_px": 1.0, "y_px": 2.0}, {"x_px": 3.0, "y_px": 4.0}),
            (1.0, {"x_px": math.inf, "y_px": 2.0}, {"x_px": 3.0, "y_px": 4.0}),
            (1.0, {"x_px": 1.0, "y_px": 2.0}, {"x_px": 3.0, "y_px": -math.inf}),
        ]
        for known, a, b in cases:
            with self.subTest(known=known, a=a, b=b), self.assertRaises(scale_reference.ScaleReferenceError):
                scale_reference.measure_segment(
                    known_length_m=known,
                    point_a=a,
                    point_b=b,
                    width_px=320,
                    height_px=240,
                )

    def test_points_outside_adr_0007_v1_window_are_rejected(self):
        for point in [
            {"x_px": -0.01, "y_px": 5.0},
            {"x_px": 320.0, "y_px": 5.0},
            {"x_px": 5.0, "y_px": -0.01},
            {"x_px": 5.0, "y_px": 240.0},
        ]:
            with self.subTest(point=point), self.assertRaisesRegex(scale_reference.ScaleReferenceError, "window"):
                scale_reference.measure_segment(
                    known_length_m=1.0,
                    point_a=point,
                    point_b={"x_px": 10.0, "y_px": 10.0},
                    width_px=320,
                    height_px=240,
                )

    def test_non_positive_known_length_is_rejected(self):
        for value in [0.0, -1.0]:
            with self.subTest(value=value), self.assertRaisesRegex(scale_reference.ScaleReferenceError, "known length"):
                scale_reference.measure_segment(
                    known_length_m=value,
                    point_a={"x_px": 1.0, "y_px": 2.0},
                    point_b={"x_px": 3.0, "y_px": 4.0},
                    width_px=320,
                    height_px=240,
                )


class RatioTests(unittest.TestCase):
    def test_ratio_uses_analysis_plate_scale_exactly(self):
        analysis = {
            "schema_version": 1,
            "identity": {
                "fixture_id": "clip",
                "source_sha256": "a" * 64,
            },
            "calibration": {
                "method": "plate_diameter",
                "method_version": 1,
                "scale": {"metres_per_pixel": 0.0025},
            },
        }
        plate_scale = scale_reference.plate_scale_from_analysis(analysis, "clip", "a" * 64)
        self.assertEqual(plate_scale, 0.0025)

        comparison = scale_reference.compare_to_plate(
            reference_scale_m_per_px=0.00255,
            reference_scale_lower_m_per_px=0.00250,
            reference_scale_upper_m_per_px=0.00260,
            plate_scale_m_per_px=plate_scale,
        )
        self.assertEqual(comparison["value"], 1.02)
        self.assertEqual(comparison["lower"], 1.0)
        self.assertEqual(comparison["upper"], 1.04)
        self.assertAlmostEqual(comparison["minus_uncertainty"], 0.02)
        self.assertAlmostEqual(comparison["plus_uncertainty"], 0.02)
        self.assertTrue(comparison["consistent_with_1"])

    def test_analysis_fixture_or_source_mismatch_is_rejected(self):
        base = {
            "schema_version": 1,
            "identity": {"fixture_id": "clip", "source_sha256": "a" * 64},
            "calibration": {
                "method": "plate_diameter",
                "method_version": 1,
                "scale": {"metres_per_pixel": 0.0025},
            },
        }
        with self.assertRaisesRegex(scale_reference.ScaleReferenceError, "fixture"):
            scale_reference.plate_scale_from_analysis(base, "other", "a" * 64)
        with self.assertRaisesRegex(scale_reference.ScaleReferenceError, "source_sha256"):
            scale_reference.plate_scale_from_analysis(base, "clip", "b" * 64)

    def test_analysis_version_and_calibration_method_fail_closed(self):
        base = {
            "schema_version": 1,
            "identity": {"fixture_id": "clip", "source_sha256": "a" * 64},
            "calibration": {
                "method": "plate_diameter",
                "method_version": 1,
                "scale": {"metres_per_pixel": 0.0025},
            },
        }
        for path, value, message in [
            (("schema_version",), 2, "analysis-v1"),
            (("calibration", "method"), "other", "plate_diameter"),
            (("calibration", "method_version"), 2, "method_version"),
        ]:
            changed = json.loads(json.dumps(base))
            target = changed
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path), self.assertRaisesRegex(scale_reference.ScaleReferenceError, message):
                scale_reference.plate_scale_from_analysis(changed, "clip", "a" * 64)

    def test_analysis_numeric_overflow_is_rejected_as_invalid(self):
        analysis = {
            "schema_version": 1,
            "identity": {"fixture_id": "clip", "source_sha256": "a" * 64},
            "calibration": {
                "method": "plate_diameter",
                "method_version": 1,
                "scale": {"metres_per_pixel": 10 ** 10000},
            },
        }
        with self.assertRaisesRegex(scale_reference.ScaleReferenceError, "finite"):
            scale_reference.plate_scale_from_analysis(analysis, "clip", "a" * 64)


class CsvTests(unittest.TestCase):
    def valid_csv(self) -> bytes:
        return (
            "fixture_id,source_video_sha256,package_id,frame_index,timestamp_s,width_px,height_px,"
            "known_length_m,point_a_x_px,point_a_y_px,point_b_x_px,point_b_y_px\n"
            f"clip,{'a' * 64},0123456789abcdef,7,0.250000,320,240,1.0,10,20,210,20\n"
        ).encode()

    def test_csv_hashes_exact_bytes_once_and_parses(self):
        data = self.valid_csv()
        record, digest = scale_reference.parse_click_csv(data)
        self.assertEqual(digest, hashlib.sha256(data).hexdigest())
        self.assertEqual(record["frame_index"], 7)
        self.assertEqual(record["point_b_x_px"], 210.0)

    def test_malformed_csv_is_rejected(self):
        bad = [
            b"fixture_id,frame_index\nclip,7\n",
            self.valid_csv() + b"clip," + b"a" * 64 + b",x,8,0.3,320,240,1,1,1,2,2\n",
            self.valid_csv().replace(b",210,20\n", b",nan,20\n"),
        ]
        for data in bad:
            with self.subTest(data=data[:60]), self.assertRaises(scale_reference.ScaleReferenceError):
                scale_reference.parse_click_csv(data)


class PackageBindingTests(unittest.TestCase):
    def test_package_and_click_must_match_fixture_video_and_selected_frame(self):
        metadata = {
            "fixture_id": "clip",
            "source_video_sha256": "a" * 64,
            "coordinate_system": {"width_px": 320, "height_px": 240},
        }
        config = {
            "fixture_id": "clip",
            "source_video_sha256": "a" * 64,
            "package_id": "0123456789abcdef",
            "width_px": 320,
            "height_px": 240,
            "known_length_m": 1.0,
            "frames": [{"frame_index": 7, "timestamp_s": 0.25, "file": "frames/frame_000007.png"}],
        }
        click = {
            "fixture_id": "clip",
            "source_video_sha256": "a" * 64,
            "package_id": "0123456789abcdef",
            "frame_index": 7,
            "timestamp_s": 0.25,
            "width_px": 320,
            "height_px": 240,
            "known_length_m": 1.0,
            "point_a_x_px": 10.0,
            "point_a_y_px": 20.0,
            "point_b_x_px": 210.0,
            "point_b_y_px": 20.0,
        }
        scale_reference.validate_package_binding(metadata, config, click, "clip", "a" * 64)
        for field, value, message in [
            ("fixture_id", "other", "fixture"),
            ("source_video_sha256", "b" * 64, "video"),
            ("frame_index", 8, "frame"),
        ]:
            changed = dict(click)
            changed[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(scale_reference.ScaleReferenceError, message):
                scale_reference.validate_package_binding(metadata, config, changed, "clip", "a" * 64)


class DeterminismTests(unittest.TestCase):
    def test_report_is_byte_identical_across_runs_and_ratio_is_never_naked(self):
        row = {
            "fixture_id": "clip",
            "source_video_sha256": "a" * 64,
            "analysis_sha256": "b" * 64,
            "click_csv_sha256": "c" * 64,
            "reference": {
                "frame_index": 7,
                "timestamp_s": 0.25,
                "known_length_m": 1.0,
                "point_a": {"x_px": 10.0, "y_px": 20.0},
                "point_b": {"x_px": 210.0, "y_px": 20.0},
                "length_px": 200.0,
                "endpoint_precision_px": 1.0,
                "length_uncertainty_px": 2.0,
                "reference_scale_m_per_px": 0.005,
                "reference_scale_lower_m_per_px": 1.0 / 202.0,
                "reference_scale_upper_m_per_px": 1.0 / 198.0,
                "reference_scale_minus_uncertainty_m_per_px": 0.005 - (1.0 / 202.0),
                "reference_scale_plus_uncertainty_m_per_px": (1.0 / 198.0) - 0.005,
            },
            "plate_scale_m_per_px": 0.0051,
            "reference_to_plate_ratio": {
                "value": 0.9803921568627451,
                "lower": (1.0 / 202.0) / 0.0051,
                "upper": (1.0 / 198.0) / 0.0051,
                "minus_uncertainty": 0.9803921568627451 - ((1.0 / 202.0) / 0.0051),
                "plus_uncertainty": ((1.0 / 198.0) / 0.0051) - 0.9803921568627451,
                "consistent_with_1": True,
            },
        }
        first_json = scale_reference.render_json_report([row])
        second_json = scale_reference.render_json_report([row])
        first_md = scale_reference.render_markdown_report([row])
        second_md = scale_reference.render_markdown_report([row])
        self.assertEqual(first_json, second_json)
        self.assertEqual(first_md, second_md)
        doc = json.loads(first_json)
        self.assertIsInstance(doc["rows"][0]["reference_to_plate_ratio"], dict)
        self.assertIn(b"-", first_md)
        self.assertIn(b"/+", first_md)

    def test_report_rejects_two_rows_for_the_same_video(self):
        row = {
            "fixture_id": "clip-a",
            "source_video_sha256": "a" * 64,
            "analysis_sha256": "b" * 64,
            "click_csv_sha256": "c" * 64,
            "reference": {},
            "plate_scale_m_per_px": 0.005,
            "reference_to_plate_ratio": {},
        }
        duplicate_video = {**row, "fixture_id": "clip-b"}
        with self.assertRaisesRegex(scale_reference.ScaleReferenceError, "one row per video"):
            scale_reference.render_json_report([row, duplicate_video])


@unittest.skipUnless(shutil.which("node"), "SKIPPED: node not on PATH")
class ReferencePageTests(unittest.TestCase):
    def test_reference_page_pure_csv_region_has_stable_columns(self):
        page = (TOOLS / "scale_reference_page.html").read_text(encoding="utf-8")
        pure = re.search(r"// BEGIN scaleReferencePure.*?\n(.*?)// END scaleReferencePure", page, re.S).group(1)
        config = {
            "fixture_id": "clip",
            "source_video_sha256": "a" * 64,
            "package_id": "0123456789abcdef",
            "width_px": 320,
            "height_px": 240,
            "known_length_m": 1.0,
            "frames": [{"frame_index": 7, "timestamp_s": 0.25, "file": "frames/frame_000007.png"}],
        }
        script = pure + (
            f"const c={json.dumps(config)};"
            "process.stdout.write(referenceCsvRows(c,[{x_px:10,y_px:20},{x_px:210,y_px:20}]));"
        )
        text = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout
        lines = text.splitlines()
        self.assertEqual(
            lines[0],
            "fixture_id,source_video_sha256,package_id,frame_index,timestamp_s,width_px,height_px,"
            "known_length_m,point_a_x_px,point_a_y_px,point_b_x_px,point_b_y_px",
        )
        self.assertEqual(len(lines), 2)

    def test_reference_page_maps_clicks_from_canvas_content_box(self):
        page = (TOOLS / "scale_reference_page.html").read_text(encoding="utf-8")
        pure = re.search(r"// BEGIN scaleReferencePure.*?\n(.*?)// END scaleReferencePure", page, re.S).group(1)
        script = pure + (
            "const p=imagePointFromContentOffset(201,101,640,480,320,240);"
            "process.stdout.write(JSON.stringify({p,inside:pointInWindow(p,320,240),"
            "right:pointInWindow({x_px:319.5,y_px:10},320,240)}));"
        )
        result = json.loads(
            subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout
        )
        self.assertEqual(result["p"], {"x_px": 100.0, "y_px": 50.0})
        self.assertTrue(result["inside"])
        self.assertFalse(result["right"])


if __name__ == "__main__":
    unittest.main()
