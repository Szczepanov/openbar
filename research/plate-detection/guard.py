"""Identity guard for a tracked plate (#57): ``track-guard-v1``.

A video tracker that loses the plate can later lock onto another object and report it as tracked
(EdgeTAM on ``self-clean-side-0203-17``, PLATE_DETECTION_SAM3_SEED_RESULTS.md). This guard turns
such cases into lost frames instead of positions:

- **Gap rule.** After the track has been lost for longer than ``MAX_GAP_S``, it stays lost until a
  new seed: a re-acquired object after a long gap cannot be told apart from another plate.
- **Step rule.** Between two consecutive tracked frames the centre may move at most
  ``MAX_SPEED_RADII_PER_S`` plate radii per second (plus ``CENTRE_SLACK_RADII``), and the radius
  may change by at most ``MAX_RADIUS_CHANGE``. A violation ends the track: that frame and every
  later one are lost.

Designed after seeing the 0203-17 failure, so its effect there is not evidence of how it behaves
on new sessions. The speed bound is generous on purpose: 18 radii/s is about 4 m/s for a 450 mm
plate, above barbell speeds in the lifts recorded here; a smaller plate makes it more generous
still. The guard reads only the tracked candidates and their timestamps, never labels. Raw tracks
are kept; the guarded track is written as a separate method.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

RULE = "track-guard-v1"
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
    rule and a status: ``tracked``, ``lost`` (the tracker reported none), ``stopped_gap`` or
    ``stopped_step`` (the frame that ended the track) or ``after_stop`` (every later frame). Stopped
    and later frames carry no candidate."""
    if any(len(r["candidates"]) > 1 for r in records):
        raise ValueError("the guard expects at most one tracked candidate per frame")
    ordered = sorted(records, key=lambda r: r["time_s"])
    out: list[dict[str, Any]] = []
    last: dict[str, Any] | None = None
    last_time = lost_since = None
    stopped = False
    for record in ordered:
        candidate = record["candidates"][0] if record["candidates"] else None
        status = "tracked"
        if stopped:
            status = "after_stop"
        elif candidate is None:
            status = "lost"
            lost_since = record["time_s"] if lost_since is None else lost_since
        elif lost_since is not None and last_time is not None and record["time_s"] - last_time > MAX_GAP_S:
            status = "stopped_gap"
        elif last is not None and not step_ok(last, candidate, record["time_s"] - last_time):
            status = "stopped_step"
        if status in ("stopped_gap", "stopped_step"):
            stopped = True
        if status == "tracked":
            last, last_time, lost_since = candidate, record["time_s"], None
        keep = status == "tracked"
        out.append({**record, "candidates": [candidate] if keep else [],
                    "guard": {"rule": RULE, "status": status}})
    return out


def config() -> dict[str, Any]:
    return {"rule": RULE, "max_gap_s": MAX_GAP_S, "max_speed_radii_per_s": MAX_SPEED_RADII_PER_S,
            "centre_slack_radii": CENTRE_SLACK_RADII, "max_radius_change": MAX_RADIUS_CHANGE}
