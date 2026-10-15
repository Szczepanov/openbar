"""Synthetic tests of the registered decision rules."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import evaluate_rules as rules  # noqa: E402


def report(errors, labelled=None):
    """A minimal motion_metrics-style report: errors maps timestamp -> (euclidean px, confidence)."""
    rows = [{"timestamp_s": t, "error_px": {"x_px": e, "y_px": 0.0}, "confidence": c} for t, (e, c) in errors.items()]
    return {"matched_errors": rows, "matched_tracked_samples": len(rows),
            "labelled_samples": labelled if labelled is not None else len(rows)}


def clip(improvement_px=0.6, coverage=1.0, arm_max_common_px=5.0, baseline_max_common_px=5.0,
         silent_false_tracks_arm=0, silent_false_tracks_baseline=1, hub_supported=True):
    return {"coverage": coverage, "improvement_px": improvement_px, "arm_max_common_px": arm_max_common_px,
            "baseline_max_common_px": baseline_max_common_px, "baseline_max_all_px": baseline_max_common_px,
            "silent_false_tracks_arm": silent_false_tracks_arm,
            "silent_false_tracks_baseline": silent_false_tracks_baseline, "hub_supported": hub_supported}


class CompareArmTests(unittest.TestCase):
    def test_common_support_mae_improvement_and_coverage(self):
        baseline = report({1.0: (4.0, 1.0), 2.0: (2.0, 1.0), 3.0: (6.0, 1.0)})
        arm = report({1.0: (3.0, 0.9), 2.0: (1.0, 0.9)}, labelled=3)
        result = rules.compare_arm(arm, baseline)
        self.assertEqual(result["common_support"], 2)
        self.assertAlmostEqual(result["coverage"], 2 / 3)
        self.assertAlmostEqual(result["baseline_mae_common_px"], 3.0)
        self.assertAlmostEqual(result["arm_mae_common_px"], 2.0)
        self.assertAlmostEqual(result["improvement_px"], 1.0)
        self.assertEqual(result["baseline_max_all_px"], 6.0)
        self.assertEqual(result["baseline_max_common_px"], 4.0)

    def test_silent_false_tracks_need_both_large_error_and_high_confidence(self):
        baseline = report({1.0: (5.0, 1.0), 2.0: (5.0, 1.0), 3.0: (1.0, 1.0)})
        arm = report({1.0: (5.0, 0.9), 2.0: (5.0, 0.3), 3.0: (1.0, 0.9)})
        result = rules.compare_arm(arm, baseline)
        self.assertEqual(result["silent_false_tracks_baseline"], 2)
        self.assertEqual(result["silent_false_tracks_arm"], 1)

    def test_no_common_support_gives_no_improvement_figure(self):
        result = rules.compare_arm(report({1.0: (1.0, 1.0)}), report({2.0: (1.0, 1.0)}))
        self.assertIsNone(result["improvement_px"])
        self.assertEqual(result["common_support"], 0)


class JudgeTests(unittest.TestCase):
    def three(self, **overrides):
        return {"a": clip(**overrides.get("a", {})), "b": clip(**overrides.get("b", {})), "c": clip(**overrides.get("c", {}))}

    def test_promising_when_every_rule_holds(self):
        self.assertTrue(rules.judge(self.three(), hub_arm=False)["promising"])

    def test_coverage_below_eighty_percent_on_one_clip_fails(self):
        self.assertFalse(rules.judge(self.three(b={"coverage": 0.79}), hub_arm=False)["promising"])
        self.assertTrue(rules.judge(self.three(b={"coverage": 0.80}), hub_arm=False)["promising"])

    def test_improvement_needs_two_clips_at_half_a_pixel_and_the_third_not_worse_than_a_quarter(self):
        ok = self.three(c={"improvement_px": -0.25})
        self.assertTrue(rules.judge(ok, hub_arm=False)["promising"])
        worse = self.three(c={"improvement_px": -0.26})
        self.assertFalse(rules.judge(worse, hub_arm=False)["promising"])
        only_one = self.three(b={"improvement_px": 0.4}, c={"improvement_px": 0.4})
        self.assertFalse(rules.judge(only_one, hub_arm=False)["promising"])

    def test_max_error_slack_is_two_pixels(self):
        self.assertTrue(rules.judge(self.three(a={"arm_max_common_px": 7.0}), hub_arm=False)["promising"])
        self.assertFalse(rules.judge(self.three(a={"arm_max_common_px": 7.1}), hub_arm=False)["promising"])

    def test_more_silent_false_tracks_than_the_baseline_on_any_clip_fails(self):
        self.assertFalse(rules.judge(self.three(a={"silent_false_tracks_arm": 2, "silent_false_tracks_baseline": 1}),
                                     hub_arm=False)["promising"])

    def test_unsupported_hub_makes_a_hub_arm_not_promising_but_not_the_colour_arm(self):
        clips = self.three(c={"hub_supported": False})
        self.assertFalse(rules.judge(clips, hub_arm=True)["promising"])
        self.assertTrue(rules.judge(clips, hub_arm=False)["promising"])

    def test_missing_improvement_figure_is_never_promising(self):
        self.assertFalse(rules.judge(self.three(a={"improvement_px": None}), hub_arm=False)["promising"])

    def test_the_lenient_max_variant_is_reported_but_does_not_decide(self):
        result = rules.judge(self.three(), hub_arm=False)
        self.assertIn("3b_lenient_variant_vs_baseline_max_over_all_labels", result["rules"])


class GateFailureTests(unittest.TestCase):
    def test_tracked_where_the_coarse_input_was_lost_is_a_violation(self):
        coarse = [{"timestamp_s": 0.0, "state": "tracked"}, {"timestamp_s": 1.0, "state": "lost"}]
        good = [{"timestamp_s": 0.0, "state": "tracked"}, {"timestamp_s": 1.0, "state": "lost"}]
        bad = [{"timestamp_s": 0.0, "state": "tracked"}, {"timestamp_s": 1.0, "state": "tracked"}]
        self.assertTrue(rules.gate_failure_without_coordinate(good, coarse))
        self.assertFalse(rules.gate_failure_without_coordinate(bad, coarse))

    def test_tracked_where_the_gate_reported_a_loss_reason_is_a_violation(self):
        coarse = [{"timestamp_s": 0.0, "state": "tracked"}]
        samples = [{"timestamp_s": 0.0, "state": "tracked"}]
        self.assertFalse(rules.gate_failure_without_coordinate(
            samples, coarse, [{"timestamp_s": 0.0, "loss_reason": "insufficient_rim_support"}]))
        self.assertTrue(rules.gate_failure_without_coordinate(samples, coarse, [{"timestamp_s": 0.0}]))

    def test_the_seed_anchor_frame_is_exempt(self):
        coarse = [{"timestamp_s": 0.0, "state": "lost"}]
        samples = [{"timestamp_s": 0.0, "state": "tracked"}]
        diagnostics = [{"timestamp_s": 0.0, "loss_reason": "insufficient_rim_support"}]
        self.assertFalse(rules.gate_failure_without_coordinate(samples, coarse, diagnostics))
        self.assertTrue(rules.gate_failure_without_coordinate(samples, coarse, diagnostics, seed_time=0.0))


if __name__ == "__main__":
    unittest.main()
