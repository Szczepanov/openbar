#!/usr/bin/env python3
"""`run --watch` (#95): only a CSV that appears or changes after the watch starts is used. Stdlib only."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_fakes as fakes  # noqa: E402
from session_harness import SessionTestCase  # noqa: E402

import session_ingest  # noqa: E402
import vbt_session  # noqa: E402

NAME = "vbt-session-2026-10-03.csv"


def fake_clock(step: float = 1.0) -> Callable[[], float]:
    now = [0.0]

    def tick() -> float:
        now[0] += step
        return now[0]
    return tick


class WatchTests(SessionTestCase):
    def watch(self, on_sleep: Callable[[int], None] | None = None, timeout_s: float = 60.0) -> bytes:
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if on_sleep is not None:
                on_sleep(len(sleeps))
        return vbt_session.watch_for_csv(self.downloads, self.session_id, timeout_s, 1.0, fake_clock(), sleep)

    def write_at(self, name: str, data: bytes, poll: int) -> Callable[[int], None]:
        def on_sleep(count: int) -> None:
            if count == poll:
                (self.downloads / name).write_bytes(data)
        return on_sleep

    def test_timeout_is_bounded_and_explained(self) -> None:
        sleeps: list[float] = []
        with self.assertRaises(session_ingest.WorkflowError) as caught:
            vbt_session.watch_for_csv(self.downloads, self.session_id, 10.0, 2.0, fake_clock(2.0), sleeps.append)
        self.assertIn(f"no new {NAME}", str(caught.exception))
        self.assertLessEqual(sum(sleeps), 12.0)

    def test_file_is_read_once_its_size_is_stable(self) -> None:
        polls: list[int] = []

        def on_sleep(count: int) -> None:
            polls.append(count)
            if count == 2:
                (self.downloads / NAME).write_bytes(b"a,b\n")
        self.assertEqual(self.watch(on_sleep), b"a,b\n")
        self.assertEqual(len(polls), 3, "found on one poll, confirmed stable on the next")

    def test_stale_file_from_before_the_watch_is_ignored(self) -> None:
        (self.downloads / NAME).write_bytes(b"old csv\n")
        with self.assertRaises(session_ingest.WorkflowError) as caught:
            self.watch(timeout_s=5.0)
        self.assertIn("existed before the watch", str(caught.exception))

    def test_stale_file_that_is_downloaded_again_is_used(self) -> None:
        stale = self.downloads / NAME
        stale.write_bytes(b"old csv\n")
        os.utime(stale, ns=(1_000_000_000, 1_000_000_000))

        def on_sleep(count: int) -> None:
            if count == 2:
                stale.write_bytes(b"new csv, same name\n")
        self.assertEqual(self.watch(on_sleep), b"new csv, same name\n")

    def test_browser_duplicate_names(self) -> None:
        for name in ("vbt-session-2026-10-03 (1).csv", "vbt-session-2026-10-03(1).csv",
                     "vbt-session-2026-10-03-1.csv"):
            with self.subTest(name=name):
                (self.downloads / NAME).write_bytes(b"stale\n")  # an older download stays ignored
                self.assertEqual(self.watch(self.write_at(name, f"{name}\n".encode(), 2)), f"{name}\n".encode())
                for path in self.downloads.iterdir():
                    path.unlink()

    def test_two_new_candidates_are_ambiguous(self) -> None:
        def on_sleep(count: int) -> None:
            if count == 1:
                (self.downloads / NAME).write_bytes(b"x")
                (self.downloads / "vbt-session-2026-10-03 (1).csv").write_bytes(b"y")
        with self.assertRaises(session_ingest.WorkflowError) as caught:
            self.watch(on_sleep)
        self.assertIn("several new session CSVs", str(caught.exception))

    def test_other_files_are_ignored(self) -> None:
        def on_sleep(count: int) -> None:
            if count == 1:
                (self.downloads / "vbt-session-2026-10-03.txt").write_bytes(b"x")
                (self.downloads / "other.csv").write_bytes(b"y")
            if count == 2:
                (self.downloads / NAME).write_bytes(b"z")
        self.assertEqual(self.watch(on_sleep), b"z")

    def test_watch_runs_the_session(self) -> None:
        self.add_video("VID_1.mp4", b"video one")
        self.ingest()
        state = self.state()
        stale = self.write_csv([fakes.accepted_row(state["clips"][0], state, "clean")])
        fresh = fakes.csv_bytes([fakes.accepted_row(state["clips"][0], state, "snatch")])
        argv = self.run_args(self.downloads / "unused.csv")
        argv[argv.index("--csv"):argv.index("--csv") + 2] = ["--watch", str(self.downloads), "--watch-timeout-s", "30"]
        polls = [0]

        def sleep(seconds: float) -> None:
            polls[0] += 1
            if polls[0] == 1:
                (self.downloads / "vbt-session-2026-10-03 (1).csv").write_bytes(fresh)
        code, out, err = self.main(argv, sleep=sleep, clock=fake_clock())
        self.assertEqual(code, 0, err)
        self.assertIn("found vbt-session-2026-10-03 (1).csv", out)
        self.assertTrue(stale.exists())
        self.assertEqual((self.session_dir / "session-input.csv").read_bytes(), fresh, "the new CSV (snatch) ran")

    def test_watch_timeout_must_be_bounded(self) -> None:
        with self.assertRaises(SystemExit):
            vbt_session.parse_args(["run", "--session", "s", "--watch", ".", "--watch-timeout-s", "1e9",
                                    "--plate-diameter-m", "0.45", "--stick-length-m", "1.3",
                                    "--tracker-policy", "csrt-all-v1", "--preset", "vbt-sg-0.15s-v1"])


if __name__ == "__main__":
    unittest.main()
