"""Per-clip arms of the colour and hub cue experiment (research only, no I/O).

Takes already decoded frames, so it can be tested on synthetic images. The registered arms are:
baseline (coarse SAM small-circle), C (colour-gated radial rim), H (coarse centres fused with hub
displacement) and C+H (gated rim fused with hub displacement). Every arm shares the manual-seed anchor
on the seed frame, as `research/bar-path-measurement/experiment.py` does.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bar-path-measurement"))
import fusion  # noqa: E402

import cues  # noqa: E402

AXES = ("x_px", "y_px")
SEED_TOLERANCE_S = 0.0005
HUB_MIN_LABELLED_SHARE = 0.5


def _tracked(t: float, result: Mapping[str, Any]) -> dict[str, Any]:
    if result["center_px"] is None:
        return {"timestamp_s": t, "state": "lost"}
    return {"timestamp_s": t, "state": "tracked", "center_px": dict(result["center_px"]),
            "confidence": result["confidence"]}


def _coarse_sample(t: float, raw: Mapping[str, Any] | None) -> dict[str, Any]:
    if raw is None or raw["state"] != "tracked":
        return {"timestamp_s": t, "state": "lost"}
    return {"timestamp_s": t, "state": "tracked", "center_px": dict(raw["center_px"]),
            "confidence": raw["confidence"]}


def process_clip(frames: Iterable, times: Sequence[float], coarse: Mapping[float, Mapping[str, Any]],
                 seed: Mapping[str, Any], *, fusion_config: Mapping[str, Any] | None = None,
                 radial_config: Mapping[str, Any] | None = None,
                 control_colour: cues.PlateColour | None = None) -> dict[str, Any]:
    """Run every arm over the frames from the seed frame to the last coarse timestamp."""
    seed_index = min(range(len(times)), key=lambda i: abs(times[i] - seed["timestamp_s"]))
    if abs(times[seed_index] - seed["timestamp_s"]) > SEED_TOLERANCE_S:
        raise ValueError("seed has no matching decoded frame")
    seed_xy = tuple(seed["target"]["center"][a] for a in AXES)
    radius = seed["target"]["radius_px"]
    end_s = max(coarse)
    if not coarse.get(times[seed_index]) or coarse[times[seed_index]]["state"] != "tracked":
        raise ValueError("coarse observations must include a tracked seed frame")
    model = hub = None
    rows: list[dict[str, Any]] = []
    for index, frame in enumerate(frames):  # drain every frame so decoder failures surface
        t = times[index]
        if index < seed_index or t > end_s:
            continue
        if index == seed_index:
            model = cues.fit_plate_colour(frame, seed_xy, radius)
            hub = cues.fit_hub(frame, seed_xy, radius)
        raw = coarse.get(t)
        tracked = raw is not None and raw["state"] == "tracked"
        centre = seed_xy if index == seed_index else (tuple(raw["center_px"][a] for a in AXES) if tracked else None)
        lost = {"center_px": None, "confidence": None, "diagnostics": {"loss_reason": "coarse_loss"}}
        gated = lost if centre is None else cues.radial_center_gated(frame, centre, radius, model, radial_config)
        control = None
        if control_colour is not None:
            control = lost if centre is None else cues.radial_center_gated(
                frame, centre, radius, control_colour, radial_config)
        if hub is None:
            located = {"center_px": None, "confidence": None, "diagnostics": {"loss_reason": "hub_unsupported"}}
        else:
            located = lost if centre is None else cues.locate_hub(frame, hub, centre, radius)
        rows.append({"t": t, "coarse": _coarse_sample(t, raw), "gated": gated, "control": control, "hub": located})
    if not rows:
        raise ValueError("no frames between the seed and the last coarse timestamp")

    anchor = {"timestamp_s": rows[0]["t"], "state": "tracked", "center_px": dict(zip(AXES, seed_xy)),
              "confidence": 1.0}
    baseline = [anchor] + [r["coarse"] for r in rows[1:]]
    arm_c = [anchor] + [_tracked(r["t"], r["gated"]) for r in rows[1:]]
    arms = {"baseline": baseline, "C": arm_c}
    if control_colour is not None:
        arms["C_control"] = [anchor] + [_tracked(r["t"], r["control"]) for r in rows[1:]]

    relative = []
    for previous, current in zip(rows, rows[1:]):
        a, b = previous["hub"], current["hub"]
        delta = confidence = None
        if a["center_px"] is not None and b["center_px"] is not None:
            delta = {axis: b["center_px"][axis] - a["center_px"][axis] for axis in AXES}
            confidence = min(a["confidence"], b["confidence"])
        relative.append({"timestamp_s": current["t"], "previous_timestamp_s": previous["t"],
                         "delta_px": delta, "confidence": confidence})

    anchor_weight = seed.get("selection_confidence", 0.0)

    def fused(absolute: list[dict[str, Any]]) -> list[dict[str, Any]]:
        weighted = [dict(s) for s in absolute]
        weighted[0]["confidence"] = anchor_weight
        result = fusion.fuse(weighted, relative, dict(fusion_config or {}))
        if result[0]["state"] == "tracked":
            result[0]["confidence"] = 1.0
        return result

    arms["H"] = fused(baseline)
    arms["CH"] = fused(arm_c)
    return {
        "arms": arms,
        "hub_supported": hub is not None,
        "relative_samples": relative,
        "diagnostics": {
            "C": [{"timestamp_s": r["t"], **r["gated"]["diagnostics"]} for r in rows],
            "hub": [{"timestamp_s": r["t"], **r["hub"]["diagnostics"],
                     "accepted": r["hub"]["center_px"] is not None} for r in rows],
        },
    }


def hub_labelled_share(hub_diagnostics: Sequence[Mapping[str, Any]], label_times: Sequence[float],
                       tolerance_s: float = SEED_TOLERANCE_S) -> float | None:
    """Share of labelled timestamps with an accepted hub; None when there is no labelled frame."""
    accepted = {d["timestamp_s"]: d["accepted"] for d in hub_diagnostics}
    stamps = sorted(accepted)
    seen = hits = 0
    for label in label_times:
        near = [s for s in stamps if abs(s - label) <= tolerance_s]
        if not near:
            continue
        seen += 1
        hits += bool(accepted[near[0]])
    return None if seen == 0 else hits / seen


def hub_supported_for_scoring(share: float | None) -> bool:
    """Registered rule: below 50% accepted on labelled frames, H is unsupported for the clip."""
    return share is not None and share >= HUB_MIN_LABELLED_SHARE
