"""Deterministic assessment orchestration; real canonical reader checked separately in CLI tests."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import assess_clip as assessment
import analyze_lift as workflow


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        (workflow.ROOT / "target").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=workflow.ROOT / "target")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.video = self.directory / "video.mp4"
        self.video.write_bytes(b"retained source")
        self.fixture = workflow.fixture_id_for(workflow.file_sha256(self.video))
        self.cli = self.directory / "openbar-cli"
        self.cli.write_bytes(b"validator")
        self.files = workflow.output_paths(self.directory, self.fixture, "csrt")
        self.seed = self.write("seed.json", {"schema_version": 1, "fixture_id": self.fixture,
            "seed": {"timestamp_s": 0.0, "frame_index": 0,
                     "target": {"center": {"x_px": 100.0, "y_px": 100.0}, "radius_px": 24.0},
                     "coordinate_space": "display_top_left", "source_rotation_deg": 0}})
        self.entry = {"id": self.fixture, "media": {"sha256": workflow.file_sha256(self.video)}}
        self.manifest = self.write("manifest.json", {"fixtures": [self.entry]})
        self.files["prediction"].write_bytes(b"retained prediction")
        document = {"identity": {"fixture_id": self.fixture, "source_sha256": workflow.file_sha256(self.video)},
                    "manual_seed": workflow.schema_check.load_strict(self.seed)["seed"],
                    "provenance": {"tracker": {"id": "opencv-csrt", "implementation": {"parameters": {
                        "prediction_sha256": workflow.file_sha256(self.files["prediction"])}}}}}
        self.files["analysis"].write_text(json.dumps(document), encoding="utf-8")
        self.record = {"format": workflow.RUN_RECORD_FORMAT, "format_version": workflow.RUN_RECORD_FORMAT_VERSION,
                       "workflow_version": workflow.WORKFLOW_VERSION, "fixture_id": self.fixture,
                       "configuration": {"tracker": "csrt", "tracker_implementation": "opencv-csrt"},
                       "inputs": {"video": self.input(self.video), "seed": self.input(self.seed),
                                  "manifest": {"path": workflow.display_path(self.manifest)},
                                  "manifest_entry": {"sha256": workflow.canonical_sha256(self.entry)}},
                       "outputs": {name: {"file": path.name, "sha256": workflow.file_sha256(path)}
                                   for name, path in self.files.items() if name != "run_record"}}
        self.save_record()
        patcher = mock.patch.object(workflow, "load_manifest_fixtures", return_value=[self.entry])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []

    def write(self, name, document):
        path = self.directory / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def input(self, path):
        return {"path": workflow.display_path(path), "sha256": workflow.file_sha256(path)}

    def save_record(self):
        self.files["run_record"].write_text(json.dumps(self.record), encoding="utf-8")

    def valid(self, cli, analysis, video, seed=None):
        self.calls.append((cli, analysis, video))
        if seed is not None:
            if workflow.schema_check.load_strict(analysis)["manual_seed"] != workflow.schema_check.load_strict(seed)["seed"]:
                return assessment.check("invalid", "analysis_seed_mismatch")
        return assessment.check("valid")

    def assess(self, validator=None):
        return assessment.assess(self.files["run_record"], self.fixture, self.cli, validator or self.valid)

    def test_complete_valid_does_not_establish_suitability_or_accuracy_and_is_stable(self):
        before = {path: path.read_bytes() for path in [*self.files.values(), self.video, self.seed, self.manifest]}
        first = self.assess()
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(self.assess(), sort_keys=True))
        self.assertEqual(first["processing"], assessment.check("complete"))
        self.assertEqual(first["mechanical"]["status"], "valid")
        self.assertEqual(first["experiment_suitability"]["status"], "unknown")
        self.assertEqual(first["accuracy"]["status"], "not_established")
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual(self.calls[2][2], self.video)

    def test_missing_record_and_missing_output_are_incomplete(self):
        self.files["analysis"].unlink()
        result = self.assess()
        self.assertEqual(result["processing"]["status"], "incomplete")
        self.assertEqual(result["mechanical"]["status"], "unknown")
        self.files["run_record"].unlink()
        result = self.assess()
        self.assertEqual(result["processing"], assessment.check("incomplete", "run_record_missing"))
        self.assertIsNone(result["sources"]["run_record"]["sha256"])

    def test_complete_processing_can_be_mechanically_invalid(self):
        result = self.assess(lambda cli, analysis, video: assessment.check("invalid", "canonical_analysis_invalid"))
        self.assertEqual(result["processing"]["status"], "complete")
        self.assertEqual(result["mechanical"]["status"], "invalid")
        self.assertEqual(result["experiment_suitability"], assessment.check("rejected", "mechanical_invalid"))

    def test_hash_mismatch_and_wrong_clip_rejected(self):
        self.video.write_bytes(b"different source")
        self.assertEqual(self.assess()["mechanical"]["checks"]["source_binding"]["status"], "invalid")
        self.record["fixture_id"] = "another-clip"
        self.save_record()
        self.assertEqual(self.assess()["processing"]["status"], "unknown")

    def test_missing_source_and_validator_stay_unknown(self):
        self.video.unlink()
        result = self.assess()
        self.assertEqual(result["processing"]["status"], "complete")
        self.assertEqual(result["mechanical"]["status"], "unknown")
        self.assertEqual(result["experiment_suitability"]["status"], "unknown")
        with mock.patch.object(assessment.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(assessment.validate(self.cli, self.files["analysis"], None),
                             assessment.check("unknown", "validator_unavailable"))

    def test_unsupported_and_unavailable_probe_are_distinct(self):
        for returncode, expected in [(4, "invalid"), (3, "unknown")]:
            with self.subTest(returncode=returncode), mock.patch.object(assessment.subprocess, "run",
                    return_value=mock.Mock(returncode=returncode)):
                result = self.assess(lambda cli, analysis, video, seed=None: self.valid(cli, analysis, video, seed)
                                     if video is None else assessment.validate(cli, analysis, video))
                self.assertEqual(result["mechanical"]["status"], expected)
                self.assertEqual(result["accuracy"]["status"], "not_established")

    def test_invalid_and_future_records_fail_closed(self):
        good = copy.deepcopy(self.record)
        for replacement in [["not", "a", "record"], {**good, "format_version": 3},
                            {**good, "format_version": 2.0}, {**good, "outputs": ["analysis", "prediction"]},
                            {**good, "outputs": {**good["outputs"], "analysis": {"file": "../outside", "sha256": "a" * 64}}}]:
            with self.subTest(replacement=replacement):
                self.record = replacement
                self.save_record()
                self.assertEqual(self.assess()["mechanical"]["status"], "invalid")

    def test_assessment_detects_changes_during_validation(self):
        def changing(cli, analysis, video, seed=None):
            self.video.write_bytes(b"changed during assessment")
            return assessment.check("valid")
        result = self.assess(changing)
        self.assertEqual(result["mechanical"]["checks"]["source_binding"],
                         assessment.check("invalid", "evidence_changed_during_assessment"))

    def test_embedded_null_paths_are_reason_coded_invalid_records(self):
        original = copy.deepcopy(self.record)
        for name in ("video", "seed", "manifest", "analysis"):
            self.record = copy.deepcopy(original)
            if name == "analysis":
                self.record["outputs"][name]["file"] = "\0invalid"
            else:
                self.record["inputs"][name]["path"] = "\0invalid"
            self.save_record()
            with self.subTest(name=name):
                self.assertEqual(self.assess()["mechanical"]["checks"]["run_binding"],
                                 assessment.check("invalid", "run_record_invalid"))

    def test_analysis_prediction_and_seed_binding(self):
        document = workflow.schema_check.load_strict(self.files["analysis"])
        document["manual_seed"]["timestamp_s"] = 0.1
        self.files["analysis"].write_text(json.dumps(document), encoding="utf-8")
        self.record["outputs"]["analysis"]["sha256"] = workflow.file_sha256(self.files["analysis"])
        self.save_record()
        self.assertEqual(self.assess()["mechanical"]["checks"]["source_binding"],
                         assessment.check("invalid", "analysis_seed_mismatch"))
        document["identity"]["fixture_id"] = "wrong-clip"
        self.files["analysis"].write_text(json.dumps(document), encoding="utf-8")
        self.assertEqual(self.assess()["mechanical"]["checks"]["source_binding"],
                         assessment.check("invalid", "analysis_run_binding_mismatch"))

    def test_schema_refuses_accuracy_and_suitability_promotion(self):
        result = self.assess()
        for name in ("accuracy", "experiment_suitability"):
            changed = copy.deepcopy(result)
            changed[name]["status"] = "validated"
            self.assertTrue(workflow.schema_check.validate_document(changed, workflow.schema_check.load_schema(assessment.SCHEMA)))

    def test_absent_optional_canonical_hash_cannot_pass_binding(self):
        document = workflow.schema_check.load_strict(self.files["analysis"])
        document["identity"]["source_sha256"] = None
        self.files["analysis"].write_text(json.dumps(document), encoding="utf-8")
        self.record["outputs"]["analysis"]["sha256"] = workflow.file_sha256(self.files["analysis"])
        self.save_record()
        self.assertEqual(self.assess()["mechanical"]["checks"]["source_binding"],
                         assessment.check("invalid", "analysis_run_binding_mismatch"))

    def test_existing_output_never_overwritten(self):
        output = self.directory / "assessment.json"
        output.write_bytes(b"keep")
        with mock.patch.object(assessment, "validate", self.valid):
            self.assertEqual(assessment.main(["--run-record", str(self.files["run_record"]),
                "--fixture-id", self.fixture, "--openbar-cli", str(self.cli), "--output", str(output)]), 2)
        self.assertEqual(output.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
