from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "validation" / "tools"
spec = importlib.util.spec_from_file_location(
    "phone_runtime_benchmark", TOOLS / "phone_runtime_benchmark.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules["phone_runtime_benchmark"] = module
spec.loader.exec_module(module)


class TrackerTimingTests(unittest.TestCase):
    def test_parses_template_stage_timing(self):
        stderr = (
            "template-sad-v1: 708 observations (21.242-44.708 s) of 1348 selected frames; "
            "tracked 39, low-confidence 644, lost 25\n"
            "  decode 4.13 s + track 52.54 s for 44.71 s of selected media "
            "(0.79x real time) -> out.json\n"
            "local-contrast-centroid-v1: 708 observations (21.242-44.708 s) of "
            "1348 selected frames; tracked 700, low-confidence 8, lost 0\n"
            "  decode 4.13 s + track 1.50 s for 44.71 s of selected media "
            "(7.94x real time) -> other.json\n"
        )
        timing = module.tracker_stage_timing(stderr)
        self.assertEqual(
            timing,
            {
                "decode_wall_s": 4.13,
                "tracker_wall_s": 52.54,
                "selected_media_span_s": 44.71,
            },
        )

    def test_missing_tracker_timing_fails_closed(self):
        with self.assertRaises(module.BenchmarkError):
            module.tracker_stage_timing("tracker template-sad-v1 failed: nope\n")


class SummaryTests(unittest.TestCase):
    def test_summary_uses_processing_over_media_ratio(self):
        runs = [
            {"returncode": 0, "wall_s": 4.0, "peak_process_tree_rss_mib": 500.0},
            {"returncode": 0, "wall_s": 6.0, "peak_process_tree_rss_mib": 550.0},
            {"returncode": 0, "wall_s": 5.0, "peak_process_tree_rss_mib": 525.0},
        ]
        summary = module.summarize_repeats(runs, 10.0)
        self.assertEqual(summary["median_wall_s"], 5.0)
        self.assertEqual(summary["median_processing_to_media_ratio"], 0.5)
        self.assertEqual(summary["max_processing_to_media_ratio"], 0.6)
        self.assertEqual(summary["peak_process_tree_rss_mib"], 550.0)

    def test_failed_run_is_not_averaged_away(self):
        runs = [
            {"returncode": 0, "wall_s": 4.0, "peak_process_tree_rss_mib": 500.0},
            {"returncode": 2, "wall_s": 3.0, "peak_process_tree_rss_mib": 510.0},
        ]
        summary = module.summarize_repeats(runs, 10.0)
        self.assertEqual(summary["failed_runs"], 1)
        self.assertIsNone(summary["median_processing_to_media_ratio"])


class GateTests(unittest.TestCase):
    @staticmethod
    def env(match=True):
        return {"reference_match": match}

    @staticmethod
    def case(
        case_id,
        ratio=0.8,
        *,
        source="self_recorded",
        support="supported",
        representative=True,
        rss=600.0,
        failed=0,
    ):
        return {
            "id": case_id,
            "source_kind": source,
            "recording_support_status": support,
            "representative_for_gate": representative,
            "end_to_end": {
                "median_processing_to_media_ratio": None if failed else ratio,
                "peak_process_tree_rss_mib": rss,
                "failed_runs": failed,
            },
        }

    def test_unfrozen_production_candidate_stays_unmeasurable(self):
        gate = module.assess_gate(
            self.env(),
            [self.case("a"), self.case("b")],
            production_candidate_frozen=False,
        )
        self.assertEqual(gate["status"], "NOT MEASURABLE YET")
        self.assertIn("#57", gate["evidence"])

    def test_non_reference_device_stays_unmeasurable(self):
        gate = module.assess_gate(
            self.env(False),
            [self.case("a"), self.case("b")],
            production_candidate_frozen=True,
        )
        self.assertEqual(gate["status"], "NOT MEASURABLE YET")

    def test_requires_multiple_representative_cases(self):
        gate = module.assess_gate(
            self.env(),
            [self.case("a")],
            production_candidate_frozen=True,
        )
        self.assertEqual(gate["status"], "NOT MEASURABLE YET")

    def test_missing_process_rss_stays_unmeasurable(self):
        gate = module.assess_gate(
            self.env(),
            [self.case("a", rss=None), self.case("b")],
            production_candidate_frozen=True,
        )
        self.assertEqual(gate["status"], "NOT MEASURABLE YET")

    def test_warning_or_synthetic_case_stays_unmeasurable(self):
        gate = module.assess_gate(
            self.env(),
            [
                self.case("a", support="warning"),
                self.case("b", source="synthetic"),
            ],
            production_candidate_frozen=True,
        )
        self.assertEqual(gate["status"], "NOT MEASURABLE YET")

    def test_reference_phone_passes_when_all_representative_medians_are_below_one(self):
        gate = module.assess_gate(
            self.env(),
            [self.case("a", 0.8), self.case("b", 0.95)],
            production_candidate_frozen=True,
        )
        self.assertEqual(gate["status"], "PASS")

    def test_reference_phone_fails_when_any_case_is_not_faster_than_video(self):
        gate = module.assess_gate(
            self.env(),
            [self.case("a", 0.8), self.case("b", 1.01)],
            production_candidate_frozen=True,
        )
        self.assertEqual(gate["status"], "FAIL")

    def test_reference_phone_failure_is_not_averaged_away(self):
        gate = module.assess_gate(
            self.env(),
            [self.case("a", 0.8), self.case("b", failed=1)],
            production_candidate_frozen=True,
        )
        self.assertEqual(gate["status"], "FAIL")


def _run(wall, returncode=0):
    return {"returncode": returncode, "wall_s": wall, "peak_process_tree_rss_mib": 10.0}


def _sidecar(stages=None, total=3.0, **overrides):
    value = {
        "schema_version": 1,
        "timings_version": "analyze-stage-timings-v1",
        "command": "analyze",
        "stages_s": {"decode": 1.0, "track": 1.5} if stages is None else stages,
        "total_s": total,
    }
    value.update(overrides)
    return json.dumps(value)


class PercentileTests(unittest.TestCase):
    def test_nearest_rank_known_values(self):
        values = [15.0, 20.0, 35.0, 40.0, 50.0]
        self.assertEqual(module.percentile_nearest_rank(values, 0.30), 20.0)
        self.assertEqual(module.percentile_nearest_rank(values, 0.40), 20.0)
        self.assertEqual(module.percentile_nearest_rank(values, 0.50), 35.0)
        self.assertEqual(module.percentile_nearest_rank(values, 0.95), 50.0)
        self.assertEqual(module.percentile_nearest_rank(values, 1.0), 50.0)

    def test_unsorted_input_and_single_value(self):
        self.assertEqual(module.percentile_nearest_rank([3.0, 1.0, 2.0], 0.5), 2.0)
        self.assertEqual(module.percentile_nearest_rank([7.0], 0.95), 7.0)

    def test_rejects_bad_input(self):
        for values in ([], [1.0, float("nan")], [float("inf")]):
            with self.assertRaises(module.BenchmarkError):
                module.percentile_nearest_rank(values, 0.5)
        for p in (0.0, -0.1, 1.1, float("nan")):
            with self.assertRaises(module.BenchmarkError):
                module.percentile_nearest_rank([1.0], p)


class LatencySummaryTests(unittest.TestCase):
    def test_cold_warm_and_tails(self):
        summary = module.summarize_latency([_run(10.0), _run(4.0), _run(6.0)], 2.0)
        self.assertFalse(summary["qualifying"])
        self.assertEqual(summary["cold_run_wall_s"], 10.0)
        self.assertEqual(summary["warm_run_wall_s"], [4.0, 6.0])
        self.assertEqual(summary["warm_median_wall_s"], 5.0)
        self.assertEqual(summary["n_runs"], 3)
        self.assertEqual(summary["median_wall_s"], 6.0)
        self.assertEqual(summary["p95_wall_s"], 10.0)
        self.assertEqual(summary["max_wall_s"], 10.0)
        self.assertEqual(summary["median_processing_to_media_ratio"], 3.0)
        self.assertEqual(summary["max_processing_to_media_ratio"], 5.0)
        self.assertEqual(summary["failed_runs"], 0)
        self.assertEqual(summary["failed_run_wall_s"], [])
        self.assertEqual(summary["failed_run_returncodes"], [])
        self.assertIsNone(summary["unmeasurable_reason"])
        self.assertTrue(summary["small_n"])
        self.assertNotIn("includes_failed_runs", summary)
        self.assertIn("not controlled", summary["cold_definition"])
        self.assertIn("NOT a verified cold start", summary["cold_definition"])
        self.assertIn("tracker-run", summary["cold_definition"])

    def test_failed_run_makes_statistics_null_and_is_listed(self):
        summary = module.summarize_latency([_run(2.0), _run(0.1, returncode=3), _run(3.0)], 1.0)
        self.assertEqual(summary["unmeasurable_reason"], "one or more runs failed")
        self.assertEqual(summary["failed_runs"], 1)
        self.assertEqual(summary["failed_run_wall_s"], [0.1])
        self.assertEqual(summary["failed_run_returncodes"], [3])
        self.assertEqual(summary["n_runs"], 3)
        for key in (
            "cold_run_wall_s", "warm_run_wall_s", "warm_median_wall_s", "median_wall_s",
            "p95_wall_s", "max_wall_s", "median_processing_to_media_ratio",
            "p95_processing_to_media_ratio", "max_processing_to_media_ratio",
            "warm_slowdown_ratio",
        ):
            self.assertIsNone(summary[key], key)

    def test_small_n_threshold(self):
        self.assertFalse(module.summarize_latency([_run(1.0)] * 20, 1.0)["small_n"])
        self.assertTrue(module.summarize_latency([_run(1.0)] * 19, 1.0)["small_n"])

    def test_warm_slowdown_ratio(self):
        three = module.summarize_latency([_run(10.0), _run(4.0), _run(6.0)], 1.0)
        self.assertEqual(three["warm_slowdown_ratio"], 1.5)
        two = module.summarize_latency([_run(10.0), _run(4.0)], 1.0)
        self.assertIsNone(two["warm_slowdown_ratio"])
        failed_last = module.summarize_latency([_run(1.0), _run(4.0), _run(6.0, 1)], 1.0)
        self.assertIsNone(failed_last["warm_slowdown_ratio"])
        failed_first = module.summarize_latency([_run(1.0), _run(4.0, 1), _run(6.0)], 1.0)
        self.assertIsNone(failed_first["warm_slowdown_ratio"])

    def test_single_run_has_no_warm_median(self):
        summary = module.summarize_latency([_run(2.0)], 1.0)
        self.assertEqual(summary["warm_run_wall_s"], [])
        self.assertIsNone(summary["warm_median_wall_s"])

    def test_rejects_empty_or_bad_media(self):
        with self.assertRaises(module.BenchmarkError):
            module.summarize_latency([], 1.0)
        with self.assertRaises(module.BenchmarkError):
            module.summarize_latency([_run(1.0)], 0.0)


class DeterminismTests(unittest.TestCase):
    def test_identical(self):
        result = module.determinism_report([b"a", b"a"], [b"s", b"s"])
        self.assertIs(result["analysis_bytes_identical"], True)
        self.assertIs(result["recording_support_bytes_identical"], True)
        self.assertEqual(result["compared_runs"], 2)
        self.assertEqual(result["analysis_sha256"], hashlib.sha256(b"a").hexdigest())

    def test_one_byte_differs(self):
        result = module.determinism_report([b"abc", b"abd"], [b"s", b"s"])
        self.assertIs(result["analysis_bytes_identical"], False)
        self.assertIs(result["recording_support_bytes_identical"], True)

    def test_missing_is_null(self):
        result = module.determinism_report([b"a", None, b"a"], [None, None, None])
        self.assertIsNone(result["analysis_bytes_identical"])
        self.assertIsNone(result["recording_support_bytes_identical"])
        self.assertEqual(result["compared_runs"], 2)
        self.assertIsNone(module.determinism_report([None], [None])["analysis_sha256"])

    def test_difference_wins_over_missing(self):
        result = module.determinism_report([b"a", b"b", None], [b"s"] * 3)
        self.assertIs(result["analysis_bytes_identical"], False)

    def test_warnings_list_case_ids_only(self):
        cases = [
            {
                "id": "ok",
                "latency_diagnostics": {
                    "determinism": {
                        "analysis_bytes_identical": True,
                        "recording_support_bytes_identical": None,
                    }
                },
            },
            {
                "id": "bad",
                "latency_diagnostics": {
                    "determinism": {
                        "analysis_bytes_identical": True,
                        "recording_support_bytes_identical": False,
                    }
                },
            },
        ]
        self.assertEqual(module.diagnostics_warnings(cases), ["bad"])


class SidecarTests(unittest.TestCase):
    def test_valid(self):
        parsed = module.parse_stage_timings_sidecar(_sidecar())
        self.assertEqual(parsed["stages_s"], {"decode": 1.0, "track": 1.5})
        self.assertEqual(parsed["total_s"], 3.0)

    def test_invalid_variants(self):
        bad = [
            "{not json",
            "[]",
            _sidecar(schema_version=2),
            _sidecar(timings_version="other"),
            _sidecar(stages={"decode": -1.0}),
            _sidecar(stages={"decode": 5.0}, total=1.0),
            _sidecar(stages={"decode": float("nan")}),
            _sidecar(total=float("inf")),
            _sidecar(stages=[1.0]),
            _sidecar(stages={"decode": True}),
            _sidecar(command="tracker-run"),
            _sidecar(extra=1),
            _sidecar(schema_version=1.0),
            _sidecar(schema_version=True),
        ]
        for text in bad:
            with self.subTest(text=text):
                with self.assertRaises(module.BenchmarkError):
                    module.parse_stage_timings_sidecar(text)

    def test_tolerance_on_sum(self):
        module.parse_stage_timings_sidecar(_sidecar(stages={"a": 1.0}, total=1.0 - 1e-9))

    def test_read_sidecar_missing_and_malformed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "t.json"
            timing, note = module.read_stage_timings(path, 1)
            self.assertIsNone(timing)
            self.assertIn("run 1", note)
            self.assertNotIn(temp, note)
            path.write_text("{bad", encoding="utf-8")
            timing, note = module.read_stage_timings(path, 2)
            self.assertIsNone(timing)
            self.assertNotIn(temp, note)
            path.write_text(_sidecar(), encoding="utf-8")
            timing, note = module.read_stage_timings(path, 3)
            self.assertIsNone(note)
            self.assertEqual(timing["total_s"], 3.0)


class StageAggregationTests(unittest.TestCase):
    def test_not_requested(self):
        self.assertEqual(
            module.aggregate_stage_timings(None, []),
            {"available": False, "reason": "not requested"},
        )

    def test_aggregate(self):
        timings = [
            {"stages_s": {"a": 1.0, "b": 2.0}, "total_s": 3.5},
            {"stages_s": {"a": 3.0, "b": 2.0}, "total_s": 5.5},
            {"stages_s": {"a": 2.0, "b": 2.0, "c": 1.0}, "total_s": 5.5},
        ]
        result = module.aggregate_stage_timings(timings, [])
        self.assertTrue(result["available"])
        self.assertEqual(sorted(result["stages"]), ["a", "b"])
        self.assertEqual(
            result["stages"]["a"], {"n": 3, "median_s": 2.0, "p95_s": 3.0, "max_s": 3.0}
        )
        self.assertEqual(result["unattributed_median_s"], 0.5)

    def test_dropped_stage_names_are_noted(self):
        timings = [
            {"stages_s": {"a": 1.0}, "total_s": 1.5},
            {"stages_s": {"a": 1.0, "z": 0.5}, "total_s": 2.0},
        ]
        result = module.aggregate_stage_timings(timings, [])
        self.assertEqual(sorted(result["stages"]), ["a"])
        self.assertEqual(len(result["notes"]), 1)
        self.assertIn("z", result["notes"][0])

    def test_partial_failure_noted(self):
        timings = [{"stages_s": {"a": 1.0}, "total_s": 1.5}, None]
        result = module.aggregate_stage_timings(timings, ["run 2: sidecar missing"])
        self.assertTrue(result["available"])
        self.assertEqual(result["stages"]["a"]["n"], 1)
        self.assertEqual(result["notes"], ["run 2: sidecar missing"])

    def test_all_unavailable(self):
        result = module.aggregate_stage_timings([None, None], ["x", "y"])
        self.assertFalse(result["available"])
        self.assertEqual(result["notes"], ["x", "y"])


class ExistingBehaviourTests(unittest.TestCase):
    def test_summarize_repeats_keys_unchanged(self):
        self.assertEqual(
            set(module.summarize_repeats([_run(1.0)], 2.0)),
            {
                "completed_runs",
                "failed_runs",
                "median_wall_s",
                "median_processing_to_media_ratio",
                "max_processing_to_media_ratio",
                "peak_process_tree_rss_mib",
            },
        )

    def test_gate_unchanged_for_unfrozen_and_non_reference(self):
        gate = module.assess_gate(
            {"reference_match": True}, [], production_candidate_frozen=False
        )
        self.assertEqual(gate["status"], "NOT MEASURABLE YET")
        self.assertIn("#57", gate["evidence"])
        gate = module.assess_gate(
            {"reference_match": False}, [], production_candidate_frozen=True
        )
        self.assertEqual(gate["status"], "NOT MEASURABLE YET")
        self.assertIn("Pixel 8", gate["evidence"])

    def test_analyze_command_timings_flag_is_opt_in(self):
        args = (Path("b"), Path("m"), "f", Path("s"), 0.45, [], Path("o"), Path("r"))
        base = module.analyze_command(*args)
        self.assertNotIn("--timings-output", base)
        with_timings = module.analyze_command(*args, timings_output=Path("t.json"))
        self.assertEqual(with_timings[: len(base)], base)
        self.assertEqual(with_timings[-2:], ["--timings-output", str(Path("t.json"))])

    def test_latency_evidence_reflects_inputs(self):
        for frozen in (True, False):
            for matched in (True, False):
                evidence = module.build_latency_evidence(
                    production_candidate_frozen=frozen,
                    reference_match=matched,
                    stage_timings_requested=frozen,
                )
                self.assertEqual(evidence["status"], "NON-QUALIFYING DIAGNOSTIC")
                self.assertIs(evidence["qualifying"], False)
                self.assertIs(evidence["product_target_declared"], False)
                self.assertEqual(evidence["contract"], "docs/validation/ANDROID_LATENCY_EVIDENCE.md")
                self.assertEqual(
                    evidence["missing_stages"],
                    [
                        "import request / app path",
                        "overlay preparation",
                        "per-rep metrics (analyze computes none)",
                    ],
                )
                self.assertIs(evidence["stage_timings_requested"], frozen)
                self.assertEqual("is not frozen" in evidence["reason"], not frozen)
                self.assertEqual("did not match" in evidence["reason"], not matched)


class ArtifactAssemblyTests(unittest.TestCase):
    def _case(self, case_id, identical):
        return {
            "id": case_id,
            "latency_diagnostics": {
                "determinism": {
                    "analysis_bytes_identical": identical,
                    "recording_support_bytes_identical": True,
                }
            },
        }

    def test_build_artifact_wiring(self):
        env = {"reference_match": False}
        artifact = module.build_artifact(
            evaluated_commit_id="abc",
            env=env,
            cases=[self._case("good", True), self._case("bad", False)],
            production_candidate_frozen=True,
            stage_timings_requested=True,
        )
        self.assertEqual(artifact["benchmark_version"], "phone-runtime-benchmark-v1")
        self.assertEqual(artifact["schema_version"], 1)
        self.assertEqual(artifact["diagnostics_warnings"], ["bad"])
        self.assertIs(artifact["latency_evidence"]["stage_timings_requested"], True)
        self.assertIs(artifact["latency_evidence"]["reference_match"], False)
        self.assertEqual(artifact["gate"]["status"], "NOT MEASURABLE YET")
        self.assertIs(artifact["frozen_configuration"]["production_candidate_frozen"], True)

    def test_warning_line_format(self):
        self.assertEqual(
            module.warning_line("c1"),
            "phone-runtime-benchmark: warning: analysis bytes differed across repeats for case c1",
        )


if __name__ == "__main__":
    unittest.main()
