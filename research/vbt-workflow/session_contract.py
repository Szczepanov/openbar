"""The session CSV contract of the one-page VBT session (#95). Standard library only.

The session page (session_page.html) writes one CSV row per clip; `parse_session_csv` validates it
against the session state written by `vbt_session.py ingest`. The contract is documented in
docs/plans/VBT_WORKFLOW_PLAN.md ("Session CSV contract"). Every check fails closed: a CSV that does
not belong to this exact session page, or that claims a suggestion was accepted unchanged when its
values differ, is refused rather than repaired.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from typing import Any

FORMAT = "openbar-vbt-session-v1"
EXERCISES = ("snatch", "clean", "back_squat", "other")
DECISIONS = ("confirmed", "skipped")
STATUSES = ("accepted", "adjusted", "manual")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
VALUE_TOLERANCE = 0.005  # coordinates are written with 2 decimals

IDENTITY_COLUMNS = [
    "format", "session_id", "page_id", "clip_index", "fixture_id", "source_video_sha256", "package_id",
    "frame_index", "timestamp_s", "width_px", "height_px",
]
PLATE_COLUMNS = [
    "plate_suggestion_id", "plate_center_x_px", "plate_center_y_px", "plate_radius_px",
    "plate_center_status", "plate_radius_status",
]
STICK_COLUMNS = [
    "stick_suggestion_id", "stick_low_x_px", "stick_low_y_px", "stick_high_x_px", "stick_high_y_px",
    "stick_low_status", "stick_high_status",
]
COLUMNS = [*IDENTITY_COLUMNS, "decision", "exercise", *PLATE_COLUMNS, *STICK_COLUMNS]
GEOMETRY_COLUMNS = [*PLATE_COLUMNS, *STICK_COLUMNS]

# Item -> (suggestion kind, value columns, matching suggestion keys, status column).
ITEMS = {
    "plate_center": ("plate", ("plate_center_x_px", "plate_center_y_px"), ("center_x_px", "center_y_px"),
                     "plate_center_status"),
    "plate_radius": ("plate", ("plate_radius_px",), ("radius_px",), "plate_radius_status"),
    "stick_low": ("stick", ("stick_low_x_px", "stick_low_y_px"), ("low_x_px", "low_y_px"), "stick_low_status"),
    "stick_high": ("stick", ("stick_high_x_px", "stick_high_y_px"), ("high_x_px", "high_y_px"), "stick_high_status"),
}


class SessionCsvError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SessionCsvError(message)


def require_session_id(value: str) -> str:
    require(SESSION_ID_RE.fullmatch(value) is not None,
            f"session id {value!r} must match {SESSION_ID_RE.pattern} (e.g. 2026-10-03)")
    return value


def page_id(page_config: dict[str, Any]) -> str:
    """Binds a CSV to one generated page: clips, frames and suggestions (frame pixels excluded)."""
    text = json.dumps(page_config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _finite(text: str, name: str, row: int) -> float:
    try:
        value = float(text)
    except ValueError as error:
        raise SessionCsvError(f"row {row}: {name} must be a number, got {text!r}") from error
    require(math.isfinite(value), f"row {row}: {name} must be finite")
    return value


def _integer(text: str, name: str, row: int) -> int:
    require(re.fullmatch(r"0|[1-9][0-9]*", text) is not None, f"row {row}: {name} must be a non-negative integer")
    return int(text)


def _read_rows(data: bytes) -> list[dict[str, str]]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SessionCsvError("session CSV is not UTF-8") from error
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise SessionCsvError(f"malformed session CSV: {error}") from error
    require(bool(rows), "session CSV is empty")
    require(rows[0] == COLUMNS, "session CSV header does not match the openbar-vbt-session-v1 columns; "
                                "download it again from this session's session.html")
    for number, row in enumerate(rows[1:], start=2):
        require(len(row) == len(COLUMNS), f"row {number}: expected {len(COLUMNS)} cells, got {len(row)}")
    return [dict(zip(COLUMNS, row)) for row in rows[1:]]


def _check_identity(row: dict[str, str], number: int, session: dict[str, Any], clip: dict[str, Any]) -> None:
    require(row["format"] == FORMAT, f"row {number}: format must be {FORMAT}")
    require(row["session_id"] == session["session_id"],
            f"row {number}: CSV is for session {row['session_id']!r}, not {session['session_id']!r}")
    require(row["page_id"] == session["page_id"],
            f"row {number}: CSV page {row['page_id']!r} is not this session page ({session['page_id']}); "
            "the page was rebuilt since the download, so confirm the clips again on the current session.html")
    expected = {
        "fixture_id": clip["fixture_id"], "source_video_sha256": clip["sha256"], "package_id": clip["package_id"],
        "frame_index": str(clip["frame_index"]), "width_px": str(clip["width_px"]), "height_px": str(clip["height_px"]),
    }
    for column, value in expected.items():
        require(row[column] == value, f"row {number}: {column} {row[column]!r} does not match the session "
                                      f"({value!r}); wrong video or session")
    require(_finite(row["timestamp_s"], "timestamp_s", number) == float(clip["timestamp_s"]),
            f"row {number}: timestamp_s does not match the session seed frame")


def _check_point(x: float, y: float, clip: dict[str, Any], name: str, number: int) -> None:
    # ADR-0007 v1 point window: [0, width) x [0, height).
    require(0 <= x < clip["width_px"] and 0 <= y < clip["height_px"],
            f"row {number}: {name} ({x}, {y}) is outside [0,{clip['width_px']}) x [0,{clip['height_px']})")


def _check_status(row: dict[str, str], values: dict[str, float], number: int, clip: dict[str, Any]) -> dict[str, str]:
    """Each item's status must be true of its values: `accepted` only if unchanged, and so on."""
    statuses = {}
    for item, (kind, columns, keys, status_column) in ITEMS.items():
        suggestion = clip["suggestions"][kind]
        status = row[status_column]
        require(status in STATUSES, f"row {number}: {status_column} must be one of {', '.join(STATUSES)}")
        suggested = suggestion.get("status") == "suggested"
        if not suggested:
            require(row[f"{kind}_suggestion_id"] == "", f"row {number}: {kind}_suggestion_id must be blank "
                                                        "because there was no suggestion")
            require(status == "manual", f"row {number}: {status_column} must be manual (no suggestion)")
        else:
            require(row[f"{kind}_suggestion_id"] == suggestion["id"],
                    f"row {number}: {kind}_suggestion_id does not match the session's suggestion")
            unchanged = all(abs(values[column] - float(suggestion[key])) <= VALUE_TOLERANCE
                            for column, key in zip(columns, keys))
            require(status != "manual", f"row {number}: {status_column} cannot be manual: there was a suggestion")
            require(unchanged == (status == "accepted"),
                    f"row {number}: {status_column} is {status}, but the values are "
                    f"{'unchanged' if unchanged else 'changed'} from the suggestion")
        statuses[item] = status
    return statuses


def _confirmed(row: dict[str, str], number: int, clip: dict[str, Any]) -> dict[str, Any]:
    require(row["exercise"] in EXERCISES, f"row {number}: exercise must be one of {', '.join(EXERCISES)}")
    values = {}
    for column in GEOMETRY_COLUMNS:
        if column.endswith("_px"):
            require(row[column] != "", f"row {number}: {column} is missing; a confirmed clip needs every item")
            values[column] = _finite(row[column], column, number)
    cx, cy, radius = values["plate_center_x_px"], values["plate_center_y_px"], values["plate_radius_px"]
    _check_point(cx, cy, clip, "plate centre", number)
    require(radius > 0, f"row {number}: plate_radius_px must be positive")
    require(cx - radius >= 0 and cy - radius >= 0 and cx + radius <= clip["width_px"]
            and cy + radius <= clip["height_px"], f"row {number}: the plate circle must fit inside the frame")
    low = (values["stick_low_x_px"], values["stick_low_y_px"])
    high = (values["stick_high_x_px"], values["stick_high_y_px"])
    _check_point(*low, clip, "stick low marker", number)
    _check_point(*high, clip, "stick high marker", number)
    require(math.hypot(high[0] - low[0], high[1] - low[1]) > 2.0,
            f"row {number}: the stick markers are too close together")
    statuses = _check_status(row, values, number, clip)
    return {"exercise": row["exercise"], "values": values, "statuses": statuses}


def parse_session_csv(data: bytes, session: dict[str, Any]) -> list[dict[str, Any]]:
    """Validated decisions in clip order: [{fixture_id, decision, exercise?, values?, statuses?}]."""
    rows = _read_rows(data)
    clips = session["clips"]
    require(len(rows) == len(clips), f"session CSV has {len(rows)} clip rows; the session has {len(clips)} clips")
    decisions = []
    for index, (row, clip) in enumerate(zip(rows, clips)):
        number = index + 2
        require(row["clip_index"] == str(index), f"row {number}: clip_index must be {index} (rows in page order)")
        _check_identity(row, number, session, clip)
        require(row["decision"] in DECISIONS, f"row {number}: decision must be confirmed or skipped; "
                                              "every clip must be confirmed or explicitly skipped")
        entry: dict[str, Any] = {"fixture_id": clip["fixture_id"], "decision": row["decision"]}
        if row["decision"] == "skipped":
            require(row["exercise"] == "" and all(row[column] == "" for column in GEOMETRY_COLUMNS),
                    f"row {number}: a skipped clip must have no exercise or geometry")
        else:
            entry.update(_confirmed(row, number, clip))
        decisions.append(entry)
    require(any(entry["decision"] == "confirmed" for entry in decisions), "no clip is confirmed; nothing to run")
    return decisions
