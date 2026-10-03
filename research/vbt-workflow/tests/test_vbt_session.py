#!/usr/bin/env python3
"""One-page VBT session (#95): ingest, run and --watch with fakes (see session_harness.py). Stdlib only."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_fakes as fakes  # noqa: E402
from session_harness import ROOT, SessionRunner, SessionTestCase  # noqa: E402

import analyze_lift  # noqa: E402
import schema_check  # noqa: E402
import session_ingest  # noqa: E402
import vbt_session  # noqa: E402

PROTECTED = ROOT / "validation" / "private" / "manifest.json"
SEED_SCHEMA = ROOT / "validation" / "schema" / "manual-target-seed-v1.schema.json"


def protected_bytes() -> bytes | None:
    return PROTECTED.read_bytes() if PROTECTED.exists() else None


class IngestTests(SessionTestCase):
    def test_ingest_copies_extracts_and_builds_the_page_without_registering(self) -> None:
        sha = self.add_video("VID_1.mp4", b"video one")
        code, out, err = self.ingest()
        self.assertEqual(code, 0, err)
        self.assertEqual((self.media / "VID_1.mp4").read_bytes(), b"video one")
        self.assertFalse(self.manifest.exists(), "ingest must not register")
        state = self.state()
        [clip] = state["clips"]
        self.assertEqual((clip["fixture_id"], clip["sha256"], clip["frame_index"]), ("vbt-" + sha[:16], sha, 0))
        self.assertEqual(clip["suggestions"], fakes.suggestions())
        page = (self.session_dir / "session.html").read_text(encoding="utf-8")
        self.assertIn(state["page_id"], page)
        self.assertNotIn("/*CONFIG*/", page)
        self.assertIn("data:image/png;base64,", page)
        self.assertIn("open ", out)

    def test_reingest_is_idempotent(self) -> None:
        self.add_video("VID_1.mp4", b"video one")
        self.assertEqual(self.ingest()[0], 0)
        before = self.snapshot()
        code, out, _ = self.ingest()
        self.assertEqual(code, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertIn("in session", out)

    def test_new_videos_are_appended_after_existing_clips(self) -> None:
        self.add_video("VID_9.mp4", b"nine")
        self.ingest()
        first = self.state()["clips"][0]["fixture_id"]
        self.add_video("VID_1.mp4", b"one")
        self.ingest()
        self.assertEqual([c["original_name"] for c in self.state()["clips"]], ["VID_9.mp4", "VID_1.mp4"])
        self.assertEqual(self.state()["clips"][0]["fixture_id"], first)

    def test_registered_videos_are_skipped_unless_included(self) -> None:
        self.media.mkdir()
        video = self.media / "old.mp4"
        video.write_bytes(b"old video")
        analyze_lift.register_video(video, self.manifest, analyze_lift.file_sha256(video), 0.45, "snatch")
        self.add_video("old copy.mp4", b"old video")
        self.add_video("new.mp4", b"new video")
        manifest_before = self.manifest.read_bytes()
        code, out, _ = self.ingest()
        self.assertEqual(code, 0)
        self.assertIn("skipped old copy.mp4: already registered", out)
        self.assertEqual([c["original_name"] for c in self.state()["clips"]], ["new.mp4"])
        self.ingest("--include-registered")
        clips = self.state()["clips"]
        self.assertEqual(clips[1]["registered_exercise"], "snatch")
        self.assertEqual(clips[1]["media_path"], analyze_lift.display_path(video), "no second copy")
        self.assertEqual(self.manifest.read_bytes(), manifest_before)

    def test_name_clash_with_different_bytes_fails_closed(self) -> None:
        self.media.mkdir()
        (self.media / "VID_1.mp4").write_bytes(b"something else")
        self.add_video("VID_1.mp4", b"video one")
        code, _, err = self.ingest()
        self.assertEqual(code, 1)
        self.assertIn("already exists with different bytes", err)
        self.assertEqual((self.media / "VID_1.mp4").read_bytes(), b"something else")

    def test_at_s_override_selects_the_seed_frame(self) -> None:
        self.add_video("VID_1.mp4", b"video one")
        self.assertEqual(self.ingest("--at-s", "VID_1.mp4=1.5")[0], 0)
        self.assertEqual(self.state()["clips"][0]["frame_index"], 90)
        self.assertEqual(self.ingest()[0], 0)
        self.assertEqual(self.state()["clips"][0]["frame_index"], 90, "the override is kept")
        code, _, err = self.ingest("--at-s", "nope.mp4=1")
        self.assertEqual(code, 1)
        self.assertIn("names no clip", err)
        self.assertEqual(self.ingest("--at-s", "bad")[0], 1)

    def test_failed_suggestions_still_build_the_page(self) -> None:
        self.add_video("VID_1.mp4", b"video one")
        failed = fakes.suggestions(fakes.FAILED_PLATE, fakes.FAILED_STICK)
        code, out, _ = self.ingest(suggestions=failed)
        self.assertEqual(code, 0)
        self.assertIn("plate failed, stick failed", out)

    def test_protected_manifest_and_bad_locations_are_refused(self) -> None:
        self.add_video("VID_1.mp4", b"video one")
        before = protected_bytes()
        argv = ["ingest", "--session", self.session_id, "--sessions-root", str(self.sessions), "--inbox",
                str(self.inbox), "--media-dir", str(self.media)]
        for manifest in (PROTECTED, ROOT / "validation" / "private" / "." / "MANIFEST.json"):
            code, _, err = self.main([*argv, "--manifest", str(manifest)])
            self.assertEqual(code, 1)
            self.assertIn("refusing", err)
        code, _, err = self.main(["ingest", "--session", self.session_id, "--sessions-root", str(self.dir),
                                  "--manifest", str(self.manifest), "--inbox", str(self.inbox)])
        self.assertIn("must be under validation/private/vbt/", err)
        code, _, err = self.main([*argv, "--manifest", str(self.manifest), "--session", "../escape"])
        self.assertIn("session id", err)
        self.assertEqual(protected_bytes(), before)
        self.assertFalse((self.media / "VID_1.mp4").exists())

    def test_missing_ffmpeg_fails_closed(self) -> None:
        self.add_video("VID_1.mp4", b"video one")
        code, _, err = self.main(["ingest", *self.common(), "--inbox", str(self.inbox)],
                                 runner=SessionRunner(missing=("ffmpeg",)))
        self.assertEqual(code, 1)
        self.assertIn("ffmpeg", err)


class RunTestCase(SessionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.add_video("VID_1.mp4", b"video one")
        self.assertEqual(self.ingest()[0], 0)
        self.clip = self.state()["clips"][0]

    def rows(self, exercise: str = "snatch", **changes: str) -> list[dict[str, str]]:
        return [{**fakes.accepted_row(self.clip, self.state(), exercise), **changes}]

    def adjusted(self) -> list[dict[str, str]]:
        return self.rows(plate_radius_px="182.00", plate_radius_status="adjusted")

    def outputs(self) -> dict[str, Path]:
        fixture_id = self.clip["fixture_id"]
        analyses = self.session_dir / "analyses"
        return {
            "seed": self.session_dir / "seeds" / f"{fixture_id}.manual-target-seed-v1.json",
            "click": self.session_dir / "scale" / f"{fixture_id}.scale-reference.csv",
            "analysis": analyses / f"{fixture_id}.opencv-csrt.analysis-v1.json",
            "run_record": analyses / f"{fixture_id}.opencv-csrt.run-record.json",
            "scale_report": self.session_dir / "scale-report" / "scale-reference-v1.json",
            "report": self.session_dir / "report.html",
            "record": self.session_dir / "session-record.json",
        }


class RunTests(RunTestCase):
    def test_run_writes_every_output(self) -> None:
        code, out, err = self.run_session(self.adjusted())
        self.assertEqual(code, 0, err)
        for name, path in self.outputs().items():
            self.assertTrue(path.is_file(), name)
        seed = schema_check.load_strict(self.outputs()["seed"])
        self.assertEqual(schema_check.validate_document(seed, schema_check.load_schema(SEED_SCHEMA)), [])
        self.assertEqual(seed["seed"]["target"], {"center": {"x_px": 400.5, "y_px": 1500.0}, "radius_px": 182.0})
        notes = seed["seed"]["notes"]
        self.assertIn("tool=annotations.py seed.", notes)
        self.assertIn("plate centre accepted, plate radius adjusted", notes)
        self.assertIn(f"suggestion {fakes.PLATE['id']} (method plate-hough-edge-v1", notes)
        [entry] = json.loads(self.manifest.read_text(encoding="utf-8"))["fixtures"]
        self.assertEqual((entry["id"], entry["exercise"]), (self.clip["fixture_id"], "snatch"))
        report = json.loads(self.outputs()["scale_report"].read_text(encoding="utf-8"))
        self.assertEqual(report["rows"][0]["reference"]["known_length_m"], 1.3)
        html = self.outputs()["report"].read_text(encoding="utf-8")
        for needle in ("Per-rep PREVIEW (not authoritative)", "Stick-corrected", "Scale ratio", "0.5502"):
            self.assertIn(needle, html)
        self.assertIn("report:", out)

    def test_session_record(self) -> None:
        self.assertEqual(self.run_session(self.adjusted())[0], 0)
        record = json.loads(self.outputs()["record"].read_text(encoding="utf-8"))
        self.assertEqual(record["format"], "openbar-research-vbt-session-record")
        self.assertEqual(record["configuration"]["tracker_policy"], "csrt-all-v1")
        self.assertEqual(record["openbar"]["git_commit"], "0123456789abcdef0123456789abcdef01234567")
        [clip] = record["clips"]
        self.assertEqual(clip["video"]["sha256"], self.clip["sha256"])
        self.assertEqual(clip["item_statuses"]["plate_radius"], "adjusted")
        self.assertEqual(clip["tracker"], "csrt")
        argv = clip["analyze_lift"]["argv"]
        self.assertEqual(argv[:3], ["python", "research/vbt-workflow/analyze_lift.py", "run"])
        self.assertNotIn("--force", argv)
        self.assertTrue(all(not part.startswith(("C:", "/")) for part in argv), argv)
        self.assertEqual(record["scale_report"]["argv"][1], "validation/tools/scale_reference.py")
        self.assertEqual(record["inputs"]["session_csv"]["sha256"],
                         analyze_lift.file_sha256(self.session_dir / "session-input.csv"))
        self.assertNotIn("registration", json.dumps(record), "first-run vs re-run facts stay out of the record")

    def test_existing_outputs_without_force_fail_and_force_rerun_is_byte_identical(self) -> None:
        self.assertEqual(self.run_session(self.adjusted())[0], 0)
        first = self.snapshot()
        code, _, err = self.run_session(self.adjusted())
        self.assertEqual(code, 1)
        self.assertIn("already exist", err)
        self.assertEqual(self.snapshot(), first)
        self.assertEqual(self.run_session(self.adjusted(), "--force")[0], 0)
        self.assertEqual(self.snapshot(), first)

    def test_skipped_clip_is_not_run(self) -> None:
        self.add_video("VID_2.mp4", b"video two")
        self.ingest()
        state = self.state()
        rows = [fakes.accepted_row(state["clips"][0], state, "clean"), fakes.skipped_row(state["clips"][1], state)]
        runner = SessionRunner()
        self.assertEqual(self.run_session(rows, runner=runner)[0], 0)
        self.assertEqual(len([argv for argv in runner.executed if "--fixture" in argv]), 2, "one track + one analyze")
        record = json.loads(self.outputs()["record"].read_text(encoding="utf-8"))
        self.assertEqual(record["skipped"], ["VID_2.mp4"])


class RunFailClosedTests(RunTestCase):
    def assert_nothing_written(self, code: int, out: str, err: str, needle: str) -> None:
        self.assertEqual(code, 1)
        self.assertIn(needle, err)
        for name in ("seeds", "analyses", "scale", "scale-report", "session-input.csv", "report.html"):
            self.assertFalse((self.session_dir / name).exists(), name)

    def test_csv_for_another_session_or_page(self) -> None:
        self.assert_nothing_written(*self.run_session(self.rows(session_id="2026-10-04")), "session CSV refused")
        self.assert_nothing_written(*self.run_session(self.rows(page_id="0" * 16)), "page")

    def test_missing_confirmation_and_malformed_csv(self) -> None:
        self.assert_nothing_written(*self.run_session(self.rows(decision="")), "confirmed or skipped")
        path = self.downloads / "broken.csv"
        path.write_bytes(b"not,a,session,csv\n")
        self.assert_nothing_written(*self.main(self.run_args(path)), "header")

    def test_registered_exercise_conflict(self) -> None:
        video = ROOT / self.clip["media_path"]
        analyze_lift.register_video(video, self.manifest, self.clip["sha256"], 0.45, "clean")
        before = self.manifest.read_bytes()
        self.assert_nothing_written(*self.run_session(self.rows("snatch")), "registered as 'clean'")
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_gpu_policy_without_gpu_python(self) -> None:
        self.assert_nothing_written(*self.run_session(self.rows(), policy="sam2-all-v1"), "needs --gpu-python")
        self.assertFalse(self.manifest.exists())

    def test_gpu_policy_without_cuda(self) -> None:
        gpu = self.make_gpu_python()
        code, _, err = self.run_session(self.rows(), "--gpu-python", str(gpu), policy="sam2-all-v1",
                                        runner=SessionRunner(cuda=False))
        self.assert_nothing_written(code, "", err, "no CUDA device")
        self.assertIn("csrt-all-v1", err)

    def test_gpu_python_with_a_cpu_policy(self) -> None:
        gpu = self.make_gpu_python()
        self.assert_nothing_written(*self.run_session(self.rows(), "--gpu-python", str(gpu)), "not used by")

    def test_mixed_policy_uses_csrt_for_squats_without_a_gpu(self) -> None:
        runner = SessionRunner()
        self.assertEqual(self.run_session(self.rows("back_squat"), policy="sam2-olympic-csrt-squat-v1",
                                          runner=runner)[0], 0)
        track = next(argv for argv in runner.executed if any(part.endswith("track.py") for part in argv))
        self.assertIn("csrt", track)

    def test_mixed_policy_runs_sam2_for_the_snatch(self) -> None:
        gpu = self.make_gpu_python()
        runner = SessionRunner()
        code, _, err = self.run_session(self.rows("snatch"), "--gpu-python", str(gpu),
                                        policy="sam2-olympic-csrt-squat-v1", runner=runner)
        self.assertEqual(code, 0, err)
        self.assertTrue(any(any(part.endswith("track_gpu.py") for part in argv) for argv in runner.executed))
        record = json.loads(self.outputs()["record"].read_text(encoding="utf-8"))
        self.assertEqual(record["clips"][0]["tracker"], "sam2.1-bplus-circle")

    def test_protected_manifest_is_refused_and_untouched(self) -> None:
        before = protected_bytes()
        argv = self.run_args(self.write_csv(self.rows()))
        argv[argv.index("--manifest") + 1] = str(PROTECTED)
        self.assert_nothing_written(*self.main(argv), "refusing")
        self.assertEqual(protected_bytes(), before)

    def test_run_without_ingest(self) -> None:
        rows = self.rows()
        self.session_id = "never-ingested"
        code, _, err = self.main(self.run_args(self.write_csv(rows)))
        self.assertEqual(code, 1)
        self.assertIn("run `ingest` first", err)

    def test_explicit_filter_flags_are_required_without_preset(self) -> None:
        argv = self.run_args(self.write_csv(self.rows()))
        argv.remove("--preset")
        argv.remove("vbt-sg-0.15s-v1")
        with self.assertRaises(SystemExit):
            vbt_session.parse_args(argv)


class WatchTests(SessionTestCase):
    def clock(self, step: float = 1.0):  # noqa: ANN201 - small fake clock
        now = [0.0]

        def tick() -> float:
            now[0] += step
            return now[0]
        return tick

    def test_timeout_is_bounded_and_explained(self) -> None:
        sleeps: list[float] = []
        with self.assertRaises(session_ingest.WorkflowError) as caught:
            vbt_session.watch_for_csv(self.downloads, self.session_id, 10.0, 2.0, self.clock(2.0), sleeps.append)
        self.assertIn("no vbt-session-2026-10-03.csv appeared", str(caught.exception))
        self.assertLessEqual(sum(sleeps), 12.0)

    def test_file_is_read_once_its_size_is_stable(self) -> None:
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) == 2:
                (self.downloads / "vbt-session-2026-10-03 (1).csv").write_bytes(b"a,b\n")
        data = vbt_session.watch_for_csv(self.downloads, self.session_id, 60.0, 1.0, self.clock(), sleep)
        self.assertEqual(data, b"a,b\n")
        self.assertEqual(len(sleeps), 3, "found on one poll, confirmed stable on the next")

    def test_several_candidates_are_ambiguous(self) -> None:
        (self.downloads / "vbt-session-2026-10-03.csv").write_bytes(b"x")
        (self.downloads / "vbt-session-2026-10-03 (1).csv").write_bytes(b"y")
        (self.downloads / "vbt-session-2026-10-03-old.csv").write_bytes(b"z")  # not a browser duplicate name
        with self.assertRaises(session_ingest.WorkflowError) as caught:
            vbt_session.watch_for_csv(self.downloads, self.session_id, 5.0, 1.0, self.clock(), lambda s: None)
        self.assertIn("several session CSVs", str(caught.exception))

    def test_watch_runs_the_session(self) -> None:
        self.add_video("VID_1.mp4", b"video one")
        self.ingest()
        state = self.state()
        self.write_csv([fakes.accepted_row(state["clips"][0], state)])
        argv = self.run_args(self.downloads / "unused.csv")
        argv[argv.index("--csv"):argv.index("--csv") + 2] = ["--watch", str(self.downloads), "--watch-timeout-s", "30"]
        code, out, err = self.main(argv, sleep=lambda s: None, clock=self.clock())
        self.assertEqual(code, 0, err)
        self.assertIn("found vbt-session-2026-10-03.csv", out)

    def test_watch_timeout_must_be_bounded(self) -> None:
        with self.assertRaises(SystemExit):
            vbt_session.parse_args(["run", "--session", "s", "--watch", ".", "--watch-timeout-s", "1e9",
                                    "--plate-diameter-m", "0.45", "--stick-length-m", "1.3",
                                    "--tracker-policy", "csrt-all-v1", "--preset", "vbt-sg-0.15s-v1"])


if __name__ == "__main__":
    unittest.main()
