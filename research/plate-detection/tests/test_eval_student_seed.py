from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import eval_student_seed as ev  # noqa: E402
import truth  # noqa: E402


def _row(cx: float, cy: float, r: float, score: float, raw: tuple[float, float, float] | None = None) -> dict:
    row = {"center_x_px": cx, "center_y_px": cy, "radius_px": r, "score": score}
    if raw is not None:
        row["raw_box_circle"] = {"center_x_px": raw[0], "center_y_px": raw[1], "radius_px": raw[2]}
    return row


POINT = truth.TruthPoint(0.0, 500.0, 800.0, 100.0, "test")


class EvalStudentSeedTests(unittest.TestCase):
    def test_clip_folds_maps_included_and_excluded(self) -> None:
        manifest = {"clips": [
            {"fixture_id": "a", "status": "included", "fold": "train"},
            {"fixture_id": "b", "status": "included", "fold": "val"},
            {"fixture_id": "c", "status": "excluded", "fold": "train"},
        ]}
        self.assertEqual(ev.clip_folds(manifest), {"a": "train", "b": "val", "c": "excluded"})
        with self.assertRaises(ValueError):
            ev.clip_folds({"clips": [{"fixture_id": "d", "status": "included", "fold": None}]})

    def test_score_seed_hit_with_raw_and_refined_errors(self) -> None:
        rows = [_row(502.0, 800.0, 101.0, 0.9, raw=(510.0, 800.0, 104.0))]
        seed = {"selected_index": 0, "refusal": {"refused": False, "reason": None}}
        self.assertEqual(ev.score_seed(rows, seed, POINT), {
            "scored": True, "selected": True, "refused": False, "refusal_reason": None,
            "plausible_top1_hit": True, "accepted_hit": True,
            "raw_centre_error_rel": 0.1, "raw_radius_abs_error_rel": 0.04,
            "refined_centre_error_rel": 0.02, "refined_radius_abs_error_rel": 0.01,
        })

    def test_score_seed_refused_hit_is_not_accepted_and_miss_has_no_errors(self) -> None:
        rows = [_row(502.0, 800.0, 101.0, 0.9), _row(100.0, 100.0, 100.0, 0.88)]
        refused = ev.score_seed(rows, {"selected_index": 0, "refusal": {"refused": True, "reason": "close_scores"}},
                                POINT)
        self.assertTrue(refused["plausible_top1_hit"])
        self.assertFalse(refused["accepted_hit"])
        miss = ev.score_seed(rows, {"selected_index": 1, "refusal": {"refused": False, "reason": None}}, POINT)
        self.assertFalse(miss["plausible_top1_hit"])
        self.assertIsNone(miss["raw_radius_abs_error_rel"])
        none = ev.score_seed([], {"selected_index": None, "refusal": {"refused": False, "reason": "no_candidate"}},
                             POINT)
        self.assertFalse(none["selected"])

    def test_summarize_separates_folds(self) -> None:
        hit = {"scored": True, "plausible_top1_hit": True, "accepted_hit": True, "refused": False,
               "raw_centre_error_rel": 0.02, "raw_radius_abs_error_rel": 0.01,
               "refined_centre_error_rel": 0.01, "refined_radius_abs_error_rel": 0.005}
        miss = {**hit, "plausible_top1_hit": False, "accepted_hit": False,
                **{k: None for k in ("raw_centre_error_rel", "raw_radius_abs_error_rel",
                                     "refined_centre_error_rel", "refined_radius_abs_error_rel")}}
        per_clip = {"a": {**hit, "fold": "train"}, "b": {**miss, "fold": "val"},
                    "c": {"scored": False, "fold": "excluded"}}
        val = ev.summarize(per_clip, "val")
        self.assertEqual((val["clips"], val["scored_clips"], val["plausible_top1_hits"]), (1, 1, 0))
        self.assertIsNone(val["raw_radius_err_median"])
        all_dev = ev.summarize(per_clip)
        self.assertEqual((all_dev["clips"], all_dev["scored_clips"], all_dev["plausible_top1_hits"]), (3, 2, 1))
        self.assertEqual(all_dev["raw_radius_err_median"], 0.01)


if __name__ == "__main__":
    unittest.main()
