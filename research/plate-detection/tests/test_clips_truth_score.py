"""Clip guard, frame plan, truth loading and scoring on synthetic data only (#233)."""
from __future__ import annotations

import json
import contextlib
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import clips  # noqa: E402
import score_phase_a  # noqa: E402
import truth  # noqa: E402

HEADER = ("timestamp_s,requested_timestamp_s,frame_index,annotation_state,visibility,quality,x_px,y_px,"
          "radius_px,diameter_px,left_px,top_px,right_px,bottom_px,notes\n")


def fixture(name: str, purpose: str = "development", *tags: str) -> dict:
    return {"id": name, "purpose": purpose, "conditions": {"challenge_tags": list(tags)}}


class GuardTests(unittest.TestCase):
    def test_roles(self) -> None:
        self.assertEqual(clips.development_role(fixture("x", "development", "split-exploration")), "exploration")
        self.assertEqual(clips.development_role(fixture("self-snatch-side-1015-01", "validation", "split-frozen-eval")),
                         "scored-development-only")

    def test_refusals(self) -> None:
        refused = [
            fixture("self-snatch-side-1015-01", "validation", "split-reserved-57"),  # reserved wins
            fixture("x", "validation", "split-exploration"),  # contradictory tags
            fixture("self-snatch-side-0203-16", "boundary", "split-frozen-eval"),  # unexposed pilot clip
            fixture("self-clean-side-004", "validation"),
        ]
        for item in refused:
            with self.subTest(item=item["id"]), self.assertRaises(clips.ClipRefused):
                clips.development_role(item)

    def test_scored_list_matches_the_baseline_report(self) -> None:
        report = Path(__file__).resolve().parents[3] / "docs" / "validation" / "PLATE_SELECTION_BASELINE_REPORT.md"
        named = set(re.findall(r"self-[a-z-]+-\d{4}-\d{2}", report.read_text(encoding="utf-8")))
        self.assertEqual(named, clips.SCORED_DEVELOPMENT_ONLY)

    def test_development_clips_drops_refused(self) -> None:
        fixtures = {f["id"]: f for f in [fixture("b", "development", "split-exploration"),
                                         fixture("a", "validation", "split-reserved-57")]}
        self.assertEqual(clips.development_clips(fixtures), {"b": "exploration"})


class ScoreEntryPointTests(unittest.TestCase):
    def test_cli_checks_all_current_roles_before_candidates_or_labels(self) -> None:
        refused = [
            (fixture("x", "validation", "split-reserved-57"), "exploration"),
            (fixture("x", "validation", "split-exploration"), "exploration"),
            (fixture("x", "development", "split-exploration"), "scored-development-only"),
            (fixture("x", "development", "split-frozen-eval"), "exploration"),
            (fixture("other", "development", "split-exploration"), "exploration"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "run"
            run.mkdir()
            private = root / "validation" / "private"
            (private / "vbt").mkdir(parents=True)
            (private / "vbt" / "manifest.json").write_text('{"fixtures": []}')
            output = root / "score.json"
            for current, recorded_role in refused:
                with self.subTest(current=current, recorded_role=recorded_role):
                    (private / "manifest.json").write_text(json.dumps({"fixtures": [
                        fixture("allowed", "development", "split-exploration"), current]}))
                    (run / "run.json").write_text(json.dumps({"clips": {
                        "allowed": "exploration", "x": recorded_role}, "methods": {}}))
                    with patch.object(score_phase_a, "score_run", side_effect=AssertionError("opened inputs")):
                        with self.assertRaises(clips.ClipRefused):
                            score_phase_a.main(["--run-dir", str(run), "--repo-root", str(root),
                                                "--output", str(output)])
                    self.assertFalse(output.exists())

    def test_cli_scores_allowed_development_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "run"
            run.mkdir()
            private = root / "validation" / "private"
            private.mkdir(parents=True)
            manifest = root / "current-manifest.json"
            manifest.write_text(json.dumps({"fixtures": [
                fixture("allowed", "development", "split-exploration"),
                fixture("self-other-side-0215-27", "validation", "split-frozen-eval")]}))
            roles = {"allowed": "exploration", "self-other-side-0215-27": "scored-development-only"}
            (run / "run.json").write_text(json.dumps({"clips": roles, "methods": {}}))
            output = root / "score.json"
            with patch.object(score_phase_a, "score_run", return_value={"methods": {}}) as score, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(score_phase_a.main(["--run-dir", str(run), "--repo-root", str(root),
                                                    "--manifest", str(manifest),
                                                    "--output", str(output)]), 0)
            score.assert_called_once_with(run, private, roles)
            self.assertEqual(json.loads(output.read_text()), {"methods": {}})


class FramePlanTests(unittest.TestCase):
    def test_grid_plus_truth_frames(self) -> None:
        times = [i / 10 for i in range(31)]  # 0.0 .. 3.0 s at 10 fps
        self.assertEqual(clips.frame_plan(times, [0.72]), [0, 5, 7, 10, 15, 20, 25, 30])

    def test_long_clip_grid_is_thinned(self) -> None:
        times = [i / 10 for i in range(1001)]  # 100 s
        plan = clips.frame_plan(times, [])
        self.assertEqual(len(plan), clips.MAX_GRID_FRAMES)
        self.assertEqual((plan[0], plan[-1]), (0, 1000))

    def test_empty_refused(self) -> None:
        with self.assertRaises(ValueError):
            clips.frame_plan([], [])


class TruthTests(unittest.TestCase):
    def test_pass_a_with_adjudicated_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labels = Path(tmp) / "frozen-eval" / "labels"
            labels.mkdir(parents=True)
            (labels / "c.a.labels.csv").write_text(
                HEADER + "0.0,,0,labelled,visible,high,10,20,5,,,,,,\n"
                "1.0,,30,labelled,visible,high,11,21,5,,,,,,\n"
                "2.0,,60,lost,not_visible,low,,,,,,,,,\n", encoding="utf-8")
            (labels / "c.adj.labels.csv").write_text(
                HEADER + "1.0,,30,labelled,visible,high,12,22,6,,,,,,\n", encoding="utf-8")
            points = truth.load_truth("c", Path(tmp))
        self.assertEqual([(p.timestamp_s, p.center_x_px, p.radius_px) for p in points], [(0.0, 10.0, 5.0), (1.0, 12.0, 6.0)])

    def test_adjudicated_lost_row_removes_the_point(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labels = Path(tmp) / "frozen-eval" / "labels"
            labels.mkdir(parents=True)
            (labels / "c.a.labels.csv").write_text(
                HEADER + "0.0,,0,labelled,visible,high,10,20,5,,,,,,\n1.0,,30,labelled,visible,high,11,21,5,,,,,,\n",
                encoding="utf-8")
            (labels / "c.adj.labels.csv").write_text(HEADER + "1.0,,30,lost,not_visible,low,,,,,,,,,\n", encoding="utf-8")
            self.assertEqual([p.timestamp_s for p in truth.load_truth("c", Path(tmp))], [0.0])

    def test_seed_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seeds = Path(tmp) / "vbt" / "seeds"
            seeds.mkdir(parents=True)
            (seeds / "v.manual-target-seed-v1.json").write_text(json.dumps({"schema_version": 1, "seed": {
                "timestamp_s": 0.0, "frame_index": 0, "target": {"center": {"x_px": 3.0, "y_px": 4.0}, "radius_px": 9.0}}}))
            self.assertEqual(truth.load_truth("v", Path(tmp)), [truth.TruthPoint(0.0, 3.0, 4.0, 9.0, "v.manual-target-seed-v1.json")])
            self.assertEqual(truth.load_truth("none", Path(tmp)), [])

    def test_non_finite_refused(self) -> None:
        with self.assertRaises(ValueError):
            truth._point(0.0, float("nan"), 1.0, 1.0, "x")


def record(index: int, time_s: float, *cands: tuple[float, float, float, float]) -> dict:
    return {"frame_index": index, "time_s": time_s, "latency_s": 0.1, "width_px": 1080, "height_px": 1920,
            "candidates": [{"center_x_px": x, "center_y_px": y, "radius_px": r, "score": s} for x, y, r, s in cands]}


class ScoreTests(unittest.TestCase):
    def test_motion_recovers_what_top1_misses(self) -> None:
        clutter = (400.0, 500.0, 60.0, 0.9)
        records = [record(0, 0.0, clutter, (100.0, 300.0, 50.0, 0.6))]
        records += [record(i, i * 0.5, clutter, (100.0, 300.0 - 40 * i, 50.0, 0.6)) for i in range(1, 6)]
        points = [truth.TruthPoint(0.0, 100.0, 300.0, 50.0, "t"), truth.TruthPoint(1.0, 100.0, 220.0, 50.0, "t")]
        result = score_phase_a.score_clip(records, points)
        self.assertEqual((result["labelled_frames"], result["frame_recall"], result["frame_top1"]), (2, 2, 0))
        self.assertEqual(result["clip"], {"top1": False, "plausible_top1": False, "largest_plausible": False,
                                          "motion": True, "plausible_motion": True})
        self.assertTrue(result["decision_has_target"])

    def test_plausibility_drops_whole_barbell_box(self) -> None:
        barbell = (500.0, 900.0, 700.0, 0.9)  # radius above 0.30 x 1080
        plate = (300.0, 600.0, 180.0, 0.5)
        records = [record(i, i * 0.5, barbell, plate) for i in range(6)]
        result = score_phase_a.score_clip(records, [truth.TruthPoint(0.0, 300.0, 600.0, 180.0, "t")])
        self.assertEqual((result["clip"]["top1"], result["clip"]["plausible_top1"]), (False, True))

    def test_frame0_decision_is_label_free(self) -> None:
        plate = (300.0, 600.0, 180.0, 0.5)
        records = [record(i, i * 0.5, plate) for i in range(6)]
        # labelled only from 1.0 s: the default decision frame comes from a label, frame 0 is not scorable
        late = score_phase_a.score_clip(records, [truth.TruthPoint(1.0, 300.0, 600.0, 180.0, "t")])
        self.assertTrue(late["decision_from_label"])
        self.assertIsNone(late["clip_frame0"])
        early = score_phase_a.score_clip(records, [truth.TruthPoint(0.0, 300.0, 600.0, 180.0, "t")])
        self.assertFalse(early["decision_from_label"])
        self.assertTrue(early["clip_frame0"]["plausible_top1"])
        aggregate = score_phase_a.aggregate({"late": late, "early": early})
        self.assertEqual((aggregate["frame0_clips"], aggregate["decision_from_label"]), (1, ["late"]))

    def test_unrecorded_clip_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "candidates" / "hough").mkdir(parents=True)
            (Path(tmp) / "candidates" / "hough" / "x.jsonl").write_text(json.dumps(record(0, 0.0)) + "\n")
            with self.assertRaises(SystemExit):
                score_phase_a.score_run(Path(tmp), Path(tmp), {})

    def test_unlabelled_clip_is_not_scored(self) -> None:
        result = score_phase_a.score_clip([record(0, 0.0)], [])
        self.assertFalse(result["scored"])
        self.assertEqual(score_phase_a.aggregate({"a": result})["scored_clips"], 0)

    def test_truth_off_the_sampled_frames_is_dropped(self) -> None:
        result = score_phase_a.score_clip([record(0, 0.0)], [truth.TruthPoint(0.25, 1.0, 1.0, 1.0, "t")])
        self.assertFalse(result["scored"])


if __name__ == "__main__":
    unittest.main()
