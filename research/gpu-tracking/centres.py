#!/usr/bin/env python3
"""Mask centre extraction pure functions (#57 Phase 3 bake-off).

Contains:
  - ADR-0007 seed-disk rasterisation
  - centroid extraction
  - contour extraction and RANSAC circle fit
  - mask area validation and lost semantics
  - coordinate conversion between frame and model resolution
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

OPENCV_TRACKING_DIR = Path(__file__).resolve().parents[1] / "opencv-tracking"
if str(OPENCV_TRACKING_DIR) not in sys.path:
    sys.path.insert(0, str(OPENCV_TRACKING_DIR))

import refine_circle as rc  # noqa: E402


@dataclass(frozen=True)
class MaskCircleFitResult:
    accepted: bool
    center_px: tuple[float, float] | None
    radius_px: float | None
    coverage_bins: int | None
    inlier_count: int | None
    reject_reasons: list[str]


def inside(x: float, y: float, width: int, height: int) -> bool:
    """ADR-0007 coordinate domain check."""
    return 0 <= x < width - 0.5 and 0 <= y < height - 0.5


def rasterize_seed_disk(cx: float, cy: float, r_seed: float, width: int, height: int) -> np.ndarray:
    """Rasterise a disk following ADR-0007 pixel centres.

    A pixel (col, row) is in the disk when (col - cx)^2 + (row - cy)^2 <= r_seed^2.
    Returns:
        (height, width) uint8 array with 1 for foreground, 0 for background.
    """
    y_grid, x_grid = np.ogrid[:height, :width]
    dists_sq = (x_grid - cx) ** 2 + (y_grid - cy) ** 2
    return (dists_sq <= r_seed ** 2).astype(np.uint8)


def validate_mask(mask: np.ndarray, seed_mask_area: int) -> bool:
    """Check if mask area is non-empty and within [0.5, 1.5] of the seed-frame mask area."""
    if seed_mask_area <= 0:
        return False
    cur_area = int(np.count_nonzero(mask))
    if cur_area == 0:
        return False
    return 0.5 * seed_mask_area <= cur_area <= 1.5 * seed_mask_area


def extract_mask_centroid(mask: np.ndarray, width: int, height: int) -> tuple[float, float] | None:
    """Compute the centroid (mean col, mean row) of the mask foreground pixels.

    Returns None if mask is empty or centroid is outside the frame.
    """
    rows, cols = np.nonzero(mask)
    if len(cols) == 0:
        return None
    mean_x = float(np.mean(cols))
    mean_y = float(np.mean(rows))
    if not (math.isfinite(mean_x) and math.isfinite(mean_y)) or not inside(mean_x, mean_y, width, height):
        return None
    return mean_x, mean_y


def fit_circle_from_mask(
    mask: np.ndarray,
    r_seed: float,
    frame_index: int,
    width: int,
    height: int,
) -> MaskCircleFitResult:
    """Extract largest connected component contour and fit a circle with RANSAC + Taubin.

    Accepts if:
      - coverage >= 18 of 36 bins
      - |r - r_seed| / r_seed <= 0.20
      - centre is inside the frame.

    If rejected, returns accepted=False and centroid as fallback center_px.
    """
    centroid = extract_mask_centroid(mask, width, height)
    if centroid is None:
        return MaskCircleFitResult(
            accepted=False,
            center_px=None,
            radius_px=None,
            coverage_bins=None,
            inlier_count=None,
            reject_reasons=["empty_or_outside_mask"],
        )

    # Largest connected component
    mask_u8 = (mask > 0).astype(np.uint8)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask_u8, connectivity=8)
    if num_labels <= 1:
        return MaskCircleFitResult(
            accepted=False,
            center_px=centroid,
            radius_px=r_seed,
            coverage_bins=None,
            inlier_count=None,
            reject_reasons=["no_connected_components"],
        )

    # Label 0 is background; find largest foreground label
    areas = stats[1:, cv2.CC_STAT_AREA]
    largest_label = int(np.argmax(areas)) + 1
    cc_mask = (labels == largest_label).astype(np.uint8)

    contours, _ = cv2.findContours(cc_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours or len(contours[0]) < 3:
        return MaskCircleFitResult(
            accepted=False,
            center_px=centroid,
            radius_px=r_seed,
            coverage_bins=None,
            inlier_count=None,
            reject_reasons=["insufficient_contour_points"],
        )

    # Take the contour with the most points
    best_contour = max(contours, key=lambda c: len(c))
    contour_pts = best_contour.reshape(-1, 2).astype(np.float64)
    if len(contour_pts) < 3:
        return MaskCircleFitResult(
            accepted=False,
            center_px=centroid,
            radius_px=r_seed,
            coverage_bins=None,
            inlier_count=None,
            reject_reasons=["insufficient_contour_points"],
        )

    fit_res = rc.fit_circle_ransac(contour_pts, r_seed, frame_index)
    if fit_res is None:
        return MaskCircleFitResult(
            accepted=False,
            center_px=centroid,
            radius_px=r_seed,
            coverage_bins=None,
            inlier_count=None,
            reject_reasons=["ransac_failed"],
        )

    cx_fit, cy_fit, r_fit, final_inliers = fit_res
    coverage_bins = rc.compute_coverage_bins(final_inliers, cx_fit, cy_fit)
    final_inlier_count = int(len(final_inliers))

    reject_reasons: list[str] = []
    if coverage_bins < 18:
        reject_reasons.append("coverage")
    if abs(r_fit - r_seed) / r_seed > 0.20:
        reject_reasons.append("seed_radius")
    if not inside(cx_fit, cy_fit, width, height):
        reject_reasons.append("center_outside")

    if reject_reasons:
        return MaskCircleFitResult(
            accepted=False,
            center_px=centroid,
            radius_px=r_fit,
            coverage_bins=coverage_bins,
            inlier_count=final_inlier_count,
            reject_reasons=reject_reasons,
        )

    return MaskCircleFitResult(
        accepted=True,
        center_px=(cx_fit, cy_fit),
        radius_px=r_fit,
        coverage_bins=coverage_bins,
        inlier_count=final_inlier_count,
        reject_reasons=[],
    )


def convert_frame_to_model(
    x_frame: float,
    y_frame: float,
    w_frame: int,
    h_frame: int,
    w_model: int,
    h_model: int,
) -> tuple[float, float]:
    """Convert ADR-0007 pixel centre from frame to model resolution."""
    x_model = (x_frame + 0.5) * w_model / w_frame - 0.5
    y_model = (y_frame + 0.5) * h_model / h_frame - 0.5
    return x_model, y_model


def convert_model_to_frame(
    x_model: float,
    y_model: float,
    w_frame: int,
    h_frame: int,
    w_model: int,
    h_model: int,
) -> tuple[float, float]:
    """Convert ADR-0007 pixel centre from model resolution back to frame."""
    x_frame = (x_model + 0.5) * w_frame / w_model - 0.5
    y_frame = (y_model + 0.5) * h_frame / h_model - 0.5
    return x_frame, y_frame
