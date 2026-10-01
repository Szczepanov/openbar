from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "validation" / "tools" / "schema_check.py"
spec = importlib.util.spec_from_file_location("openbar_schema_check", MODULE_PATH)
assert spec and spec.loader
schema_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schema_check)

SCHEMA_DIR = ROOT / "validation" / "schema"
ANALYSIS_SCHEMA = SCHEMA_DIR / "analysis-v1.schema.json"
GOLDEN_ANALYSIS = ROOT / "crates" / "openbar-core" / "tests" / "fixtures" / "analysis-v1.golden.json"


def errors(document, schema_path: Path = ANALYSIS_SCHEMA) -> list[str]:
    return schema_check.validate_document(document, schema_check.load_schema(schema_path))


class CatalogueTests(unittest.TestCase):
    def test_every_committed_document_matches_its_schema(self):
        self.assertEqual(schema_check.check_catalogue(), [])

    def test_every_committed_json_document_is_mapped_or_explicitly_schemaless(self):
        self.assertEqual(schema_check.uncovered_documents(), [])

    def test_every_schema_is_supported_and_versioned_2020_12(self):
        schemas = sorted(SCHEMA_DIR.glob("*.schema.json"))
        self.assertGreater(len(schemas), 0)
        for path in schemas:
            with self.subTest(schema=path.name):
                schema = schema_check.load_schema(path)
                self.assertEqual(schema["$schema"], schema_check.DRAFT_2020_12)

    def test_every_schema_is_exercised_by_the_catalogue_or_ci(self):
        exercised = set(schema_check.CATALOGUE) | {"benchmark-result-v1.schema.json", "m0-evidence-v1.schema.json"}
        self.assertEqual({path.name for path in SCHEMA_DIR.glob("*.schema.json")}, exercised)


class AnalysisSchemaTests(unittest.TestCase):
    def setUp(self):
        self.golden = schema_check.load_strict(GOLDEN_ANALYSIS)

    def test_golden_analysis_is_valid(self):
        self.assertEqual(errors(self.golden), [])

    def test_unsupported_schema_version_is_rejected(self):
        document = copy.deepcopy(self.golden)
        document["schema_version"] = 2
        self.assertEqual(errors(document), ["/schema_version: must equal 1"])

    def test_unknown_field_is_rejected(self):
        document = copy.deepcopy(self.golden)
        document["identity"]["run_id"] = "random"
        self.assertEqual(errors(document), ["/identity/run_id: unknown property 'run_id'"])

    def test_lost_observation_with_coordinate_is_rejected(self):
        document = copy.deepcopy(self.golden)
        document["raw_observations"][1]["measurement"] = copy.deepcopy(
            document["raw_observations"][0]["measurement"]
        )
        self.assertEqual(errors(document), ["/raw_observations/1: matches a forbidden 'not' schema"])

    def test_lost_observation_with_bounds_is_rejected(self):
        document = copy.deepcopy(self.golden)
        document["raw_observations"][1]["target_bounds_px"] = copy.deepcopy(
            document["raw_observations"][0]["target_bounds_px"]
        )
        self.assertEqual(errors(document), ["/raw_observations/1: matches a forbidden 'not' schema"])

    def test_tracked_observation_without_coordinate_is_rejected(self):
        document = copy.deepcopy(self.golden)
        del document["raw_observations"][0]["measurement"]
        self.assertEqual(errors(document), ["/raw_observations/0: missing required property 'measurement'"])

    def test_out_of_range_confidence_is_rejected(self):
        document = copy.deepcopy(self.golden)
        document["derived"]["calibrated"]["samples"][0]["confidence"] = 1.5
        self.assertEqual(
            errors(document), ["/derived/calibrated/samples/0/confidence: 1.5 is above maximum 1"]
        )

    def test_fractional_integer_is_rejected_like_serde(self):
        document = copy.deepcopy(self.golden)
        document["manual_seed"]["frame_index"] = 60.0
        self.assertEqual(
            errors(document), ["/manual_seed/frame_index: expected type integer, got number"]
        )

    def test_fractional_integer_is_rejected_by_const_and_enum(self):
        for pointer, value, expected in (
            (("schema_version",), 1.0, "/schema_version: must equal 1"),
            (("calibration", "method_version"), 1.0, "/calibration/method_version: must equal 1"),
            (("video", "source_rotation_deg"), 0.0, "/video/source_rotation_deg: 0.0 is not one of [0, 90, 180, 270]"),
        ):
            with self.subTest(pointer=pointer):
                document = copy.deepcopy(self.golden)
                parent = document
                for key in pointer[:-1]:
                    parent = parent[key]
                parent[pointer[-1]] = value
                self.assertEqual(errors(document), [expected])

    def test_warning_quality_requires_a_warning_flag(self):
        document = copy.deepcopy(self.golden)
        document["calibration"]["quality"] = {"status": "warning", "warnings": []}
        self.assertEqual(
            errors(document), ["/calibration/quality/warnings: expected at least 1 item(s)"]
        )
        document["calibration"]["quality"]["warnings"] = ["camera_yaw"]
        self.assertEqual(errors(document), [])

    def test_trailing_newline_does_not_satisfy_anchored_pattern(self):
        document = copy.deepcopy(self.golden)
        document["identity"]["fixture_id"] = "synthetic-clean-side-12\n"
        self.assertEqual(
            errors(document),
            ["/identity/fixture_id: 'synthetic-clean-side-12\\n' does not match '^[a-z0-9][a-z0-9._-]*$'"],
        )

    def test_calibration_provenance_variants_are_exclusive(self):
        document = copy.deepcopy(self.golden)
        provenance = document["calibration"]["reference"]["provenance"]
        provenance["source"] = "manual_plate_measurement"
        self.assertEqual(
            errors(document),
            ["/calibration/reference/provenance: must match exactly one oneOf branch, matched 0"],
        )
        del provenance["selection_confidence"]
        self.assertEqual(errors(document), [])

    def test_kinematic_velocity_may_be_null_but_not_text(self):
        document = copy.deepcopy(self.golden)
        sample = {"timestamp_s": 1.0, "x_m": 0.0, "y_m": 0.0, "vx_mps": None, "vy_mps": 0.1, "confidence": 0.9}
        document["derived"]["kinematics"] = {
            "input": "calibrated",
            "method": {"implementation": "first-difference", "version": "1"},
            "samples": [sample],
        }
        self.assertEqual(errors(document), [])
        sample["vx_mps"] = "fast"
        self.assertEqual(
            errors(document),
            ["/derived/kinematics/samples/0/vx_mps: expected type ['number', 'null'], got string"],
        )

    def test_blank_parameter_name_is_rejected(self):
        document = copy.deepcopy(self.golden)
        document["provenance"]["tracker"]["implementation"]["parameters"] = {" ": 1}
        self.assertEqual(
            errors(document),
            [
                "/provenance/tracker/implementation/parameters: ' ' does not match '\\\\S'"
                " (property name ' ')"
            ],
        )


class StrictParsingTests(unittest.TestCase):
    def test_non_finite_numbers_are_rejected(self):
        for literal in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(literal=literal):
                with self.assertRaises(schema_check.DocumentError):
                    schema_check.loads_strict(f'{{"x": {literal}}}')

    def test_out_of_range_numbers_are_rejected(self):
        for literal in ("1e999", "-1e999"):
            with self.subTest(literal=literal):
                with self.assertRaises(schema_check.DocumentError):
                    schema_check.loads_strict(f'{{"x": {literal}}}')

    def test_non_utf8_file_is_a_document_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latin1.json"
            path.write_bytes(b'{"x": "\xff"}')
            with self.assertRaises(schema_check.DocumentError):
                schema_check.load_strict(path)

    def test_duplicate_keys_are_rejected(self):
        with self.assertRaises(schema_check.DocumentError):
            schema_check.loads_strict('{"x": 1, "x": 2}')

    def test_booleans_are_not_numbers(self):
        validator = schema_check.Validator({"type": "number"})
        self.assertEqual(validator.errors(True), ["/: expected type number, got boolean"])
        self.assertEqual(schema_check.Validator({"const": 1}).errors(True), ["/: must equal 1"])


class FailClosedSchemaTests(unittest.TestCase):
    def test_unknown_keyword_is_rejected_even_in_unreferenced_defs(self):
        schema = {"$defs": {"unused": {"type": "string", "multipleOf": 2}}}
        with self.assertRaises(schema_check.SchemaError):
            schema_check.audit_schema(schema)

    def test_remote_and_anchor_refs_are_rejected(self):
        for ref in ("https://example.com/schema.json", "#foo"):
            with self.subTest(ref=ref):
                with self.assertRaises(schema_check.SchemaError):
                    schema_check.audit_schema({"$ref": ref})
                with self.assertRaises(schema_check.SchemaError):
                    schema_check.Validator({"$ref": ref}).errors(1)

    def test_schemaless_entries_must_exist(self):
        self.assertEqual(schema_check.stale_schemaless(), [])

    def test_unknown_keyword_is_rejected_during_validation(self):
        with self.assertRaises(schema_check.SchemaError):
            schema_check.Validator({"dependentRequired": {}}).errors({})

    def test_cli_reports_failures_with_non_zero_exit(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(stderr):
            document = Path(directory) / "bad.json"
            document.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
            self.assertEqual(schema_check.main(["--schema", str(ANALYSIS_SCHEMA), str(document)]), 1)
            self.assertEqual(schema_check.main(["--schema", str(ANALYSIS_SCHEMA)]), 2)
        self.assertIn("/schema_version: must equal 1", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
