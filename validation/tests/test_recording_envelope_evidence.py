from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "validation" / "tools"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


recording_envelope = load("recording_envelope_evidence")


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(fixture_id: str, *, purpose: str = "validation", kind: str = "self_recorded"):
    return {"id": fixture_id, "purpose": purpose, "source": {"kind": kind}}


def metrics(samples: int = 30):
    return {
        "comparable_samples": samples,
        "plate_center_mae_px": 1.0,
        "plate_center_rmse_px": 1.2,
        "plate_center_p50_px": 0.8,
        "plate_center_p90_px": 1.5,
        "plate_center_p95_px": 1.7,
        "plate_center_max_px": 2.4,
        "tracking_availability": 1.0,
        "lost_frame_percentage": 0.0,
        "max_consecutive_tracking_loss_duration_s": 0.0,
    }


def study(*, requested: str = "unknown", minima=(None, None), candidate_status: str = "frozen"):
    return {
        "schema_version": 1,
        "study_id": "test-study",
        "fixture_manifest": "manifest.json",
        "benchmark_results": ["result.json"],
        "measurement_evidence": [{
            "path": "measurement.json",
            "constructs": ["calibrated_position", "velocity"],
            "git_commit": "abc",
        }],
        "frozen_candidate": {
            "status": candidate_status,
            "git_commit": "abc",
            "pipeline_version": "m0-benchmark-v1",
            "tracker": {"name": "template-sad", "version": "1", "config": {"radius": 12}},
            "filter": {"name": "raw-identity", "version": "1", "config": {}},
        },
        "evidence_policy": {
            "eligible_purposes": ["validation"],
            "require_non_synthetic": True,
            "minimum_distinct_held_out_fixtures_per_boundary": minima[0],
            "minimum_comparable_samples_per_boundary": minima[1],
        },
        "boundaries": [{
            "id": "yaw",
            "dimension": "camera_geometry",
            "variable": "approx_yaw_deg",
            "unit": "deg",
            "tested_values": [0, 5, 10],
            "fixture_ids": ["real-a"],
            "requested_classification": requested,
            "decision_rationale": "controlled yaw sweep",
        }],
    }


def benchmark_result(*, implementation=None, commit: str = "abc"):
    implementation = implementation or {
        "name": "template-sad",
        "version": "1",
        "config": {"radius": 12},
    }
    return {
        "git_commit": commit,
        "pipeline_version": "m0-benchmark-v1",
        "benchmark_metric_version": "benchmark-metrics-v1",
        "cases": [{
            "case_id": "case-a",
            "fixture_id": "real-a",
            "implementation": implementation,
            "metrics": metrics(),
            "warnings": [],
        }],
    }


class RecordingEnvelopeEvidenceTests(unittest.TestCase):
    def build(self, *, spec=None, fixtures=None, result=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        write_json(root / "manifest.json", {"fixtures": fixtures or [fixture("real-a")]})
        write_json(root / "result.json", result or benchmark_result())
        write_json(root / "measurement.json", {"evaluated_commit": "abc", "metrics": {}})
        write_json(root / "study.json", spec or study())
        return recording_envelope.build_artifact(root / "study.json")

    def test_unknown_collects_evidence_without_promoting_boundary(self):
        artifact = self.build()
        boundary = artifact["boundaries"][0]
        self.assertEqual(boundary["classification"], "unknown")
        self.assertEqual(boundary["evidence"]["eligible_fixture_count"], 1)
        self.assertEqual(boundary["evidence"]["comparable_samples"], 30)

    def test_promotion_requires_preregistered_minima(self):
        with self.assertRaisesRegex(
            recording_envelope.EvidenceError, "minima are not preregistered"
        ):
            self.build(spec=study(requested="supported"))

    def test_promotion_requires_frozen_candidate(self):
        with self.assertRaisesRegex(
            recording_envelope.EvidenceError, "candidate is not frozen"
        ):
            self.build(
                spec=study(
                    requested="supported",
                    minima=(1, 20),
                    candidate_status="not_frozen",
                )
            )

    def test_synthetic_fixture_cannot_support_boundary(self):
        with self.assertRaisesRegex(recording_envelope.EvidenceError, "synthetic"):
            self.build(
                spec=study(requested="supported", minima=(1, 20)),
                fixtures=[fixture("real-a", kind="synthetic")],
            )

    def test_development_fixture_is_ineligible(self):
        with self.assertRaisesRegex(
            recording_envelope.EvidenceError, "ineligible purpose"
        ):
            self.build(
                spec=study(requested="warning_boundary", minima=(1, 20)),
                fixtures=[fixture("real-a", purpose="development")],
            )

    def test_candidate_mismatch_fails_closed(self):
        other = {"name": "other", "version": "1", "config": {}}
        with self.assertRaisesRegex(
            recording_envelope.EvidenceError, "different tracker candidate"
        ):
            self.build(
                spec=study(requested="unsupported", minima=(1, 20)),
                result=benchmark_result(implementation=other),
            )

    def test_commit_mismatch_fails_closed(self):
        with self.assertRaisesRegex(
            recording_envelope.EvidenceError, "does not match frozen"
        ):
            self.build(
                spec=study(requested="supported", minima=(1, 20)),
                result=benchmark_result(commit="different"),
            )

    def test_development_samples_do_not_inflate_eligible_sample_count(self):
        spec = study(requested="supported", minima=(1, 40))
        spec["boundaries"][0]["fixture_ids"] = ["real-a", "dev-b"]
        result = benchmark_result()
        result["cases"].append({
            "case_id": "case-dev",
            "fixture_id": "dev-b",
            "implementation": {
                "name": "template-sad",
                "version": "1",
                "config": {"radius": 12},
            },
            "metrics": metrics(100),
            "warnings": [],
        })
        with self.assertRaisesRegex(
            recording_envelope.EvidenceError, "comparable sample count 30"
        ):
            self.build(
                spec=spec,
                fixtures=[fixture("real-a"), fixture("dev-b", purpose="development")],
                result=result,
            )

    def test_promotion_requires_matched_reference_measurement_evidence(self):
        spec = study(requested="supported", minima=(1, 20))
        spec["measurement_evidence"] = []
        with self.assertRaisesRegex(
            recording_envelope.EvidenceError, "matched-reference measurement evidence"
        ):
            self.build(spec=spec)

    def test_valid_preregistered_promotion_passes(self):
        artifact = self.build(
            spec=study(requested="supported", minima=(1, 20))
        )
        self.assertEqual(artifact["boundaries"][0]["classification"], "supported")
        self.assertTrue(artifact["readiness"]["can_promote_boundaries"])


if __name__ == "__main__":
    unittest.main()
