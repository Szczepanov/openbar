"""Contract hardening tests for the per-clip VBT assessment (#111)."""
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


class AssessmentContractTests(unittest.TestCase):
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
        self.seed = self.write(
            "seed.json",
            {
                "schema_version": 1,
                "fixture_id": self.fixture,
                "seed": {
                    "timestamp_s": 0.0,
                    "frame_index": 0,
                    "target": {
                        "center": {"x_px": 100.0, "y_px": 100.0},
                        "radius_px": 24.0,
                    },
                    "coordinate_space": "display_top_left",
                    "source_rotation_deg": 0,
                },
            },
        )
        self.entry = {
            "id": self.fixture,
            "media": {"sha256": workflow.file_sha256(self.video)},
        }
        self.manifest = self.write("manifest.json", {"fixtures": [self.entry]})
        self.files["prediction"].write_bytes(b"retained prediction")
        analysis = {
            "identity": {
                "fixture_id": self.fixture,
                "source_sha256": workflow.file_sha256(self.video),
            },
            "manual_seed": workflow.schema_check.load_strict(self.seed)["seed"],
            "provenance": {
                "tracker": {
                    "id": "opencv-csrt",
                    "implementation": {
                        "parameters": {
                            "prediction_sha256": workflow.file_sha256(
                                self.files["prediction"]
                            )
                        }
                    },
                }
            },
        }
        self.files["analysis"].write_text(json.dumps(analysis), encoding="utf-8")
        self.record = {
            "format": workflow.RUN_RECORD_FORMAT,
            "format_version": workflow.RUN_RECORD_FORMAT_VERSION,
            "workflow_version": workflow.WORKFLOW_VERSION,
            "fixture_id": self.fixture,
            "configuration": {
                "tracker": "csrt",
                "tracker_implementation": "opencv-csrt",
            },
            "inputs": {
                "video": self.input(self.video),
                "seed": self.input(self.seed),
                "manifest": {"path": workflow.display_path(self.manifest)},
                "manifest_entry": {"sha256": workflow.canonical_sha256(self.entry)},
            },
            "outputs": {
                name: {"file": path.name, "sha256": workflow.file_sha256(path)}
                for name, path in self.files.items()
                if name != "run_record"
            },
        }
        self.save_record()
        patcher = mock.patch.object(
            workflow, "load_manifest_fixtures", return_value=[self.entry]
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name, document):
        path = self.directory / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def input(self, path):
        return {
            "path": workflow.display_path(path),
            "sha256": workflow.file_sha256(path),
        }

    def save_record(self):
        self.files["run_record"].write_text(json.dumps(self.record), encoding="utf-8")

    @staticmethod
    def valid(cli, analysis, video, seed=None):
        return assessment.check("valid")

    def assess(self):
        return assessment.assess(
            self.files["run_record"], self.fixture, self.cli, self.valid
        )

    def test_run_record_rejects_absolute_input_paths_that_producer_cannot_emit(self):
        self.record["inputs"]["video"]["path"] = str(self.video.resolve())
        self.save_record()

        result = self.assess()

        self.assertEqual(
            result["mechanical"]["checks"]["run_binding"],
            assessment.check("invalid", "run_record_invalid"),
        )

    def test_schema_rejects_contradictory_summary_and_reason_states(self):
        result = self.assess()
        schema = workflow.schema_check.load_schema(assessment.SCHEMA)

        contradictory_mechanical = copy.deepcopy(result)
        contradictory_mechanical["mechanical"]["checks"]["decoded_pts"] = assessment.check(
            "invalid", "source_pts_invalid"
        )
        self.assertTrue(
            workflow.schema_check.validate_document(contradictory_mechanical, schema),
            "mechanical=valid must not validate when a required check is invalid",
        )

        contradictory_processing = copy.deepcopy(result)
        contradictory_processing["processing"]["reasons"] = ["run_record_missing"]
        self.assertTrue(
            workflow.schema_check.validate_document(contradictory_processing, schema),
            "processing=complete must not carry failure reasons",
        )

        contradictory_check = copy.deepcopy(result)
        contradictory_check["mechanical"]["checks"]["run_binding"]["reasons"] = [
            "run_output_missing"
        ]
        self.assertTrue(
            workflow.schema_check.validate_document(contradictory_check, schema),
            "a valid mechanical check must not carry failure reasons",
        )


if __name__ == "__main__":
    unittest.main()
