"""Phase A.2 pure functions: track frame plan, seed choice, refusal, tracked rows and scoring (#233)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import clips  # noqa: E402
import geometry  # noqa: E402
import score_phase_a  # noqa: E402
import selection  # noqa: E402
import track  # noqa: E402
import truth  # noqa: E402
from selection import Candidate  # noqa: E402


def row(x: float, y: float, r: float, s: float) -> dict:
    return {"center_x_px": x, "center_y_px": y, "radius_px": r, "score": s}


class TrackPlanTests(unittest.TestCase):
    def test_stride_plus_labelled_frames(self) -> None:
        times = [i / 10 for i in range(10)]
        self.assertEqual(clips.track_plan(times, [0.31, 0.9], stride=4), [0, 3, 4, 8, 9])

    def test_frame_zero_is_always_first(self) -> None:
        self.assertEqual(clips.track_plan([0.0, 0.1, 0.2], [0.2], stride=5)[0], 0)

    def test_invalid(self) -> None:
        for times, stride in (([], 2), ([0.0], 0)):
            with self.subTest(times=times, stride=stride), self.assertRaises(ValueError):
                clips.track_plan(times, [], stride)


class RefusalTests(unittest.TestCase):
    def test_close_scores_refuse_at_the_ratio(self) -> None:
        at = selection.refusal([Candidate(0, 0, 10, 0.5), Candidate(50, 0, 10, 0.45)])
        self.assertEqual((at["refused"], at["reason"], at["ratio"]), (True, "close_scores", 0.9))
        below = selection.refusal([Candidate(0, 0, 10, 0.5), Candidate(50, 0, 10, 0.44)])
        self.assertFalse(below["refused"])

    def test_one_or_no_candidate_does_not_refuse(self) -> None:
        self.assertFalse(selection.refusal([Candidate(0, 0, 10, 0.3)])["refused"])
        self.assertEqual(selection.refusal([])["reason"], "no_candidate")

    def test_order_does_not_matter(self) -> None:
        a, b, c = Candidate(0, 0, 10, 0.2), Candidate(1, 0, 10, 0.9), Candidate(2, 0, 10, 0.85)
        self.assertEqual(selection.refusal([a, b, c]), selection.refusal([c, a, b]))


class SeedTests(unittest.TestCase):
    def test_plausible_top1_index(self) -> None:
        rows = [row(500, 900, 700, 0.9), row(300, 600, 100, 0.4), row(800, 600, 90, 0.5)]
        seed = track.choose_seed(rows, 1080, 1920)
        self.assertEqual((seed["selected_index"], seed["plausible_indices"]), (2, [1, 2]))
        self.assertFalse(seed["refusal"]["refused"])  # 0.4 / 0.5 = 0.8

    def test_nothing_plausible(self) -> None:
        seed = track.choose_seed([row(500, 900, 700, 0.9)], 1080, 1920)
        self.assertIsNone(seed["selected_index"])
        self.assertEqual(seed["refusal"]["reason"], "no_candidate")

    def test_tracked_row(self) -> None:
        mask = np.zeros((60, 60), dtype=bool)
        yy, xx = np.mgrid[:60, :60]
        mask[(xx - 30) ** 2 + (yy - 25) ** 2 <= 15 ** 2] = True
        out = track.tracked_row(geometry.circle_from_mask(mask), 0.0)
        self.assertEqual(out["score"], 0.5)
        self.assertAlmostEqual(out["center_x_px"], 30.0, delta=0.5)
        self.assertAlmostEqual(out["radius_px"], 15.5, delta=0.6)
        self.assertIsNone(track.tracked_row(None, 3.0))
        with self.assertRaises(ValueError):
            track.tracked_row(geometry.circle_from_mask(mask), float("nan"))


def record(index: int, time_s: float, cands: list[dict], **extra) -> dict:
    return {"frame_index": index, "time_s": time_s, "latency_s": 0.1, "width_px": 1080, "height_px": 1920,
            "candidates": cands, **extra}


class ScoreTrackTests(unittest.TestCase):
    PLATE = row(300.0, 600.0, 100.0, 0.99)
    POINTS = [truth.TruthPoint(t, 300.0, 600.0, 100.0, "t") for t in (0.0, 0.5, 1.0)]

    def track_records(self, seed_rows: list[dict], lost_at: float | None = None) -> list[dict]:
        records = [record(0, 0.0, [self.PLATE], seed={"candidates": seed_rows})]
        for i, t in enumerate((0.5, 1.0), start=1):
            records.append(record(i * 30, t, [] if t == lost_at else [self.PLATE]))
        return records

    def test_track_frame0_uses_seed_and_refusal(self) -> None:
        seed = [row(300.0, 600.0, 100.0, 0.50), row(800.0, 600.0, 90.0, 0.48)]  # ratio 0.96
        result = score_phase_a.score_clip(self.track_records(seed, lost_at=1.0), self.POINTS)
        self.assertTrue(result["frame0_refusal"]["refused"])
        self.assertTrue(result["clip_frame0"]["plausible_top1"])
        self.assertEqual((result["frame_plausible_top1"], result["labelled_frames"], result["lost_frames"]), (2, 3, 1))
        aggregate = score_phase_a.aggregate({"c": {"role": "exploration", **result}})
        refusal = aggregate["refusal"]
        self.assertEqual((refusal["refused_clips"], refusal["frame0_refused"],
                          refusal["frame0_refused_would_be_correct"], refusal["frame0_accepted"],
                          refusal["accepted_labelled_frames"]), (["c"], 1, 1, 0, 0))
        self.assertEqual(aggregate["clips_every_frame_on_target"], 0)

    def test_accepted_clip_counts_its_frames(self) -> None:
        seed = [row(300.0, 600.0, 100.0, 0.50), row(800.0, 600.0, 90.0, 0.20)]
        result = score_phase_a.score_clip(self.track_records(seed), self.POINTS)
        refusal = score_phase_a.aggregate({"c": result})["refusal"]
        self.assertEqual((refusal["frame0_accepted_correct"], refusal["accepted_frame_plausible_top1"],
                          refusal["accepted_labelled_frames"]), (1, 3, 3))

    def test_wrong_seed_is_a_frame0_miss(self) -> None:
        stored = row(800.0, 600.0, 90.0, 0.60)
        records = [record(0, 0.0, [stored], seed={"candidates": [stored, row(300.0, 600.0, 100.0, 0.30)]})]
        records += [record(30, 0.5, [stored])]
        result = score_phase_a.score_clip(records, self.POINTS[:2])
        self.assertFalse(result["clip_frame0"]["plausible_top1"])
        self.assertEqual(result["frame_plausible_top1"], 0)

    def test_detection_records_refuse_on_their_own_candidates(self) -> None:
        records = [record(i * 30, t, [row(300.0, 600.0, 100.0, 0.5), row(800.0, 600.0, 90.0, 0.47)])
                   for i, t in enumerate((0.0, 0.5, 1.0))]
        result = score_phase_a.score_clip(records, self.POINTS)
        self.assertTrue(result["frame0_refusal"]["refused"])

    def test_per_clip_table(self) -> None:
        seed = [row(300.0, 600.0, 100.0, 0.50), row(800.0, 600.0, 90.0, 0.48)]
        result = score_phase_a.score_clip(self.track_records(seed, lost_at=1.0), self.POINTS)
        text = score_phase_a.per_clip_table({"track_sam2": {"per_clip": {"c": result}}})
        self.assertIn("| c | 2/3 ok R |", text)


if __name__ == "__main__":
    unittest.main()
