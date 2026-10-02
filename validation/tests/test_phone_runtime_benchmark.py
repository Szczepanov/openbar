from __future__ import annotations

import importlib.util
import sys
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


if __name__ == "__main__":
    unittest.main()
