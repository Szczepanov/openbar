"""Seed-frame suggestions for the one-page VBT session (#95). Research venv only (OpenCV, NumPy).

Suggestions are proposals for a human to confirm or adjust on the session page; nothing here is
used without that confirmation. A failed suggestion returns a reason and the page leaves the item
for manual clicks. Two methods, each recorded with its id and parameters:

  plate-hough-edge-v1          Hough circles on a downscaled, blurred grey frame, limited to plausible
                               radii; every candidate that fits inside the frame is scored by edge
                               support (the fraction of 360 rim samples within a tolerance of a Canny
                               edge whose smoothed gradient is radial); the best candidate is refined
                               by a coarse-to-fine local search on the same score.
  stick-yellow-markers-v1      the yellow stick is the most elongated yellow (HSV) component, extended
                               along its principal axis to the collinear yellow pieces; markers are runs
                               along the axis where both side bands just outside the stick are dark,
                               bounded by yellow beyond both ends (which rejects the dark stand at the
                               foot). The lowest and highest markers are proposed (0.20 m and 1.50 m in
                               the owner's protocol). Each point is the run centre on the local stick
                               centre line.

Coordinates use the OpenBar display pixel-centre convention (ADR-0007): integer (i, j) is the
centre of pixel (i, j). Confidence is a heuristic score in [0, 1] (edge support for the plate, the
weaker marker's side-band darkness for the stick), not a calibrated probability.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2  # research venv (research/opencv-tracking/requirements.txt)
import numpy as np

PLATE_METHOD = "plate-hough-edge-v1"
STICK_METHOD = "stick-yellow-markers-v1"
RIM_SAMPLES = 360


@dataclass(frozen=True)
class PlateParams:
    min_radius_fraction: float = 0.04  # of the shorter display side
    max_radius_fraction: float = 0.30
    hough_max_side_px: int = 960  # Hough runs on a copy downscaled to this longer side
    hough_param1: float = 120.0  # Canny high threshold inside HoughCircles
    hough_param2: float = 25.0  # accumulator threshold
    min_dist_fraction: float = 0.05
    max_candidates: int = 60
    canny_low: float = 40.0
    canny_high: float = 120.0
    rank_tolerance_px: float = 2.0
    refine_tolerance_px: float = 1.5
    min_alignment: float = 0.7  # |cos| between the gradient and the radial direction
    min_support: float = 0.4


@dataclass(frozen=True)
class StickParams:
    hue_min: int = 20  # OpenCV hue, 0..179
    hue_max: int = 45
    saturation_min: int = 80
    value_min: int = 100
    min_area_fraction: float = 0.0005
    min_elongation: float = 8.0
    dark_value_max: int = 70
    side_band_inner: float = 1.0  # side bands, in stick half-widths from the axis (+1 px)
    side_band_outer: float = 3.5
    dark_fraction_min: float = 0.3
    merge_gap_px: int = 3
    min_run_half_widths: float = 0.8
    max_run_half_widths: float = 6.0
    yellow_margin_half_widths: float = 3.0  # yellow needed beyond both ends of a marker run


def suggestion_id(method: str, payload: dict[str, Any]) -> str:
    text = json.dumps({"method": method, **payload}, sort_keys=True, separators=(",", ":"))
    return f"{method}:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


def r2(value: float) -> float:
    return round(float(value), 2)


# --- Plate ---------------------------------------------------------------------------------------

def _plate_features(image: np.ndarray, params: PlateParams) -> tuple[np.ndarray, tuple[np.ndarray, ...]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 1.5)
    edges = cv2.Canny(blur, params.canny_low, params.canny_high)
    distance = cv2.distanceTransform(255 - edges, cv2.DIST_L2, 3)
    gx = cv2.GaussianBlur(cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3), (7, 7), 2)
    gy = cv2.GaussianBlur(cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3), (7, 7), 2)
    return blur, (distance, gx, gy, np.hypot(gx, gy))


def edge_support(features: tuple[np.ndarray, ...], cx: float, cy: float, r: float,
                 tolerance_px: float, min_alignment: float) -> float:
    """Fraction of rim samples on a radial edge; samples outside the frame count as unsupported."""
    distance, gx, gy, magnitude = features
    height, width = distance.shape
    angles = np.linspace(0.0, 2.0 * np.pi, RIM_SAMPLES, endpoint=False)
    cos, sin = np.cos(angles), np.sin(angles)
    xs = np.round(cx + r * cos).astype(int)  # pixel-centre coordinates index the array directly
    ys = np.round(cy + r * sin).astype(int)
    inside = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
    xi, yi = xs[inside], ys[inside]
    alignment = np.abs(gx[yi, xi] * cos[inside] + gy[yi, xi] * sin[inside]) / np.maximum(magnitude[yi, xi], 1e-6)
    supported = (distance[yi, xi] <= tolerance_px) & (alignment >= min_alignment)
    return float(np.count_nonzero(supported)) / RIM_SAMPLES


def _refine(features: tuple[np.ndarray, ...], start: tuple[float, float, float],
            params: PlateParams) -> tuple[float, float, float, float]:
    def score(cx: float, cy: float, r: float) -> float:
        return edge_support(features, cx, cy, r, params.refine_tolerance_px, params.min_alignment)

    best = (score(*start), *start)
    for step, span in ((2.0, 3), (1.0, 2), (0.5, 2)):
        _, bx, by, br = best
        for dx in range(-span, span + 1):
            for dy in range(-span, span + 1):
                for dr in range(-span, span + 1):
                    candidate = (bx + dx * step, by + dy * step, br + dr * step)
                    value = score(*candidate)
                    if value > best[0]:
                        best = (value, *candidate)
    return best


def _fits(cx: float, cy: float, r: float, width: int, height: int) -> bool:
    return r > 0 and cx - r >= 0 and cy - r >= 0 and cx + r <= width and cy + r <= height


def suggest_plate(image: np.ndarray, params: PlateParams = PlateParams()) -> dict[str, Any]:
    """Propose one plate circle, or explain why there is none."""
    height, width = image.shape[:2]
    blur, features = _plate_features(image, params)
    shorter = min(width, height)
    scale = min(1.0, params.hough_max_side_px / max(width, height))
    small = cv2.resize(blur, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else blur
    circles = cv2.HoughCircles(
        small, cv2.HOUGH_GRADIENT, dp=1,
        minDist=max(4, int(params.min_dist_fraction * shorter * scale)),
        param1=params.hough_param1, param2=params.hough_param2,
        minRadius=max(1, int(params.min_radius_fraction * shorter * scale)),
        maxRadius=max(2, int(params.max_radius_fraction * shorter * scale)),
    )
    method = {"method": PLATE_METHOD, "parameters": asdict(params)}
    candidates = []
    for x, y, r in ([] if circles is None else circles[0][: params.max_candidates]):
        cx, cy, radius = (float(x) + 0.5) / scale - 0.5, (float(y) + 0.5) / scale - 0.5, float(r) / scale
        if _fits(cx, cy, radius, width, height):
            support = edge_support(features, cx, cy, radius, params.rank_tolerance_px, params.min_alignment)
            candidates.append((support, cx, cy, radius))
    if not candidates:
        return {**method, "status": "failed", "reason": "no circle of plausible radius fits inside the frame"}
    candidates.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))
    support, cx, cy, radius = _refine(features, candidates[0][1:], params)
    if support < params.min_support or not _fits(cx, cy, radius, width, height):
        return {**method, "status": "failed",
                "reason": f"best circle has edge support {support:.2f} < {params.min_support}"}
    values = {"center_x_px": r2(cx), "center_y_px": r2(cy), "radius_px": r2(radius)}
    if not _fits(values["center_x_px"], values["center_y_px"], values["radius_px"], width, height):
        return {**method, "status": "failed", "reason": "the rounded circle does not fit inside the frame"}
    return {**method, "status": "suggested", "id": suggestion_id(PLATE_METHOD, {**values, "parameters": asdict(params)}),
            "confidence": round(support, 4), **values}


# --- Stick ---------------------------------------------------------------------------------------

def _principal_axis(xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
    points = np.stack([xs, ys], 1).astype(np.float64)
    mean = points.mean(0)
    values, vectors = np.linalg.eigh(np.cov((points - mean).T))
    direction = vectors[:, 1] if vectors[1, 1] >= 0 else -vectors[:, 1]  # pointing down the frame
    return mean, direction, float(max(values[0], 0.0)), float(max(values[1], 0.0))


def _stick_pixels(mask: np.ndarray, params: StickParams) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    height, width = mask.shape
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    seed = None
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < params.min_area_fraction * width * height:
            continue
        ys, xs = np.nonzero(labels == label)
        _, _, minor, major = _principal_axis(xs, ys)
        if math.sqrt(major / max(minor, 1e-9)) >= params.min_elongation and (seed is None or area > seed[0]):
            seed = (area, xs, ys)
    if seed is None:
        return None
    mean, direction, minor, _ = _principal_axis(seed[1], seed[2])
    all_y, all_x = np.nonzero(mask)
    keep = np.zeros(all_x.shape, bool)
    for _ in range(3):  # pull in the collinear yellow pieces between the markers, then refit
        normal = np.array([-direction[1], direction[0]])
        offset = (all_x - mean[0]) * normal[0] + (all_y - mean[1]) * normal[1]
        keep = np.abs(offset) <= max(2.0 * math.sqrt(minor), 6.0) * 1.5
        mean, direction, minor, _ = _principal_axis(all_x[keep], all_y[keep])
    return all_x[keep], all_y[keep], mean, direction


def _runs(flags: np.ndarray, merge_gap: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start = None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        if start is not None and (not flag or index == len(flags) - 1):
            runs.append((start, index if flag else index - 1))
            start = None
    merged: list[tuple[int, int]] = []
    for run in runs:
        if merged and run[0] - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], run[1])
        else:
            merged.append(run)
    return merged


def suggest_stick(image: np.ndarray, params: StickParams = StickParams()) -> dict[str, Any]:
    """Propose the lowest and highest marker centres on the yellow stick, or explain why not."""
    height, width = image.shape[:2]
    method = {"method": STICK_METHOD, "parameters": asdict(params)}
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (params.hue_min, params.saturation_min, params.value_min), (params.hue_max, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    stick = _stick_pixels(mask, params)
    if stick is None:
        return {**method, "status": "failed", "reason": "no elongated yellow stick found"}
    xs, ys, mean, direction = stick
    normal = np.array([-direction[1], direction[0]])
    along = (xs - mean[0]) * direction[0] + (ys - mean[1]) * direction[1]
    across = (xs - mean[0]) * normal[0] + (ys - mean[1]) * normal[1]
    half_width = max(float(np.percentile(np.abs(across), 95)), 1.0)
    positions = np.arange(math.floor(along.min()), math.ceil(along.max()) + 1)
    dark = hsv[:, :, 2] <= params.dark_value_max
    offsets = np.arange(half_width * params.side_band_inner + 1, half_width * params.side_band_outer + 2)
    offsets = np.concatenate([-offsets, offsets])
    px = mean[0] + np.outer(positions, direction)[:, 0:1] + offsets[None, :] * normal[0]
    py = mean[1] + np.outer(positions, direction)[:, 1:2] + offsets[None, :] * normal[1]
    xi, yi = np.round(px).astype(int), np.round(py).astype(int)
    inside = (xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)
    darkness = np.where(inside, dark[np.clip(yi, 0, height - 1), np.clip(xi, 0, width - 1)], False)
    profile = darkness.sum(1) / np.maximum(inside.sum(1), 1)
    yellow_along = np.zeros(len(positions), bool)
    yellow_along[np.clip(np.round(along - positions[0]).astype(int), 0, len(positions) - 1)] = True
    margin = int(round(params.yellow_margin_half_widths * half_width))
    markers = []
    for first, last in _runs(profile >= params.dark_fraction_min, params.merge_gap_px):
        length = last - first + 1
        if not params.min_run_half_widths * half_width <= length <= params.max_run_half_widths * half_width:
            continue
        before, after = yellow_along[max(0, first - margin):first], yellow_along[last + 1:last + 1 + margin]
        if not before.any() or not after.any():
            continue  # the dark stand at the foot (or anything past the stick's end)
        centre_along = (positions[first] + positions[last]) / 2.0
        near = np.abs(along - centre_along) <= 3 * half_width + length / 2.0
        local = float(np.median(across[near])) if near.any() else 0.0
        point = mean + direction * centre_along + normal * local
        markers.append((float(point[0]), float(point[1]), float(profile[first:last + 1].mean())))
    if len(markers) < 2:
        return {**method, "status": "failed", "reason": f"found {len(markers)} marker(s) on the stick; need 2"}
    low = max(markers, key=lambda item: item[1])  # +Y is down: the lowest marker has the largest y
    high = min(markers, key=lambda item: item[1])
    values = {"low_x_px": r2(low[0]), "low_y_px": r2(low[1]), "high_x_px": r2(high[0]), "high_y_px": r2(high[1])}
    if not all(0 <= values[f"{end}_x_px"] < width and 0 <= values[f"{end}_y_px"] < height for end in ("low", "high")):
        return {**method, "status": "failed", "reason": "a marker lies outside the ADR-0007 point window"}
    return {**method, "status": "suggested", "id": suggestion_id(STICK_METHOD, {**values, "parameters": asdict(params)}),
            "confidence": round(min(1.0, low[2], high[2]), 4), "marker_count": len(markers), **values}


def environment() -> dict[str, str]:
    """Library versions that can change a suggestion; ingest records them in session.json."""
    return {"opencv_version": cv2.__version__, "numpy_version": np.__version__}


def _guarded(method: str, suggest: Any, *args: Any) -> dict[str, Any]:
    """A method that raises fails only its own item, which the page then leaves for manual clicks."""
    try:
        return suggest(*args)
    except Exception as error:  # noqa: BLE001 - any failure means "no suggestion", never a crash
        return {"method": method, "status": "failed", "reason": f"{type(error).__name__}: {error}"}


def suggest_frame(path: Path, plate: PlateParams = PlateParams(), stick: StickParams = StickParams()) -> dict[str, Any]:
    """Both suggestions for one decoded seed-frame PNG (from label_package)."""
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        failure = {"status": "failed", "reason": f"cannot read {path.name}"}
        return {"plate": {"method": PLATE_METHOD, **failure}, "stick": {"method": STICK_METHOD, **failure}}
    return {"plate": _guarded(PLATE_METHOD, suggest_plate, image, plate),
            "stick": _guarded(STICK_METHOD, suggest_stick, image, stick)}
