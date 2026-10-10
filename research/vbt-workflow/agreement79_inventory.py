#!/usr/bin/env python3
"""#79 private input inventory, 18-slot accounting, per-lift pairs files, collection-lock summary, rehash.

`inventory` reads the owner's strict slots.json and the retained session outputs, derives each slot's
failures (stage:reason) next to the owner's, checks every analyzable pair with the pinned consumer's own
parsers (accept/reject only), and writes, into a NEW directory under validation/private/vbt/study-79/:
the private inventory.json binding every input by exact-byte SHA-256, a public-safe lock-summary.json
carrying only the inventory digest, counts and freeze references, and - only when the inventory is
lockable - one consumer pairs file per lift with analyzable slots. `verify` rehashes everything recorded.

No outcome is computed: velocities and scale ratios are never read, frozen values come only from
agreement79_study.py, and nothing is overwritten. Exit 0 written, 1 invalid input, 3 infrastructure abort
(nothing written; retry unchanged inputs). Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

import agreement79_study as study
import analyze_lift as workflow
from agreement79_slot_evidence import Preflight, Probe, StatusCheck
import agreement79_slot_evidence as evidence

ROOT = workflow.ROOT
INVENTORY_NAME = "inventory.json"
LOCK_SUMMARY_NAME = "lock-summary.json"
TOP_KEYS = {"format", "format_version", "study_id", "collection_status", "plate_diameter_m", "stick_length_m",
            "wl_analysis_version", "assessments_dir", "slots"}
COLLECTION_STATUSES = ("in_progress", "complete", "concluded")
NOT_RECORDED_KEYS = {"slot", "status", "protocol_failures"}
ENROLLED_KEYS = NOT_RECORDED_KEYS | {"session_id", "session_date", "original_name", "fixture_id", "wl_csv",
                                    "attempted_rep_start_s"}
# Derived failures in these stages make a slot non-analyzable; owner failures never do.
BLOCKING_STAGES = ("novelty", "confirmation", "processing", "assessment", "wl_export")
FIXTURE_ID = re.compile(r"vbt-[0-9a-f]{16}")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
GitState = Callable[[Path], "tuple[str, bool]"]

# Compatibility names for the report tool; both are the shared agreement79_study objects.
InventoryInputError = study.StudyInputError
git_state = study.git_state


def require(condition: bool, message: str) -> None:
    if not condition:
        raise study.StudyInputError(message)


def resolve(path: Path) -> Path:
    """``workflow.safe_resolve`` with an unresolvable path reported as invalid input (exit 1)."""
    try:
        return workflow.safe_resolve(path)
    except RuntimeError as error:
        raise study.StudyInputError(str(error)) from error


# --- slots.json ----------------------------------------------------------------------------------

def single_line(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\n" not in value and "\r" not in value


def parse_failures(slot: str, value: Any) -> list[dict[str, str]]:
    require(isinstance(value, list), f"{slot}: protocol_failures must be a list")
    for item in value:
        require(isinstance(item, dict) and set(item) == {"stage", "reason"},
                f"{slot}: each protocol failure has exactly stage and reason")
        require(item["stage"] in study.FAILURE_STAGES, f"{slot}: unknown failure stage {item['stage']!r}")
        require(single_line(item["reason"]), f"{slot}: failure reason must be a non-empty single line")
    return value


def parse_slot(expected: str, entry: Any) -> dict[str, Any]:
    require(isinstance(entry, dict), f"slot {expected} must be an object")
    require(entry.get("slot") == expected, f"slots must follow the frozen order; expected {expected}, "
                                           f"got {entry.get('slot')!r}")
    status = entry.get("status")
    require(status in ("enrolled", "not_recorded"), f"{expected}: status must be enrolled or not_recorded")
    keys = ENROLLED_KEYS if status == "enrolled" else NOT_RECORDED_KEYS
    require(set(entry) == keys, f"{expected}: {status} slot keys must be exactly {sorted(keys)}")
    failures = parse_failures(expected, entry["protocol_failures"])
    if status == "not_recorded":
        require(bool(failures), f"{expected}: a not_recorded slot needs at least one protocol failure")
        return entry
    require(isinstance(entry["session_id"], str) and SESSION_ID.fullmatch(entry["session_id"]) is not None,
            f"{expected}: session_id must be a plain folder name")
    require(valid_date(entry["session_date"]), f"{expected}: session_date must be a YYYY-MM-DD date")
    require(single_line(entry["original_name"]), f"{expected}: original_name must be a non-empty single line")
    require(entry["fixture_id"] is None or (isinstance(entry["fixture_id"], str)
                                            and FIXTURE_ID.fullmatch(entry["fixture_id"]) is not None),
            f"{expected}: fixture_id must be null or vbt-<16 hex>")
    require(entry["wl_csv"] is None or isinstance(entry["wl_csv"], str), f"{expected}: wl_csv must be text or null")
    reps = entry["attempted_rep_start_s"]
    require(isinstance(reps, list) and all(type(value) in (int, float) for value in reps),
            f"{expected}: attempted_rep_start_s must be a list of numbers")
    return entry


def valid_date(value: Any) -> bool:
    if not isinstance(value, str) or ISO_DATE.fullmatch(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def check_sessions(slots: list[dict[str, Any]]) -> None:
    """Slots of session k share one id and date; the sessions' ids and dates are distinct."""
    identities = {}
    for entry in slots:
        if entry["status"] == "enrolled":
            identities.setdefault(study.slot_session(entry["slot"]), set()).add(
                (entry["session_id"], entry["session_date"]))
    for session, values in identities.items():
        require(len(values) == 1, f"session {session}: enrolled slots must share one session_id and session_date")
    chosen = [next(iter(values)) for values in identities.values()]
    require(len({identity for identity, _ in chosen}) == len(chosen), "the three sessions need distinct session_ids")
    require(len({day for _, day in chosen}) == len(chosen), "the three sessions need distinct session_dates")


def parse_slots(root: Path, data: bytes) -> dict[str, Any]:
    try:
        document = workflow.schema_check.loads_strict(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise study.StudyInputError(f"slots file is not strict UTF-8 JSON: {error}") from error
    require(isinstance(document, dict) and set(document) == TOP_KEYS, f"slots keys must be exactly {sorted(TOP_KEYS)}")
    require(document["format"] == study.SLOTS_FORMAT, f"slots format must be {study.SLOTS_FORMAT}")
    version = document["format_version"]
    require(type(version) is int and version == study.STUDY_DOCUMENT_VERSION, "slots format_version must be 1")
    require(document["study_id"] == study.STUDY_ID, f"study_id must be {study.STUDY_ID}")
    require(document["collection_status"] in COLLECTION_STATUSES,
            f"collection_status must be one of {', '.join(COLLECTION_STATUSES)}")
    plate = document["plate_diameter_m"]
    require(evidence.is_number(plate) and plate > 0, "plate_diameter_m must be a positive finite number")
    stick = document["stick_length_m"]
    require(evidence.is_number(stick) and stick == study.STICK_LENGTH_M,
            f"stick_length_m must equal the frozen {study.STICK_LENGTH_M}")
    require(single_line(document["wl_analysis_version"]), "wl_analysis_version must be a non-empty single line")
    require(study.recorded_path(root, document["assessments_dir"]) is not None,
            "assessments_dir must be a canonical repository-relative path inside the repository")
    slots = document["slots"]
    require(isinstance(slots, list) and len(slots) == len(study.SLOTS), f"slots must list exactly {len(study.SLOTS)}")
    parsed = [parse_slot(expected, entry) for expected, entry in zip(study.SLOTS, slots)]
    check_sessions(parsed)
    return document


# --- Derivation ----------------------------------------------------------------------------------

def failure_list(owner: list[dict[str, str]], derived: list[tuple[str, str]]) -> list[dict[str, str]]:
    items = {(item["stage"], item["reason"], "owner") for item in owner}
    items |= {(stage, reason, "derived") for stage, reason in derived}
    ordered = sorted(items, key=lambda item: (study.FAILURE_STAGES.index(item[0]), item[1], item[2]))
    return [{"stage": stage, "reason": reason, "source": source} for stage, reason, source in ordered]


def derive_all(root: Path, document: dict[str, Any], context: dict[str, Any],
               status: StatusCheck) -> tuple[list[evidence.SlotEvidence | None], dict[str, dict[str, Any]]]:
    sessions: dict[str, dict[str, Any]] = {}
    results: list[evidence.SlotEvidence | None] = []
    seen: dict[str, str] = {}
    for entry in document["slots"]:
        if entry["status"] != "enrolled":
            results.append(None)
            continue
        if entry["fixture_id"] is None:
            result = evidence.SlotEvidence()
            if not entry["protocol_failures"]:
                result.fail("processing", "not_processed")
            results.append(result)
            continue
        if entry["session_id"] not in sessions:
            sessions[entry["session_id"]] = evidence.load_session(root, entry["session_id"], status)
        result = evidence.derive_slot(root, entry, sessions[entry["session_id"]], context)
        if result.video_sha256 is not None:
            if result.video_sha256 in seen:
                result.fail("novelty", f"duplicate_of_slot:{seen[result.video_sha256]}")
            else:
                seen[result.video_sha256] = entry["slot"]
        results.append(result)
    return results, sessions


def reference_environment(results: list[evidence.SlotEvidence | None]) -> dict[str, Any] | None:
    """Run environment of the first processed slot in frozen order; a differing slot is a protocol note.

    Values are compared as canonical JSON, so nested dicts or lists never break the comparison. The
    difference is non-blocking: the frozen method is checked field by field elsewhere.
    """
    processed = [result for result in results
                 if result is not None and result.processed and result.environment is not None]
    if not processed:
        return None
    reference = processed[0].environment
    for result in processed[1:]:
        if workflow.canonical_sha256(result.environment) != workflow.canonical_sha256(reference):
            result.fail("protocol", "environment_mismatch")
    return reference


def session_membership(document: dict[str, Any], results: list[evidence.SlotEvidence | None],
                       sessions: dict[str, dict[str, Any]]) -> None:
    """Every enrolled slot of a session whose record holds a clip or skipped name no slot of it claims."""
    for session_id, session in sessions.items():
        entries = [(entry, result) for entry, result in zip(document["slots"], results)
                   if entry["status"] == "enrolled" and entry["session_id"] == session_id]
        record = session["record"]
        if record is None:
            continue
        fixture_ids = {entry["fixture_id"] for entry, _ in entries if entry["fixture_id"] is not None}
        names = {entry["original_name"] for entry, _ in entries}
        clips = record.get("clips") if isinstance(record.get("clips"), list) else []
        skipped = record.get("skipped") if isinstance(record.get("skipped"), list) else []
        stray = (any(evidence.dig(clip, "fixture_id") not in fixture_ids for clip in clips)
                 or any(name not in names for name in skipped))
        if stray:
            for _, result in entries:
                if result is not None:
                    result.fail("protocol", "session_has_unenrolled_clip")


def slot_order(document: dict[str, Any], results: list[evidence.SlotEvidence | None]) -> None:
    """Within a session, each slot's container creation instant must be strictly after the previous one's."""
    previous: dict[str, Any] = {}
    for entry, result in zip(document["slots"], results):
        if entry["status"] != "enrolled" or result is None:
            continue
        instant = evidence.parse_creation_time(result.creation_time)
        if instant is None:
            continue
        earlier = previous.get(entry["session_id"])
        if earlier is not None and not instant > earlier:
            result.fail("protocol", "slot_order_violation")
        previous[entry["session_id"]] = instant


def blocked(result: evidence.SlotEvidence) -> bool:
    return any(stage in BLOCKING_STAGES for stage, _ in result.failures)


def parser_preflight(root: Path, app: Path, results: list[evidence.SlotEvidence | None],
                     preflight: Preflight) -> None:
    """Run the pinned consumer's parsers on every analyzable-so-far slot holding both files.

    A rejection blocks the slot; the parser's message stays in the private inventory only.
    """
    for result in results:
        if result is None or not result.processed or blocked(result):
            continue
        if "analysis" not in result.files or "wl_csv" not in result.files:
            continue
        verdict = preflight(app, root / result.files["analysis"]["path"], root / result.files["wl_csv"]["path"])
        result.parser_preflight = verdict
        if not verdict["openbar"]["accepted"]:
            result.fail("processing", "openbar_parser_rejected")
        if not verdict["wl"]["accepted"]:
            result.fail("wl_export", "wl_parser_rejected")


def slot_document(entry: dict[str, Any], result: evidence.SlotEvidence | None) -> dict[str, Any]:
    lift = study.slot_lift(entry["slot"])
    enrolled = entry["status"] == "enrolled"
    derived = result.failures if result is not None else []
    processed = result is not None and result.processed
    analyzable = enrolled and processed and not blocked(result)
    failures = failure_list(entry["protocol_failures"], derived)
    return {
        "slot": entry["slot"], "lift": lift["code"], "exercise": lift["exercise"], "load_kg": lift["load_kg"],
        "session": study.slot_session(entry["slot"]), "status": entry["status"],
        "session_id": entry.get("session_id"), "session_date": entry.get("session_date"),
        "fixture_id": entry.get("fixture_id"), "processed": processed, "analyzable": analyzable,
        "protocol_conforming": analyzable and not failures, "failures": failures,
        "assessment_statuses": None if result is None else result.assessment_statuses,
        "seed_timestamp_s": None if result is None else result.seed_timestamp_s,
        "attempted_rep_start_s": entry.get("attempted_rep_start_s"),
        "creation_time": None if result is None else result.creation_time,
        "manifest_entry_sha256": None if result is None else result.manifest_entry_sha256,
        "parser_preflight": None if result is None else result.parser_preflight,
        "files": {} if result is None else result.files,
    }


def pairs_documents(root: Path, slots: list[dict[str, Any]], output_dir: Path) -> dict[str, bytes]:
    """Consumer pairs files: analyzable slots in frozen order, paths relative to the pairs file's folder."""
    def relative(recorded: str) -> str:
        return Path(os.path.relpath(root / recorded, output_dir)).as_posix()

    documents = {}
    for lift in study.LIFTS.values():
        pairs = [{"label": slot["slot"], "loadKg": slot["load_kg"], "wlCsv": relative(slot["files"]["wl_csv"]["path"]),
                  "openBarAnalysis": relative(slot["files"]["analysis"]["path"]), "offsetS": 0}
                 for slot in slots if slot["exercise"] == lift["exercise"] and slot["analyzable"]]
        if pairs:
            documents[lift["exercise"]] = study.serialize({"pairs": pairs})
    return documents


def counts(slots: list[dict[str, Any]]) -> dict[str, Any]:
    def tally(items: list[dict[str, Any]]) -> dict[str, int]:
        return {"enrolled": sum(slot["status"] == "enrolled" for slot in items),
                "analyzable": sum(slot["analyzable"] for slot in items),
                "protocol_conforming": sum(slot["protocol_conforming"] for slot in items)}

    return {"planned_videos": len(study.SLOTS), "planned_reps": len(study.SLOTS) * study.PLANNED_REPS_PER_VIDEO,
            **tally(slots), "not_recorded": sum(slot["status"] == "not_recorded" for slot in slots),
            "processed": sum(slot["processed"] for slot in slots),
            "per_lift": {lift["exercise"]: tally([slot for slot in slots if slot["exercise"] == lift["exercise"]])
                         for lift in study.LIFTS.values()}}


def lift_records(root: Path, slots: list[dict[str, Any]], pairs: dict[str, bytes],
                 output_dir: Path, lockable: bool) -> dict[str, Any]:
    """Per-lift slot lists; a pairs file is recorded only for a lockable inventory."""
    records = {}
    for lift in study.LIFTS.values():
        exercise = lift["exercise"]
        record: dict[str, Any] = {
            "slots": [slot["slot"] for slot in slots if slot["exercise"] == exercise],
            "analyzable_slots": [slot["slot"] for slot in slots if slot["exercise"] == exercise and slot["analyzable"]]}
        if not lockable:
            record.update(pairs_file=None, reason="collection_in_progress")
        elif exercise in pairs:
            record["pairs_file"] = {"path": study.repo_relative(output_dir / f"pairs-{exercise}.json", root),
                                    "sha256": hashlib.sha256(pairs[exercise]).hexdigest()}
        else:
            record.update(pairs_file=None, reason="no_analyzable_slots")
        records[exercise] = record
    return records


# --- Command -------------------------------------------------------------------------------------

def check_paths(root: Path, slots_path: Path, output_dir: Path, exclusion_list: Path) -> None:
    study_dir = resolve(root / study.STUDY_DIR)
    for flag, path in (("--slots", slots_path), ("--exclusion-list", exclusion_list)):
        require(workflow.is_within(path, root), f"{flag} {path} is outside the repository")
    require(slots_path.is_file(), f"--slots {slots_path} is not a file")
    require(workflow.is_within(output_dir, study_dir) and output_dir != study_dir,
            f"--output-dir must be a new folder under {study.STUDY_DIR.as_posix()}/")
    require(not output_dir.exists(), f"--output-dir {study.repo_relative(output_dir, root)} already exists; "
                                     "retained outputs are never replaced")


def check_consumer(app: Path, git: GitState) -> Path:
    """The pinned consumer app must sit in a tracked-clean checkout at CONSUMER_COMMIT (else exit 3)."""
    app = resolve(app)
    commit, clean = git(app.parent)
    if commit != study.CONSUMER_COMMIT or not clean:
        raise study.StudyInfrastructureError(
            f"the consumer checkout must be the clean pinned {study.CONSUMER_COMMIT}; found {commit} "
            f"(tracked files clean: {clean})")
    return app


def derive(root: Path, document: dict[str, Any], context: dict[str, Any], status: StatusCheck, app: Path,
           preflight: Preflight) -> tuple[list[evidence.SlotEvidence | None], dict[str, Any], dict[str, Any]]:
    """Every derived failure: per slot, across slots, then the consumer parser preflight."""
    results, sessions = derive_all(root, document, context, status)
    environment = reference_environment(results)
    session_membership(document, results, sessions)
    slot_order(document, results)
    parser_preflight(root, app, results, preflight)
    return results, sessions, environment


def build(root: Path, slots_path: Path, output_dir: Path, consumer_app: Path, exclusion_list: Path | None = None, *,
          status: StatusCheck = evidence.session_status, probe: Probe = evidence.ffprobe_creation_time,
          git: GitState = study.git_state, preflight: Preflight = evidence.consumer_preflight,
          exclusion_sha256: str | None = None, tool_root: Path = ROOT) -> dict[str, Any]:
    """Every check and every derived value; no writes. Raises StudyInputError or StudyInfrastructureError.

    ``root`` is the pinned data checkout that ran the session workflow; ``tool_root`` is this tool's own
    checkout. Both commits are recorded, and the data checkout must be the clean frozen baseline. A
    lockable inventory also needs a tracked-clean tool checkout.
    """
    root = resolve(root)
    data_commit, data_clean = git(root)
    require(data_commit == study.OPENBAR_BASELINE_COMMIT and data_clean,
            f"the data checkout must be the clean pinned baseline {study.OPENBAR_BASELINE_COMMIT}; "
            f"found {data_commit} (tracked files clean: {data_clean})")
    tool_commit, tool_clean = git(resolve(tool_root))
    slots_path, output_dir = resolve(slots_path), resolve(output_dir)
    exclusion_list = resolve(exclusion_list or root / study.EXCLUSION_LIST)
    check_paths(root, slots_path, output_dir, exclusion_list)
    data = slots_path.read_bytes()
    document = parse_slots(root, data)
    lockable = document["collection_status"] != "in_progress"
    require(tool_clean or not lockable,
            f"a lockable inventory needs a tracked-clean tool checkout; {tool_root} at {tool_commit} has tracked "
            "changes (commit or revert them, or keep collection_status in_progress for a draft)")
    app = check_consumer(consumer_app, git)
    exclusion_digest, excluded = evidence.load_exclusion_list(
        exclusion_list, exclusion_sha256 or study.NOVELTY_EXCLUSION_SHA256)
    context = {"plate_diameter_m": document["plate_diameter_m"], "excluded": excluded, "probe": probe,
               "assessments_dir": study.recorded_path(root, document["assessments_dir"])}
    results, sessions, environment = derive(root, document, context, status, app, preflight)
    slots = [slot_document(entry, result) for entry, result in zip(document["slots"], results)]
    pairs = pairs_documents(root, slots, output_dir) if lockable else {}
    tally = counts(slots)
    inventory = {
        "format": study.INVENTORY_FORMAT, "format_version": study.STUDY_DOCUMENT_VERSION,
        "study_id": study.STUDY_ID, "tool_version": study.TOOL_VERSION, "freeze": study.freeze_references(),
        "collection_status": document["collection_status"], "plate_diameter_m": document["plate_diameter_m"],
        "stick_length_m": document["stick_length_m"], "wl_analysis_version": document["wl_analysis_version"],
        "assessments_dir": document["assessments_dir"],
        "slots_file": {"path": study.repo_relative(slots_path, root), "sha256": hashlib.sha256(data).hexdigest()},
        "exclusion_list": {"path": study.repo_relative(exclusion_list, root), "sha256": exclusion_digest},
        "slots": slots,
        "sessions": {key: {"files": value["files"], "status_detail": value["status_detail"]}
                     for key, value in sessions.items()},
        "lifts": lift_records(root, slots, pairs, output_dir, lockable), "environment": environment, "counts": tally,
        "lockable": lockable, "data_root_commit": data_commit, "tool_commit": tool_commit,
        "tool_tree_clean": tool_clean,
    }
    inventory_bytes = study.serialize(inventory)
    summary = {
        "format": study.LOCK_SUMMARY_FORMAT, "format_version": study.STUDY_DOCUMENT_VERSION,
        "study_id": study.STUDY_ID, "tool_version": study.TOOL_VERSION, "tool_commit": tool_commit,
        "tool_tree_clean": tool_clean, "data_root_commit": data_commit,
        "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "counts": tally, "lockable": lockable, "freeze": study.freeze_references(),
        "exclusion_list_sha256": exclusion_digest,
    }
    return {"root": root, "output_dir": output_dir, "pairs": pairs, "inventory": inventory_bytes,
            "summary": summary, "lock_summary": study.serialize(summary)}


def write(plan: dict[str, Any]) -> None:
    """Pairs files first, then the inventory binding their hashes, then the public lock summary."""
    output_dir = plan["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=False)
    for exercise, data in plan["pairs"].items():
        study.write_new(output_dir / f"pairs-{exercise}.json", data)
    study.write_new(output_dir / INVENTORY_NAME, plan["inventory"])
    study.write_new(output_dir / LOCK_SUMMARY_NAME, plan["lock_summary"])


# --- verify --------------------------------------------------------------------------------------

def recorded_files(document: dict[str, Any]) -> list[tuple[str, dict[str, str]]]:
    """Every (label, {path, sha256}) the inventory binds; a malformed structure raises StudyInputError."""
    require(isinstance(document["slots"], list) and isinstance(document["sessions"], dict)
            and isinstance(document["lifts"], dict), "slots, sessions or lifts have the wrong type")
    entries = [(f"slot {slot['slot']} {role}", item) for slot in document["slots"]
               for role, item in sorted(slot["files"].items())]
    entries += [(f"session {name} {role}", item) for name, session in sorted(document["sessions"].items())
                for role, item in sorted(session["files"].items())]
    entries += [("slots_file", document["slots_file"]), ("exclusion_list", document["exclusion_list"])]
    entries += [(f"pairs {exercise}", lift["pairs_file"]) for exercise, lift in sorted(document["lifts"].items())
                if lift["pairs_file"] is not None]
    for label, item in entries:
        require(isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("sha256"), str),
                f"{label} is not a {{path, sha256}} record")
    return entries


def verify(inventory_path: Path, lock_summary_path: Path | None = None, root: Path = ROOT) -> dict[str, Any]:
    """Rehash every recorded input and the inventory itself against the lock summary; never writes."""
    try:
        data = inventory_path.read_bytes()
        document = workflow.schema_check.loads_strict(data.decode("utf-8"))
        require(isinstance(document, dict) and document.get("format") == study.INVENTORY_FORMAT
                and document.get("format_version") == study.STUDY_DOCUMENT_VERSION, "not a v1 #79 inventory")
        entries = recorded_files(document)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise study.StudyInputError(f"cannot read inventory {inventory_path}: {error}") from error
    mismatches = []
    for label, item in entries:
        path = study.recorded_path(root, item["path"])
        actual = evidence.hashed(root, path)
        actual_sha = None if actual is None else actual["sha256"]
        if actual_sha != item["sha256"]:
            mismatches.append({"role": label, "path": item["path"], "expected": item["sha256"],
                               "actual": actual_sha})
    inventory_sha = hashlib.sha256(data).hexdigest()
    summary = evidence.load_document(lock_summary_path or inventory_path.parent / LOCK_SUMMARY_NAME)
    matches = (isinstance(summary, dict) and summary.get("format") == study.LOCK_SUMMARY_FORMAT
               and summary.get("inventory_sha256") == inventory_sha)
    return {"ok": not mismatches and matches, "inputs_unchanged": not mismatches,
            "inventory_matches_lock_summary": matches, "inventory_sha256": inventory_sha,
            "checked": len(entries), "mismatches": mismatches, "mismatch_count": len(mismatches)}


def print_verification(result: dict[str, Any]) -> None:
    for item in result["mismatches"]:
        print(f"mismatch: {item['role']} {item['path']} expected {item['expected']} actual {item['actual']}")
    if not result["inventory_matches_lock_summary"]:
        print(f"mismatch: inventory sha256 {result['inventory_sha256']} is not the lock summary's")
    verdict = "ok" if result["ok"] else "FAILED"
    print(f"verify {verdict}: {result['checked'] - result['mismatch_count']}/{result['checked']} recorded files "
          f"unchanged; inventory matches lock summary: {result['inventory_matches_lock_summary']}")


def main(argv: list[str] | None = None, *, root: Path = ROOT, status: StatusCheck = evidence.session_status,
         probe: Probe = evidence.ffprobe_creation_time, git: GitState = study.git_state,
         preflight: Preflight = evidence.consumer_preflight, exclusion_sha256: str | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("inventory", help="derive the 18-slot inventory, pairs files and lock summary")
    for sub in (build_parser, verify_parser := commands.add_parser(
            "verify", help="rehash every recorded input and the inventory")):
        sub.add_argument("--root", type=Path, default=root,
                         help="pinned data checkout holding the study evidence (default: this checkout)")
    build_parser.add_argument("--slots", type=Path, required=True, help="private owner slots.json")
    build_parser.add_argument("--output-dir", type=Path, required=True,
                              help=f"new folder under {study.STUDY_DIR.as_posix()}/")
    build_parser.add_argument("--consumer-app", type=Path, required=True,
                              help=f"pinned consumer app folder (checkout at {study.CONSUMER_COMMIT[:12]})")
    build_parser.add_argument("--exclusion-list", type=Path,
                              help=f"frozen novelty list (default {study.EXCLUSION_LIST.as_posix()})")
    verify_parser.add_argument("--inventory", type=Path, required=True)
    verify_parser.add_argument("--lock-summary", type=Path, help="default: lock-summary.json beside the inventory")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify(resolve(args.inventory), args.lock_summary and resolve(args.lock_summary), args.root)
            print_verification(result)
            return 0 if result["ok"] else 1
        plan = build(args.root, args.slots, args.output_dir, args.consumer_app, args.exclusion_list, status=status,
                     probe=probe, git=git, preflight=preflight, exclusion_sha256=exclusion_sha256)
        write(plan)
    except study.StudyInfrastructureError as error:
        print(f"error: infrastructure abort ({error}); retry with unchanged inputs", file=sys.stderr)
        return 3
    except (study.StudyInputError, workflow.WorkflowError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"error: infrastructure abort ({error}); retry with unchanged inputs", file=sys.stderr)
        return 3
    tally = plan["summary"]["counts"]
    print(f"inventory {study.repo_relative(plan['output_dir'], plan['root'])}: {tally['enrolled']}/18 enrolled, "
          f"{tally['analyzable']} analyzable, {tally['protocol_conforming']} protocol-conforming; "
          f"lockable={plan['summary']['lockable']}; inventory sha256 {plan['summary']['inventory_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
