#!/usr/bin/env python3
"""time_session.py: one set timed through ingest, confirmation and run, with the #95 fakes. Stdlib only."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_fakes as fakes  # noqa: E402
from session_harness import SessionRunner, SessionTestCase, fake_cropper  # noqa: E402

import session_ingest  # noqa: E402
import time_session  # noqa: E402
import vbt_session  # noqa: E402

ARRIVED = datetime(2026, 10, 3, 18, 0, 30, tzinfo=timezone.utc)
RUN_OPTIONS = ["--plate-diameter-m", "0.45", "--stick-length-m", "1.30", "--tracker-policy", "csrt-all-v1",
               "--preset", "vbt-sg-0.15s-v1"]


def counting_clock() -> Callable[[], float]:
    now = [0.0]

    def tick() -> float:
        now[0] += 1.0
        return now[0]
    return tick


def recording(video: Path) -> dict[str, Any]:
    return {"creation_time": "2026-10-03T18:00:00.000000Z", "duration_s": 13.0}


class TimeSessionTests(SessionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.output = self.dir / "timing" / "t.timing.json"
        self.sleeps = 0

    def phone_and_owner(self, send_csv: bool = True) -> Callable[[float], None]:
        """First poll: the video lands. Once the page exists: the owner downloads the confirmed CSV."""
        def sleep(seconds: float) -> None:
            self.sleeps += 1
            if self.sleeps == 1:
                self.add_video("VID_set1.mp4", b"set one video")
            state_path = self.session_dir / session_ingest.STATE_NAME
            csv = self.downloads / vbt_session.csv_name(self.session_id)
            if send_csv and state_path.exists() and not csv.exists():
                state = self.state()
                self.write_csv([fakes.accepted_row(state["clips"][0], state)])
        return sleep

    def time(self, *extra: str, run_options: list[str] | None = None,
             sleep: Callable[[float], None] | None = None) -> tuple[int, str, str]:
        argv = ["--session", self.session_id, "--sessions-root", str(self.sessions), "--manifest", str(self.manifest),
                "--inbox", str(self.inbox), "--watch", str(self.downloads), "--media-dir", str(self.media),
                "--output", str(self.output), *extra, "--", *(RUN_OPTIONS if run_options is None else run_options)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = time_session.main(argv, runner=SessionRunner(), clock=counting_clock(), wall=lambda: ARRIVED,
                                     sleep=sleep or self.phone_and_owner(), probe=recording,
                                     suggester=lambda frame: fakes.suggestions(), cropper=fake_cropper)
        return code, stdout.getvalue(), stderr.getvalue()

    def record(self) -> dict[str, Any]:
        return json.loads(self.output.read_text(encoding="utf-8"))

    def test_a_set_is_timed_stage_by_stage(self) -> None:
        code, stdout, stderr = self.time("--rest-s", "180")
        self.assertEqual(code, 0, stderr)
        record = self.record()
        self.assertEqual(record["status"], "complete")
        stages = record["stages"]
        self.assertEqual(stages["video"]["name"], "VID_set1.mp4")
        self.assertEqual(stages["video"]["recording"]["arrival_minus_creation_s"], 30.0)
        fixture_id = self.state()["clips"][0]["fixture_id"]
        self.assertEqual([(p["step"], p["fixture_id"]) for p in stages["run"]["processes"]],
                         [("track", fixture_id), ("analyze", fixture_id)])
        for name in ("ingest_s", "confirm_s"):
            self.assertGreater(stages[name], 0)
        totals = record["totals"]
        self.assertEqual(totals["complete_to_report_s"], totals["machine_s"] + stages["confirm_s"])
        self.assertTrue((self.session_dir / "session-record.json").is_file())
        self.assertFalse(self.output.with_name("t.timing.session.csv").exists())
        self.assertIn("rest 180 s:", stdout)
        self.assertIn("status: complete", stdout)

    def test_inbox_must_start_empty(self) -> None:
        self.add_video("old.mp4", b"an older video")
        code, _, stderr = self.time()
        self.assertEqual(code, 1)
        self.assertIn("already holds videos", stderr)
        self.assertEqual(self.sleeps, 0)

    def test_two_arriving_videos_are_ambiguous(self):
        def arrive(seconds):
            self.add_video("one.mp4", b"one")
            self.add_video("two.mp4", b"two")
        with self.assertRaisesRegex(session_ingest.WorkflowError, "ambiguous"):
            time_session.wait_for_video(self.inbox, 5, 1, counting_clock(), arrive)

    def test_equal_size_rewrite_waits_for_stable_bytes(self):
        polls = []
        def arrive(seconds):
            polls.append(seconds)
            if len(polls) <= 2:
                self.add_video("one.mp4", b"old" if len(polls) == 1 else b"new")
        result = time_session.wait_for_video(self.inbox, 10, 1, counting_clock(), arrive)
        self.assertEqual(result["path"].read_bytes(), b"new")
        self.assertGreaterEqual(len(polls), 3)

    def test_session_must_be_new(self) -> None:
        self.session_dir.mkdir(parents=True)
        (self.session_dir / session_ingest.STATE_NAME).write_text("{}", encoding="utf-8")
        code, _, stderr = self.time()
        self.assertEqual(code, 1)
        self.assertIn("already exists", stderr)

    def test_run_options_are_checked_before_the_wait(self) -> None:
        code, _, stderr = self.time(run_options=["--plate-diameter-m", "0.45"])
        self.assertEqual(code, 1)
        self.assertIn("run options after `--` are invalid", stderr)
        self.assertEqual(self.sleeps, 0)
        self.assertFalse(self.output.exists())

    def test_a_missing_csv_still_writes_the_stages_reached(self) -> None:
        code, stdout, _ = self.time("--confirm-timeout-s", "5", sleep=self.phone_and_owner(send_csv=False))
        self.assertEqual(code, 1)
        record = self.record()
        self.assertEqual(record["status"], "no session CSV")
        self.assertIn("ingest_s", record["stages"])
        self.assertNotIn("run", record["stages"])
        self.assertIsNone(record["totals"]["complete_to_report_s"])
        self.assertIn("status: no session CSV", stdout)


class PieceTests(unittest.TestCase):
    def test_steps_are_named_from_the_command(self) -> None:
        self.assertEqual(time_session.step_name(["py", "research/gpu-tracking/track_gpu.py", "--fixture", "x"]),
                         "track")
        self.assertEqual(time_session.step_name(["openbar-cli", "analyze", "--observations", "p.json"]), "analyze")
        self.assertEqual(time_session.step_name(["git", "rev-parse", "HEAD"]), "checks")
        self.assertIsNone(time_session.option_value(["track.py", "--fixture"], "--fixture"))

    def test_timed_runner_records_failures_too(self) -> None:
        class Failing(SessionRunner):
            def execute(self, argv: list[str]) -> None:
                raise time_session.WorkflowError("boom")
        timed = time_session.TimedRunner(Failing(), counting_clock())
        with self.assertRaises(time_session.WorkflowError):
            timed.execute(["py", "track.py", "--fixture", "vbt-1"])
        self.assertEqual(timed.steps, [{"step": "track", "fixture_id": "vbt-1", "program": "py", "seconds": 1.0}])

    def test_creation_time_without_a_zone_is_not_compared(self) -> None:
        gap = time_session.recording_gap({"creation_time": "2026-10-03T18:00:00"}, ARRIVED)
        self.assertIsNone(gap["arrival_minus_creation_s"])
        self.assertIsNone(time_session.parse_utc("not a time"))


if __name__ == "__main__":
    unittest.main()
