"""Shared stand-ins for the #95 session tests (not a test module). Standard library only."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

WORKFLOW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKFLOW_DIR))
import session_contract  # noqa: E402

SHA = "ab" * 32
FIXTURE_ID = "vbt-" + SHA[:16]
PLATE = {"method": "plate-hough-edge-v1", "status": "suggested", "id": "plate-hough-edge-v1:111111111111",
         "confidence": 0.8, "center_x_px": 400.5, "center_y_px": 1500.0, "radius_px": 180.25}
STICK = {"method": "stick-yellow-markers-v1", "status": "suggested", "id": "stick-yellow-markers-v1:222222222222",
         "confidence": 0.4, "marker_count": 7, "low_x_px": 830.0, "low_y_px": 1630.0, "high_x_px": 840.0,
         "high_y_px": 580.0}
FAILED_PLATE = {"method": "plate-hough-edge-v1", "status": "failed", "reason": "no circle"}
FAILED_STICK = {"method": "stick-yellow-markers-v1", "status": "failed", "reason": "no stick"}


def suggestions(plate: dict[str, Any] | None = None, stick: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"plate": dict(plate or PLATE), "stick": dict(stick or STICK)}


def clip(index: int = 0, sha: str = SHA, plate: dict[str, Any] | None = None,
         stick: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"clip_index": index, "fixture_id": "vbt-" + sha[:16], "sha256": sha, "package_id": "0123456789abcdef",
            "frame_index": 0, "timestamp_s": 0.0, "width_px": 1080, "height_px": 1920,
            "original_name": f"VID_{index}.mp4", "registered_exercise": None,
            "suggestions": suggestions(plate, stick)}


def session(clips: list[dict[str, Any]] | None = None, session_id: str = "2026-10-03") -> dict[str, Any]:
    return {"session_id": session_id, "page_id": "fedcba9876543210", "clips": clips or [clip()]}


def accepted_row(c: dict[str, Any], state: dict[str, Any], exercise: str = "snatch") -> dict[str, str]:
    """Every suggestion accepted unchanged; a failed suggestion gets PLATE/STICK values placed by hand."""
    p, s = c["suggestions"]["plate"], c["suggestions"]["stick"]
    manual_plate, manual_stick = p.get("status") != "suggested", s.get("status") != "suggested"
    p, s = (PLATE if manual_plate else p), (STICK if manual_stick else s)
    row = {
        "format": session_contract.FORMAT, "session_id": state["session_id"], "page_id": state["page_id"],
        "clip_index": str(c["clip_index"]), "fixture_id": c["fixture_id"], "source_video_sha256": c["sha256"],
        "package_id": c["package_id"], "frame_index": str(c["frame_index"]), "timestamp_s": str(c["timestamp_s"]),
        "width_px": str(c["width_px"]), "height_px": str(c["height_px"]), "decision": "confirmed",
        "exercise": exercise, "plate_suggestion_id": p["id"], "plate_center_x_px": f"{p['center_x_px']:.2f}",
        "plate_center_y_px": f"{p['center_y_px']:.2f}", "plate_radius_px": f"{p['radius_px']:.2f}",
        "plate_center_status": "accepted", "plate_radius_status": "accepted", "stick_suggestion_id": s["id"],
        "stick_low_x_px": f"{s['low_x_px']:.2f}", "stick_low_y_px": f"{s['low_y_px']:.2f}",
        "stick_high_x_px": f"{s['high_x_px']:.2f}", "stick_high_y_px": f"{s['high_y_px']:.2f}",
        "stick_low_status": "accepted", "stick_high_status": "accepted",
    }
    if manual_plate:
        row.update(plate_suggestion_id="", plate_center_status="manual", plate_radius_status="manual")
    if manual_stick:
        row.update(stick_suggestion_id="", stick_low_status="manual", stick_high_status="manual")
    return row


def skipped_row(c: dict[str, Any], state: dict[str, Any]) -> dict[str, str]:
    row = accepted_row(c, state)
    return {**row, "decision": "skipped", "exercise": "",
            **{column: "" for column in session_contract.GEOMETRY_COLUMNS}}


def csv_bytes(rows: list[dict[str, str]]) -> bytes:
    lines = [",".join(session_contract.COLUMNS)] + [",".join(row[c] for c in session_contract.COLUMNS) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dumps(document: Any) -> str:
    return json.dumps(document, indent=2, sort_keys=True)
