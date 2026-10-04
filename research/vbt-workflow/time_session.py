#!/usr/bin/env python3
"""Time one set end to end through the session workflow, to see whether between-set feedback fits a rest.

  1. waits for a new video in --inbox (send it from the phone however you would in training);
  2. times `vbt_session.py ingest` for it;
  3. waits for the confirmed session CSV in --watch (you confirm the clip on session.html);
  4. times `vbt_session.py run`, with every tracking and analysis process timed separately;
  5. writes a timing record and prints a stage table.

Usage, from the repository root with the research venv's python:

  time_session.py --session t1 --inbox <empty folder> --watch <downloads> [--rest-s 180] -- \\
      --plate-diameter-m 0.45 --stick-length-m 1.30 --tracker-policy sam2-all-v1 \\
      --gpu-python <gpu venv python> --openbar-cli target/release/openbar-cli.exe --preset vbt-sg-0.15s-v1

Everything after `--` is passed to `vbt_session.py run` unchanged and is checked before the wait starts.
The inbox must start empty and the session must be new, so only this set is timed. Wall-clock times in
the record are diagnostics only; nothing here reaches a seed, prediction or analysis. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import analyze_lift
import session_ingest
import vbt_session
from vbt_process import Runner, WorkflowError

TIMING_FORMAT = "openbar-research-vbt-timing"
TIMING_VERSION = 1
DEFAULT_TIMING_DIR = analyze_lift.PERSONAL_ROOT / "timing"
VIDEO_TIMEOUT_S = 900.0
POLL_S = 1.0
Clock = Callable[[], float]
WallClock = Callable[[], datetime]
RecordingProbe = Callable[[Path], dict[str, Any]]


class TimedRunner(Runner):
    """Delegates to another runner and times every process it starts, labelled by step and clip."""

    def __init__(self, inner: Runner, clock: Clock) -> None:
        self.inner = inner
        self.clock = clock
        self.steps: list[dict[str, Any]] = []

    def which(self, name: str) -> str | None:
        return self.inner.which(name)

    def succeeds(self, argv: list[str]) -> bool:
        return self._timed(argv, lambda: self.inner.succeeds(argv))

    def capture_bytes(self, argv: list[str]) -> bytes:
        return self._timed(argv, lambda: self.inner.capture_bytes(argv))

    def capture(self, argv: list[str]) -> str:
        return self.capture_bytes(argv).decode("utf-8", errors="replace")

    def execute(self, argv: list[str]) -> None:
        self._timed(argv, lambda: self.inner.execute(argv))

    def _timed(self, argv: list[str], action: Callable[[], Any]) -> Any:
        start = self.clock()
        try:
            return action()
        finally:
            self.steps.append({"step": step_name(argv), "fixture_id": option_value(argv, "--fixture"),
                               "program": Path(argv[0]).name, "seconds": self.clock() - start})


def step_name(argv: list[str]) -> str:
    if any(part.endswith(("track.py", "track_gpu.py")) for part in argv):
        return "track"
    if "analyze" in argv[1:] and "--observations" in argv:
        return "analyze"
    return "checks"  # git and tool versions, the CUDA probe, ffprobe


def option_value(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv[:-1] else None


def ffprobe_recording(runner: Runner) -> RecordingProbe:
    """The container's creation_time tag and duration. Phones disagree on what creation_time marks."""
    def probe(video: Path) -> dict[str, Any]:
        try:
            output = runner.capture(["ffprobe", "-v", "error", "-show_entries",
                                     "format=duration:format_tags=creation_time", "-of", "json", str(video)])
            fmt = json.loads(output).get("format", {})
            return {"creation_time": fmt.get("tags", {}).get("creation_time"),
                    "duration_s": float(fmt["duration"]) if "duration" in fmt else None}
        except (WorkflowError, ValueError, AttributeError) as error:
            return {"creation_time": None, "duration_s": None, "error": str(error)}
    return probe


def parse_utc(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def videos_with_stamps(inbox: Path) -> dict[Path, tuple[int, int]]:
    found = {}
    for path in session_ingest.list_inbox(inbox):
        try:
            info = path.stat()
        except OSError:
            continue
        found[path] = (info.st_mtime_ns, info.st_size)
    return found


def wait_for_video(inbox: Path, timeout_s: float, poll_s: float, clock: Clock,
                   sleep: Callable[[float], None]) -> dict[str, Any]:
    """First new video in the inbox, once its size is unchanged across two polls (the transfer finished)."""
    deadline = clock() + timeout_s
    first_seen: float | None = None
    last: tuple[Path, int] | None = None
    while True:
        videos = videos_with_stamps(inbox)
        if videos:
            path = sorted(videos)[0]
            size = videos[path][1]
            first_seen = clock() if first_seen is None else first_seen
            if size > 0 and last == (path, size):
                return {"path": path, "first_seen": first_seen, "complete": clock(), "bytes": size,
                        "videos": sorted(p.name for p in videos)}
            last = (path, size)
        if clock() >= deadline:
            raise WorkflowError(f"no video arrived in {inbox} within {timeout_s:g} s")
        sleep(poll_s)


def check_start(args: argparse.Namespace, run_options: list[str]) -> None:
    """Fail before the wait: bad run options, a used session, or an inbox that already holds videos."""
    with_csv = ["run", *session_options(args), "--csv", "unused.csv", *run_options]
    try:
        vbt_session.parse_args(with_csv)
    except SystemExit as error:
        raise WorkflowError("the run options after `--` are invalid; see the message above") from error
    directory = session_ingest.session_dir(args.sessions_root, args.session)
    if (directory / session_ingest.STATE_NAME).exists():
        raise WorkflowError(f"session {args.session} already exists; time a new session id")
    if not args.watch.is_dir():
        raise WorkflowError(f"--watch {args.watch} is not a folder")
    if session_ingest.list_inbox(args.inbox):
        raise WorkflowError(f"--inbox {args.inbox} already holds videos; use an empty folder so only this set "
                            "is ingested and timed")


def session_options(args: argparse.Namespace) -> list[str]:
    return ["--session", args.session, "--sessions-root", str(args.sessions_root), "--manifest", str(args.manifest)]


def run_stage(steps: list[dict[str, Any]], total_s: float) -> dict[str, Any]:
    tracked = [s for s in steps if s["step"] in ("track", "analyze")]
    checks = sum(s["seconds"] for s in steps if s["step"] == "checks")
    return {"seconds": total_s, "processes": tracked, "checks_s": checks,
            "rest_of_run_s": total_s - checks - sum(s["seconds"] for s in tracked)}


def recording_gap(probe: dict[str, Any], arrived: datetime) -> dict[str, Any]:
    created = parse_utc(probe.get("creation_time"))
    if created is None:
        return {**probe, "arrival_minus_creation_s": None}
    return {**probe, "arrival_minus_creation_s": (arrived - created).total_seconds()}


def summary(record: dict[str, Any], rest_s: float | None) -> list[str]:
    stages = record["stages"]
    rows = [("transfer (first seen -> complete)", stages["video"]["complete_minus_first_seen_s"]),
            ("ingest", stages.get("ingest_s")), ("confirm (page ready -> CSV)", stages.get("confirm_s"))]
    for process in stages.get("run", {}).get("processes", []):
        rows.append((f"  {process['step']} {process['fixture_id']}", process["seconds"]))
    if "run" in stages:
        rows += [("  checks (git, versions, CUDA)", stages["run"]["checks_s"]),
                 ("  seeds, scale, report", stages["run"]["rest_of_run_s"]), ("run total", stages["run"]["seconds"])]
    rows += [("machine time (ingest + run)", record["totals"]["machine_s"]),
             ("video complete -> report", record["totals"]["complete_to_report_s"])]
    lines = [f"{label:<40} {'-' if value is None else f'{value:8.1f} s'}" for label, value in rows]
    gap = stages["video"]["recording"].get("arrival_minus_creation_s")
    if gap is not None:
        lines.append(f"{'video arrival - creation_time tag':<40} {gap:8.1f} s  (phone clock; may include the set)")
    total = record["totals"]["complete_to_report_s"]
    if rest_s is not None and total is not None:
        lines.append(f"rest {rest_s:g} s: {rest_s - total:+.1f} s margin, not counting stopping the recording "
                     "and sending the video")
    lines.append(f"status: {record['status']}")
    return lines


def timed_session(args: argparse.Namespace, run_options: list[str], runner: Runner, clock: Clock,
                  wall: WallClock, sleep: Callable[[float], None], probe: RecordingProbe,
                  **hooks: Any) -> dict[str, Any]:
    """Run the four stages; the record holds every stage reached, and the failure if one failed."""
    record: dict[str, Any] = {"format": TIMING_FORMAT, "format_version": TIMING_VERSION, "session_id": args.session,
                              "run_options": run_options, "stages": {}, "status": "incomplete"}
    stages = record["stages"]
    print(f"waiting up to {args.video_timeout_s:g} s for a video in {args.inbox}: send the set now", flush=True)
    video = wait_for_video(args.inbox, args.video_timeout_s, POLL_S, clock, sleep)
    arrived = wall()
    stages["video"] = {"name": video["path"].name, "bytes": video["bytes"], "videos": video["videos"],
                       "complete_minus_first_seen_s": video["complete"] - video["first_seen"],
                       "recording": recording_gap(probe(video["path"]), arrived)}
    start = clock()
    ingest = ["ingest", *session_options(args), "--inbox", str(args.inbox), "--media-dir", str(args.media_dir)]
    if vbt_session.main(ingest, runner=runner, suggester=hooks.get("suggester"), sleep=sleep, clock=clock) != 0:
        record["status"] = "ingest failed"
        return record
    page_ready = clock()
    stages["ingest_s"] = page_ready - start
    print("page ready: confirm the clip on session.html and download the session CSV", flush=True)
    try:
        csv_bytes = vbt_session.watch_for_csv(args.watch, args.session, args.confirm_timeout_s, POLL_S, clock, sleep)
    except WorkflowError as error:
        print(f"error: {error}", file=sys.stderr)
        record["status"] = "no session CSV"
        return record
    run_start = clock()
    stages["confirm_s"] = run_start - page_ready
    # `run` copies the CSV into the session; this copy only exists to pass it by path.
    csv_path = args.output.with_name(f"{args.output.stem}.session.csv")
    csv_path.write_bytes(csv_bytes)
    timed = TimedRunner(runner, clock)
    run = ["run", *session_options(args), "--csv", str(csv_path), *run_options]
    try:
        code = vbt_session.main(run, runner=timed, cropper=hooks.get("cropper"), sleep=sleep, clock=clock)
    finally:
        csv_path.unlink(missing_ok=True)
    stages["run"] = run_stage(timed.steps, clock() - run_start)
    record["status"] = "complete" if code == 0 else "run failed"
    return record


def finish(record: dict[str, Any], output: Path) -> None:
    stages = record["stages"]
    complete = record["status"] == "complete"
    machine = stages.get("ingest_s", 0.0) + stages.get("run", {}).get("seconds", 0.0)
    record["totals"] = {"machine_s": machine if complete else None,
                        "complete_to_report_s": machine + stages["confirm_s"] if complete else None}
    output.write_text(json.dumps(rounded(record), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def rounded(value: Any) -> Any:
    """Millisecond resolution is plenty for stage times and keeps the record readable."""
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {key: rounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rounded(item) for item in value]
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", required=True, help="a new session id, e.g. timing-2026-10-04")
    parser.add_argument("--inbox", type=Path, required=True, help="empty folder the phone sends the video to")
    parser.add_argument("--watch", type=Path, required=True, help="folder the browser downloads the session CSV to")
    parser.add_argument("--sessions-root", type=Path, default=vbt_session.DEFAULT_SESSIONS_ROOT)
    parser.add_argument("--manifest", type=Path, default=analyze_lift.DEFAULT_MANIFEST)
    parser.add_argument("--media-dir", type=Path, default=analyze_lift.PERSONAL_MEDIA_DIR)
    parser.add_argument("--output", type=Path, help="timing record (default validation/private/vbt/timing/"
                                                    "<session>.timing.json)")
    parser.add_argument("--rest-s", type=vbt_session.bounded_timeout, help="your rest between sets, for the summary")
    parser.add_argument("--video-timeout-s", type=vbt_session.bounded_timeout, default=VIDEO_TIMEOUT_S)
    parser.add_argument("--confirm-timeout-s", type=vbt_session.bounded_timeout, default=vbt_session.WATCH_TIMEOUT_S)
    parser.add_argument("run_options", nargs=argparse.REMAINDER, help="-- then the `vbt_session.py run` options")
    return parser


def main(argv: list[str] | None = None, runner: Runner | None = None, clock: Clock = time.monotonic,
         wall: WallClock = lambda: datetime.now(timezone.utc), sleep: Callable[[float], None] = time.sleep,
         probe: RecordingProbe | None = None, **hooks: Any) -> int:
    args = build_parser().parse_args(argv)
    run_options = args.run_options[1:] if args.run_options[:1] == ["--"] else args.run_options
    args.output = args.output or DEFAULT_TIMING_DIR / f"{args.session}.timing.json"
    runner = runner or Runner()
    try:
        check_start(args, run_options)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        record = timed_session(args, run_options, runner, clock, wall, sleep, probe or ffprobe_recording(runner),
                               **hooks)
    except (WorkflowError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finish(record, args.output)
    print("\n".join(summary(record, args.rest_s)))
    print(f"timing record: {analyze_lift.display_path(args.output)}")
    return 0 if record["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
