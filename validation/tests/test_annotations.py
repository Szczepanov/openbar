from __future__ import annotations

import copy
import contextlib
import io
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "validation" / "tools" / "annotations.py"
spec = importlib.util.spec_from_file_location("openbar_annotations", MODULE_PATH)
annotations = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(annotations)

MANIFEST_PATH = ROOT / "validation" / "fixtures" / "public" / "manifest.json"
EXAMPLE_PATH = ROOT / "validation" / "fixtures" / "public" / "annotations" / "synthetic-clean-side-12.annotation-v1.json"
REPEAT_PATH = ROOT / "validation" / "fixtures" / "public" / "annotations" / "synthetic-clean-side-12.repeat-b.annotation-v1.json"
METADATA_PATH = ROOT / "validation" / "examples" / "annotation-import-metadata.example.json"
CSV_PATH = ROOT / "validation" / "examples" / "annotation.example.csv"
SCHEMA_PATH = ROOT / "validation" / "schema" / "annotation-v1.schema.json"
FIXTURE_PATH = ROOT / "validation" / "fixtures" / "public" / "synthetic-clean-side-12.mp4"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class AnnotationValidationTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load(MANIFEST_PATH)
        self.example = load(EXAMPLE_PATH)

    def test_schema_is_json_and_versioned(self):
        schema = load(SCHEMA_PATH)
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)

    def test_example_validates(self):
        summary = annotations.validate_annotation(self.example, self.manifest)
        self.assertEqual(summary["sample_count"], 12)
        self.assertEqual(summary["labelled"], 10)
        self.assertEqual(summary["unlabelable"], 1)
        self.assertEqual(summary["not_annotated"], 1)

    def test_labelled_sample_requires_centre(self):
        doc = copy.deepcopy(self.example)
        del doc["samples"][0]["center_px"]
        with self.assertRaisesRegex(annotations.AnnotationError, "requires center_px"):
            annotations.validate_annotation(doc, self.manifest)

    def test_unlabelable_sample_rejects_fake_centre(self):
        doc = copy.deepcopy(self.example)
        sample = doc["samples"][7]
        sample["center_px"] = {"x_px": 0, "y_px": 0}
        with self.assertRaisesRegex(annotations.AnnotationError, "must not contain fabricated centre"):
            annotations.validate_annotation(doc, self.manifest)

    def test_not_annotated_sample_rejects_coordinates(self):
        doc = copy.deepcopy(self.example)
        sample = doc["samples"][10]
        sample["center_px"] = {"x_px": 110, "y_px": 98}
        with self.assertRaisesRegex(annotations.AnnotationError, "must not contain centre"):
            annotations.validate_annotation(doc, self.manifest)

    def test_fully_occluded_sample_cannot_be_labelled(self):
        doc = copy.deepcopy(self.example)
        sample = doc["samples"][7]
        sample.update({
            "annotation_state": "labelled",
            "quality": "low",
            "center_px": {"x_px": 107, "y_px": 82},
        })
        with self.assertRaisesRegex(annotations.AnnotationError, "fully occluded target cannot be labelled"):
            annotations.validate_annotation(doc, self.manifest)

    def test_timestamp_match_tolerance_accepts_edge_and_rejects_beyond(self):
        doc = copy.deepcopy(self.example)
        doc["samples"][1]["requested_timestamp_s"] = 0.082833
        annotations.validate_annotation(doc, self.manifest)
        doc["samples"][1]["requested_timestamp_s"] = 0.082832
        with self.assertRaisesRegex(annotations.AnnotationError, "exceeds decoder_match_tolerance"):
            annotations.validate_annotation(doc, self.manifest)

    def test_timestamps_must_be_strictly_increasing(self):
        doc = copy.deepcopy(self.example)
        doc["samples"][2]["timestamp_s"] = doc["samples"][1]["timestamp_s"]
        with self.assertRaisesRegex(annotations.AnnotationError, "strictly increasing"):
            annotations.validate_annotation(doc, self.manifest)

    def test_source_hash_must_match_manifest(self):
        doc = copy.deepcopy(self.example)
        doc["source_video_sha256"] = "0" * 64
        with self.assertRaisesRegex(annotations.AnnotationError, "does not match"):
            annotations.validate_annotation(doc, self.manifest)

    def test_committed_fixture_bytes_match_manifest_and_annotation_hash(self):
        digest = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
        fixture = next(item for item in self.manifest["fixtures"] if item["id"] == self.example["fixture_id"])
        self.assertEqual(digest, fixture["media"]["sha256"])
        self.assertEqual(digest, self.example["source_video_sha256"])

    def test_unknown_fields_are_rejected_instead_of_silently_ignored(self):
        doc = copy.deepcopy(self.example)
        doc["samples"][0]["centerr_px"] = {"x_px": 100, "y_px": 190}
        with self.assertRaisesRegex(annotations.AnnotationError, "unsupported fields"):
            annotations.validate_annotation(doc, self.manifest)

    def test_notes_must_be_strings(self):
        doc = copy.deepcopy(self.example)
        doc["samples"][0]["notes"] = 123
        with self.assertRaisesRegex(annotations.AnnotationError, "notes must be a string"):
            annotations.validate_annotation(doc, self.manifest)

    def test_fixture_id_must_follow_manifest_identifier_contract(self):
        doc = copy.deepcopy(self.example)
        fixture = copy.deepcopy(self.manifest["fixtures"][0])
        fixture["id"] = "Bad fixture"
        self.manifest["fixtures"].append(fixture)
        doc["fixture_id"] = "Bad fixture"
        with self.assertRaisesRegex(annotations.AnnotationError, "fixture_id must match"):
            annotations.validate_annotation(doc, self.manifest)

    def test_provenance_timestamp_requires_timezone(self):
        doc = copy.deepcopy(self.example)
        doc["provenance"]["annotated_at"] = "2026-10-01T10:15:00"
        with self.assertRaisesRegex(annotations.AnnotationError, "include a timezone"):
            annotations.validate_annotation(doc, self.manifest)

    def test_centre_keeps_v1_window_in_half_pixel_border(self):
        # ADR-0007: integer coordinates are pixel centres, so the raster spans
        # [-0.5, width - 0.5). v1 keeps [0, width); these points sit where the two differ.
        width = self.example["coordinate_system"]["width_px"]
        height = self.example["coordinate_system"]["height_px"]

        doc = copy.deepcopy(self.example)
        doc["samples"][0]["center_px"] = {"x_px": width - 0.25, "y_px": height - 0.25}
        annotations.validate_annotation(doc, self.manifest)

        for x, y in ((-0.25, 100.0), (100.0, -0.25)):
            doc = copy.deepcopy(self.example)
            doc["samples"][0]["center_px"] = {"x_px": x, "y_px": y}
            with self.assertRaisesRegex(annotations.AnnotationError, "must be >= 0"):
                annotations.validate_annotation(doc, self.manifest)

        doc = copy.deepcopy(self.example)
        doc["samples"][0]["center_px"] = {"x_px": float(width), "y_px": 100.0}
        with self.assertRaisesRegex(annotations.AnnotationError, "must lie inside display dimensions"):
            annotations.validate_annotation(doc, self.manifest)

    def test_rotated_fixture_uses_display_oriented_dimensions(self):
        manifest = copy.deepcopy(self.manifest)
        fixture = copy.deepcopy(manifest["fixtures"][0])
        fixture["id"] = "synthetic-rotated"
        fixture["video"]["rotation_deg"] = 90
        fixture.pop("media", None)
        manifest["fixtures"].append(fixture)

        doc = copy.deepcopy(self.example)
        doc["fixture_id"] = "synthetic-rotated"
        doc.pop("source_video_sha256", None)
        doc["coordinate_system"]["width_px"] = 240
        doc["coordinate_system"]["height_px"] = 320
        annotations.validate_annotation(doc, manifest)

        doc["coordinate_system"]["width_px"] = 320
        doc["coordinate_system"]["height_px"] = 240
        with self.assertRaisesRegex(annotations.AnnotationError, "display-oriented fixture dimensions"):
            annotations.validate_annotation(doc, manifest)

    def test_csv_import_is_deterministic_and_matches_committed_example(self):
        metadata = load(METADATA_PATH)
        imported_a = annotations.import_csv(metadata, CSV_PATH, self.manifest)
        imported_b = annotations.import_csv(metadata, CSV_PATH, self.manifest)
        self.assertEqual(imported_a, imported_b)
        self.assertEqual(imported_a, self.example)

    def test_repeatability_metrics_are_stable(self):
        result = annotations.repeatability(self.example, load(REPEAT_PATH), self.manifest)
        self.assertEqual(result["matching_labelled_samples"], 10)
        self.assertEqual(result["mean_abs_dx_px"], 0.5)
        self.assertEqual(result["mean_abs_dy_px"], 0.4)
        self.assertEqual(result["mean_euclidean_disagreement_px"], 0.9)
        self.assertEqual(result["rmse_euclidean_disagreement_px"], 0.948683)
        self.assertEqual(result["max_euclidean_disagreement_px"], 1.0)

    def test_csv_import_rejects_extra_cells(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            malformed = Path(temp_dir) / "annotation.csv"
            lines = CSV_PATH.read_text(encoding="utf-8").splitlines()
            lines[1] += ",unexpected"
            malformed.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(annotations.AnnotationError, "exactly the documented number of cells"):
                annotations.import_csv(load(METADATA_PATH), malformed, self.manifest)

    def test_cli_import_writes_stable_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "annotation.json"
            rc = annotations.main([
                "import-csv",
                "--metadata", str(METADATA_PATH),
                "--csv", str(CSV_PATH),
                "--manifest", str(MANIFEST_PATH),
                "--output", str(output),
            ])
            self.assertEqual(rc, 0)
            self.assertEqual(load(output), self.example)


class SeedTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("label_package", ROOT / "validation/tools/label_package.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.manifest = load(MANIFEST_PATH)
        self.metadata = module.metadata(self.manifest["fixtures"][0], (320, 240), "seed", "single frame")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.csv = Path(self.temp.name) / "seed.csv"
        self.header = "timestamp_s,requested_timestamp_s,frame_index,annotation_state,visibility,quality,x_px,y_px,radius_px,diameter_px,left_px,top_px,right_px,bottom_px,notes"
        self.row = "0.000000,,0,labelled,visible,high,100.00,190.00,24.00,,,,,,"
        self.write_rows(self.row)

    def write_rows(self, *rows):
        self.csv.write_text(self.header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")

    def build(self, confidence=None):
        return annotations.build_seed(self.metadata, self.csv, self.manifest, confidence)

    def cli(self, output, *extra):
        metadata = Path(self.temp.name) / "metadata.json"
        metadata.write_text(json.dumps(self.metadata), encoding="utf-8")
        return annotations.main(["seed", "--manifest", str(MANIFEST_PATH), "--metadata", str(metadata),
                                 "--csv", str(self.csv), "--output", str(output), *extra])

    def test_seed_reproduces_committed_synthetic_seed_except_notes(self):
        expected = load(ROOT / "validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json")
        actual = self.build(1.0)
        del actual["seed"]["notes"]
        del expected["seed"]["notes"]
        self.assertEqual(actual, expected)
        self.assertEqual(self.metadata["provenance"]["annotated_at"], "FILL-AT-IMPORT")

    def test_seed_is_byte_identical_for_same_csv(self):
        first, second = Path(self.temp.name) / "a.json", Path(self.temp.name) / "b.json"
        self.assertEqual(self.cli(first), 0)
        self.assertEqual(self.cli(second), 0)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertTrue(first.read_bytes().endswith(b"\n"))
        self.assertNotIn(b"\r", first.read_bytes())
        self.assertEqual(list(load(first)), ["schema_version", "fixture_id", "seed"])
        self.assertNotIn("selection_confidence", load(first)["seed"])

    def test_seed_notes_record_csv_sha256_and_annotator(self):
        self.assertEqual(self.build()["seed"]["notes"],
                         f"Manual seed from label CSV sha256={hashlib.sha256(self.csv.read_bytes()).hexdigest()}; "
                         "annotator_id=seed; tool=annotations.py seed.")

    def test_seed_picks_first_labelled_row_with_centre_and_radius(self):
        self.write_rows("0,,0,not_annotated,visible,not_assessed,,,,,,,,,",
                        "0.1,,1,labelled,visible,high,100,190,,,,,,,",
                        self.row.replace("0.000000,,0", "0.2,,2"),
                        self.row.replace("0.000000,,0", "0.3,,3"))
        self.assertEqual(self.build()["seed"]["timestamp_s"], 0.2)
        self.assertEqual(self.build()["seed"]["frame_index"], 2)

    def test_seed_rejects_no_candidate(self):
        self.write_rows("0,,0,labelled,visible,high,100,190,,,,,,,")
        with self.assertRaisesRegex(annotations.AnnotationError, "no labelled sample with centre and radius"):
            self.build()

    def test_seed_rejects_invalid_radius(self):
        for value, message in [("0", "must be > 0"), ("-1", "must be > 0"), ("nan", "must be finite"), ("inf", "must be finite")]:
            with self.subTest(value=value):
                self.write_rows(self.row.replace("24.00", value))
                with self.assertRaisesRegex(annotations.AnnotationError, message):
                    self.build()

    def test_seed_rejects_centres_outside_display(self):
        for value, message in [("320,190", "inside display dimensions"), ("100,240", "inside display dimensions"),
                               ("-0.1,190", "must be >= 0")]:
            with self.subTest(value=value):
                self.write_rows(self.row.replace("100.00,190.00", value))
                with self.assertRaisesRegex(annotations.AnnotationError, message):
                    self.build()

    def test_seed_rejects_circle_crossing_frame_edges(self):
        for value in ["10,190", "100,230", "100,10", "310,190"]:
            with self.subTest(value=value):
                self.write_rows(self.row.replace("100.00,190.00", value))
                with self.assertRaisesRegex(annotations.AnnotationError, "circle must fit within display dimensions"):
                    self.build()

    def test_seed_accepts_circle_touching_frame_edge(self):
        self.write_rows(self.row.replace("100.00,190.00", "24,216"))
        self.assertEqual(self.build()["seed"]["target"]["center"], {"x_px": 24.0, "y_px": 216.0})

    def test_seed_rejects_unknown_fixture(self):
        self.metadata["fixture_id"] = "missing"
        with self.assertRaisesRegex(annotations.AnnotationError, "exactly one manifest fixture"):
            self.build()

    def test_seed_rejects_mismatched_or_missing_video_hash(self):
        for value in ["0" * 64, None]:
            with self.subTest(value=value):
                self.metadata["source_video_sha256"] = value
                with self.assertRaisesRegex(annotations.AnnotationError, "source_video_sha256"):
                    self.build()

    def test_seed_rejects_wrong_coordinate_constants_and_dimensions(self):
        original = copy.deepcopy(self.metadata)
        for key, value in [("space", "encoded_pixels"), ("origin", "bottom_left"), ("x_direction", "left"),
                           ("y_direction", "up"), ("rotation_applied", False), ("width_px", 240), ("height_px", 320)]:
            with self.subTest(key=key):
                self.metadata = copy.deepcopy(original)
                self.metadata["coordinate_system"][key] = value
                with self.assertRaisesRegex(annotations.AnnotationError, "coordinate"):
                    self.build()

    def test_seed_uses_manifest_rotation_and_display_dimensions(self):
        for rotation in [90, 180, 270]:
            with self.subTest(rotation=rotation):
                self.manifest["fixtures"][0]["video"]["rotation_deg"] = rotation
                width, height = (240, 320) if rotation in {90, 270} else (320, 240)
                self.metadata["coordinate_system"].update(width_px=width, height_px=height)
                self.assertEqual(self.build()["seed"]["source_rotation_deg"], rotation)

    def test_seed_rejects_invalid_selection_confidence(self):
        for value in [1.5, -0.1, float("nan"), float("inf")]:
            with self.subTest(value=value), self.assertRaisesRegex(annotations.AnnotationError, "selection_confidence"):
                self.build(value)

    def test_seed_requires_frame_index(self):
        self.write_rows(self.row.replace(",,0,", ",,,"))
        with self.assertRaisesRegex(annotations.AnnotationError, "frame_index is required"):
            self.build()

    def test_seed_validates_all_rows(self):
        for row, message in [(self.row, "strictly increasing"),
                             (self.row.replace("0.000000,,0", "0.1,,1").replace("high", "not_assessed"), "quality"),
                             ("0.1,,1,not_annotated,visible,not_assessed,100,190,24,,,,,,", "must not contain centre")]:
            with self.subTest(row=row):
                self.write_rows(self.row, row)
                with self.assertRaisesRegex(annotations.AnnotationError, message):
                    self.build()

    def test_seed_refuses_overwrite_without_force(self):
        output = Path(self.temp.name) / "seed.json"
        output.write_bytes(b"existing seed")
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(self.cli(output), 2)
        self.assertIn("already exists", stderr.getvalue())
        self.assertEqual(output.read_bytes(), b"existing seed")
        self.assertEqual(self.cli(output, "--force"), 0)

    def test_seed_rejects_malformed_csv_header(self):
        self.csv.write_text("timestamp_s,x_px\n0,100\n", encoding="utf-8")
        with self.assertRaisesRegex(annotations.AnnotationError, "CSV header"):
            self.build()

    def test_seed_rejects_unterminated_csv_quote(self):
        self.write_rows(self.row + '"unterminated',
                        "0.1,,1,not_annotated,visible,not_assessed,100,190,24,,,,,,")
        with self.assertRaisesRegex(annotations.AnnotationError, "malformed CSV"):
            self.build()

    def test_seed_accepts_quoted_multiline_notes(self):
        self.write_rows(self.row + '"first line\nsecond line"')
        self.assertEqual(self.build()["seed"]["frame_index"], 0)

    def test_seed_frame_index_fits_rust_u64(self):
        self.write_rows(self.row.replace(",,0,", f",,{2**64 - 1},"))
        self.assertEqual(self.build()["seed"]["frame_index"], 2**64 - 1)
        self.write_rows(self.row.replace(",,0,", f",,{2**64},"))
        with self.assertRaisesRegex(annotations.AnnotationError, "frame_index must fit u64"):
            self.build()

    def test_seed_rejects_u64_overflow_in_any_row(self):
        overflow = "0,,0,not_annotated,visible,not_assessed,,,,,,,,,".replace(
            "0,,0", f"0.1,,{2**64}"
        )
        self.write_rows(self.row, overflow)
        with self.assertRaisesRegex(annotations.AnnotationError, r"samples\[1\]\.frame_index must fit u64"):
            self.build()

    def test_seed_rejects_non_utf8_csv(self):
        self.csv.write_bytes(b"\xff")
        with self.assertRaisesRegex(annotations.AnnotationError, "UTF-8"):
            self.build()

    def test_seed_output_passes_schema_check(self):
        spec = importlib.util.spec_from_file_location("schema_check", ROOT / "validation/tools/schema_check.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        schema = module.load_schema(ROOT / "validation/schema/manual-target-seed-v1.schema.json")
        self.assertEqual(module.validate_document(self.build(1.0), schema), [])


if __name__ == "__main__":
    unittest.main()
