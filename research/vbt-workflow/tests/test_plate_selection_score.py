"""Frozen plate-selection scorer: outcomes, groups, denominators, intervals, score-once.

Synthetic data only; no private media or labels are read.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import plate_selection_score as pss  # noqa: E402
from test_plate_selection_eval import Fixture, sha  # noqa: E402

HEADER = ("timestamp_s,requested_timestamp_s,frame_index,annotation_state,visibility,quality,x_px,y_px,"
          "radius_px,diameter_px,left_px,top_px,right_px,bottom_px,notes\n")
SHEET = ("fixture_id,annotator,expected_policy,movement_onset_s\n"
         "clip-a,a,select,1.0\nclip-a,b,select,1.0\nclip-b,a,select,1.0\nclip-b,b,select,1.0\n")
TC, TR = 5.4, 5.5


def label_csv(x: float, y: float, r: float, state: str = "labelled") -> bytes:
    geometry = f"{x},{y},{r}" if state == "labelled" else ",,"
    return (HEADER + f"0.0,,0,{state},visible,high,{geometry},,,,,,\n").encode()


def circle(x, y, r, **extra):
    return {"center_x_px": x, "center_y_px": y, "radius_px": r, **extra}


def observation(final, raw=(), ranked=(), selected=None, margin=0.1):
    return {"final": final, "raw": list(raw), "ranked": list(ranked), "selected": selected, "support_margin": margin}


def entry(fid, final, obs=None, **extra):
    return {"fixture_id": fid, "frame": {"requested_time_s": 0.0, "frame_index": 0, "actual_time_s": 0.0, "probe_s": 0.1, "decode_s": 0.2},
            "final": final,
            "observation": obs or observation(final, selected=final if final.get("status") == "suggested" else None),
            "latency": {"cold_search_s": 0.05, "warm_median_s": 0.04, "warm_search_s": [0.04]},
            "wall_s": 1.0, "timed_out": False, **extra}


SUGGESTED = {"status": "suggested", **circle(101.0, 100.0, 50.0)}
FAILED = {"status": "failed", "reason": "best circle has edge support 0.30 < 0.4"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = Fixture(Path(self.tmp.name))
        for fid in ("clip-a", "clip-b"):
            self.set_labels(fid, label_csv(100, 100, 50), label_csv(102, 100, 50))
        self.fx.record["scoring"]["geometry_reference"] = f"mean of a/b; Tc={TC} px, Tr={TR} px (pilot)"
        self.set_sheet(SHEET)
        self.set_adjudication([])
        self.run_dir = self.fx.root / "run"
        self.run_dir.mkdir()

    def set_labels(self, fid, a, b):
        for key, data in (("a", a), ("b", b)):
            path = f"labels/{fid}.{key}.csv"
            digest = self.fx.write(path, data)
            for clip in self.fx.record["inputs"]["clips"]:
                for label in clip["label_files"]:
                    if label["path"] == path:
                        label["sha256"] = digest

    def set_sheet(self, text):
        self.fx.write("labels.csv", text.encode())
        self.fx.record["inputs"]["labels_csv"]["sha256"] = sha(self.fx.files["labels.csv"])

    def set_adjudication(self, frames):
        digest = self.fx.write("adjudication.json", json.dumps({"frames": frames}).encode())
        for clip in self.fx.record["inputs"]["clips"]:
            if clip["adjudication"]["path"] is not None:
                clip["adjudication"]["sha256"] = digest

    def write_run(self, clips, record_sha256):
        for name, document in (("started.json", {"record_sha256": record_sha256}),
                               ("observations.json", {"record_sha256": record_sha256, "clips": clips})):
            (self.run_dir / name).write_text(json.dumps(document), encoding="utf-8")
        (self.run_dir / "clips.jsonl").write_text("".join(json.dumps(clip) + "\n" for clip in clips),
                                                  encoding="utf-8")

    def score(self, clips, record=None):
        path, digest = self.fx.save(record)
        self.write_run(clips, digest)
        return json.loads(pss.score(path, digest, self.fx.root, self.run_dir).read_text(encoding="utf-8"))

    def outcomes(self, document):
        return {row["fixture_id"]: row["outcome"] for row in document["clips"]}


class WilsonAndToleranceTests(unittest.TestCase):
    def test_wilson_matches_protocol_examples(self):
        full = pss.wilson(20, 20)
        self.assertAlmostEqual(full["low"] * 100, 83.89, places=2)
        self.assertEqual(full["high"], 1.0)
        ninety = pss.wilson(18, 20)
        self.assertAlmostEqual(ninety["low"] * 100, 69.90, places=2)
        self.assertAlmostEqual(ninety["high"] * 100, 97.21, places=2)
        self.assertEqual(pss.wilson(0, 0), {"x": 0, "n": 0, "rate": None, "low": None, "high": None})

    def test_tolerances_come_from_the_record_text_once(self):
        self.assertEqual(pss.tolerances({"scoring": {"geometry_reference": "Tc=5.4 px, Tr=5.5 px"}}), (5.4, 5.5))
        for text in ("no tolerances", "Tc=5 px, Tr=5 px; Tc=6 px, Tr=6 px", "Tc=0 px, Tr=1 px", None):
            with self.subTest(text=text), self.assertRaises(pss.ScoreError):
                pss.tolerances({"scoring": {"geometry_reference": text}})


class OutcomeTests(Base):
    def test_accepted_correct_and_missed(self):
        document = self.score([entry("clip-a", SUGGESTED), entry("clip-b", FAILED)])
        self.assertEqual(self.outcomes(document), {"clip-a": "accepted_correct", "clip-b": "missed_refused"})
        pooled = document["pooled"]
        self.assertEqual((pooled["accepted_correct_per_eligible"]["x"], pooled["accepted_correct_per_eligible"]["n"]),
                         (1, 2))
        self.assertIsNone(pooled["unsafe_acceptance_per_required_refusal"]["rate"])
        self.assertEqual(document["clips"][0]["final_geometry_error"]["centre_error_px"], 0.0)
        self.assertEqual(document["geometry"]["accepted_correct"]["centre_error_px"]["n"], 1)

    def test_wrong_target_needs_both_centre_and_radius(self):
        far = {"status": "suggested", **circle(110.0, 100.0, 50.0)}
        inner = {"status": "suggested", **circle(101.0, 100.0, 40.0)}  # concentric inner rim
        document = self.score([entry("clip-a", far), entry("clip-b", inner)])
        self.assertEqual(self.outcomes(document), {"clip-a": "wrong_target", "clip-b": "wrong_target"})
        self.assertEqual(document["clips"][0]["wrong_target_class"], "unknown")
        self.assertEqual(document["pooled"]["wrong_target_per_emitted_eligible"]["n"], 2)

    def test_errors_and_timeouts_keep_their_group_and_denominator(self):
        timed = entry("clip-b", SUGGESTED, timed_out=True)  # a correct emission past the budget
        error = {"fixture_id": "clip-a", "error": "RuntimeError: decoder failed", "wall_s": 0.5, "timed_out": False}
        document = self.score([error, timed])
        self.assertEqual(self.outcomes(document), {"clip-a": "error", "clip-b": "timeout"})
        self.assertEqual([row["group"] for row in document["clips"]], ["S", "S"])
        self.assertEqual(document["pooled"]["accepted_correct_per_eligible"], pss.wilson(0, 2))
        self.assertEqual((document["stages"]["n_eligible"], document["stages"]["errors"]), (2, 1))

    def test_timing_failure_when_decision_frame_is_not_before_onset(self):
        self.set_sheet(SHEET.replace("clip-a,b,select,1.0", "clip-a,b,select,0.0"))
        document = self.score([entry("clip-a", SUGGESTED), entry("clip-b", SUGGESTED)])
        self.assertEqual(self.outcomes(document), {"clip-a": "timing_failure", "clip-b": "accepted_correct"})
        self.assertEqual(document["stages"]["final_stage_changes"], 0)

    def test_policy_groups(self):
        record = copy.deepcopy(self.fx.record)
        record["inputs"]["clips"][0]["expected_policy"] = "indeterminate"
        record["inputs"]["clips"][1]["expected_policy"] = "refuse_no_target"
        document = self.score([entry("clip-a", SUGGESTED), entry("clip-b", SUGGESTED)], record)
        self.assertEqual(self.outcomes(document), {"clip-a": "indeterminate_emitted", "clip-b": "unsafe_acceptance"})
        self.assertEqual(document["pooled"]["unsafe_acceptance_per_required_refusal"]["x"], 1)
        self.assertEqual(document["pooled"]["accepted_correct_per_eligible"]["n"], 0)

    def test_one_missing_pass_makes_a_select_case_indeterminate(self):
        self.set_labels("clip-b", label_csv(0, 0, 0, state="unlabelable"), label_csv(102, 100, 50))
        row = self.score([entry("clip-a", SUGGESTED), entry("clip-b", FAILED)])["clips"][1]
        self.assertEqual((row["group"], row["outcome"], row["reference"]), ("I", "indeterminate_refused", None))

    def test_disagreeing_passes_are_never_averaged(self):
        self.set_labels("clip-a", label_csv(100, 100, 50), label_csv(160, 100, 30))
        midpoint = {"status": "suggested", **circle(130.0, 100.0, 40.0)}
        document = self.score([entry("clip-a", midpoint), entry("clip-b", SUGGESTED)])
        row = document["clips"][0]
        self.assertEqual((row["group"], row["outcome"]), ("I", "indeterminate_emitted"))
        self.assertIn("disagree", row["group_reason"])
        self.assertEqual(document["pooled"]["accepted_correct_per_eligible"]["n"], 1)

    def test_adjudication_at_the_decision_frame_decides(self):
        self.set_labels("clip-a", label_csv(100, 100, 50), label_csv(110, 100, 50))
        self.set_adjudication([{"fixture_id": "clip-a", "frame_index": 0, "status": "adjudicated",
                                "x_px": 104.0, "y_px": 100.0},
                               {"fixture_id": "clip-b", "frame_index": 0, "status": "unresolved", "x_px": None}])
        near = {"status": "suggested", **circle(105.0, 100.0, 50.0)}
        document = self.score([entry("clip-a", near), entry("clip-b", SUGGESTED)])
        self.assertEqual(self.outcomes(document), {"clip-a": "accepted_correct", "clip-b": "indeterminate_emitted"})
        self.assertEqual(document["clips"][0]["reference"]["x_px"], 104.0)

    def test_adjudication_elsewhere_in_the_clip_does_not_apply(self):
        self.set_adjudication([{"fixture_id": "clip-a", "frame_index": 7, "status": "unresolved", "x_px": None}])
        document = self.score([entry("clip-a", SUGGESTED), entry("clip-b", SUGGESTED)])
        self.assertEqual(document["clips"][0]["outcome"], "accepted_correct")

    def test_clip_marked_unresolved_in_the_record_is_indeterminate(self):
        self.fx.record["inputs"]["clips"][0]["adjudication"]["unresolved"] = True
        self.fx.record["inputs"]["clips"][1]["adjudication"] = {"path": None, "sha256": None, "unresolved": True}
        document = self.score([entry("clip-a", SUGGESTED), entry("clip-b", SUGGESTED)])
        self.assertEqual([row["group"] for row in document["clips"]], ["I", "I"])

    def test_adjudicated_centre_never_averages_concentric_radii(self):
        self.set_labels("clip-a", label_csv(100, 100, 50), label_csv(100, 100, 30))
        self.set_adjudication([{"fixture_id": "clip-a", "frame_index": 0, "status": "adjudicated",
                                "x_px": 100.0, "y_px": 100.0}])
        middle = {"status": "suggested", **circle(100.0, 100.0, 40.0)}
        row = self.score([entry("clip-a", middle), entry("clip-b", SUGGESTED)])["clips"][0]
        self.assertEqual((row["group"], row["outcome"]), ("I", "indeterminate_emitted"))
        self.assertIn("radii disagree", row["group_reason"])

    def test_requested_time_between_frames_uses_the_nearest_frame(self):
        rows = "0.0,,0,labelled,visible,high,10,10,50,,,,,,\n1.001001,,30,labelled,visible,high,100,100,50,,,,,,\n"
        self.set_labels("clip-a", (HEADER + rows).encode(), (HEADER + rows.replace("100,100,50", "102,100,50")).encode())
        self.fx.record["inputs"]["clips"][0]["decision_frame_pts_s"] = 1.0
        self.set_sheet(SHEET.replace("clip-a,a,select,1.0", "clip-a,a,select,2.0")
                       .replace("clip-a,b,select,1.0", "clip-a,b,select,2.0"))
        later = entry("clip-a", SUGGESTED)
        later["frame"] = {**later["frame"], "requested_time_s": 1.0, "frame_index": 30, "actual_time_s": 1.001001}
        document = self.score([later, entry("clip-b", SUGGESTED)])
        self.assertEqual(document["clips"][0]["outcome"], "accepted_correct")

    def test_single_row_packages_and_between_frame_times(self):
        one = "1.001001,,30,labelled,visible,high,100,100,50,,,,,,\n"
        self.set_labels("clip-a", (HEADER + one).encode(), (HEADER + one.replace("100,100,50", "102,100,50")).encode())
        self.set_labels("clip-b", (HEADER + one).encode(), (HEADER + one.replace("100,100,50", "102,100,50")).encode())
        for clip in self.fx.record["inputs"]["clips"]:
            clip["decision_frame_pts_s"] = 1.0
        self.set_sheet(SHEET.replace(",1.0\n", ",2.0\n"))
        decoded = entry("clip-a", SUGGESTED)
        decoded["frame"] = {**decoded["frame"], "requested_time_s": 1.0, "frame_index": 30, "actual_time_s": 1.001001}
        failed = {"fixture_id": "clip-b", "error": "RuntimeError: decoder failed", "wall_s": 0.5, "timed_out": False}
        document = self.score([decoded, failed])
        self.assertEqual(self.outcomes(document), {"clip-a": "accepted_correct", "clip-b": "error"})
        self.assertEqual([row["group"] for row in document["clips"]], ["S", "S"])

    def test_error_far_from_any_label_row_is_unresolved_not_refused(self):
        self.fx.record["inputs"]["clips"][1]["decision_frame_pts_s"] = 3.0
        failed = {"fixture_id": "clip-b", "error": "RuntimeError: decoder failed", "wall_s": 0.5, "timed_out": False}
        row = self.score([entry("clip-a", SUGGESTED), failed])["clips"][1]
        self.assertEqual((row["group"], row["outcome"]), ("I", "error"))

    def test_hidden_emissions_and_policy_differences_are_reported(self):
        record = copy.deepcopy(self.fx.record)
        record["inputs"]["clips"][1]["expected_policy"] = "refuse_no_target"
        document = self.score([entry("clip-a", SUGGESTED), entry("clip-b", SUGGESTED, timed_out=True)], record)
        self.assertEqual(document["pooled"]["emitted_and_timed_out_by_group"], {"R": 1})
        self.assertEqual([row["label_sheet_policy_differs"] for row in document["clips"]], [False, True])

    def test_missing_onset_makes_a_select_case_indeterminate(self):
        self.set_sheet("fixture_id,annotator,expected_policy,movement_onset_s\nclip-b,a,select,1.0\n")
        document = self.score([entry("clip-a", SUGGESTED), entry("clip-b", SUGGESTED)])
        self.assertEqual(document["clips"][0]["group_reason"], "no movement onset label")
        self.assertEqual(document["clips"][1]["label_sheet_policies"], {"a": "select"})

    def test_funnel_and_pre_refinement_top1(self):
        target, other = circle(101.0, 100.0, 50.0), circle(300.0, 300.0, 40.0)
        raw = [{**other, "raw_index": 0, "retained_first_60": True},
               {**target, "raw_index": 1, "retained_first_60": True}]
        ranked = [{**other, "rank": 1, "raw_index": 0, "support": 0.6},
                  {**target, "rank": 2, "raw_index": 1, "support": 0.5}]
        wrong = {"status": "suggested", **other}
        obs_a = observation(wrong, raw, ranked, selected=ranked[0])
        obs_b = observation(FAILED, [{**target, "raw_index": 0, "retained_first_60": False}])
        document = self.score([entry("clip-a", wrong, obs_a), entry("clip-b", FAILED, obs_b)])
        first, second = (row["membership"] for row in document["clips"])
        self.assertEqual((first["funnel_k20"], first["first_matching_rank"]), ("rank_2_20_member", 2))
        self.assertEqual(first["top_k_member"], {"1": False, "3": True, "5": True, "10": True, "20": True})
        self.assertEqual(second["funnel_k20"], "truncation_loss")
        stages = document["stages"]
        self.assertEqual((stages["raw_member"]["x"], stages["top_k_member"]["3"]["x"]), (2, 1))
        self.assertEqual(stages["pre_refinement_top1_correct"]["x"], 0)
        self.assertIn("wrong_top1", document["margins"])

    def test_strata_and_latency(self):
        document = self.score([entry("clip-a", SUGGESTED), entry("clip-b", FAILED)])
        self.assertEqual(set(document["by_session"]), {"session-20261015", "session-20230203"})
        self.assertEqual(document["latency"]["wall_s_including_failures"]["n"], 2)
        self.assertEqual(document["mode"], "descriptive")


class RefusalTests(Base):
    def refused(self, clips, record=None):
        with self.assertRaises(pss.ScoreError):
            self.score(clips, record)
        self.assertFalse((self.run_dir / pss.SCORE_FILE).exists())

    def test_scored_once(self):
        self.score([entry("clip-a", SUGGESTED), entry("clip-b", FAILED)])
        path, digest = self.fx.save()
        with self.assertRaises(pss.ScoreError):
            pss.score(path, digest, self.fx.root, self.run_dir)

    def test_label_change_and_other_record_are_refused(self):
        path, digest = self.fx.save()
        self.write_run([entry("clip-a", SUGGESTED), entry("clip-b", FAILED)], "0" * 64)
        with self.assertRaises(pss.ScoreError):
            pss.score(path, digest, self.fx.root, self.run_dir)
        self.write_run([entry("clip-a", SUGGESTED), entry("clip-b", FAILED)], digest)
        (self.fx.root / "labels/clip-a.a.csv").write_bytes(label_csv(1, 1, 1))
        with self.assertRaises(pss.ScoreError):
            pss.score(path, digest, self.fx.root, self.run_dir)
        self.assertFalse((self.run_dir / pss.SCORE_FILE).exists())

    def test_bad_onsets_are_refused(self):
        for value in ("nan", "inf", "-1"):
            with self.subTest(value=value):
                self.set_sheet(SHEET.replace("clip-a,a,select,1.0", f"clip-a,a,select,{value}"))
                self.refused([entry("clip-a", SUGGESTED), entry("clip-b", SUGGESTED)])

    def test_missing_label_row_is_refused(self):
        self.set_labels("clip-a", (HEADER + "1.0,,30,labelled,visible,high,1,1,1,,,,,,\n").encode(),
                        label_csv(100, 100, 50))
        self.refused([entry("clip-a", SUGGESTED), entry("clip-b", SUGGESTED)])

    def test_run_must_have_requested_the_record_time(self):
        moved = entry("clip-a", SUGGESTED)
        moved["frame"] = {**moved["frame"], "requested_time_s": 0.5}
        self.refused([moved, entry("clip-b", SUGGESTED)])

    def test_run_frame_must_be_the_labelled_frame(self):
        shifted = entry("clip-a", SUGGESTED)
        shifted["frame"] = {**shifted["frame"], "frame_index": 1, "actual_time_s": 0.016}
        self.refused([shifted, entry("clip-b", SUGGESTED)])

    def test_edited_observations_are_refused(self):
        path, digest = self.fx.save()
        self.write_run([entry("clip-a", SUGGESTED), entry("clip-b", FAILED)], digest)
        (self.run_dir / "clips.jsonl").write_text(json.dumps(entry("clip-a", FAILED)) + "\n", encoding="utf-8")
        with self.assertRaises(pss.ScoreError):
            pss.score(path, digest, self.fx.root, self.run_dir)

    def test_duplicate_adjudication_bad_gaps_and_missing_log_are_refused(self):
        twice = {"fixture_id": "clip-a", "frame_index": 0, "status": "adjudicated", "x_px": 100.0, "y_px": 100.0}
        self.set_adjudication([twice, dict(twice)])
        self.refused([entry("clip-a", SUGGESTED), entry("clip-b", SUGGESTED)])
        self.set_adjudication([])
        record = copy.deepcopy(self.fx.record)
        record["known_gaps"] = "one string"
        self.refused([entry("clip-a", SUGGESTED), entry("clip-b", SUGGESTED)], record)
        path, digest = self.fx.save()
        self.write_run([entry("clip-a", SUGGESTED), entry("clip-b", SUGGESTED)], digest)
        (self.run_dir / "clips.jsonl").unlink()
        with self.assertRaises(pss.ScoreError):
            pss.score(path, digest, self.fx.root, self.run_dir)

    def test_observations_must_cover_exactly_the_record(self):
        for clips in ([entry("clip-a", SUGGESTED)],
                      [entry("clip-a", SUGGESTED), entry("clip-a", FAILED), entry("clip-b", FAILED)]):
            with self.subTest(n=len(clips)):
                self.refused(clips)


if __name__ == "__main__":
    unittest.main()
