"""Prompt-1 observation only: execute unchanged suggest_plate and retain its stages.

No media reader or formal scoring entry point. The CLI generates synthetic pixels
only. Calling observe_plate on real pixels requires the plan's input/freeze gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

import vbt_suggest

HARNESS_ID = "hough-stage-observer-v1"


def observe_plate(image: np.ndarray) -> dict[str, Any]:
    """Observe one decoded BGR uint8 frame; no labels enter the algorithm.

    The current-thread profile hook copies locals at function return. It neither
    replaces functions nor reimplements Hough, ranking, refinement or selection.
    Do not use observer timings as uninstrumented algorithm latency.
    """
    if (not isinstance(image, np.ndarray) or image.dtype != np.uint8 or
            image.ndim != 3 or image.shape[2] != 3 or min(image.shape[:2]) <= 0):
        raise ValueError("expected nonempty BGR uint8 image with three channels")
    if sys.getprofile() is not None:
        raise RuntimeError("an existing current-thread profiler is active; observation refused")
    captured: dict[str, Any] = {}
    suggest_code = vbt_suggest.suggest_plate.__code__
    refine_code = vbt_suggest._refine.__code__

    def capture(frame, event, value):
        if event == "return":
            if frame.f_code is suggest_code:
                captured["locals"] = frame.f_locals.copy()
            elif frame.f_code is refine_code and frame.f_back.f_code is suggest_code:
                captured["refinement"] = value

    sys.setprofile(capture)
    try:
        final = vbt_suggest.suggest_plate(image)
    finally:
        sys.setprofile(None)

    state = captured["locals"]
    params = state["params"]
    if params != vbt_suggest.PlateParams():
        raise RuntimeError("observer requires unchanged default PlateParams")
    scale, width, height = state["scale"], state["width"], state["height"]
    circles = state["circles"]
    raw = []
    retained_by_geometry: dict[tuple[float, ...], deque] = defaultdict(deque)
    for index, circle in enumerate([] if circles is None else circles[0]):
        x, y, radius_small = map(float, circle)
        cx, cy = (x + 0.5) / scale - 0.5, (y + 0.5) / scale - 0.5
        radius = radius_small / scale
        fits = vbt_suggest._fits(cx, cy, radius, width, height)
        retained = index < params.max_candidates
        row = {"raw_index": index, "hough_geometry": [x, y, radius_small],
               "center_x_px": cx, "center_y_px": cy, "radius_px": radius,
               "retained_first_60": retained, "fits": fits}
        raw.append(row)
        if retained and fits:
            retained_by_geometry[(cx, cy, radius)].append(row)

    ranked = []
    for rank, (support, cx, cy, radius) in enumerate(state["candidates"], 1):
        candidates = retained_by_geometry[(cx, cy, radius)]
        if not candidates:
            raise RuntimeError("observed ranking does not match retained Hough geometry")
        row = candidates.popleft()
        ranked.append({"rank": rank, "raw_index": row["raw_index"], "support": support,
                       "center_x_px": cx, "center_y_px": cy, "radius_px": radius})
    if any(retained_by_geometry.values()):
        raise RuntimeError("observed ranking omitted retained fitting candidates")

    refinement = captured.get("refinement")
    config = asdict(params)
    result = {
        "harness": HARNESS_ID, "method": vbt_suggest.PLATE_METHOD,
        "parameters": config, "width_px": width, "height_px": height, "scale": scale,
        "hough_width_px": int(state["small"].shape[1]),
        "hough_height_px": int(state["small"].shape[0]),
        "raw_count": len(raw), "post_first_60_count": min(len(raw), params.max_candidates),
        "post_fit_count": len(ranked), "raw": raw, "ranked": ranked,
        "selected": ranked[0] if ranked else None,
        "refinement": None if refinement is None else dict(zip(
            ("support", "center_x_px", "center_y_px", "radius_px"), refinement)),
        "final": final,
        "support_margin": ranked[0]["support"] - ranked[1]["support"] if len(ranked) >= 2 else None,
        "provenance": {
            "python": platform.python_version(), **vbt_suggest.environment(),
            "image_bgr_uint8_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
            "parameters_sha256": hashlib.sha256(json.dumps(
                config, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "suggest_source_sha256": hashlib.sha256(Path(vbt_suggest.__file__).read_bytes()).hexdigest(),
            "observer_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    }
    json.dumps(result, allow_nan=False)  # Refuse non-finite research output too.
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true", required=True,
                        help="observe generated synthetic pixels only; no media is read")
    parser.parse_args()
    image = np.full((240, 320, 3), 220, dtype=np.uint8)
    cv2.circle(image, (130, 120), 45, (15, 15, 15), -1)
    original = image.copy()
    expected = vbt_suggest.suggest_plate(image)
    observed = observe_plate(image)
    if observed["final"] != expected or not np.array_equal(image, original):
        raise RuntimeError("observation changed existing behavior or input pixels")
    if observed != observe_plate(image):
        raise RuntimeError("observation is not repeatable in this environment")
    print(json.dumps({"status": "synthetic-self-check-passed", "observation": observed},
                     indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
