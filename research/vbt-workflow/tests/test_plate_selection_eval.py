"""Frozen plate-selection executor: refusal before decode, run-once, truth separation.

Synthetic records and fake media bytes only; no private media is read. Checks that need the
real candidate parameters, OpenCV or FFmpeg skip without them.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import plate_selection_eval as pse  # noqa: E402

HAVE_CV = all(importlib.util.find_spec(name) for name in ("cv2", "numpy"))
PARAMS = {"min_support": 0.4}  # stands in for the PlateParams dump where OpenCV is not needed
COMMIT = "a" * 40


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def always(*_args) -> bool:
    return True


class Fixture:
    """A temporary repo root holding a manifest, fake media, labels and a complete record."""

    def __init__(self, root: Path):
        self.root = root
        self.files: dict[str, bytes] = {}
        self.fixtures = [self.fixture("clip-a", "VID_20261015_070000.mp4", "validation", ["split-frozen-eval"]),
                         self.fixture("clip-b", "VID_20230203_070000.mp4", "boundary",
                                      ["session-20230203", "split-frozen-eval"])]
        self.write("labels.csv", b"fixture_id,annotator\n")
        self.write("adjudication.json", b"{}")
        self.write_manifest()
        self.record = self.make_record()

    def write(self, path: str, data: bytes) -> str:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.files[path] = data
        return sha(data)

    def fixture(self, fid: str, media: str, purpose: str, tags: list[str]) -> dict:
        digest = self.write(f"media/{media}", f"media {fid}".encode())
        self.write(f"labels/{fid}.a.csv", f"a {fid}".encode())
        self.write(f"labels/{fid}.b.csv", f"b {fid}".encode())
        return {"id": fid, "purpose": purpose, "media": {"repository_path": f"media/{media}", "sha256": digest},
                "conditions": {"challenge_tags": tags}, "source": {"reference": media}}

    def write_manifest(self) -> None:
        self.write("manifest.json", json.dumps({"fixtures": self.fixtures}).encode())
        if hasattr(self, "record"):
            self.record["inputs"]["manifest_sha256"] = sha(self.files["manifest.json"])

    def clip(self, fixture: dict, session: str) -> dict:
        fid = fixture["id"]
        return {"fixture_id": fid, "session_id": session, "role": "split-frozen-eval",
                "media_sha256": fixture["media"]["sha256"], "purpose": fixture["purpose"],
                "challenge": "side view", "expected_policy": "select", "decision_frame_pts_s": 0.0,
                "label_files": [{"annotator": k, "path": f"labels/{fid}.{k}.csv",
                                 "sha256": sha(self.files[f"labels/{fid}.{k}.csv"])} for k in "ab"],
                "adjudication": {"path": "adjudication.json", "sha256": sha(self.files["adjudication.json"]),
                                 "unresolved": False}}

    def make_record(self) -> dict:
        sessions = ["session-20261015", "session-20230203"]
        return {
            "record_version": 1, "freeze_id": "plate-selection-freeze-test-1", "supersedes": None,
            "created_utc": "2026-10-16T00:00:00Z", "created_by": "test",
            "authority": {"plan_path": "docs/plans/AUTOMATIC_PLATE_SELECTION_PLAN.md", "plan_commit": COMMIT,
                          "split_doc_commit": COMMIT, "owner_decision_url": "https://example.invalid/1",
                          "owner_decision_record": None, "allowed_entry_point": pse.SELF_PATH},
            "inputs": {"manifest_path": "manifest.json", "manifest_sha256": sha(self.files["manifest.json"]),
                       "sessions": [{"session_id": s} for s in sessions],
                       "clips": [self.clip(f, s) for f, s in zip(self.fixtures, sessions)],
                       "labels_csv": {"path": "labels.csv", "sha256": sha(self.files["labels.csv"])},
                       "synthetic_inputs": []},
            "reserved_exclusion": {"tag": "split-reserved-57", "clips_in_record": [],
                                   "guard_module": pse.GUARD_PATH, "guard_commit": COMMIT},
            "candidates": [{"name": "baseline", "method_id": pse.METHOD_ID, "code_commit": COMMIT,
                            "entry_point": pse.SUGGEST_PATH, "parameters": PARAMS,
                            "parameters_sha256": pse.parameters_sha256(PARAMS), "model": None,
                            "added_rules": "none"}],
            "search_rules": {"frame_search_horizon_s": 0.0, "timestamp_sampling": "nearest 0.0",
                             "target_identity_rule": "outer rim", "tie_and_ambiguity_rule": "baseline",
                             "refusal_rules": ["baseline"], "per_clip_timeout_s": 60, "resource_limits": "cpu"},
            "scoring": {"outcome_definitions": "plan", "count_once_before_correction": True,
                        "intervals": "95% Wilson", "strata": ["session_id"], "geometry_reference": "a/b",
                        "latency": {"cold_definition": "first", "warm_definition": "median", "repeats": 2,
                                    "stages_recorded": ["probe"]}},
            "decision_limits": {"mode": "descriptive", "numeric_limits": None, "rationale": None},
            "environment": {"os": "test", "hardware": "test", "python": "3", "opencv": "4", "ffmpeg": "9",
                            "dependency_lock": "none"},
            "statement": "No scored run precedes this record.",
        }

    def save(self, record: dict | None = None, raw: bytes | None = None) -> tuple[Path, str]:
        data = raw if raw is not None else json.dumps(record or self.record, indent=2).encode()
        path = self.root / "freeze" / "record.json"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        return path, sha(data)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = Fixture(Path(self.tmp.name))
        for name, value in (("default_plate_parameters", lambda: PARAMS),
                            ("current_environment", lambda: {"python": "test"})):
            patcher = mock.patch.object(pse, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class ValidationTests(Base):
    def check(self, record=None, allow_held_out=True, file_matches=always, commit_exists=always, raw=None):
        path, digest = self.fx.save(record, raw)
        return pse.validate(path, digest, self.fx.root, allow_held_out, file_matches, commit_exists,
                            code_root=self.fx.root)

    def refused(self, record=None, match="", **kwargs):
        with self.assertRaises(pse.FreezeError) as caught:
            self.check(record, **kwargs)
        self.assertIn(match, str(caught.exception))

    def mutated(self, mutate):
        record = copy.deepcopy(self.fx.record)
        mutate(record)
        return record

    def clip(self, record, index=0):
        return record["inputs"]["clips"][index]

    def test_complete_record_passes(self):
        record, fixtures = self.check()
        self.assertEqual(set(fixtures), {"clip-a", "clip-b"})
        self.assertEqual(record["freeze_id"], "plate-selection-freeze-test-1")

    def test_record_hash_mismatch_is_refused(self):
        path, _ = self.fx.save()
        with self.assertRaises(pse.FreezeError):
            pse.validate(path, "0" * 64, self.fx.root, True, always, always, code_root=self.fx.root)

    def test_missing_field_and_malformed_record_are_refused(self):
        self.refused(self.mutated(lambda r: r["search_rules"].pop("per_clip_timeout_s")), match="per_clip_timeout_s")
        self.refused(raw=b"[1, 2]", match="object")
        self.refused(raw=b"{not json", match="malformed")
        self.refused(self.mutated(lambda r: r["inputs"].update(clips=[None])))

    def test_placeholder_and_draft_are_refused(self):
        self.refused(self.mutated(lambda r: r["authority"].update(plan_commit="<PENDING>")), match="placeholders")
        self.refused(self.mutated(lambda r: r["search_rules"].update(resource_limits="<PENDING")), match="placeholders")
        self.refused(self.mutated(lambda r: r.update(status="DRAFT: not final")), match="DRAFT")
        self.check(self.mutated(lambda r: r["search_rules"].update(resource_limits="x < 3 and y > 2")))

    def test_null_and_empty_required_values_are_refused(self):
        self.refused(self.mutated(lambda r: r["authority"].update(owner_decision_url=None)), match="owner decision")
        self.refused(self.mutated(lambda r: r.update(statement=None)), match="statement")
        self.refused(self.mutated(lambda r: r["environment"].update(os="")), match="environment.os")
        self.refused(self.mutated(lambda r: self.clip(r).update(label_files=[])), match="passes a and b")
        self.refused(self.mutated(lambda r: self.clip(r)["label_files"][1].update(annotator="a")),
                     match="passes a and b")
        self.refused(self.mutated(lambda r: self.clip(r).update(session_id=None)), match="session_id")
        self.refused(self.mutated(lambda r: self.clip(r)["label_files"][0].update(sha256="abc")), match="64-hex")
        self.refused(self.mutated(lambda r: r.update(record_version=True)), match="record_version")

    def test_commits_must_be_full_and_exist(self):
        self.refused(self.mutated(lambda r: r["candidates"][0].update(code_commit="HEAD")), match="40-hex")
        self.refused(self.mutated(lambda r: r["authority"].update(plan_commit="main")), match="40-hex")
        self.refused(commit_exists=lambda *_: False, match="not a commit")

    def test_held_out_clip_needs_explicit_flag(self):
        self.refused(allow_held_out=False, match="held-out")

    def test_reserved_tag_is_refused_even_with_flag(self):
        self.fx.fixtures[1]["conditions"]["challenge_tags"].append("split-reserved-57")
        self.fx.write_manifest()
        self.refused(match="split-reserved-57")

    def test_reserved_clip_list_must_be_empty(self):
        self.refused(self.mutated(lambda r: r["reserved_exclusion"].update(clips_in_record=["x"])), match="reserved")

    def test_wrong_or_missing_role_is_refused(self):
        for tags in (["session-20230203", "split-exploration"], ["session-20230203"]):
            with self.subTest(tags=tags):
                self.fx.fixtures[1]["conditions"]["challenge_tags"] = tags
                self.fx.write_manifest()
                self.refused(match="split-frozen-eval")

    def test_session_rules_are_enforced(self):
        self.refused(self.mutated(lambda r: self.clip(r).update(session_id="session-20990101")))
        self.refused(self.mutated(lambda r: r["inputs"]["sessions"].pop()), match="inputs.sessions")
        self.fx.fixtures.append(self.fx.fixture("dev", "VID_20230203_080000.mp4", "development",
                                                ["session-20230203", "split-exploration"]))
        self.fx.write_manifest()
        self.refused(match="one-role-per-session")

    def test_session_crossing_in_other_manifest_is_refused(self):
        dev = self.fx.fixture("vbt", "VID_20261015_090000.mp4", "development", ["split-exploration"])
        self.fx.write(pse.OTHER_MANIFESTS[0], json.dumps({"fixtures": [dev]}).encode())
        self.refused(match="one-role-per-session")

    def test_clip_without_derivable_session_is_refused(self):
        self.fx.fixtures[0]["media"]["repository_path"] = "media/plain.mp4"
        self.fx.fixtures[0]["source"]["reference"] = "plain.mp4"
        self.fx.write("media/plain.mp4", self.fx.files["media/VID_20261015_070000.mp4"])
        self.fx.write_manifest()
        self.refused(match="session")

    def test_record_and_manifest_must_agree(self):
        self.refused(self.mutated(lambda r: self.clip(r, 1).update(purpose="ood")), match="purpose")
        self.refused(self.mutated(lambda r: self.clip(r).update(media_sha256="0" * 64)), match="media sha256")

    def test_changed_files_are_refused(self):
        for path in ("manifest.json", "media/VID_20261015_070000.mp4", "labels/clip-b.b.csv", "labels.csv",
                     "adjudication.json"):
            with self.subTest(path=path):
                original = self.fx.files[path]
                (self.fx.root / path).write_bytes(original + b"changed")
                self.refused()
                (self.fx.root / path).write_bytes(original)

    def test_only_fixed_time_rule_and_descriptive_mode(self):
        self.refused(self.mutated(lambda r: r["search_rules"].update(frame_search_horizon_s=1.0)), match="horizon")
        self.refused(self.mutated(lambda r: r["decision_limits"].update(mode="numeric")), match="descriptive")

    def test_bad_numbers_and_duplicates_are_refused(self):
        self.refused(self.mutated(lambda r: r["search_rules"].update(per_clip_timeout_s=True)))
        self.refused(self.mutated(lambda r: r["scoring"]["latency"].update(repeats=0)))
        self.refused(self.mutated(lambda r: self.clip(r).update(decision_frame_pts_s=-1)))
        self.refused(self.mutated(lambda r: r["inputs"]["clips"].append(copy.deepcopy(self.clip(r)))),
                     match="twice")
        raw = json.dumps(self.fx.record).replace('"decision_frame_pts_s": 0.0', '"decision_frame_pts_s": NaN', 1)
        self.refused(raw=raw.encode(), match="non-finite")

    def test_candidate_identity_is_enforced(self):
        self.refused(self.mutated(lambda r: r["candidates"][0].update(method_id="other")), match="baseline")
        self.refused(self.mutated(lambda r: r["candidates"][0].update(entry_point="x.py")), match="baseline")
        self.refused(self.mutated(lambda r: r["reserved_exclusion"].update(guard_module="x.py")), match="guard")
        self.refused(self.mutated(lambda r: r["candidates"][0].update(parameters_sha256="0" * 64)),
                     match="parameters_sha256")

    def test_every_pinned_file_must_match_its_commit(self):
        pinned = [*pse.PINNED_PATHS, pse.GUARD_PATH]
        for path in pinned:
            with self.subTest(path=path):
                self.refused(file_matches=lambda _root, _commit, checked, path=path: checked != path, match=path)

    @unittest.skipUnless(HAVE_CV, "requires the research OpenCV environment")
    def test_changed_parameters_are_refused(self):
        mock.patch.stopall()
        params = {**pse.default_plate_parameters(), "min_support": 0.3}
        record = self.mutated(lambda r: r["candidates"][0].update(parameters=params,
                                                                  parameters_sha256=pse.parameters_sha256(params)))
        self.refused(record, match="PlateParams")


class GitTests(unittest.TestCase):
    """git_file_matches against this repository's own history."""

    @unittest.skipUnless(shutil.which("git"), "requires git")
    def test_git_file_matches(self):
        head = subprocess.run(["git", "-C", str(pse.ROOT), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=False).stdout.strip()
        if not head:
            self.skipTest("not a git checkout")
        self.assertTrue(pse.git_commit_exists(pse.ROOT, head))
        self.assertFalse(pse.git_commit_exists(pse.ROOT, "0" * 40))
        self.assertFalse(pse.git_file_matches(pse.ROOT, "0" * 40, pse.SUGGEST_PATH))
        self.assertFalse(pse.git_file_matches(pse.ROOT, head, "research/vbt-workflow/no_such_file.py"))
        self.assertEqual(pse.git_file_matches(pse.ROOT, head, pse.GUARD_PATH),
                         subprocess.run(["git", "-C", str(pse.ROOT), "status", "--porcelain", "--", pse.GUARD_PATH],
                                        capture_output=True, text=True, check=False).stdout.strip() == "")


class RunTests(Base):
    def setUp(self):
        super().setUp()
        self.runs = self.fx.root / pse.PRIVATE_RUNS
        self.seen: list = []

    def decode(self, media, fixture, requested_s, work_dir):
        self.seen.append((fixture["id"], requested_s))
        if fixture["id"] == "clip-b":
            raise RuntimeError("decoder failed")
        return "image", {"requested_time_s": requested_s, "frame_index": 0, "actual_time_s": 0.0}

    def execute(self, image, repeats):
        return {"final": {"status": "suggested", "image": image, "repeats": repeats}}

    def run_once(self, name="run-1", **overrides):
        path, digest = self.fx.save()
        kwargs = dict(decode=self.decode, execute=self.execute, file_matches=always, commit_exists=always,
                      code_root=self.fx.root)
        kwargs.update(overrides)
        return pse.run(path, digest, self.fx.root, self.runs / name, True, **kwargs)

    def test_run_records_outcomes_and_never_reads_truth(self):
        out = self.run_once()
        document = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(self.seen, [("clip-a", 0.0), ("clip-b", 0.0)])
        first, second = document["clips"]
        self.assertEqual(first["final"], {"status": "suggested", "image": "image", "repeats": 2})
        self.assertEqual(second["error"], "RuntimeError: decoder failed")
        self.assertNotIn("expected_policy", out.read_text(encoding="utf-8"))
        self.assertEqual(len((out.parent / pse.CLIP_LOG).read_text(encoding="utf-8").splitlines()), 2)

    def test_a_second_run_is_refused_in_any_directory(self):
        self.run_once()
        self.seen.clear()
        for name in ("run-1", "run-2"):
            with self.subTest(name=name), self.assertRaises(pse.FreezeError) as caught:
                self.run_once(name)
            self.assertRegex(str(caught.exception), "run once|already ran")
        self.assertEqual(self.seen, [])
        self.assertFalse((self.runs / "run-2").exists())

    def test_a_new_freeze_id_cannot_rerun_the_same_candidate(self):
        self.run_once()
        self.fx.record["freeze_id"] = "plate-selection-freeze-test-2"
        with self.assertRaises(pse.FreezeError):
            self.run_once("run-2")
        self.fx.record["supersedes"] = "plate-selection-freeze-test-1"
        self.assertTrue(self.run_once("run-3").exists())
        started = json.loads((self.runs / "run-3" / pse.STARTED).read_text(encoding="utf-8"))
        self.assertEqual(started["supersedes"], "plate-selection-freeze-test-1")
        self.assertEqual(started["environment"], {"python": "test"})
        self.assertEqual(len(started["media_sha256"]), 2)

    def test_directory_without_marker_counts_as_a_run(self):
        for content in (None, b"[]", b"{broken"):
            with self.subTest(content=content):
                stray = self.runs / "stray"
                stray.mkdir(parents=True, exist_ok=True)
                if content is not None:
                    (stray / pse.STARTED).write_bytes(content)
                with self.assertRaises(pse.FreezeError) as caught:
                    self.run_once()
                self.assertIn("stray", str(caught.exception))
                shutil.rmtree(stray)
        self.assertEqual(self.seen, [])

    def test_unserialisable_result_becomes_a_clip_error(self):
        out = self.run_once(execute=lambda image, repeats: {"final": {"value": float("nan")}})
        first = json.loads(out.read_text(encoding="utf-8"))["clips"][0]
        self.assertIn("unserialisable", first["error"])

    def test_refusals_happen_before_any_decode_or_output(self):
        self.fx.record["authority"]["plan_commit"] = "<PENDING>"
        with self.assertRaises(pse.FreezeError):
            self.run_once()
        self.fx.record["authority"]["plan_commit"] = COMMIT
        path, digest = self.fx.save()
        for label, target in (("outside runs", self.fx.root / "elsewhere"),
                              ("nested", self.runs / "a" / "b")):
            with self.subTest(label), self.assertRaises(pse.FreezeError):
                pse.run(path, digest, self.fx.root, target, True, self.decode, self.execute, always, always,
                        self.fx.root)
        with self.assertRaises(pse.FreezeError):
            self.run_once(code_root=self.fx.root / "other")
        self.assertEqual(self.seen, [])
        self.assertFalse(self.runs.exists())


@unittest.skipUnless(HAVE_CV and shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "requires the research OpenCV environment and FFmpeg")
class SyntheticDecodeTests(unittest.TestCase):
    """Real probe, PTS-checked decode and unchanged baseline on a generated video."""

    def test_decode_and_execute_match_the_unchanged_baseline(self):
        import cv2
        import numpy as np
        import vbt_suggest

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(3):
                image = np.full((240, 320, 3), 220, dtype=np.uint8)
                cv2.circle(image, (130 + 10 * index, 120), 45, (15, 15, 15), -1)
                cv2.imwrite(str(root / f"in_{index}.png"), image)
            media = root / "synthetic.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-framerate", "10", "-i", str(root / "in_%d.png"),
                            "-c:v", "ffv1", str(media)], check=True)
            fixture = {"id": "synthetic", "media": {"sha256": pse.sha256_file(media)},
                       "video": {"width_px": 320, "height_px": 240, "rotation_deg": 0},
                       "source": {"redistribution_status": "allowed"}}
            image, frame = pse.decode_frame(media, fixture, 0.0, root / "frames")
            self.assertEqual((frame["frame_index"], frame["actual_time_s"]), (0, 0.0))
            result = pse.run_clip(image, 2)
            self.assertEqual(result["final"], vbt_suggest.suggest_plate(image))
            self.assertEqual(result["final"]["status"], "suggested")
            self.assertEqual(len(result["latency"]["warm_search_s"]), 2)
            json.dumps(result, allow_nan=False)
            with self.assertRaises(pse.FreezeError):
                pse.decode_frame(media, fixture, 99.0, root / "frames")
            fixture["media"]["sha256"] = "0" * 64
            with self.assertRaises(Exception):
                pse.decode_frame(media, fixture, 0.0, root / "frames")


class FrameRuleTests(unittest.TestCase):
    def test_nearest_frame_prefers_earlier_on_tie_and_refuses_outside_range(self):
        timestamps = [0.0, 0.02, 0.04]
        self.assertEqual(pse.nearest_frame(timestamps, 0.0), 0)
        self.assertEqual(pse.nearest_frame(timestamps, 0.01), 0)
        self.assertEqual(pse.nearest_frame(timestamps, 0.011), 1)
        self.assertEqual(pse.nearest_frame(timestamps, 0.04), 2)
        for bad in (9.0, -0.01, float("nan"), float("inf")):
            with self.subTest(bad=bad), self.assertRaises(pse.FreezeError):
                pse.nearest_frame(timestamps, bad)
        with self.assertRaises(pse.FreezeError):
            pse.nearest_frame([], 0.0)


if __name__ == "__main__":
    unittest.main()
