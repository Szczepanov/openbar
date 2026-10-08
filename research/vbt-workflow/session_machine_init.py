"""`vbt_session.py init-research` (#113): research-only machine initialization of an ingested session.

Decision: docs/analysis/VBT_INITIALIZATION_DECISION.md (proceed, bounded to #113). A validated profile
(exercise, plate diameter, stick length) and the seed-frame suggestions of one #95 session become
`machine-init.json`: a provenance record whose every value has `origin: "machine"`, is never
human-confirmed, is research-only and is not consumer-eligible. A clip with any applicable reason
code is rejected and falls back to the #95 confirmation page. The command runs no tracking or
analysis, writes no seed, CSV, personal manifest or page, and sets no confidence thresholds.
Standard library only. Contract: docs/validation/VBT_RESEARCH_INITIALIZATION.md.

The reader binds to the disk: it loads session.json itself and needs the profile bytes. The record
is accepted only if its bytes equal the record recomputed from those inputs, except the recorded
implementation hash, which is copied after a format check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import analyze_lift
import session_contract
import session_ingest
from vbt_process import WorkflowError

PROFILE_FORMAT = "openbar-research-vbt-init-profile"
PROFILE_VERSION = 1
RECORD_FORMAT = "openbar-research-vbt-machine-init"
RECORD_VERSION = 1
RECORD_NAME = "machine-init.json"
IMPLEMENTATION = "session_machine_init"
IMPLEMENTATION_VERSION = 1
ORIGIN = "machine"
INIT_EXERCISES = ("snatch", "clean", "back_squat")
PROFILE_FIELDS = ("profile_id", "exercise", "plate_diameter_m", "stick_length_m")
PROFILE_KEYS = ("format", "format_version", *PROFILE_FIELDS)
RECORD_KEYS = ("format", "format_version", "origin", "human_confirmed", "research_only", "consumer_eligible",
               "session_id", "page_id", "inputs", "profile", "implementation", "suggester_environment", "clips",
               "summary")
CLIP_KEYS = ("clip_index", "fixture_id", "source_video_sha256", "package_id", "frame_index", "timestamp_s",
             "width_px", "height_px", "outcome", "reasons", "items")
ITEM_KEYS = ("origin", "values", "suggestion")
SUGGESTION_KEYS = ("id", "method", "parameters", "confidence")
SUMMARY_KEYS = ("initialized", "rejected")
OUTCOMES = ("initialized", "rejected")
REASONS = ("exercise_conflict", "plate_geometry_invalid", "plate_suggestion_missing", "stick_geometry_invalid",
           "stick_suggestion_missing", "suggestion_confidence_invalid", "suggestion_provenance_invalid")
FORBIDDEN_KEYS = ("status", "statuses")
MIN_MARKER_SEPARATION_PX = 2.0  # the same geometry rule as session_contract (markers must be > 2 px apart)
SHA256_RE = re.compile(r"[0-9a-f]{64}")


# --- Strict JSON and small checks ----------------------------------------------------------------

def _refuse_constant(name: str) -> None:
    raise ValueError(f"{name} is not allowed in JSON")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate key {key!r}")
        document[key] = value
    return document


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):  # e.g. 1e400 overflows to inf; the record is written with allow_nan=False
        raise ValueError(f"non-finite number {text}")
    return value


def parse_json_object(raw: bytes, name: str) -> dict[str, Any]:
    """Strict JSON: no NaN or Infinity, no duplicate keys, an object at the top level."""
    try:
        document = json.loads(raw.decode("utf-8"), parse_constant=_refuse_constant, parse_float=_finite_float,
                              object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as error:
        raise WorkflowError(f"{name} is not strict JSON: {error}") from error
    if not isinstance(document, dict):
        raise WorkflowError(f"{name} must hold a JSON object")
    return document


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_exact_int(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected  # bool is an int subclass, so exclude it by type


def _is_int(value: Any) -> bool:
    return type(value) is int


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _real(value: Any) -> float | None:
    """A finite JSON number that is not a bool, else None (huge integers are refused, not raised)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _require_keys(document: dict[str, Any], expected: tuple[str, ...], name: str) -> None:
    """Exactly these keys: a missing or unknown key is refused and named."""
    missing = [key for key in expected if key not in document]
    unknown = sorted(key for key in document if key not in expected)
    problems = []
    if missing:
        problems.append(f"missing {', '.join(missing)}")
    if unknown:
        problems.append(f"unknown {', '.join(unknown)}")
    if problems:
        raise WorkflowError(f"{name} must have exactly the keys {', '.join(expected)}: {'; '.join(problems)}")


def _positive(value: Any, key: str) -> float:
    number = _real(value)
    if number is None or number <= 0:
        raise WorkflowError(f"profile {key} must be a finite JSON number greater than 0")
    return number


# --- Profile -------------------------------------------------------------------------------------

def _check_profile_fields(fields: dict[str, Any]) -> dict[str, Any]:
    profile_id = fields["profile_id"]
    if not isinstance(profile_id, str) or session_contract.SESSION_ID_RE.fullmatch(profile_id) is None:
        raise WorkflowError(f"profile profile_id must match {session_contract.SESSION_ID_RE.pattern}")
    exercise = fields["exercise"]
    if exercise == "other":
        raise WorkflowError("profile exercise 'other' is ambiguous; use snatch, clean or back_squat")
    if exercise not in INIT_EXERCISES:
        raise WorkflowError(f"profile exercise must be one of {', '.join(INIT_EXERCISES)}")
    return {"profile_id": profile_id, "exercise": exercise,
            "plate_diameter_m": _positive(fields["plate_diameter_m"], "plate_diameter_m"),
            "stick_length_m": _positive(fields["stick_length_m"], "stick_length_m")}


def validate_profile(document: dict[str, Any]) -> dict[str, Any]:
    _require_keys(document, PROFILE_KEYS, "profile")
    if document["format"] != PROFILE_FORMAT:
        raise WorkflowError(f"profile format must be {PROFILE_FORMAT}")
    if not _is_exact_int(document["format_version"], PROFILE_VERSION):
        raise WorkflowError(f"profile format_version must be the integer {PROFILE_VERSION}")
    return _check_profile_fields({key: document[key] for key in PROFILE_FIELDS})


def profile_from_bytes(raw: bytes, name: str = "profile") -> dict[str, Any]:
    """The validated profile of these exact bytes; the caller hashes the same bytes."""
    return validate_profile(parse_json_object(raw, name))


def read_profile(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise WorkflowError(f"cannot read --profile {path.name}: {error}") from error


# --- Per-clip evaluation -------------------------------------------------------------------------

def _confidence_ok(suggestion: dict[str, Any]) -> bool:
    confidence = _real(suggestion.get("confidence"))
    return confidence is not None and 0 <= confidence <= 1


def _provenance_ok(suggestion: dict[str, Any]) -> bool:
    identifier, method = suggestion.get("id"), suggestion.get("method")
    return (isinstance(identifier, str) and identifier != "" and isinstance(method, str) and method != ""
            and isinstance(suggestion.get("parameters"), dict))


def _point(suggestion: dict[str, Any], x_key: str, y_key: str, width: float,
           height: float) -> tuple[float, float] | None:
    """A point in ADR-0007's v1 window [0, width) x [0, height)."""
    x, y = _real(suggestion.get(x_key)), _real(suggestion.get(y_key))
    if x is None or y is None or not (0 <= x < width and 0 <= y < height):
        return None
    return x, y


def _plate_geometry_ok(suggestion: dict[str, Any], width: float, height: float) -> bool:
    centre = _point(suggestion, "center_x_px", "center_y_px", width, height)
    radius = _real(suggestion.get("radius_px"))
    if centre is None or radius is None or radius <= 0:
        return False
    cx, cy = centre
    # The circle must lie inside [0, width] x [0, height], as session_contract requires for a confirmed plate.
    return cx - radius >= 0 and cy - radius >= 0 and cx + radius <= width and cy + radius <= height


def _stick_geometry_ok(suggestion: dict[str, Any], width: float, height: float) -> bool:
    low = _point(suggestion, "low_x_px", "low_y_px", width, height)
    high = _point(suggestion, "high_x_px", "high_y_px", width, height)
    if low is None or high is None:
        return False
    return math.hypot(high[0] - low[0], high[1] - low[1]) > MIN_MARKER_SEPARATION_PX


def _suggestion_reasons(kind: str, suggestion: dict[str, Any], width: float, height: float) -> set[str]:
    if suggestion.get("status") != "suggested":
        return {f"{kind}_suggestion_missing"}
    reasons = set()
    if not _confidence_ok(suggestion):
        reasons.add("suggestion_confidence_invalid")
    if not _provenance_ok(suggestion):
        reasons.add("suggestion_provenance_invalid")
    geometry_ok = (_plate_geometry_ok if kind == "plate" else _stick_geometry_ok)(suggestion, width, height)
    if not geometry_ok:
        reasons.add(f"{kind}_geometry_invalid")
    return reasons


def clip_reasons(clip: dict[str, Any], exercise: str) -> list[str]:
    """Every applicable reason code, sorted and without duplicates; an empty list means initialized."""
    reasons: set[str] = set()
    if clip["registered_exercise"] is not None and clip["registered_exercise"] != exercise:
        reasons.add("exercise_conflict")
    for kind in ("plate", "stick"):
        reasons |= _suggestion_reasons(kind, clip["suggestions"][kind], clip["width_px"], clip["height_px"])
    return sorted(reasons)


def _suggestion_block(suggestion: dict[str, Any]) -> dict[str, Any]:
    return {"id": suggestion["id"], "method": suggestion["method"], "parameters": suggestion["parameters"],
            "confidence": suggestion["confidence"]}


def clip_items(clip: dict[str, Any]) -> dict[str, Any]:
    """The four session items, named as in session_contract.ITEMS, with the raw suggestion values."""
    items = {}
    for item, (kind, _columns, keys, _status_column) in session_contract.ITEMS.items():
        suggestion = clip["suggestions"][kind]
        items[item] = {"origin": ORIGIN, "values": {key: suggestion[key] for key in keys},
                       "suggestion": _suggestion_block(suggestion)}
    return items


def clip_record(index: int, clip: dict[str, Any], exercise: str) -> dict[str, Any]:
    reasons = clip_reasons(clip, exercise)
    initialized = not reasons
    return {
        "clip_index": index, "fixture_id": clip["fixture_id"], "source_video_sha256": clip["sha256"],
        "package_id": clip["package_id"], "frame_index": clip["frame_index"], "timestamp_s": clip["timestamp_s"],
        "width_px": clip["width_px"], "height_px": clip["height_px"],
        "outcome": "initialized" if initialized else "rejected", "reasons": reasons,
        "items": clip_items(clip) if initialized else {},
    }


# --- Record --------------------------------------------------------------------------------------

def _source_sha256() -> str:
    """Hash of this module's raw bytes; stable across checkouts because *.py is eol=lf in .gitattributes."""
    return sha256_hex(Path(__file__).read_bytes())


def build_record(state: dict[str, Any], state_sha256: str, profile: dict[str, Any],
                 profile_sha256: str) -> dict[str, Any]:
    clips = [clip_record(index, clip, profile["exercise"]) for index, clip in enumerate(state["clips"])]
    initialized = sum(1 for clip in clips if clip["outcome"] == "initialized")
    return {
        "format": RECORD_FORMAT, "format_version": RECORD_VERSION,
        "origin": ORIGIN, "human_confirmed": False, "research_only": True, "consumer_eligible": False,
        "session_id": state["session_id"], "page_id": state["page_id"],
        "inputs": {"session_state_sha256": state_sha256, "profile_sha256": profile_sha256},
        "profile": profile,
        "implementation": {"name": IMPLEMENTATION, "version": IMPLEMENTATION_VERSION,
                           "source_sha256": _source_sha256()},
        "suggester_environment": state.get("suggester_environment"),
        "clips": clips,
        "summary": {"initialized": initialized, "rejected": len(clips) - initialized},
    }


def render_record(record: dict[str, Any]) -> str:
    """The same bytes session_ingest.write_json writes, computed before anything touches the disk."""
    return json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n"


def write_record(path: Path, text: str, force: bool) -> str:
    """Write, or report 'unchanged'. Different bytes are refused unless forced; nothing is written on refusal."""
    if path.is_file() and path.read_bytes() == text.encode("utf-8"):
        return "unchanged"
    existed = path.exists()
    if existed and not force:
        raise WorkflowError(f"{RECORD_NAME} already exists with different content (the profile, the session state "
                            "or a clip outcome changed); pass --force to replace it. Nothing was written.")
    session_ingest.write_text(path, text)
    return "replaced" if existed else "wrote"


# --- Session state -------------------------------------------------------------------------------

def _is_suggestion_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _check_clip_state(clip: Any, index: int) -> None:
    where = f"{session_ingest.STATE_NAME} clip {index}"
    if not isinstance(clip, dict):
        raise WorkflowError(f"{where} must be an object")
    for key in ("fixture_id", "sha256", "package_id", "original_name"):
        if not isinstance(clip.get(key), str):
            raise WorkflowError(f"{where} {key} must be a string")
    if not _is_int(clip.get("frame_index")) or clip["frame_index"] < 0:
        raise WorkflowError(f"{where} frame_index must be a non-negative integer")
    for key in ("width_px", "height_px"):
        if not _is_int(clip.get(key)) or clip[key] <= 0:
            raise WorkflowError(f"{where} {key} must be a positive integer")
    if _real(clip.get("timestamp_s")) is None:
        raise WorkflowError(f"{where} timestamp_s must be a finite number")
    registered = clip.get("registered_exercise")
    if registered is not None and not isinstance(registered, str):
        raise WorkflowError(f"{where} registered_exercise must be a string or null")
    suggestions = clip.get("suggestions")
    if not isinstance(suggestions, dict):
        raise WorkflowError(f"{where} suggestions must be an object")
    for kind in ("plate", "stick"):
        if not _is_suggestion_dict(suggestions.get(kind)):
            raise WorkflowError(f"{where} suggestions.{kind} must be an object")


def _check_state_shape(state: dict[str, Any]) -> None:
    for key in ("session_id", "page_id", "template_sha256"):
        if not isinstance(state.get(key), str):
            raise WorkflowError(f"{session_ingest.STATE_NAME} {key} must be a string")
    clips = state.get("clips")
    if not isinstance(clips, list):
        raise WorkflowError(f"{session_ingest.STATE_NAME} clips must be a list")
    if not clips:
        raise WorkflowError("session has no clips; nothing to initialize")
    for index, clip in enumerate(clips):
        _check_clip_state(clip, index)


def load_session_state(directory: Path) -> tuple[dict[str, Any], bytes]:
    """The current session state and its exact bytes, after the same page-id integrity check as `run`."""
    path = directory / session_ingest.STATE_NAME
    if not path.is_file():
        raise WorkflowError(f"session {directory.name} has no {session_ingest.STATE_NAME}; run `ingest` first")
    raw = path.read_bytes()
    state = parse_json_object(raw, session_ingest.STATE_NAME)
    if state.get("format") != session_ingest.SESSION_STATE_FORMAT or not _is_exact_int(
            state.get("format_version"), session_ingest.SESSION_STATE_VERSION):
        raise WorkflowError(f"{session_ingest.STATE_NAME} is not a {session_ingest.SESSION_STATE_FORMAT} "
                            f"version {session_ingest.SESSION_STATE_VERSION}")
    _check_state_shape(state)
    try:
        page_matches = session_ingest.compute_page_id(state) == state["page_id"]
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise WorkflowError(f"{session_ingest.STATE_NAME} is malformed: {error}") from error
    if not page_matches:
        raise WorkflowError(f"{session_ingest.STATE_NAME} does not match its page id; it was edited after ingest. "
                            "Re-run `ingest --force` before init-research")
    return state, raw


# --- Verification (shared by the writer's self-check and the reader) ------------------------------

def _reject_status_keys(value: Any, where: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_KEYS:
                raise WorkflowError(f"{RECORD_NAME} must not carry a status key (found at {where}.{key}); "
                                    "machine values have no status")
            _reject_status_keys(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_status_keys(item, f"{where}[{index}]")


def _check_flags(record: dict[str, Any]) -> None:
    if (record.get("origin") != ORIGIN or record.get("human_confirmed") is not False
            or record.get("research_only") is not True or record.get("consumer_eligible") is not False):
        raise WorkflowError(f"{RECORD_NAME} must be machine origin, not human-confirmed, research-only and not "
                            "consumer-eligible")


def _check_header(record: dict[str, Any]) -> None:
    if record["format"] != RECORD_FORMAT or not _is_exact_int(record["format_version"], RECORD_VERSION):
        raise WorkflowError(f"{RECORD_NAME} is not a {RECORD_FORMAT} version {RECORD_VERSION}")


def _check_binding(record: dict[str, Any], state: dict[str, Any]) -> None:
    if record["session_id"] != state["session_id"]:
        raise WorkflowError(f"{RECORD_NAME} is for session {record['session_id']!r}, not {state['session_id']!r}")
    if record["page_id"] != state["page_id"]:
        raise WorkflowError(f"{RECORD_NAME} is stale: its page id is not this session page; the session was "
                            "re-ingested. Run init-research again")


def _check_inputs(record: dict[str, Any], state_sha256: str, profile_sha256: str) -> None:
    inputs = record["inputs"]
    if not isinstance(inputs, dict):
        raise WorkflowError(f"{RECORD_NAME} inputs must be an object")
    _require_keys(inputs, ("session_state_sha256", "profile_sha256"), "inputs")
    if inputs["session_state_sha256"] != state_sha256:
        raise WorkflowError(f"{RECORD_NAME} is stale: session.json changed after it was written")
    if not _is_sha256(inputs["profile_sha256"]):
        raise WorkflowError(f"{RECORD_NAME} profile_sha256 must be a SHA-256 hex digest")
    if inputs["profile_sha256"] != profile_sha256:
        raise WorkflowError(f"{RECORD_NAME} was written with a different profile: the profile bytes do not match its "
                            "profile_sha256. Pass the profile the record was written with")


def _check_record_blocks(record: dict[str, Any]) -> None:
    profile = record["profile"]
    if not isinstance(profile, dict):
        raise WorkflowError(f"{RECORD_NAME} profile must be an object")
    _require_keys(profile, PROFILE_FIELDS, "record profile")
    _check_profile_fields(profile)
    implementation = record["implementation"]
    if not isinstance(implementation, dict):
        raise WorkflowError(f"{RECORD_NAME} implementation must be an object")
    _require_keys(implementation, ("name", "version", "source_sha256"), "implementation")
    if implementation["name"] != IMPLEMENTATION or not _is_exact_int(implementation["version"], IMPLEMENTATION_VERSION):
        raise WorkflowError(f"{RECORD_NAME} was written by an unknown implementation")
    if not _is_sha256(implementation["source_sha256"]):
        raise WorkflowError(f"{RECORD_NAME} source_sha256 must be a SHA-256 hex digest")


def _check_item_shape(name: str, item: Any) -> None:
    if not isinstance(item, dict):
        raise WorkflowError(f"item {name} must be an object")
    _require_keys(item, ITEM_KEYS, f"item {name}")
    if item["origin"] != ORIGIN:
        raise WorkflowError(f"item {name} has origin {item['origin']!r}; only machine values are in this record")
    if not isinstance(item["suggestion"], dict) or not isinstance(item["values"], dict):
        raise WorkflowError(f"item {name} suggestion and values must be objects")
    _require_keys(item["suggestion"], SUGGESTION_KEYS, f"item {name} suggestion")
    _require_keys(item["values"], session_contract.ITEMS[name][2], f"item {name} values")


def _check_clip_shape(clip: Any, index: int) -> None:
    if not isinstance(clip, dict):
        raise WorkflowError(f"clip {index} must be an object")
    _require_keys(clip, CLIP_KEYS, f"clip {index}")
    if not _is_exact_int(clip["clip_index"], index):
        raise WorkflowError(f"clip {index} has clip_index {clip['clip_index']!r}; clips must be in page order")
    if clip["outcome"] not in OUTCOMES:
        raise WorkflowError(f"clip {index} outcome must be one of {', '.join(OUTCOMES)}")
    reasons = clip["reasons"]
    if not isinstance(reasons, list) or any(reason not in REASONS for reason in reasons) or reasons != sorted(
            set(reasons)):
        raise WorkflowError(f"clip {index} reasons must be sorted, unique codes from {', '.join(REASONS)}")
    if not isinstance(clip["items"], dict):
        raise WorkflowError(f"clip {index} items must be an object")
    if clip["outcome"] == "initialized":
        if reasons:
            raise WorkflowError(f"clip {index} is initialized but carries reasons")
        _require_keys(clip["items"], tuple(session_contract.ITEMS), f"clip {index} items")
    else:
        if not reasons or clip["items"] != {}:
            raise WorkflowError(f"clip {index} is rejected: it needs reasons and no items")
    for name, item in clip["items"].items():
        _check_item_shape(name, item)


def verify_record(record_raw: bytes, state: dict[str, Any], state_raw: bytes, profile_raw: bytes) -> dict[str, Any]:
    """Fail-closed verification of one record against the session bytes and the profile bytes.

    The writer runs this on its in-memory bytes before writing; the reader runs it on the file. The final
    check is byte equality with the record recomputed from these inputs, so a field edit, a JSON type change
    (false vs 0, 1080.0 vs 1080) or a changed suggester environment cannot pass. The one exception is the
    recorded implementation hash, which is copied after its format check.
    """
    record = parse_json_object(record_raw, RECORD_NAME)
    _check_flags(record)
    _reject_status_keys(record, RECORD_NAME)
    _require_keys(record, RECORD_KEYS, RECORD_NAME)
    _check_header(record)
    _check_binding(record, state)
    profile = profile_from_bytes(profile_raw)
    _check_inputs(record, sha256_hex(state_raw), sha256_hex(profile_raw))
    _check_record_blocks(record)
    if not isinstance(record["clips"], list) or len(record["clips"]) != len(state["clips"]):
        raise WorkflowError(f"{RECORD_NAME} must have one clip record per session clip")
    for index, clip in enumerate(record["clips"]):
        _check_clip_shape(clip, index)
    if not isinstance(record["summary"], dict):
        raise WorkflowError(f"{RECORD_NAME} summary must be an object")
    _require_keys(record["summary"], SUMMARY_KEYS, f"{RECORD_NAME} summary")
    expected = build_record(state, sha256_hex(state_raw), profile, sha256_hex(profile_raw))
    recorded_hash = record["implementation"]["source_sha256"]
    expected = {**expected, "implementation": {**expected["implementation"], "source_sha256": recorded_hash}}
    if render_record(expected).encode("utf-8") != record_raw:
        raise WorkflowError(f"{RECORD_NAME} does not match the record recomputed from the session state and the "
                            "profile: a field was edited, or a value changed its JSON type")
    return record


# --- Command and reader --------------------------------------------------------------------------

def command_init_research(args: argparse.Namespace) -> int:
    profile_raw = read_profile(args.profile)
    profile = profile_from_bytes(profile_raw, args.profile.name)
    directory = session_ingest.session_dir(args.sessions_root, args.session)
    state, state_raw = load_session_state(directory)
    record = build_record(state, sha256_hex(state_raw), profile, sha256_hex(profile_raw))
    text = render_record(record)
    verify_record(text.encode("utf-8"), state, state_raw, profile_raw)  # nothing is written unless this passes
    path = directory / RECORD_NAME
    action = write_record(path, text, args.force)
    for clip in record["clips"]:
        detail = "initialized" if clip["outcome"] == "initialized" else f"rejected ({', '.join(clip['reasons'])})"
        print(f"{clip['fixture_id']}: {detail}")
    print(f"{action}: {analyze_lift.display_path(path)}")
    if record["summary"]["initialized"] == 0:
        print("no clip initialized; use the #95 confirmation page for every clip")
    print("research only: machine-initialized, never human-confirmed, not consumer-eligible. Rejected clips use "
          "the #95 confirmation page (session.html) instead.")
    return 0


def load_machine_init(directory: Path, profile_bytes: bytes) -> dict[str, Any]:
    """Fail-closed reader. Loads session.json from disk, and needs the profile bytes the record was written with.

    Returns the record only if its bytes equal the record recomputed from the on-disk session state and the
    supplied profile (see verify_record). A caller cannot supply its own session, so nothing it returns is
    unbound from the disk state.
    """
    state, state_raw = load_session_state(directory)
    path = directory / RECORD_NAME
    if not path.is_file():
        raise WorkflowError(f"session {directory.name} has no {RECORD_NAME}")
    return verify_record(path.read_bytes(), state, state_raw, profile_bytes)
