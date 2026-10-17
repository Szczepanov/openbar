"""Plate circles from detector boxes and segmentation masks (numpy only, #233).

Pixel convention follows ADR-0007: integer (i, j) is the centre of a pixel, so the centre of the
top-left pixel is (0, 0). Detector boxes arrive as continuous pixel-edge coordinates in
``[0, width] x [0, height]``; subtracting 0.5 moves their centre into pixel-centre coordinates.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

MIN_FIT_POINTS = 12
ARC_BINS = 36
RANSAC_SEED = 0
RANSAC_ITERATIONS = 300
RANSAC_MIN_BAND_PX = 1.5
RANSAC_BAND_FRACTION = 0.02


@dataclass(frozen=True)
class Circle:
    center_x_px: float
    center_y_px: float
    radius_px: float
    fit: str  # "box", "mask_contour" or "mask_area"
    fit_rms_px: float | None = None
    arc_coverage: float | None = None  # share of ARC_BINS angular bins holding a fitted point


def circle_from_box(x0: float, y0: float, x1: float, y1: float) -> Circle:
    """Circle inscribed in the larger box side: a plate cut by the frame edge keeps its true size
    on the uncut side. Boxes are pixel-edge coordinates."""
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
        raise ValueError(f"invalid box {(x0, y0, x1, y1)!r}")
    radius = max(x1 - x0, y1 - y0) / 2.0
    return Circle((x0 + x1) / 2.0 - 0.5, (y0 + y1) / 2.0 - 0.5, radius, "box")


def fit_circle(xs: np.ndarray, ys: np.ndarray) -> tuple[float, float, float]:
    """Algebraic least-squares circle (Kasa) through points; raises on degenerate input."""
    if xs.size < 3:
        raise ValueError("a circle needs at least three points")
    a = np.column_stack([xs, ys, np.ones_like(xs)])
    b = xs * xs + ys * ys
    solution, _, rank, _ = np.linalg.lstsq(a, b, rcond=None)
    if rank < 3:
        raise ValueError("collinear points do not define a circle")
    cx, cy = solution[0] / 2.0, solution[1] / 2.0
    radius_sq = solution[2] + cx * cx + cy * cy
    if not math.isfinite(radius_sq) or radius_sq <= 0:
        raise ValueError("degenerate circle fit")
    return float(cx), float(cy), math.sqrt(radius_sq)


def boundary_points(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Centres of mask pixels with a 4-neighbour outside the mask, excluding pixels on the image
    border: there the mask edge is the frame edge, not the plate rim."""
    if mask.ndim != 2:
        raise ValueError("mask must be two-dimensional")
    inside = mask.astype(bool)
    padded = np.pad(inside, 1, constant_values=False)
    interior = (padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:])
    edge = inside & ~interior
    edge[0, :] = edge[-1, :] = False
    edge[:, 0] = edge[:, -1] = False
    ys, xs = np.nonzero(edge)
    return xs.astype(np.float64), ys.astype(np.float64)


def arc_coverage(xs: np.ndarray, ys: np.ndarray, cx: float, cy: float) -> float:
    angles = np.arctan2(ys - cy, xs - cx)
    bins = np.floor((angles + math.pi) / (2 * math.pi) * ARC_BINS).astype(int) % ARC_BINS
    return float(np.unique(bins).size) / ARC_BINS


def ransac_inliers(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Inlier mask of the circle through three boundary points that explains the most points.

    Seeded, so the same mask always gives the same fit. Inliers lie within
    max(RANSAC_MIN_BAND_PX, RANSAC_BAND_FRACTION x radius) of the circle; circles wider than the
    point cloud's extent are skipped.
    """
    rng = np.random.default_rng(RANSAC_SEED)
    best = np.zeros(xs.size, dtype=bool)
    max_radius = max(np.ptp(xs), np.ptp(ys))  # a near-straight triple fits a huge, all-swallowing circle
    for _ in range(RANSAC_ITERATIONS):
        pick = rng.choice(xs.size, size=3, replace=False)
        try:
            cx, cy, radius = fit_circle(xs[pick], ys[pick])
        except ValueError:
            continue
        if radius > max_radius:
            continue
        band = max(RANSAC_MIN_BAND_PX, RANSAC_BAND_FRACTION * radius)
        inliers = np.abs(np.hypot(xs - cx, ys - cy) - radius) <= band
        if np.count_nonzero(inliers) > np.count_nonzero(best):
            best = inliers
    return best


def circle_from_mask(mask: np.ndarray) -> Circle | None:
    """Robust rim fit to the mask boundary; falls back to the equal-area disc when the boundary is
    too short to fit. Returns None for an empty mask.

    A hand, the bar sleeve or a neighbouring plate merged into the mask adds boundary points off
    the rim, so a seeded RANSAC picks the dominant circle and least squares refits its inliers.
    Boundary pixel centres sit half a pixel inside the true edge, so 0.5 px is added to the radius.
    """
    inside = mask.astype(bool)
    area = int(np.count_nonzero(inside))
    if area == 0:
        return None
    xs, ys = boundary_points(inside)
    if xs.size >= MIN_FIT_POINTS:
        try:
            keep = ransac_inliers(xs, ys)
            if np.count_nonzero(keep) >= MIN_FIT_POINTS:
                xs, ys = xs[keep], ys[keep]
            cx, cy, radius = fit_circle(xs, ys)
            rms = float(np.sqrt(np.mean((np.hypot(xs - cx, ys - cy) - radius) ** 2)))
            return Circle(cx, cy, radius + 0.5, "mask_contour", rms, arc_coverage(xs, ys, cx, cy))
        except ValueError:
            pass
    rows, cols = np.nonzero(inside)
    return Circle(float(cols.mean()), float(rows.mean()), math.sqrt(area / math.pi), "mask_area")
