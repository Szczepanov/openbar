"""Plate-face colour gate and hub cue for plate-centre estimation (research only).

Implements the fixed configuration of docs/plans/COLOUR_HUB_CUE_EXPERIMENT_PLAN.md. Nothing here is
tuned: every constant below is the registered value. A lost observation carries no coordinate and
nothing is interpolated or substituted from another estimator. Coordinates follow ADR-0007 (integer
coordinates are pixel centres, +x right, +y down, display orientation).
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bar-path-measurement"))
import vision  # noqa: E402

COLOUR_Z_MAX = 3.0
FACE_INNER_FACTOR = 0.35
FACE_OUTER_FACTOR = 0.85
SCALE_FLOOR = 1.0
MAD_TO_SIGMA = 1.4826
INSIDE_OFFSETS_PX = (2.0, 3.0, 4.0, 5.0, 6.0)
MIN_FACE_PIXELS = 50

HUB_RADIUS_FACTOR = 0.12
HUB_MIN_SIDE_PX = 7
HUB_MIN_STD = 2.0
HUB_SEARCH_FACTOR = 0.5
HUB_NCC_MIN = 0.6


@dataclass(frozen=True)
class PlateColour:
    """Per-channel median and robust scale of the plate face in OpenCV 8-bit Lab."""

    median: tuple[float, float, float]
    scale: tuple[float, float, float]


def _lab(frame_bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB).astype(np.float64)


def fit_plate_colour(frame_bgr: np.ndarray, centre: tuple, radius_px: float) -> PlateColour:
    """Colour model of the annulus 0.35 r to 0.85 r around the manual seed (seed frame only)."""
    x, y, r = vision._geometry(centre, radius_px)
    if not vision._bgr_valid(frame_bgr):
        raise ValueError("frame must be a BGR uint8 image of at least 8 x 8 pixels")
    lab = _lab(frame_bgr)
    height, width = lab.shape[:2]
    x0, x1 = max(0, math.floor(x - r)), min(width, math.ceil(x + r) + 1)
    y0, y1 = max(0, math.floor(y - r)), min(height, math.ceil(y + r) + 1)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    distance = np.hypot(xx - x, yy - y)
    mask = (distance >= FACE_INNER_FACTOR * r) & (distance < FACE_OUTER_FACTOR * r)
    pixels = lab[y0:y1, x0:x1][mask]
    if len(pixels) < MIN_FACE_PIXELS:
        raise ValueError("plate face annulus has too few pixels inside the image")
    median = np.median(pixels, axis=0)
    scale = np.maximum(MAD_TO_SIGMA * np.median(np.abs(pixels - median), axis=0), SCALE_FLOOR)
    return PlateColour(tuple(float(v) for v in median), tuple(float(v) for v in scale))


def inside_colour_eligible(lab: np.ndarray, xx: np.ndarray, yy: np.ndarray, dx: float, dy: float,
                           model: PlateColour) -> np.ndarray:
    """For each ray sample: does the colour 2..6 px inside it match the plate model on all channels?"""
    height, width = lab.shape[:2]
    offsets = np.array(INSIDE_OFFSETS_PX)
    px = np.clip(xx[:, None] - dx * offsets[None, :], 0, width - 1)
    py = np.clip(yy[:, None] - dy * offsets[None, :], 0, height - 1)
    channels = [vision._bilinear(lab[:, :, c], px, py) for c in range(3)]
    inside = np.median(np.stack(channels, axis=-1), axis=1)
    z = np.abs(inside - np.array(model.median)) / np.array(model.scale)
    return np.all(z < COLOUR_Z_MAX, axis=1)


def radial_center_gated(frame_bgr, coarse_center, radius_px, model: PlateColour, config=None) -> dict:
    """`vision.radial_center` with one change: only colour-eligible edge candidates can be selected.

    The strongest eligible gradient is chosen; a ray with no eligible candidate supplies no edge. There
    is no fallback. With a model that admits every pixel the result equals `vision.radial_center`.
    """
    cfg = vision._config(vision.RADIAL_CONFIG, config)
    x, y, radius = vision._geometry(coarse_center, radius_px)
    if not vision._bgr_valid(frame_bgr):
        return vision._lost("invalid_bgr_image")
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY).astype(float)
    lab = _lab(frame_bgr)
    h, w = gray.shape
    if not 0 <= x < w or not 0 <= y < h:
        return vision._lost("coarse_center_outside_image")
    step = cfg["sample_step_px"]
    distances = np.arange(radius * (1 - cfg["radius_band_fraction"]),
                          radius * (1 + cfg["radius_band_fraction"]) + step / 2, step)
    if len(distances) < 7:
        return vision._lost("radius_band_too_narrow")
    points, evidence, without_eligible = [], [], 0
    for angle in np.arange(cfg["ray_count"]) * (2 * np.pi / cfg["ray_count"]):
        dx, dy = math.cos(angle), math.sin(angle)
        xx, yy = x + distances * dx, y + distances * dy
        if np.any((xx < 0) | (yy < 0) | (xx >= w - 1) | (yy >= h - 1)):
            continue
        values = vision._bilinear(gray, xx, yy)
        gradient = np.abs(np.gradient(values, step))
        eligible = inside_colour_eligible(lab, xx, yy, dx, dy, model)
        masked = np.where(eligible, gradient, -np.inf)
        index = int(np.argmax(masked[2:-2])) + 2
        if not np.isfinite(masked[index]):
            without_eligible += 1
            continue
        strength = float(gradient[index])
        reach = max(2, math.ceil(2 / step))
        contrast = abs(float(values[min(len(values) - 1, index + reach)] - values[max(0, index - reach)]))
        if strength < cfg["min_gradient"] or contrast < cfg["min_contrast"]:
            continue
        curvature = gradient[index - 1] - 2 * gradient[index] + gradient[index + 1]
        offset = 0.0 if curvature >= -1e-10 else float(np.clip(
            0.5 * (gradient[index - 1] - gradient[index + 1]) / curvature, -0.5, 0.5))
        distance = distances[index] + offset * step
        lo = hi = index
        while lo > 0 and gradient[lo - 1] >= strength / 2:
            lo -= 1
        while hi < len(gradient) - 1 and gradient[hi + 1] >= strength / 2:
            hi += 1
        points.append([x + distance * dx, y + distance * dy])
        evidence.append({"angle_rad": float(angle), "gradient": strength,
                         "contrast": contrast, "width_px": float((hi - lo + 1) * step)})
    diagnostics = {"ray_count": cfg["ray_count"], "edge_count": len(points), "rays": evidence,
                   "rays_without_eligible_edge": without_eligible}
    if len(points) < max(6, math.ceil(cfg["ray_count"] * cfg["min_support_fraction"])):
        return vision._lost("insufficient_rim_support", diagnostics)
    points = np.array(points)
    fit = vision._robust_circle(points, np.array([x, y]), radius, cfg)
    if fit is None:
        return vision._lost("no_plausible_circle", diagnostics)
    circle, inliers, residuals = fit
    rms = float(np.sqrt(np.mean(residuals[inliers] ** 2)))
    support = float(inliers.sum() / cfg["ray_count"])
    arc = vision._arc_fraction(points[inliers], circle[:2])
    diagnostics.update({"radius_px": float(circle[2]), "fit_rms_px": rms,
                        "inlier_count": int(inliers.sum()), "support_fraction": support,
                        "arc_fraction": arc})
    if support < cfg["min_support_fraction"] or arc < cfg["min_arc_fraction"] or rms > cfg["max_residual_px"]:
        return vision._lost("rim_geometry_gate", diagnostics)
    return vision._success(circle[0], circle[1], support * math.exp(-rms / cfg["max_residual_px"]), diagnostics)


@dataclass(frozen=True)
class HubTemplate:
    patch: np.ndarray  # uint8 grey, odd side
    half: int


def fit_hub(frame_bgr: np.ndarray, centre: tuple, radius_px: float) -> HubTemplate | None:
    """Grey template around the manual seed; None when the hub is unsupported (flat or off-image)."""
    x, y, r = vision._geometry(centre, radius_px)
    if not vision._bgr_valid(frame_bgr):
        raise ValueError("frame must be a BGR uint8 image of at least 8 x 8 pixels")
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    half = max((HUB_MIN_SIDE_PX - 1) // 2, math.ceil(HUB_RADIUS_FACTOR * r))
    cx, cy = int(round(x)), int(round(y))
    if cx - half < 0 or cy - half < 0 or cx + half >= width or cy + half >= height:
        return None
    patch = gray[cy - half:cy + half + 1, cx - half:cx + half + 1].copy()
    if float(patch.std()) < HUB_MIN_STD:
        return None
    return HubTemplate(patch, half)


def _parabola_offset(left: float, mid: float, right: float) -> float:
    curvature = left - 2 * mid + right
    return 0.0 if curvature >= -1e-12 else float(np.clip(0.5 * (left - right) / curvature, -0.5, 0.5))


def locate_hub(frame_bgr, template: HubTemplate, coarse_center, radius_px) -> dict:
    """Normalised cross-correlation of the hub template near the coarse centre; lost below 0.6."""
    x, y, r = vision._geometry(coarse_center, radius_px)
    if not vision._bgr_valid(frame_bgr):
        return vision._lost("invalid_bgr_image")
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    if not 0 <= x < width or not 0 <= y < height:
        return vision._lost("coarse_center_outside_image")
    reach = math.ceil(HUB_SEARCH_FACTOR * r) + template.half
    cx, cy = int(round(x)), int(round(y))
    x0, y0 = max(0, cx - reach), max(0, cy - reach)
    x1, y1 = min(width, cx + reach + 1), min(height, cy + reach + 1)
    side = 2 * template.half + 1
    crop = gray[y0:y1, x0:x1]
    if crop.shape[0] < side or crop.shape[1] < side:
        return vision._lost("hub_window_outside_image")
    scores = np.nan_to_num(cv2.matchTemplate(crop, template.patch, cv2.TM_CCOEFF_NORMED),
                           nan=-1.0, posinf=-1.0, neginf=-1.0)
    ly, lx = np.unravel_index(int(np.argmax(scores)), scores.shape)
    peak = float(scores[ly, lx])
    if peak < HUB_NCC_MIN:
        return vision._lost("hub_ncc_below_threshold", {"ncc_peak": peak})
    sub_x = _parabola_offset(scores[ly, lx - 1], peak, scores[ly, lx + 1]) if 0 < lx < scores.shape[1] - 1 else 0.0
    sub_y = _parabola_offset(scores[ly - 1, lx], peak, scores[ly + 1, lx]) if 0 < ly < scores.shape[0] - 1 else 0.0
    return vision._success(x0 + lx + sub_x + template.half, y0 + ly + sub_y + template.half,
                           peak, {"ncc_peak": peak})
