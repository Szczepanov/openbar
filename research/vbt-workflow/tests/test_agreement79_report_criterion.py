"""#79 aggregate report: the frozen criterion as a pure-function truth table (no files, no processes)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agreement79_report as report_tool  # noqa: E402
import agreement79_study as study  # noqa: E402
from test_agreement79_report import EXERCISES, TOOL_COMMIT, consumer_report, slot_doc, video  # noqa: E402


def passing_facts() -> dict[str, Any]:
    slots = [slot_doc(slot) for slot in study.SLOTS]
    reports = {exercise: consumer_report([video(s["slot"], "1" * 64, "2" * 64) for s in slots if s["exercise"] == exercise])
               for exercise in EXERCISES}
    return {"collection_status": "complete", "slots": slots, "reports": reports,
            "reproducibility": {exercise: {"json_identical": True, "md_identical": True} for exercise in EXERCISES},
            "inputs_unchanged": True, "handcheck": {"status": "passed", "reasons": []},
            "tool": {"commit": TOOL_COMMIT, "tree_clean": True, "lock_commit": TOOL_COMMIT,
                     "handcheck_commit": TOOL_COMMIT}}


def report_video(facts: dict[str, Any], slot: str) -> dict[str, Any]:
    report = facts["reports"][study.slot_lift(slot)["exercise"]]
    return next(item for item in report["videos"] if item["label"] == slot)


def primary(facts: dict[str, Any], exercise: str = "snatch") -> dict[str, Any]:
    return facts["reports"][exercise]["pooled"]["stats"]["meanVelocityMps"]


class CriterionTests(unittest.TestCase):
    def evaluate(self, change: Callable[[dict], None] = lambda _: None) -> dict[str, Any]:
        facts = passing_facts()
        change(facts)
        return report_tool.evaluate_criterion(facts)

    def failing(self, result: dict[str, Any]) -> list[str]:
        return [name for name, item in result["conditions"].items() if not item["passed"]]

    def test_all_conditions_pass(self) -> None:
        result = self.evaluate()
        self.assertEqual((result["verdict"], result["provisional"], self.failing(result)), ("PASS", False, []))

    def test_pending_overrides_while_collection_in_progress(self) -> None:
        def in_progress(facts: dict) -> None:
            facts["collection_status"] = "in_progress"
        result = self.evaluate(in_progress)
        self.assertEqual((result["verdict"], result["provisional"], self.failing(result)), ("PENDING", True, []))

        def in_progress_failing(facts: dict) -> None:
            in_progress(facts)
            facts["inputs_unchanged"] = False
        result = self.evaluate(in_progress_failing)
        self.assertEqual((result["verdict"], self.failing(result)), ("PENDING", ["C4"]))

    def test_concluded_is_evaluated_like_complete(self) -> None:
        self.assertEqual(self.evaluate(lambda f: f.update(collection_status="concluded"))["verdict"], "PASS")

    def test_c1_fails_alone_on_an_owner_protocol_failure(self) -> None:
        def owner_failure(facts: dict) -> None:
            slot = facts["slots"][4]
            slot.update(protocol_conforming=False, failures=[{"stage": "protocol", "reason": "late_seed", "source": "owner"}])
        result = self.evaluate(owner_failure)
        self.assertEqual((result["verdict"], self.failing(result)), ("FAIL", ["C1"]))
        self.assertEqual(result["conditions"]["C1"]["reasons"],
                         ["S1-CL-1: protocol:late_seed", "clean: 5/6 conforming slots across 3/3 sessions"])

    def test_c1_owner_free_text_reason_is_replaced_by_a_placeholder(self) -> None:
        def owner_failure(facts: dict) -> None:
            facts["slots"][4].update(protocol_conforming=False, failures=[
                {"stage": "protocol", "reason": "Seed late in IMG_0042.MOV", "source": "owner"}])
        reasons = self.evaluate(owner_failure)["conditions"]["C1"]["reasons"]
        self.assertEqual(reasons[0], "S1-CL-1: protocol:[owner note]")

    def test_c2_fails_alone_on_each_count_and_completeness_breach(self) -> None:
        cases = {
            "wlTotal": lambda f: report_video(f, "S2-SQ-1")["counts"].update(wlTotal=4),
            "openBarTotal": lambda f: report_video(f, "S2-SQ-1")["counts"].update(openBarTotal=2),
            "paired": lambda f: report_video(f, "S2-SQ-1")["counts"].update(paired=2),
            "wlOnly": lambda f: report_video(f, "S2-SQ-1")["counts"].update(wlOnly=1),
            "openBarOnly": lambda f: report_video(f, "S2-SQ-1")["counts"].update(openBarOnly=1),
            "openBarExcluded": lambda f: report_video(f, "S2-SQ-1")["counts"].update(openBarExcluded=1),
            "paired_rep_incomplete": lambda f: report_video(f, "S2-SQ-1")["paired"][1].update(openBarComplete=False),
            "wl_incomplete": lambda f: report_video(f, "S2-SQ-1")["paired"][0].update(wlComplete=False),
            "attempted_reps_not_3": lambda f: f["slots"][6].update(attempted_rep_start_s=[2.0, 5.0]),
            "pooled paired=17": lambda f: f["reports"]["back_squat"]["pooled"]["counts"].update(paired=17),
        }
        for expected, change in cases.items():
            with self.subTest(expected):
                result = self.evaluate(change)
                self.assertEqual((result["verdict"], self.failing(result)), ("FAIL", ["C2"]))
                self.assertEqual(len(result["conditions"]["C2"]["reasons"]), 1)
        incomplete = self.evaluate(cases["paired_rep_incomplete"])["conditions"]["C2"]["reasons"]
        self.assertEqual(incomplete, ["S2-SQ-1: paired_rep_incomplete"])

    def test_a_non_analyzable_slot_fails_c2(self) -> None:
        result = self.evaluate(lambda f: f["slots"][16].update(analyzable=False))
        self.assertEqual(self.failing(result), ["C2"])
        self.assertEqual(result["conditions"]["C2"]["reasons"], ["S3-CL-1: not_analyzable"])

    def test_c3_loa_bounds_are_inclusive_at_the_tolerance(self) -> None:
        for lower, upper in ((-0.05, 0.05), (-0.05, -0.01), (0.01, 0.05), (0.0, 0.0)):
            with self.subTest(lower=lower, upper=upper):
                result = self.evaluate(lambda f: primary(f).update(lowerLoA=lower, upperLoA=upper))
                self.assertEqual((result["verdict"], self.failing(result)), ("PASS", []))

    def test_c3_fails_alone_just_outside_or_null(self) -> None:
        cases = {"lowerLoA=-0.050001": {"lowerLoA": -0.050001}, "upperLoA=0.050001": {"upperLoA": 0.050001},
                 "lowerLoA unavailable (insufficient_n)": {"lowerLoA": None},
                 "upperLoA unavailable (insufficient_n)": {"upperLoA": None}}
        for expected, values in cases.items():
            with self.subTest(expected):
                def change(facts: dict, values: dict = values) -> None:
                    stats_item = primary(facts)
                    stats_item.update(values)
                    for key, value in values.items():
                        stats_item["reasons"][key] = "insufficient_n" if value is None else None
                result = self.evaluate(change)
                self.assertEqual((result["verdict"], self.failing(result)), ("FAIL", ["C3"]))
                self.assertEqual(len(result["conditions"]["C3"]["reasons"]), 1)
                self.assertTrue(result["conditions"]["C3"]["reasons"][0].startswith(f"snatch: {expected}"))

    def test_c3_ignores_secondary_metrics(self) -> None:
        def wide_secondary(facts: dict) -> None:
            stats_item = facts["reports"]["clean"]["pooled"]["stats"]
            stats_item["peakVelocityMps"].update(lowerLoA=-0.4, upperLoA=0.4)
            stats_item["romCm"].update(lowerLoA=None, upperLoA=None)
        self.assertEqual(self.evaluate(wide_secondary)["verdict"], "PASS")

    def test_c3_fails_alone_when_method_config_differs_across_lifts(self) -> None:
        result = self.evaluate(lambda f: f["reports"]["snatch"].update(openBarMethodConfigSha256="8" * 64))
        self.assertEqual((result["verdict"], self.failing(result)), ("FAIL", ["C3"]))
        self.assertEqual(result["conditions"]["C3"]["reasons"], ["method_config_mismatch_across_lifts"])

    def test_method_config_is_compared_only_across_produced_reports(self) -> None:
        def no_snatch(facts: dict) -> None:
            facts["reports"]["snatch"] = None
            del facts["reproducibility"]["snatch"]
        reasons = self.evaluate(no_snatch)["conditions"]["C3"]["reasons"]
        self.assertNotIn("method_config_mismatch_across_lifts", reasons)

    def test_a_lift_without_a_report_fails_c3(self) -> None:
        def no_snatch(facts: dict) -> None:
            facts["reports"]["snatch"] = None
            del facts["reproducibility"]["snatch"]
        result = self.evaluate(no_snatch)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertEqual(result["conditions"]["C3"]["reasons"], ["snatch: statistics_unavailable"])
        self.assertIn("snatch: pooled paired=unavailable (expected 18)", result["conditions"]["C2"]["reasons"])
        self.assertTrue(result["conditions"]["C4"]["passed"])

    def test_c4_fails_alone_on_each_reproducibility_breach(self) -> None:
        def tool(**values: Any) -> Callable[[dict], None]:
            return lambda f: f["tool"].update(values)
        cases = {
            "clean: run1/run2 JSON bytes differ": lambda f: f["reproducibility"]["clean"].update(json_identical=False),
            "clean: run1/run2 Markdown bytes differ": lambda f: f["reproducibility"]["clean"].update(md_identical=False),
            "inputs_changed_since_lock": lambda f: f.update(inputs_unchanged=False),
            "handcheck_failed: value_mismatch": lambda f: f.update(handcheck={"status": "failed",
                                                                              "reasons": ["value_mismatch"]}),
            "tool_tree_dirty": tool(tree_clean=False),
            "tool_commit_differs_from_lock": tool(commit="e" * 40),
            "handcheck_tool_commit_differs": tool(handcheck_commit="e" * 40),
        }
        for expected, change in cases.items():
            with self.subTest(expected):
                result = self.evaluate(change)
                self.assertEqual((result["verdict"], self.failing(result)), ("FAIL", ["C4"]))
                self.assertEqual(result["conditions"]["C4"]["reasons"], [expected])

    def test_c4_missing_handcheck_tool_commit_differs(self) -> None:
        result = self.evaluate(lambda f: f["tool"].update(handcheck_commit=None))
        self.assertEqual(result["conditions"]["C4"]["reasons"], ["handcheck_tool_commit_differs"])


if __name__ == "__main__":
    unittest.main()
