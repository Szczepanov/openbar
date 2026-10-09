"""`vbt_session.py ingest` (#95): inbox videos -> media copies, seed-frame packages, suggestions, one page.

Registration is deferred to `run`, where the lift picked on the page is known: ingest never writes
the personal manifest. To extract the seed frame it writes a session-local frame manifest
(`frame-manifest.json`) holding only what label_package.py reads (media path and SHA-256, raster
size, rotation, redistribution status). That file is not a fixture manifest and is never passed to
tracking or `analyze`. Standard library only, except the suggester, which imports OpenCV lazily.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable

import analyze_lift
import label_package
import scale_reference
import session_contract
from vbt_process import WorkflowError

SESSION_STATE_FORMAT = "openbar-research-vbt-session-state"
SESSION_STATE_VERSION = 1
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v")
PAGE_TEMPLATE = Path(__file__).with_name("session_page.html")
PAGE_NAME = "session.html"
STATE_NAME = "session.json"
FRAME_MANIFEST_NAME = "frame-manifest.json"
RECORD_NAME = "session-record.json"
PACKAGE_ANNOTATOR = "session-seed"
Suggester = Callable[[Path], dict[str, Any]]


def load_default_suggester() -> tuple[Suggester, dict[str, str]]:
    """vbt_suggest needs OpenCV, which lives in the research venv; fail clearly (before copying) without it."""
    try:
        import vbt_suggest
    except ImportError as error:
        raise WorkflowError(
            f"OpenCV is not importable ({error}); run ingest with the research venv interpreter, "
            "e.g. research/opencv-tracking/.venv/Scripts/python research/vbt-workflow/vbt_session.py ingest ..."
        ) from error
    return vbt_suggest.suggest_frame, vbt_suggest.environment()


def safe_suggestions(suggest: Suggester, frame: Path) -> dict[str, Any]:
    """A suggester that raises gives no suggestion (both items manual), never a failed ingest."""
    try:
        return suggest(frame)
    except Exception as error:  # noqa: BLE001 - a suggestion is optional; the page falls back to clicks
        reason = f"suggester raised {type(error).__name__}: {error}"
        return {kind: {"method": method, "status": "failed", "reason": reason}
                for kind, method in (("plate", "plate-hough-edge-v1"), ("stick", "stick-yellow-markers-v1"))}


def require_allowed(path: Path, flag: str) -> Path:
    if not any(analyze_lift.is_within(path, root) for root in analyze_lift.ALLOWED_ROOTS):
        raise WorkflowError(f"{flag} {path} must be under validation/private/vbt/ or target/ (git-ignored)")
    return path


def session_dir(sessions_root: Path, session_id: str) -> Path:
    """Sessions hold decoded frames of private videos, so they live under validation/private/vbt/ only."""
    if not analyze_lift.is_within(sessions_root, analyze_lift.PERSONAL_ROOT):
        raise WorkflowError(f"--sessions-root {sessions_root} must be under validation/private/vbt/ "
                            "(sessions hold frames of private videos)")
    return sessions_root / session_contract.require_session_id(session_id)


def read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkflowError(f"cannot read {analyze_lift.display_path(path)}: {error}") from error
    if not isinstance(document, dict):
        raise WorkflowError(f"{analyze_lift.display_path(path)} must hold a JSON object")
    return document


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temporary, path)


def write_json(path: Path, document: dict[str, Any]) -> None:
    write_text(path, json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n")


def load_session(directory: Path) -> dict[str, Any] | None:
    path = directory / STATE_NAME
    if not path.exists():
        return None
    state = read_json(path)
    if state.get("format") != SESSION_STATE_FORMAT or state.get("format_version") != SESSION_STATE_VERSION:
        raise WorkflowError(f"{analyze_lift.display_path(path)} is not a {SESSION_STATE_FORMAT} "
                            f"version {SESSION_STATE_VERSION}")
    return state


# --- Inbox and media -----------------------------------------------------------------------------

def list_inbox(inbox: Path) -> list[Path]:
    if not inbox.is_dir():
        raise WorkflowError(f"--inbox {inbox} is not a folder")
    return sorted((path for path in inbox.iterdir()
                   if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in VIDEO_SUFFIXES),
                  key=lambda path: path.name)


def inbox_snapshot(inbox: Path) -> dict[Path, tuple[int, int, str | None]]:
    """Streaming hashes also detect equal-size replacements with a preserved modification time."""
    found = {}
    for path in list_inbox(inbox):
        stamp = (-1, 0, None)
        try:
            info = path.stat()
            stamp = (info.st_mtime_ns, info.st_size, None)
            found[path] = (info.st_mtime_ns, info.st_size, analyze_lift.file_sha256(path))
        except FileNotFoundError:
            continue
        except OSError:
            found[path] = stamp  # still a candidate while the sync client holds it unreadable
    return found


def stable_inbox(inbox: Path, timeout_s: float, clock: Any, sleep: Any) -> dict[Path, str]:
    deadline = clock() + timeout_s
    last = None
    while True:
        current = inbox_snapshot(inbox)
        if not current:
            return {}
        if current == last and all(stamp[1] > 0 and stamp[2] is not None for stamp in current.values()):
            return {path: stamp[2] for path, stamp in current.items() if stamp[2] is not None}
        if clock() >= deadline:
            raise WorkflowError(f"inbox files did not become stable within {timeout_s:g} s; incomplete arrivals are not imported")
        last = current
        sleep(2.0)


def copy_verified(source: Path, target: Path, sha256: str) -> None:
    """Byte copy through a staging name; the copy must hash to the source's SHA-256."""
    staging = target.with_name(f".{target.name}.tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source, staging)
        if analyze_lift.file_sha256(staging) != sha256 or analyze_lift.file_sha256(source) != sha256:
            raise WorkflowError(f"copy of {source.name} does not match its SHA-256; the inbox file may still be "
                                "syncing, so try again once it is complete")
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)


def import_video(source: Path, media_dir: Path, registered: list[dict[str, Any]], sha256: str) -> tuple[Path, str, str]:
    """Return (media path in the repository, SHA-256, action). Never overwrites a different file."""
    if analyze_lift.file_sha256(source) != sha256:
        raise WorkflowError(f"{source.name} changed after inbox planning; retry once stable")
    for fixture in registered:
        if str(fixture.get("media", {}).get("sha256", "")).lower() == sha256:
            path = analyze_lift.ROOT / fixture["media"]["repository_path"]
            if path.is_file() and analyze_lift.file_sha256(path) == sha256:
                return path, sha256, "already registered"
    target = media_dir / source.name
    if target.exists():
        if analyze_lift.file_sha256(target) != sha256:
            raise WorkflowError(f"{analyze_lift.display_path(target)} already exists with different bytes; "
                                f"rename {source.name} in the inbox (nothing is overwritten)")
        return target, sha256, "already copied"
    copy_verified(source, target, sha256)
    return target, sha256, "copied"


def frame_entry(fixture_id: str, media: Path, sha256: str, probe: dict[str, Any]) -> dict[str, Any]:
    """The fields label_package.py reads; see the module docstring."""
    return {
        "id": fixture_id,
        "media": {"repository_path": analyze_lift.display_path(media), "sha256": sha256},
        "video": {"width_px": probe["encoded_width_px"], "height_px": probe["encoded_height_px"],
                  "rotation_deg": probe["rotation_deg"]},
        "source": {"redistribution_status": "private_only"},
    }


# --- Packages, suggestions and the page ------------------------------------------------------------

def build_package(frame_manifest: Path, fixture_id: str, at_s: float | None, output_dir: Path) -> dict[str, Any]:
    """One decoded seed frame through label_package.py (the only decode path); returns its CONFIG."""
    arguments = argparse.Namespace(
        manifest=frame_manifest, fixture=fixture_id, step_s=None,
        frame_index=0 if at_s is None else None, at_s=at_s, start_s=None, end_s=None, include_frame=[],
        annotator_id=PACKAGE_ANNOTATOR, output_dir=output_dir,
    )
    try:
        package = label_package.build(arguments)
        page = (package / "index.html").read_text(encoding="utf-8")
    except (label_package.PackageError, OSError, KeyError, ValueError) as error:
        raise WorkflowError(f"cannot extract the seed frame of {fixture_id}: {error}") from error
    match = scale_reference.LABEL_CONFIG_RE.search(page)
    if match is None:
        raise WorkflowError(f"{fixture_id}: label package page has no CONFIG")
    config = json.loads(match.group(1))
    if len(config.get("frames", [])) != 1:
        raise WorkflowError(f"{fixture_id}: expected exactly one seed frame")
    return config


def page_clip(clip: dict[str, Any]) -> dict[str, Any]:
    keep = ("method", "status", "id", "confidence", "reason", "center_x_px", "center_y_px", "radius_px",
            "low_x_px", "low_y_px", "high_x_px", "high_y_px")
    return {
        **{key: clip[key] for key in ("fixture_id", "sha256", "package_id", "frame_index", "timestamp_s",
                                      "width_px", "height_px", "original_name", "registered_exercise")},
        "suggestions": {kind: {key: value for key, value in suggestion.items() if key in keep}
                        for kind, suggestion in clip["suggestions"].items()},
    }


def render_page(state: dict[str, Any], images: list[str], template: str) -> str:
    config = {"format": session_contract.FORMAT, "session_id": state["session_id"],
              "page_id": state["page_id"], "clips": [page_clip(clip) for clip in state["clips"]]}
    replacements = {"/*CONFIG*/null": config, "/*IMAGES*/null": images}
    for placeholder, value in replacements.items():
        if template.count(placeholder) != 1:
            raise WorkflowError(f"session page template must contain {placeholder} exactly once")
        # Every "<" is escaped, so no file name can close the script or open a comment inside it.
        template = template.replace(placeholder, json.dumps(value, sort_keys=True).replace("<", "\\u003c"))
    return template


def compute_page_id(state: dict[str, Any]) -> str:
    """The page id binds a CSV to the clips, frames and suggestions shown, and to the page template."""
    return session_contract.page_id({"template_sha256": state["template_sha256"], "session_id": state["session_id"],
                                     "clips": [page_clip(clip) for clip in state["clips"]]})


def frame_data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def parse_at_s(values: list[str]) -> dict[str, float]:
    overrides = {}
    for value in values:
        match = re.fullmatch(r"(.+)=([0-9]+(?:\.[0-9]+)?)", value)
        if match is None:
            raise WorkflowError(f"--at-s {value!r} must be <file name or fixture id>=<seconds>")
        overrides[match.group(1)] = float(match.group(2))
    return overrides


# --- Command -------------------------------------------------------------------------------------

def plan_clips(previous: dict[str, Any] | None, inbox_files: dict[Path, str], registered: list[dict[str, Any]],
               include_registered: bool) -> tuple[list[dict[str, Any]], list[str]]:
    """Existing session clips keep their place; new inbox videos follow in file-name order."""
    clips = [dict(clip) for clip in (previous or {}).get("clips", [])]
    known = {clip["sha256"] for clip in clips}
    registered_sha = {str(f.get("media", {}).get("sha256", "")).lower(): f for f in registered}
    notes = []
    for source, sha256 in sorted(inbox_files.items()):
        if analyze_lift.file_sha256(source) != sha256:
            raise WorkflowError(f"{source.name} changed after stable inbox polling; retry once stable")
        if sha256 in known:
            continue
        if sha256 in registered_sha and not include_registered:
            notes.append(f"skipped {source.name}: already registered as {registered_sha[sha256]['id']} "
                         "(pass --include-registered to add it to this session)")
            continue
        clips.append({"source": source, "sha256": sha256})
        known.add(sha256)
    return clips, notes


def ingest_clip(clip: dict[str, Any], args: argparse.Namespace, registered: list[dict[str, Any]],
                directory: Path, overrides: dict[str, float]) -> tuple[dict[str, Any], dict[str, Any], str]:
    if "source" in clip:
        media, sha256, action = import_video(clip["source"], args.media_dir, registered, clip["sha256"])
        original = clip["source"].name
    else:
        media, sha256, action = analyze_lift.ROOT / clip["media_path"], clip["sha256"], "in session"
        if not media.is_file() or analyze_lift.file_sha256(media) != sha256:
            raise WorkflowError(f"{clip['media_path']} is missing or changed since it was ingested")
        original = clip["original_name"]
    fixture_id = analyze_lift.fixture_id_for(sha256)
    if original in overrides and fixture_id in overrides and original != fixture_id:
        raise WorkflowError(f"--at-s names {original} both by file name and by id {fixture_id}; give only one")
    at_s = overrides.pop(original, overrides.pop(fixture_id, clip.get("at_s")))
    probe = analyze_lift.fixture_probe.probe_video(media)
    if probe["sha256"].lower() != sha256:
        raise WorkflowError(f"{media.name} changed while it was being probed")
    entry = frame_entry(fixture_id, media, sha256, probe)
    registered_entry = next((f for f in registered if f.get("id") == fixture_id), None)
    return {
        "fixture_id": fixture_id, "sha256": sha256, "media_path": analyze_lift.display_path(media),
        "original_name": original, "at_s": at_s,
        "registered_exercise": None if registered_entry is None else registered_entry.get("exercise"),
        "rotation_deg": probe["rotation_deg"], "package_dir": analyze_lift.display_path(
            directory / "packages" / fixture_id),
    }, entry, action


def command_ingest(args: argparse.Namespace, suggester: Suggester | None = None,
                   clock: Any = time.monotonic, sleep: Any = time.sleep) -> int:
    manifest = analyze_lift.require_personal_manifest(args.manifest)
    require_allowed(args.media_dir, "--media-dir")
    directory = session_dir(args.sessions_root, args.session)
    record = directory / RECORD_NAME
    if record.exists() and not args.force:
        raise WorkflowError(
            f"session {args.session} was already run ({analyze_lift.display_path(record)} exists); re-ingesting would "
            "rebuild the page next to that run. Pass --force to do it anyway: the record is removed first, so the "
            "earlier run counts as incomplete until `run --force`")
    previous = load_session(directory)
    overrides = parse_at_s(args.at_s)
    suggest, environment = (suggester, None) if suggester is not None else load_default_suggester()
    registered = analyze_lift.load_manifest_fixtures(manifest)
    planned, notes = plan_clips(previous, stable_inbox(args.inbox, args.inbox_timeout_s, clock, sleep),
                               registered, args.include_registered)
    if not planned:
        raise WorkflowError(f"no new videos in {args.inbox} and no clips in session {args.session}")
    ingested = [ingest_clip(clip, args, registered, directory, overrides) for clip in planned]
    if overrides:
        raise WorkflowError(f"--at-s names no clip of this session: {', '.join(sorted(overrides))}")
    frame_manifest = directory / FRAME_MANIFEST_NAME
    # All read-only planning/probing succeeded. From this point the page is being rebuilt, so a
    # completed prior run must stop looking current before any session-local artifact changes.
    record.unlink(missing_ok=True)  # only changes anything with --force when a record exists
    write_json(frame_manifest, {"format": "openbar-research-vbt-frame-manifest", "format_version": 1,
                                "note": "seed-frame extraction only; not a fixture manifest",
                                "fixtures": [entry for _, entry, _ in ingested]})
    clips = []
    for index, (clip, _, action) in enumerate(ingested):
        package_dir = analyze_lift.ROOT / clip["package_dir"]
        config = build_package(frame_manifest, clip["fixture_id"], clip["at_s"], package_dir)
        frame = config["frames"][0]
        suggestions = safe_suggestions(suggest, package_dir / frame["file"])
        clips.append({**clip, "clip_index": index, "package_id": config["package_id"],
                      "frame_index": frame["frame_index"], "timestamp_s": frame["timestamp_s"],
                      "frame_file": frame["file"], "width_px": config["width_px"], "height_px": config["height_px"],
                      "suggestions": suggestions})
        print(f"{clip['fixture_id']} ({clip['original_name']}): {action}; seed frame {frame['frame_index']} "
              f"at {frame['timestamp_s']} s; plate {suggestions['plate']['status']}, stick {suggestions['stick']['status']}")
    state = {"format": SESSION_STATE_FORMAT, "format_version": SESSION_STATE_VERSION,
             "session_id": args.session, "workflow_version": analyze_lift.WORKFLOW_VERSION,
             "suggester_environment": environment, "clips": clips}
    template = PAGE_TEMPLATE.read_text(encoding="utf-8")
    state["template_sha256"] = hashlib.sha256(template.encode("utf-8")).hexdigest()
    state["page_id"] = compute_page_id(state)
    images = [frame_data_uri(analyze_lift.ROOT / clip["package_dir"] / clip["frame_file"]) for clip in clips]
    write_json(directory / STATE_NAME, state)
    write_text(directory / PAGE_NAME, render_page(state, images, template))
    for note in notes:
        print(note)
    print(f"open {analyze_lift.display_path(directory / PAGE_NAME)}: confirm or adjust each clip, pick the lift, "
          f"then Download session CSV (vbt-session-{args.session}.csv)")
    return 0
