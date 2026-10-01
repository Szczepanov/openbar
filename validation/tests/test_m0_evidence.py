from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "validation" / "tools" / "m0_evidence.py"
spec = importlib.util.spec_from_file_location("openbar_m0_evidence", MODULE_PATH)
assert spec and spec.loader
m0_evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m0_evidence)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = {}
        self.write(
            "manifest",
            "manifest.json",
            {
                "schema_version": 1,
                "fixtures": [
                    {
                        "id": "synthetic-clean-side-12",
                        "exercise": "clean",
                        "purpose": "development",
                        "video": {"nominal_fps": 12, "measured_fps": 12},
                        "camera": {"view": "side"},
                        "source": {"kind": "synthetic"},
                    }
                ],
            },
        )
        self.write(
            "annotation",
            "annotation.json",
            {"schema_version": 1, "fixture_id": "synthetic-clean-side-12"},
        )
        self.write(
            "benchmark",
            "benchmark.json",
            {
                "schema_version": 1,
                "git_commit": "abc123",
                "cases": [
                    {
                        "case_id": "decoded-template",
                        "fixture_id": "synthetic-clean-side-12",
                        "implementation": {"name": "template-sad-v1"},
                        "metrics": {
                            "plate_center_mae_px": 1.2,
                            "plate_center_rmse_px": 1.4,
                            "tracking_availability": 1.0,
                            "lost_frame_percentage": 0.0,
                            "max_consecutive_tracking_loss_samples": 0,
                        },
                        "runtime": {"media_seconds_per_wall_second": 2.0},
                    }
                ],
            },
        )
        self.write("tracker_experiment", "tracker.json", {"schema_version": 1})
        self.write("filter_experiment", "filter.json", {"schema_version": 3})
        analysis = {
            "schema_version": 1,
            "provenance": {"pipeline": {"git_commit": "abc123"}},
        }
        self.write("analysis", "analysis.json", analysis)
        self.write("analysis_repeat", "analysis-repeat.json", analysis)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, key, name, document):
        path = self.root / name
        path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
        self.paths[key] = path

    def args(self):
        return argparse.Namespace(
            manifest=self.paths["manifest"],
            annotation=[self.paths["annotation"]],
            decoded_tracker_benchmark=self.paths["benchmark"],
            tracker_experiment=self.paths["tracker_experiment"],
            filter_experiment=self.paths["filter_experiment"],
            analysis=self.paths["analysis"],
            analysis_repeat=self.paths["analysis_repeat"],
            output_json=self.root / "out.json",
            output_markdown=self.root / "out.md",
        )

    def test_synthetic_only_keeps_accuracy_gates_unmeasurable(self):
        evidence = m0_evidence.build(self.args())
        statuses = {item["id"]: item["status"] for item in evidence["gate_statuses"]}
        self.assertEqual(evidence["overall_status"], "BLOCKED_ON_REFERENCE_EVIDENCE")
        self.assertEqual(statuses["repeat_analysis_determinism"], "PASS")
        self.assertEqual(statuses["plate_center_tracking_mae"], "NOT_MEASURABLE_YET")
        self.assertEqual(statuses["tracking_availability"], "NOT_MEASURABLE_YET")
        self.assertEqual(
            evidence["dataset_coverage"]["held_out_real_annotated_fixture_count"], 0
        )
        self.assertEqual(
            evidence["synthetic_tracker_diagnostics"][0]["implementation"],
            "template-sad-v1",
        )

    def test_non_identical_analysis_fails_determinism_gate(self):
        repeat = json.loads(self.paths["analysis_repeat"].read_text(encoding="utf-8"))
        repeat["extra"] = "different"
        self.paths["analysis_repeat"].write_text(json.dumps(repeat) + "\n", encoding="utf-8")
        evidence = m0_evidence.build(self.args())
        status = next(
            item
            for item in evidence["gate_statuses"]
            if item["id"] == "repeat_analysis_determinism"
        )
        self.assertEqual(status["status"], "FAIL")
        self.assertEqual(evidence["overall_status"], "PRECHECK_FAILED")
        self.assertFalse(evidence["analysis_determinism"]["byte_identical"])

    def test_mismatched_commit_provenance_fails_closed(self):
        benchmark = json.loads(self.paths["benchmark"].read_text(encoding="utf-8"))
        benchmark["git_commit"] = "different"
        self.paths["benchmark"].write_text(json.dumps(benchmark) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "disagree on evaluated git commit"):
            m0_evidence.build(self.args())


if __name__ == "__main__":
    unittest.main()
