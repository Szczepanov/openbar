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


m0_evidence = load("m0_evidence")
m0_private_evidence = load("m0_private_evidence")


def fixture(fixture_id, *, kind="synthetic", purpose="development", exercise="clean", fps=12):
    return {
        "id": fixture_id,
        "exercise": exercise,
        "purpose": purpose,
        "media": {"sha256": "0" * 64},
        "video": {"nominal_fps": fps, "measured_fps": fps, "width_px": 1280, "height_px": 720,
                  "duration_s": 10.0, "rotation_deg": 90},
        "camera": {"view": "side", "movement": "fixed"},
        "load": {"plate_diameter_m": 0.45},
        "conditions": {"lighting": "good", "motion_blur": "none", "occlusion": "none",
                       "plate_visibility": "clear", "challenge_tags": ["cfr"]},
        "source": {"kind": kind, "redistribution_status": "private_only"},
        "notes": "free-text notes that must never reach an aggregate report",
    }


def coverage_for(fixtures, labelled):
    return m0_evidence.dataset_coverage({"schema_version": 1, "fixtures": fixtures}, labelled)


def gates_by_metric(gates):
    return {item["metric"]: item for item in gates}


class CoverageTests(unittest.TestCase):
    def test_coverage_reports_conditions_and_missing_exercises_from_metadata(self):
        coverage = coverage_for(
            [fixture("synthetic-a"), fixture("real-b", kind="self_recorded", exercise="snatch", fps=30)],
            {"synthetic-a": 10, "real-b": 0},
        )

        self.assertEqual(coverage["synthetic_fixture_count"], 1)
        self.assertEqual(coverage["non_synthetic_fixture_count"], 1)
        self.assertEqual(coverage["annotated_fixture_count"], 1)
        self.assertEqual(coverage["held_out_real_annotated_fixture_count"], 0)
        self.assertEqual(coverage["comparable_labelled_samples"], 10)
        self.assertEqual(coverage["exercises"], ["clean", "snatch"])
        self.assertEqual(coverage["missing_exercises"], ["back_squat"])
        self.assertEqual(coverage["nominal_fps"], [12, 30])

    def test_only_annotated_non_synthetic_validation_fixtures_count_as_held_out_real(self):
        coverage = coverage_for(
            [
                fixture("synthetic-held-out", purpose="validation"),
                fixture("real-dev", kind="self_recorded"),
                fixture("real-held-out", kind="self_recorded", purpose="validation"),
                fixture("real-held-out-unlabelled", kind="self_recorded", purpose="validation"),
                fixture("real-boundary", kind="self_recorded", purpose="boundary"),
            ],
            {"synthetic-held-out": 5, "real-dev": 5, "real-held-out": 5, "real-held-out-unlabelled": 0,
             "real-boundary": 5},
        )

        self.assertEqual(coverage["held_out_fixture_count"], 3)
        self.assertEqual(coverage["held_out_real_annotated_fixture_count"], 1)


class GateTests(unittest.TestCase):
    def assess(self, coverage, *, determinism_pass=True, selected=False, diagnostics=()):
        return gates_by_metric(
            m0_evidence.assess_gates(
                coverage=coverage,
                determinism_pass=determinism_pass,
                determinism_scope="the test subset",
                runtime_diagnostics=list(diagnostics),
                production_tracker_selected=selected,
            )
        )

    def test_synthetic_only_evidence_keeps_real_world_gates_unmeasurable(self):
        gates = self.assess(coverage_for([fixture("synthetic-a")], {"synthetic-a": 10}))

        self.assertEqual(len(gates), 7)
        self.assertEqual(gates["Repeat-analysis determinism"]["status"], "PASS")
        for metric, item in gates.items():
            if metric != "Repeat-analysis determinism":
                self.assertEqual(item["status"], "NOT MEASURABLE YET", metric)
        self.assertIn("no annotated held-out non-synthetic fixture", gates["Plate-centre tracking MAE"]["rationale"].lower())

    def test_non_identical_repeats_fail_the_determinism_gate(self):
        gates = self.assess(coverage_for([fixture("synthetic-a")], {"synthetic-a": 10}), determinism_pass=False)

        self.assertEqual(gates["Repeat-analysis determinism"]["status"], "FAIL")

    def test_held_out_real_data_without_selected_tracker_stays_unmeasurable(self):
        coverage = coverage_for([fixture("real", kind="self_recorded", purpose="validation")], {"real": 30})

        gates = self.assess(coverage)

        rationale = gates["Plate-centre tracking MAE"]["rationale"]
        self.assertEqual(gates["Plate-centre tracking MAE"]["status"], "NOT MEASURABLE YET")
        self.assertIn("no production tracker is selected", rationale.lower())
        self.assertNotIn("held-out", rationale.lower())

    def test_unimplemented_held_out_evaluation_fails_closed_instead_of_passing(self):
        coverage = coverage_for([fixture("real", kind="self_recorded", purpose="validation")], {"real": 30})

        with self.assertRaises(m0_evidence.EvidenceError):
            self.assess(coverage, selected=True)

    def test_runtime_gate_reports_diagnostics_but_stays_unmeasurable_without_reference_hardware(self):
        gates = self.assess(
            coverage_for([fixture("synthetic-a")], {"synthetic-a": 10}),
            diagnostics=["clip template-sad-v1 0.79x real time"],
        )

        runtime = gates["Offline processing"]
        self.assertEqual(runtime["status"], "NOT MEASURABLE YET")
        self.assertIn("0.79x real time", runtime["evidence"])
        self.assertIn("phone-class", runtime["rationale"])


class CommitAgreementTests(unittest.TestCase):
    def test_missing_commits_are_allowed(self):
        m0_evidence.require_commit_agreement("abc123", [None, "abc123", None])

    def test_conflicting_commit_fails_closed(self):
        with self.assertRaisesRegex(m0_evidence.EvidenceError, "disagree on evaluated git commit"):
            m0_evidence.require_commit_agreement("abc123", ["abc123", "different"])


class TrackerRunSummaryTests(unittest.TestCase):
    STDERR = (
        "template-sad-v1: 708 observations (21.242-44.708 s) of 1348 selected frames; tracked 39, "
        "low-confidence 644, lost 25 (first lost at 43.912 s)\n"
        "  decode 4.13 s + track 52.54 s for 44.71 s of selected media (0.79x real time) -> out.json\n"
        "tracker local-contrast-centroid-v1 failed: manual seed target/background contrast 11.869 is "
        "insufficient for the contrast tracker\n"
    )

    def test_parses_cli_counts_timing_and_failures(self):
        outcomes = m0_private_evidence.parse_tracker_run(
            self.STDERR, ["template-sad-v1", "local-contrast-centroid-v1"]
        )

        template, contrast = outcomes
        self.assertEqual(
            (template["tracked"], template["low_confidence"], template["lost"], template["selected_frames"]),
            (39, 644, 25, 1348),
        )
        self.assertEqual(template["realtime_ratio"], 0.79)
        self.assertEqual(contrast["status"], "failed")
        self.assertIn("contrast 11.869", contrast["failure_reason"])

    def test_unaccounted_tracker_fails_closed(self):
        with self.assertRaises(m0_evidence.EvidenceError):
            m0_private_evidence.parse_tracker_run(self.STDERR, ["template-sad-v1", "unknown-tracker"])


class PrivateAggregateTests(unittest.TestCase):
    def test_fixture_facts_exclude_notes_and_use_display_orientation(self):
        facts = m0_private_evidence.fixture_facts(fixture("real", kind="self_recorded"))

        self.assertNotIn("notes", facts)
        self.assertNotIn("free-text notes", repr(facts))
        self.assertEqual(facts["display_size_px"], "720x1280")

    def test_failure_reasons_that_could_carry_paths_are_withheld(self):
        publishable = m0_private_evidence.publishable_reason
        withheld = m0_private_evidence.WITHHELD_REASON

        self.assertEqual(
            publishable("manual seed target/background contrast 11.869 is insufficient"),
            "manual seed target/background contrast 11.869 is insufficient",
        )
        for leaky in (
            r"failed to read 'C:\Users\someone\clip.mp4'",
            "cannot open /home/someone/clip.mp4",
            "cannot open ../private/clip.mp4",
            "seed: missing",
        ):
            self.assertEqual(publishable(leaky), withheld, leaky)

    def test_analyze_failures_publish_only_the_cli_error_category(self):
        stderr = (
            "status=failure error[media]: failed to read 'C:\\Users\\someone\\clip.mp4'\n"
            "error: process didn't exit successfully: `openbar-cli analyze --manifest C:\\private`\n"
        )

        self.assertEqual(m0_private_evidence.cli_error_category(stderr), "media")
        self.assertEqual(m0_private_evidence.cli_error_category("no category here"), "unknown")

    def test_failed_or_missing_analysis_fails_private_determinism(self):
        identical = {"analysis": {"runs": 2, "identical": True, "error": None}}
        failed = {"analysis": {"runs": 2, "identical": None, "error": "analyze-failed:media"}}
        skipped = {"status": "skipped: no manual target seed"}

        self.assertTrue(m0_private_evidence.private_determinism_pass([identical, skipped]))
        self.assertFalse(m0_private_evidence.private_determinism_pass([identical, failed]))
        self.assertFalse(
            m0_private_evidence.private_determinism_pass([{**identical, "benchmark_outputs_identical": False}])
        )

    def test_successful_tracker_without_prediction_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            with self.assertRaisesRegex(m0_evidence.EvidenceError, "wrote no prediction"):
                m0_private_evidence.benchmark_trackers(
                    manifest=run_dir / "manifest.json", fixture_id="real", annotation=run_dir / "a.json",
                    seed=run_dir / "s.json", run_dir=run_dir, out_dir=run_dir, commit="abc",
                    outcomes=[{"implementation": "template-sad-v1", "status": "ok"}],
                )

    def test_empty_prediction_fails_closed_and_failed_trackers_are_not_benchmarked(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "real.template-sad-v1.prediction-v1.json").write_text(
                json.dumps({"samples": []}), encoding="utf-8"
            )
            stale = run_dir / "real.local-contrast-centroid-v1.prediction-v1.json"
            stale.write_text(json.dumps({"samples": [{"timestamp_s": 0.0}]}), encoding="utf-8")
            with self.assertRaisesRegex(m0_evidence.EvidenceError, "no samples"):
                m0_private_evidence.benchmark_trackers(
                    manifest=run_dir / "manifest.json", fixture_id="real", annotation=run_dir / "a.json",
                    seed=run_dir / "s.json", run_dir=run_dir, out_dir=run_dir, commit="abc",
                    outcomes=[
                        {"implementation": "local-contrast-centroid-v1", "status": "failed",
                         "failure_reason": "low contrast"},
                        {"implementation": "template-sad-v1", "status": "ok"},
                    ],
                )


if __name__ == "__main__":
    unittest.main()
