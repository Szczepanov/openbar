from __future__ import annotations

import copy
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


if __name__ == "__main__":
    unittest.main()
