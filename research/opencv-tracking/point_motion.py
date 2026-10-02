#!/usr/bin/env python3
"""Point-motion tracking and centre estimation (#57 Phase 3 bake-off).

Shared logic for optical flow and neural point trackers:
  - query point selection on the seed annulus [0.25, 0.90] * r_seed
  - forward-backward PyrLK point tracking
  - RANSAC similarity transform from seed points to current points
  - ADR-0007 centre estimation and lost semantics
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class PointMotionResult:
    state: str  # "tracked" or "lost"
    center_px: tuple[float, float] | None
    confidence: float
    inlier_count: int
    surviving_count: int
    initial_count: int
    affine_matrix: list[list[float]] | None = None


def inside(x: float, y: float, width: int, height: int) -> bool:
    """ADR-0007 coordinate domain check."""
    return 0 <= x < width - 0.5 and 0 <= y < height - 0.5


def extract_query_points(
    gray: np.ndarray,
    cx: float,
    cy: float,
    r_seed: float,
    max_corners: int = 200,
    quality_level: float = 0.01,
    min_distance: float = 5.0,
    block_size: int = 7,
) -> np.ndarray:
    """Extract query points in the seed annulus [0.25, 0.90] * r_seed.

    Returns:
        (N, 2) float32 array of (x, y) coordinates.
    """
    h, w = gray.shape[:2]
    y_grid, x_grid = np.ogrid[:h, :w]
    dists = np.hypot(x_grid - cx, y_grid - cy)
    mask = ((dists >= 0.25 * r_seed) & (dists <= 0.90 * r_seed)).astype(np.uint8) * 255

    corners = cv2.goodFeaturesToTrack(
        gray,
        maxCorners=max_corners,
        qualityLevel=quality_level,
        minDistance=min_distance,
        mask=mask,
        blockSize=block_size,
    )
    if corners is None or len(corners) == 0:
        return np.empty((0, 2), dtype=np.float32)
    return corners.reshape(-1, 2).astype(np.float32)


def track_points_lk(
    prev_gray: np.ndarray,
    cur_gray: np.ndarray,
    prev_pts: np.ndarray,
    win_size: tuple[int, int] = (21, 21),
    max_level: int = 3,
    max_fb_error: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Track points frame to frame using pyramidal LK with forward-backward error check.

    Args:
        prev_gray: previous grayscale frame.
        cur_gray: current grayscale frame.
        prev_pts: (N, 2) array of coordinates on previous frame.
        win_size: LK window size.
        max_level: LK pyramid max level.
        max_fb_error: maximum forward-backward discrepancy in pixels.

    Returns:
        (cur_pts, valid_mask): (N, 2) float32 coordinates and (N,) bool mask.
    """
    n = len(prev_pts)
    if n == 0:
        return np.empty((0, 2), dtype=np.float32), np.zeros(0, dtype=bool)

    pts_in = prev_pts.astype(np.float32).reshape(-1, 1, 2)
    criteria = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 30, 0.01)

    fwd_pts, status_fwd, _ = cv2.calcOpticalFlowPyrLK(
        prev_gray,
        cur_gray,
        pts_in,
        None,
        winSize=win_size,
        maxLevel=max_level,
        criteria=criteria,
    )

    back_pts, status_back, _ = cv2.calcOpticalFlowPyrLK(
        cur_gray,
        prev_gray,
        fwd_pts,
        None,
        winSize=win_size,
        maxLevel=max_level,
        criteria=criteria,
    )

    fwd_pts_2d = fwd_pts.reshape(-1, 2)
    back_pts_2d = back_pts.reshape(-1, 2)
    prev_pts_2d = prev_pts.reshape(-1, 2)

    fb_err = np.hypot(prev_pts_2d[:, 0] - back_pts_2d[:, 0], prev_pts_2d[:, 1] - back_pts_2d[:, 1])

    valid = (
        (status_fwd.reshape(-1) == 1)
        & (status_back.reshape(-1) == 1)
        & (fb_err <= max_fb_error)
        & np.isfinite(fwd_pts_2d[:, 0])
        & np.isfinite(fwd_pts_2d[:, 1])
    )

    return fwd_pts_2d, valid


def estimate_center_from_points(
    seed_pts: np.ndarray,
    cur_pts: np.ndarray,
    seed_center: tuple[float, float],
    initial_query_count: int,
    width: int,
    height: int,
    rng_seed: int = 42,
    min_inliers: int = 12,
    reproj_threshold: float = 1.5,
    max_iters: int = 2000,
    confidence: float = 0.999,
    refine_iters: int = 10,
) -> PointMotionResult:
    """Estimate the current plate centre from seed -> current point correspondences.

    Uses cv2.estimateAffinePartial2D with RANSAC.
    """
    surviving_count = len(seed_pts)
    if surviving_count < min_inliers or initial_query_count <= 0:
        return PointMotionResult(
            state="lost",
            center_px=None,
            confidence=0.0,
            inlier_count=0,
            surviving_count=surviving_count,
            initial_count=initial_query_count,
        )

    cv2.setRNGSeed(rng_seed)
    m_affine, inliers = cv2.estimateAffinePartial2D(
        seed_pts.astype(np.float32),
        cur_pts.astype(np.float32),
        method=cv2.RANSAC,
        ransacReprojThreshold=reproj_threshold,
        maxIters=max_iters,
        confidence=confidence,
        refineIters=refine_iters,
    )

    if m_affine is None or inliers is None:
        return PointMotionResult(
            state="lost",
            center_px=None,
            confidence=0.0,
            inlier_count=0,
            surviving_count=surviving_count,
            initial_count=initial_query_count,
        )

    inlier_count = int(np.sum(inliers))
    if inlier_count < min_inliers:
        return PointMotionResult(
            state="lost",
            center_px=None,
            confidence=0.0,
            inlier_count=inlier_count,
            surviving_count=surviving_count,
            initial_count=initial_query_count,
        )

    cx_seed, cy_seed = seed_center
    cur_cx = float(m_affine[0, 0] * cx_seed + m_affine[0, 1] * cy_seed + m_affine[0, 2])
    cur_cy = float(m_affine[1, 0] * cx_seed + m_affine[1, 1] * cy_seed + m_affine[1, 2])

    if not (math.isfinite(cur_cx) and math.isfinite(cur_cy)) or not inside(cur_cx, cur_cy, width, height):
        return PointMotionResult(
            state="lost",
            center_px=None,
            confidence=0.0,
            inlier_count=inlier_count,
            surviving_count=surviving_count,
            initial_count=initial_query_count,
        )

    conf = min(1.0, max(0.0, float(inlier_count) / float(initial_query_count)))
    return PointMotionResult(
        state="tracked",
        center_px=(cur_cx, cur_cy),
        confidence=round(conf, 4),
        inlier_count=inlier_count,
        surviving_count=surviving_count,
        initial_count=initial_query_count,
        affine_matrix=[[float(v) for v in row] for row in m_affine],
    )
