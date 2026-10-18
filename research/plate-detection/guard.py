"""Identity guard for a tracked plate (#57): ``track-guard-v2``.

A video tracker that loses the plate can later lock onto another object and report it as tracked
(EdgeTAM on ``self-clean-side-0203-17``, PLATE_DETECTION_SAM3_SEED_RESULTS.md). This guard turns
such cases into lost frames instead of positions:

- **Step rule.** Against the last accepted frame, the centre may move at most
  ``MAX_SPEED_RADII_PER_S`` plate radii per second (plus ``CENTRE_SLACK_RADII``), and the radius may
  change by at most ``MAX_RADIUS_CHANGE``. A frame that breaks it is rejected (no coordinate) and the
  track goes on: during fast pulls the mask and its rim fit glitch for a frame or two.
- **Gap rule.** When no frame has been accepted for longer than ``MAX_GAP_S`` (lost or rejected
  frames alike), the track stops and every later frame is lost until a new seed: an object
  re-acquired after a long gap cannot be told apart from another plate.

v1 ended the track at the first step violation. On the development runs that stopped 7 correct SAM
2.1 tracks during fast snatch pulls, so v2 rejects single frames instead. Both versions were designed
after seeing the 0203-17 failure, and v2 after seeing v1's results, so their effect on these clips is
not evidence of how they behave on new sessions.

The speed bound is generous on purpose: 18 radii/s is about 4 m/s for a 450 mm plate, above barbell
speeds in the lifts recorded here; a smaller plate makes it more generous still. Because the bound
grows with the time since the last accepted frame, the gap rule is what limits re-acquisition. The guard reads only the tracked candidates and their timestamps, never labels. Raw tracks
are kept; the guarded track is written as a separate method.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

RULE = "track-guard-v2"
MAX_GAP_S = 0.5
MAX_SPEED_RADII_PER_S = 18.0
CENTRE_SLACK_RADII = 0.25
MAX_RADIUS_CHANGE = 0.15


def step_ok(previous: dict[str, Any], current: dict[str, Any], dt_s: float) -> bool:
    """Whether ``current`` is a plausible next position of the plate tracked as ``previous``."""
    r = previous["radius_px"]
    moved = math.hypot(current["center_x_px"] - previous["center_x_px"], current["center_y_px"] - previous["center_y_px"])
    return (moved <= r * (MAX_SPEED_RADII_PER_S * dt_s + CENTRE_SLACK_RADII)
            and abs(current["radius_px"] - r) <= MAX_RADIUS_CHANGE * r)


def guard_track(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Guarded copies of a clip's track records in time order. Each copy carries ``guard`` with the
    rule and a status: ``tracked``, ``lost`` (the tracker reported none), ``rejected_step`` (a single
    implausible frame), ``stopped_gap`` (the frame that ended the track) or ``after_stop``. Only
    ``tracked`` frames carry a candidate."""
    if any(len(r["candidates"]) > 1 for r in records):
        raise ValueError("the guard expects at most one tracked candidate per frame")
    out: list[dict[str, Any]] = []
    last: dict[str, Any] | None = None
    last_time: float | None = None
    stopped = False
    for record in sorted(records, key=lambda r: r["time_s"]):
        candidate = record["candidates"][0] if record["candidates"] else None
        if stopped:
            status = "after_stop"
        elif last_time is not None and record["time_s"] - last_time > MAX_GAP_S:
            status, stopped = "stopped_gap", True
        elif candidate is None:
            status = "lost"
        elif last is not None and not step_ok(last, candidate, record["time_s"] - last_time):
            status = "rejected_step"
        else:
            status = "tracked"
            last, last_time = candidate, record["time_s"]
        out.append({**record, "candidates": [candidate] if status == "tracked" else [],
                    "guard": {"rule": RULE, "status": status}})
    return out


def config() -> dict[str, Any]:
    return {"rule": RULE, "max_gap_s": MAX_GAP_S, "max_speed_radii_per_s": MAX_SPEED_RADII_PER_S,
            "centre_slack_radii": CENTRE_SLACK_RADII, "max_radius_change": MAX_RADIUS_CHANGE}
