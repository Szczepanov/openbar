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
import run_phase_a  # noqa: E402
import run_phase_a2  # noqa: E402
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


class Sam3RuleTests(unittest.TestCase):
    def test_in_frame_keeps_pixel_centre_window(self) -> None:
        inside = [Candidate(0, 0, 10, 0.9), Candidate(99.9, 49.9, 10, 0.9)]
        outside = [Candidate(-0.1, 10, 10, 0.9), Candidate(100, 10, 10, 0.9), Candidate(10, 50, 10, 0.9)]
        self.assertEqual(selection.in_frame(inside + outside, 100, 50), inside)

    def test_margin_refuses_a_close_different_plate(self) -> None:
        close = selection.margin_refusal([Candidate(0, 0, 100, 0.95), Candidate(500, 0, 100, 0.91)])
        self.assertEqual((close["rule"], close["refused"], close["reason"], close["margin"]),
                         (selection.MARGIN_RULE, True, "close_scores", 0.04))
        clear = selection.margin_refusal([Candidate(0, 0, 100, 0.95), Candidate(500, 0, 100, 0.87)])
        self.assertEqual((clear["refused"], clear["margin"]), (False, 0.08))

    def test_margin_boundary_is_not_refused_whatever_the_float_error(self) -> None:
        for best, second in [(1.0, 0.95), (0.15, 0.10), (0.92, 0.87), (0.06, 0.01), (0.09, 0.04)]:
            with self.subTest(best=best):
                at = selection.margin_refusal([Candidate(0, 0, 100, best), Candidate(500, 0, 100, second)])
                self.assertEqual((at["refused"], at["margin"]), (False, 0.05))
        below = selection.margin_refusal([Candidate(0, 0, 100, 1.0), Candidate(500, 0, 100, 0.95001)])
        self.assertEqual((below["refused"], below["margin"]), (True, 0.04999))

    def test_concentric_runner_up_is_not_a_rival(self) -> None:
        inner_rim = selection.margin_refusal([Candidate(0, 0, 100, 0.95), Candidate(5, 0, 90, 0.94)])
        self.assertEqual((inner_rim["refused"], inner_rim["margin"]), (False, None))
        hub = selection.margin_refusal([Candidate(0, 0, 100, 0.95), Candidate(20, 10, 60, 0.94)])
        self.assertEqual((hub["refused"], hub["margin"]), (False, None))
        off_centre = selection.margin_refusal([Candidate(0, 0, 100, 0.95), Candidate(26, 0, 60, 0.94)])
        self.assertTrue(off_centre["refused"])
        third = selection.margin_refusal([Candidate(0, 0, 100, 0.95), Candidate(5, 0, 90, 0.94),
                                          Candidate(500, 0, 100, 0.93)])
        self.assertEqual((third["refused"], third["margin"]), (True, 0.02))

    def test_margin_empty_and_order(self) -> None:
        self.assertEqual(selection.margin_refusal([])["reason"], "no_candidate")
        a, b, c = Candidate(0, 0, 100, 0.2), Candidate(300, 0, 100, 0.9), Candidate(600, 0, 100, 0.88)
        self.assertEqual(selection.margin_refusal([a, b, c]), selection.margin_refusal([c, a, b]))

    def test_sam3_seed_drops_off_frame_and_implausible_candidates(self) -> None:
        rows = [row(-23.0, 900.0, 200.0, 0.97), row(500.0, 900.0, 700.0, 0.99), row(600.0, 1200.0, 180.0, 0.90),
                row(200.0, 1500.0, 150.0, 0.70)]
        seed = track.choose_seed_sam3(rows, 1080, 1920)
        self.assertEqual((seed["rule"], seed["selected_index"], seed["plausible_indices"]),
                         (track.SAM3_SEED_RULE, 2, [2, 3]))
        self.assertEqual((seed["refusal"]["rule"], seed["refusal"]["refused"], seed["refusal"]["margin"]),
                         (selection.MARGIN_RULE, False, 0.2))
        self.assertEqual(seed["close_score_refusal"]["rule"], selection.REFUSAL_RULE)

    def test_sam3_seed_skips_box_only_candidates(self) -> None:
        rows = [{**row(500.0, 900.0, 200.0, 0.99), "fit": "box"}, {**row(600.0, 1200.0, 180.0, 0.90), "fit": "mask_contour"}]
        seed = track.choose_seed_sam3(rows, 1080, 1920)
        self.assertEqual((seed["selected_index"], seed["plausible_indices"]), (1, [1]))
        only_box = track.choose_seed_sam3(rows[:1], 1080, 1920)
        self.assertEqual((only_box["selected_index"], only_box["refusal"]["reason"]), (None, "no_candidate"))

    def test_sam3_seed_with_nothing_selectable(self) -> None:
        seed = track.choose_seed_sam3([row(-5.0, 900.0, 200.0, 0.9)], 1080, 1920)
        self.assertEqual((seed["selected_index"], seed["refusal"]["reason"]), (None, "no_candidate"))


class TrackerChoiceTests(unittest.TestCase):
    def test_method_names_keep_phase_a2_names(self) -> None:
        self.assertEqual(track.method_name("sam2", "gdino_sam2"), track.METHOD)
        self.assertEqual(track.method_name("sam2", "sam3"), track.SAM3_METHOD)
        self.assertEqual(track.method_name("edgetam", "sam3"), "track_edgetam_sam3seed")
        self.assertEqual(track.method_name("edgetam", "gdino_sam2"), "track_edgetam")
        with self.assertRaises(ValueError):
            track.method_name("csrt", "sam3")
        with self.assertRaises(ValueError):
            track.method_name("edgetam", "hough")
        self.assertEqual(tuple(sorted(run_phase_a2.SEEDS)), track.SEED_NAMES)

    def test_provenance_hashes_the_pinned_tracker_and_records_the_rules(self) -> None:
        calls = []

        class Stub:
            def __init__(self, name: str) -> None:
                self.name = name

            def config(self) -> dict:
                return {"stub": self.name}

        original = run_phase_a.checkpoint_files
        run_phase_a.checkpoint_files = lambda repo, revision=None: calls.append((repo, revision)) or {"repo_id": repo}
        try:
            record = run_phase_a2.provenance("sam3", Stub("sam3"), Stub("edgetam"), 2, Path("openbar"))
        finally:
            run_phase_a.checkpoint_files = original
        import detectors
        self.assertEqual(sorted(calls), sorted([(detectors.SAM3_CHECKPOINT, None),
                                                (track.EDGETAM_VIDEO_CHECKPOINT, track.TRACKERS["edgetam"]["revision"])]))
        method = record["methods"]["track_edgetam_sam3seed"]
        self.assertEqual((method["seed_rule"]["rule"], method["refusal"]["rule"], method["track_stride"]),
                         (track.SAM3_SEED_RULE, selection.MARGIN_RULE, 2))
        import importlib.util
        for key, version in record["packages"].items():
            if importlib.util.find_spec(key) is not None:
                self.assertIsNotNone(version, f"{key} is importable but its distribution version was not found")

    def test_trackers_are_pinned_transformers_video_models(self) -> None:
        self.assertEqual(sorted(track.TRACKERS), ["edgetam", "sam2"])
        self.assertEqual(track.TRACKERS["edgetam"]["model_class"], "EdgeTamVideoModel")
        self.assertRegex(track.TRACKERS["edgetam"]["revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(track.TRACKERS["sam2"]["checkpoint"], track.SAM2_VIDEO_CHECKPOINT)


class RefusalRuleTests(unittest.TestCase):
    CLIP = {"scored": False, "frame0_refusal": {"rule": selection.MARGIN_RULE, "refused": True}}

    def test_uses_the_constants_the_run_recorded(self) -> None:
        recorded = {"rule": selection.MARGIN_RULE, "margin_score": 0.07, "also_recorded": {"rule": "x"}}
        self.assertEqual(score_phase_a.refusal_rule({"c": self.CLIP}, recorded),
                         {"rule": selection.MARGIN_RULE, "margin_score": 0.07})

    def test_unscored_clips_name_the_rule_and_nothing_is_never_defaulted(self) -> None:
        self.assertEqual(score_phase_a.refusal_rule({"c": self.CLIP})["rule"], selection.MARGIN_RULE)
        with self.assertRaises(SystemExit):
            score_phase_a.refusal_rule({})
        with self.assertRaises(SystemExit):
            score_phase_a.refusal_rule({"c": self.CLIP}, {"rule": selection.REFUSAL_RULE, "score_ratio": 0.9})


class RedetectTests(unittest.TestCase):
    def rows(self, *spec):
        return [{**row(x, y, r, sc), "fit": "mask_contour"} for x, y, r, sc in spec]

    def test_picks_the_plate_matching_the_seed_radius(self) -> None:
        rows = self.rows((500, 900, 130, 0.97), (600, 1200, 200, 0.99), (200, 1500, 125, 0.80))
        found = track.choose_redetect(rows, 1080, 1920, seed_radius_px=128.0)
        self.assertEqual((found["rule"], found["selected_index"], found["matching_indices"]),
                         (track.REDETECT_RULE, 0, [0, 2]))

    def test_refuses_two_close_plates_of_the_seed_size(self) -> None:
        rows = self.rows((500, 900, 130, 0.95), (200, 1500, 125, 0.93))
        found = track.choose_redetect(rows, 1080, 1920, seed_radius_px=128.0)
        self.assertEqual((found["selected_index"], found["refusal"]["refused"]), (None, True))

    def test_nothing_of_the_seed_size(self) -> None:
        rows = self.rows((500, 900, 200, 0.99), (-5, 900, 128, 0.99))
        found = track.choose_redetect(rows, 1080, 1920, seed_radius_px=128.0)
        self.assertEqual((found["selected_index"], found["matching_indices"]), (None, []))

    def test_method_name_for_redetect(self) -> None:
        self.assertEqual(track.method_name("edgetam", "sam3", redetect=True), "track_edgetam_sam3seed_redetect")
        with self.assertRaises(ValueError):
            track.method_name("sam2", "gdino_sam2", redetect=True)

    def test_run_clip_redetect_preserves_both_pre_and_post_restart_outcomes(self) -> None:
        import json
        import tempfile
        from unittest.mock import patch

        yy, xx = np.mgrid[:1920, :1080]
        mask_plate = (xx - 300) ** 2 + (yy - 600) ** 2 <= 100 ** 2
        mask_wrong = (xx - 850) ** 2 + (yy - 600) ** 2 <= 100 ** 2

        class FakeDetector:
            def propose_with_masks(self, _image: np.ndarray):
                return ([{**row(300.0, 600.0, 100.0, 0.96), "fit": "mask_contour"}], [mask_plate])

        class FakeTracker:
            name = "edgetam"

            def __init__(self) -> None:
                self.starts = 0
                self.step_calls = 0

            def start(self, _seed_mask: np.ndarray) -> None:
                self.starts += 1

            def step(self, _image: np.ndarray):
                self.step_calls += 1
                # Call 1 (frame 0, t=0.0): session 0 tracks plate at cx=300
                # Call 2 (frame 2, t=0.6): session 0 outputs wrong/stale object at cx=850 after gap -> triggers stopped_gap
                # Call 3 (frame 2, t=0.6): after restart, session 1 tracks plate at cx=300
                if self.step_calls == 2:
                    return mask_wrong, 1.5
                return mask_plate, 2.5

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            fake_frame = tmp / "f.png"
            fake_frame.write_bytes(b"fake")
            fixture = {
                "id": "self-clean-side-0203-17",
                "media": {"repository_path": "dummy.mp4", "sha256": "00"},
                "video": {"width_px": 1080, "height_px": 1920, "rotation_deg": 0},
            }
            probed = {
                "pts": [0, 300, 600],
                "timestamps_s": [0.0, 0.3, 0.6],
                "width_px": 1080,
                "height_px": 1920,
                "rotation_deg": 0,
            }
            dummy_img = np.zeros((1920, 1080, 3), dtype=np.uint8)
            with patch.object(run_phase_a2.label_package, "require_media_hash"), \
                    patch.object(run_phase_a2.label_package, "probe", return_value=probed), \
                    patch.object(run_phase_a2.label_package, "require_fixture_probe_match"), \
                    patch.object(run_phase_a2.label_package, "extract", return_value=[fake_frame, fake_frame]), \
                    patch.object(run_phase_a2.truth, "load_truth", return_value=[]), \
                    patch("cv2.imread", return_value=dummy_img):
                summary = run_phase_a2.run_clip(
                    fixture,
                    "scored-development-only",
                    tmp,
                    FakeDetector(),
                    FakeTracker(),
                    stride=2,
                    output_dir=tmp / "out",
                    seed_name="sam3",
                    redetect=True,
                )

            self.assertEqual(summary["redetected"], 1)
            out_jsonl = tmp / "out" / "candidates" / "track_edgetam_sam3seed_redetect" / "self-clean-side-0203-17.jsonl"
            records = [json.loads(line) for line in out_jsonl.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 2)
            restart_rec = records[1]

            # Pre-restart raw observation (cx ~ 850, session 0) must NOT be overwritten
            self.assertEqual(restart_rec["tracker_session_index"], 0)
            self.assertEqual( len(restart_rec["tracker_candidates"]), 1)
            self.assertAlmostEqual(restart_rec["tracker_candidates"][0]["center_x_px"], 850.0, delta=1.0)

            # Post-restart raw observation (cx ~ 300, session 1) must also be preserved
            self.assertEqual(restart_rec["post_restart_session_index"], 1)
            self.assertEqual(len(restart_rec["post_restart_tracker_candidates"]), 1)
            self.assertAlmostEqual(restart_rec["post_restart_tracker_candidates"][0]["center_x_px"], 300.0, delta=1.0)

            # Both pre- and post-restart candidates and provenance exist inside redetect
            self.assertEqual(restart_rec["redetect"]["pre_restart_session_index"], 0)
            self.assertEqual(restart_rec["redetect"]["pre_restart_guard_status"], "stopped_gap")
            self.assertEqual(restart_rec["redetect"]["pre_restart_tracker_candidates"], restart_rec["tracker_candidates"])
            self.assertEqual(restart_rec["redetect"]["post_restart_session_index"], 1)
            self.assertEqual(
                restart_rec["redetect"]["post_restart_tracker_candidates"],
                restart_rec["post_restart_tracker_candidates"],
            )
            self.assertEqual(restart_rec["guard"]["pre_restart_status"], "stopped_gap")
            self.assertEqual(restart_rec["guard"]["status"], "redetected")
            self.assertEqual(restart_rec["guard"]["session_index"], 1)
            self.assertEqual(restart_rec["candidates"], restart_rec["post_restart_tracker_candidates"])


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

    def test_choose_seed_student_accepts_synthetic_disc_from_box(self) -> None:
        student_row = {
            **row(300, 600, 100, 0.92, fit="box"),
            "mask_source": "synthetic_disc_from_box",
        }
        plain_box_row = row(500, 600, 100, 0.99, fit="box")
        seed = track.choose_seed_student([plain_box_row, student_row], 1080, 1920)
        self.assertEqual(seed["rule"], track.STUDENT_SEED_RULE)
        self.assertEqual(seed["selected_index"], 1)
        self.assertEqual(seed["plausible_indices"], [1])
        self.assertEqual(
            track.method_name("edgetam", "dfine_s_student", redetect=True),
            "track_edgetam_dfinestudentseed_redetect",
        )

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

    def test_recorded_seed_decision_is_scored_as_it_ran(self) -> None:
        # The run picked index 1 and recorded a margin refusal; the scorer must not recompute either.
        seed_rows = [row(-20.0, 600.0, 100.0, 0.99), row(300.0, 600.0, 100.0, 0.90)]
        refusal = {"rule": selection.MARGIN_RULE, "refused": False, "reason": None, "margin": None}
        records = [record(0, 0.0, [self.PLATE], seed={"candidates": seed_rows, "selected_index": 1,
                                                      "refusal": refusal})]
        records += [record(30, 0.5, [self.PLATE]), record(60, 1.0, [self.PLATE])]
        result = score_phase_a.score_clip(records, self.POINTS)
        self.assertTrue(result["clip_frame0"]["plausible_top1"])
        self.assertEqual(result["frame0_refusal"], refusal)
        close = {"rule": selection.REFUSAL_RULE, "refused": True, "reason": "close_scores", "ratio": 0.95}
        records[0]["seed"]["close_score_refusal"] = close
        compared = score_phase_a.score_clip(records, self.POINTS)
        self.assertEqual(score_phase_a.aggregate({"c": compared})["refusal"]["rule"], selection.MARGIN_RULE)
        self.assertEqual(score_phase_a.aggregate({"c": compared})["refusal"]["margin_score"], selection.MARGIN_SCORE)
        self.assertEqual(score_phase_a.aggregate({"c": compared})["refusal"]["close_score_comparison"]["refused_clips"], ["c"])
        mixed = {"a": compared, "b": score_phase_a.score_clip(self.track_records([row(300.0, 600.0, 100.0, 0.5)]), self.POINTS)}
        with self.assertRaises(SystemExit):
            score_phase_a.aggregate(mixed)
        nothing = [record(0, 0.0, [], seed={"candidates": seed_rows[:1], "selected_index": None, "refusal": refusal})]
        self.assertFalse(score_phase_a.score_clip(nothing, self.POINTS[:1])["clip_frame0"]["plausible_top1"])

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
