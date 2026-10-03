#!/usr/bin/env python3
"""One-page VBT session workflow (#95): ingest a folder of lift videos, confirm on one page, run all.

  ingest  copy new videos from --inbox (e.g. a Google Drive for desktop folder) into the personal
          media folder without modifying them (SHA-256 checked), skip videos already registered, extract
          each clip's seed frame through label_package.py (frame 0, or --at-s <name>=<seconds>), propose
          the plate circle and the two stick markers (OpenCV, vbt_suggest.py), and write
          validation/private/vbt/sessions/<session>/session.html. Ingest never writes the personal
          manifest: registration waits for `run`, when the lift chosen on the page is known.
  run     validate the downloaded session CSV against the session (fail closed), register each
          confirmed clip with its confirmed lift, write manual-target-seed-v1 seeds (annotations.py
          seed code path, with suggestion provenance in the notes) and scale-reference click CSVs,
          run analyze_lift.py `run` per clip with the tracker that the named --tracker-policy assigns
          to its lift, then scale_reference.py `report`, report.html and a session record.
          --watch <folder> waits (bounded by --watch-timeout-s) for vbt-session-<session>.csv.

Suggestions are proposals only: nothing is used until the clip is confirmed on the page, and the
CSV records per item whether a suggestion was accepted unchanged, adjusted, or placed by hand.
Research orchestration: no measurement logic, no calibration change. Run it with the research venv
interpreter (research/opencv-tracking/.venv). See docs/plans/VBT_WORKFLOW_PLAN.md (step 2).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable

import analyze_lift
import session_ingest
import session_run
from vbt_process import Runner, WorkflowError

DEFAULT_SESSIONS_ROOT = analyze_lift.PERSONAL_ROOT / "sessions"
WATCH_TIMEOUT_S = 900.0
WATCH_MAX_TIMEOUT_S = 6 * 3600.0
WATCH_POLL_S = 2.0


def csv_name(session_id: str) -> str:
    return f"vbt-session-{session_id}.csv"


def watch_for_csv(folder: Path, session_id: str, timeout_s: float, poll_s: float,
                  clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> bytes:
    """Wait for exactly one vbt-session-<session>*.csv whose size is stable across two polls."""
    if not folder.is_dir():
        raise WorkflowError(f"--watch {folder} is not a folder")
    stem = csv_name(session_id)[:-len(".csv")]
    print(f"waiting up to {timeout_s:g} s for {csv_name(session_id)} in {folder} (Ctrl+C to stop)...", flush=True)
    deadline = clock() + timeout_s
    last_size: int | None = None
    while True:
        # Browsers name a repeated download "<name> (1).csv"; more than one candidate is ambiguous.
        found = sorted(path for path in folder.glob(f"{stem}*.csv")
                       if path.is_file() and path.name[len(stem):-4] in ("",) + tuple(f" ({n})" for n in range(1, 100)))
        if len(found) > 1:
            raise WorkflowError(f"several session CSVs in {folder} ({', '.join(path.name for path in found)}); "
                                "pass the right one with --csv")
        if found:
            size = found[0].stat().st_size
            if size > 0 and size == last_size:
                print(f"found {found[0].name}", flush=True)
                return found[0].read_bytes()
            last_size = size
        if clock() >= deadline:
            raise WorkflowError(f"no {csv_name(session_id)} appeared in {folder} within {timeout_s:g} s; download it "
                                "from session.html, then run again (or pass --csv)")
        sleep(poll_s)


def bounded_timeout(text: str) -> float:
    value = float(text)
    if not 0 < value <= WATCH_MAX_TIMEOUT_S:
        raise argparse.ArgumentTypeError(f"must be in (0, {WATCH_MAX_TIMEOUT_S:g}] seconds")
    return value


def add_session(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", required=True, help="session id, e.g. 2026-10-03")
    parser.add_argument("--sessions-root", type=Path, default=DEFAULT_SESSIONS_ROOT,
                        help="under validation/private/vbt/ or target/ (default validation/private/vbt/sessions)")
    parser.add_argument("--manifest", type=Path, default=analyze_lift.DEFAULT_MANIFEST,
                        help="personal manifest (default validation/private/vbt/manifest.json)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="copy inbox videos, extract seed frames, build session.html")
    add_session(ingest)
    ingest.add_argument("--inbox", type=Path, required=True, help="folder with new videos (read only)")
    ingest.add_argument("--media-dir", type=Path, default=analyze_lift.PERSONAL_MEDIA_DIR)
    ingest.add_argument("--at-s", action="append", default=[], metavar="NAME=SECONDS",
                        help="seed frame nearest SECONDS for the clip with this file name or fixture id "
                             "(default frame 0); repeatable")
    ingest.add_argument("--include-registered", action="store_true",
                        help="also add inbox videos that are already in the personal manifest")

    run = sub.add_parser("run", help="validate the session CSV, then seed, track, analyze and report")
    add_session(run)
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", type=Path, help="the downloaded session CSV")
    source.add_argument("--watch", type=Path, help="folder to poll for vbt-session-<session>.csv")
    run.add_argument("--watch-timeout-s", type=bounded_timeout, default=WATCH_TIMEOUT_S)
    run.add_argument("--plate-diameter-m", required=True)
    run.add_argument("--stick-length-m", required=True, help="known length between the lowest and highest marker")
    run.add_argument("--tracker-policy", required=True, choices=sorted(session_run.TRACKER_POLICIES),
                     help="; ".join(f"{name}: {policy['description']}"
                                    for name, policy in sorted(session_run.TRACKER_POLICIES.items())))
    run.add_argument("--gpu-python", help="GPU venv interpreter, needed when the policy runs SAM 2")
    run.add_argument("--openbar-cli", help="prebuilt openbar-cli binary passed to analyze_lift.py")
    run.add_argument("--force", action="store_true", help="replace existing session outputs")
    run.add_argument("--preset", choices=sorted(analyze_lift.PRESETS))
    run.add_argument("--filter", choices=analyze_lift.FILTERS)
    for flag, kind in analyze_lift.FILTER_FLAGS:
        run.add_argument(flag, type=kind)
    for flag in analyze_lift.KINEMATICS_FLAGS:
        run.add_argument(flag, type=analyze_lift.finite_number)
    return parser


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "run":
        return args
    explicit = [flag for flag in ("--filter", *(f for f, _ in analyze_lift.FILTER_FLAGS), *analyze_lift.KINEMATICS_FLAGS)
                if getattr(args, analyze_lift.dest(flag)) is not None]
    if args.preset is not None and explicit:
        parser.error(f"--preset cannot be combined with {', '.join(explicit)}")
    if args.preset is None:
        missing = [flag for flag in ("--filter", *analyze_lift.KINEMATICS_FLAGS)
                   if getattr(args, analyze_lift.dest(flag)) is None]
        if missing:
            parser.error(f"either --preset or explicit {', '.join(missing)} is required (no silent defaults)")
    return args


def main(argv: list[str] | None = None, runner: Runner | None = None,
         suggester: session_ingest.Suggester | None = None, cropper: session_run.session_report.Cropper | None = None,
         sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> int:
    args = parse_args(argv)
    runner = runner or Runner()
    try:
        session_ingest.session_dir(args.sessions_root, args.session)  # fail fast on a bad id or location
        if args.command == "ingest":
            analyze_lift.require_tools(runner, ("ffmpeg", "ffprobe"))
            return session_ingest.command_ingest(args, suggester)
        if args.csv is not None:
            try:
                data = args.csv.read_bytes()
            except OSError as error:
                raise WorkflowError(f"cannot read --csv {args.csv}: {error}") from error
        else:
            data = watch_for_csv(args.watch, args.session, args.watch_timeout_s, WATCH_POLL_S, clock, sleep)
        return session_run.command_run(args, runner, data, cropper)
    except (WorkflowError, session_ingest.session_contract.SessionCsvError, analyze_lift.fixture_probe.ProbeError,
            OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
