#!/usr/bin/env python3
"""Plate-geometry circle fit refinement for OpenCV tracker spike (#57 Phase 2).

Pure functions on decoded frames and base bounding boxes. Coordinates follow ADR-0007:
integer pixel coordinates are pixel centres; (0, 0) is the centre of the top-left pixel.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class RefinementResult:
    accepted: bool
    center_px: tuple[float, float]
    radius_px: float
    confidence: float
    fit_attempted: bool
    reject_reasons: list[str] = field(default_factory=list)
    inlier_count: int | None = None
    edge_count: int | None = None
    coverage_bins: int | None = None
    canny_lower: float | None = None
    canny_upper: float | None = None
    ellipse: dict[str, float] | None = None
    base_confidence: float = 0.0


def inside(x: float, y: float, width: int, height: int) -> bool:
    """ADR-0007 coordinate domain check."""
    return 0 <= x < width - 0.5 and 0 <= y < height - 0.5


def box_center(box: tuple[float, float, float, float]) -> tuple[float, float]:
    """Center of an OpenCV bounding box (x, y, w, h) per ADR-0007."""
    x, y, w, h = box
    return x + (w - 1) / 2.0, y + (h - 1) / 2.0


def appearance_confidence(gray: np.ndarray, box: tuple[float, float, float, float], template: np.ndarray) -> float:
    """Normalized cross-correlation of the tracked box against the seed template, clamped to [0, 1]."""
    x, y, w, h = (int(round(v)) for v in box)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(gray.shape[1], x + w), min(gray.shape[0], y + h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return 0.0
    patch = gray[y0:y1, x0:x1]
    resized = cv2.resize(template, (patch.shape[1], patch.shape[0]), interpolation=cv2.INTER_AREA)
    score = float(cv2.matchTemplate(patch, resized, cv2.TM_CCOEFF_NORMED)[0, 0])
    return min(1.0, max(0.0, score)) if math.isfinite(score) else 0.0


def circumcircle_3pts(
    p1: tuple[float, float] | np.ndarray,
    p2: tuple[float, float] | np.ndarray,
    p3: tuple[float, float] | np.ndarray,
) -> tuple[float, float, float] | None:
    """Calculate circumcentre and circumradius of 3 2D points.
    
    Returns (cx, cy, r) or None if collinear / degenerate.
    """
    x1, y1 = float(p1[0]), float(p1[1])
    x2, y2 = float(p2[0]), float(p2[1])
    x3, y3 = float(p3[0]), float(p3[1])

    d = 2.0 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return None

    sq1 = x1 * x1 + y1 * y1
    sq2 = x2 * x2 + y2 * y2
    sq3 = x3 * x3 + y3 * y3

    cx = (sq1 * (y2 - y3) + sq2 * (y3 - y1) + sq3 * (y1 - y2)) / d
    cy = (sq1 * (x3 - x2) + sq2 * (x1 - x3) + sq3 * (x2 - x1)) / d
    r = math.hypot(x1 - cx, y1 - cy)
    if not (math.isfinite(cx) and math.isfinite(cy) and math.isfinite(r)):
        return None
    return cx, cy, r


def fit_circle_taubin(points: np.ndarray) -> tuple[float, float, float]:
    """Taubin algebraic circle fit (Taubin 1991, Chernov 2005).
    
    Fits (center_x, center_y, radius) to an (N, 2) array of coordinates.
    """
    n = points.shape[0]
    if n < 3:
        raise ValueError(f"Taubin fit requires at least 3 points, got {n}")

    centroid = np.mean(points, axis=0)
    x = points[:, 0] - centroid[0]
    y = points[:, 1] - centroid[1]
    z = x * x + y * y

    mxx = float(np.mean(x * x))
    myy = float(np.mean(y * y))
    mxy = float(np.mean(x * y))
    mxz = float(np.mean(x * z))
    myz = float(np.mean(y * z))
    mzz = float(np.mean(z * z))

    mz = mxx + myy
    cov_xy = mxx * myy - mxy * mxy
    a3 = 4.0 * mz
    a2 = -3.0 * mz * mz - mzz
    a1 = mzz * mz + 4.0 * cov_xy * mz - mxz * mxz - myz * myz - mz * mz * mz
    a0 = mxz * mxz * myy + myz * myz * mxx - mzz * cov_xy - 2.0 * mxz * myz * mxy + mz * mz * cov_xy
    a22 = a2 + a2
    a33 = a3 + a3 + a3

    xnew = 0.0
    ynew = 1e20
    epsilon = 1e-12
    iter_max = 20

    for iter_idx in range(iter_max):
        yold = ynew
        ynew = a0 + xnew * (a1 + xnew * (a2 + xnew * a3))
        if abs(ynew) > abs(yold):
            xnew = 0.0
            break
        dy = a1 + xnew * (a22 + xnew * a33)
        xold = xnew
        if abs(dy) < 1e-16:
            xnew = 0.0
            break
        xnew = xold - ynew / dy
        if abs((xnew - xold) / (xnew + 1e-16)) < epsilon:
            break
        if iter_idx >= iter_max - 1 or xnew < 0:
            xnew = 0.0

    det = xnew * xnew - xnew * mz + cov_xy
    if abs(det) < 1e-16:
        raise ValueError("Taubin determinant near zero")

    cx_rel = (mxz * (myy - xnew) - myz * mxy) / (2.0 * det)
    cy_rel = (myz * (mxx - xnew) - mxz * mxy) / (2.0 * det)
    rad_sq = cx_rel * cx_rel + cy_rel * cy_rel + mz
    if rad_sq < 0:
        raise ValueError("Taubin negative squared radius")
    r = math.sqrt(rad_sq)

    cx = float(cx_rel + centroid[0])
    cy = float(cy_rel + centroid[1])
    if not (math.isfinite(cx) and math.isfinite(cy) and math.isfinite(r)):
        raise ValueError("Taubin produced non-finite circle parameters")
    return cx, cy, float(r)


def compute_coverage_bins(inlier_points: np.ndarray, center_x: float, center_y: float) -> int:
    """Number of occupied 10-degree angular bins (out of 36) about (center_x, center_y)."""
    if len(inlier_points) == 0:
        return 0
    angles_rad = np.arctan2(inlier_points[:, 1] - center_y, inlier_points[:, 0] - center_x)
    angles_deg = np.rad2deg(angles_rad) % 360.0
    bins = (angles_deg // 10.0).astype(int) % 36
    return int(len(np.unique(bins)))


@dataclass(frozen=True)
class CircleFitAttempt:
    """Detailed internal outcome used to preserve Phase 2 diagnostics across the refactor."""

    fit: tuple[float, float, float, np.ndarray] | None
    failure_reason: str | None
    best_inlier_count: int


def _fit_circle_ransac_detailed(
    points: np.ndarray,
    r_ref: float,
    frame_index: int,
    num_iterations: int = 1000,
    inlier_tolerance: float = 1.5,
) -> CircleFitAttempt:
    """RANSAC + Taubin circle fit with the original Phase 2 failure diagnostics."""
    n_points = points.shape[0]
    if n_points < 3:
        return CircleFitAttempt(None, "ransac_failed", 0)

    rng = np.random.default_rng([42, frame_index])
    best_inlier_mask: np.ndarray | None = None
    best_inlier_count = 0

    for _ in range(num_iterations):
        sample_indices = rng.choice(n_points, size=3, replace=False)
        cand_circle = circumcircle_3pts(
            points[sample_indices[0]],
            points[sample_indices[1]],
            points[sample_indices[2]],
        )
        if cand_circle is None:
            continue
        c_cx, c_cy, c_r = cand_circle
        if c_r > 10.0 * r_ref:
            continue
        residuals = np.abs(np.hypot(points[:, 0] - c_cx, points[:, 1] - c_cy) - c_r)
        inlier_mask = residuals <= inlier_tolerance
        count = int(np.sum(inlier_mask))
        if count > best_inlier_count:
            best_inlier_count = count
            best_inlier_mask = inlier_mask

    if best_inlier_mask is None or best_inlier_count < 3:
        return CircleFitAttempt(None, "ransac_failed", best_inlier_count)

    try:
        refit_cx, refit_cy, refit_r = fit_circle_taubin(points[best_inlier_mask])
    except (ValueError, ZeroDivisionError):
        return CircleFitAttempt(None, "taubin_refit_failed", best_inlier_count)

    refit_residuals = np.abs(np.hypot(points[:, 0] - refit_cx, points[:, 1] - refit_cy) - refit_r)
    final_inliers = points[refit_residuals <= inlier_tolerance]
    return CircleFitAttempt(
        (refit_cx, refit_cy, refit_r, final_inliers),
        None,
        best_inlier_count,
    )


def fit_circle_ransac(
    points: np.ndarray,
    r_ref: float,
    frame_index: int,
    num_iterations: int = 1000,
    inlier_tolerance: float = 1.5,
) -> tuple[float, float, float, np.ndarray] | None:
    """RANSAC + Taubin circle fit on 2D points; None means the fit did not complete."""
    return _fit_circle_ransac_detailed(
        points,
        r_ref,
        frame_index,
        num_iterations=num_iterations,
        inlier_tolerance=inlier_tolerance,
    ).fit


def refine_circle(
    frame_bgr: np.ndarray,
    base_box: tuple[float, float, float, float] | None,
    r_prev: float,
    r_seed: float,
    base_confidence: float,
    frame_index: int,
    seed_template: np.ndarray | None = None,
    method: str = "circle",
) -> RefinementResult:
    """Plate geometry refinement on top of a base box tracker.
    
    Args:
        frame_bgr: full decoded BGR (or grayscale) video frame.
        base_box: (x, y, w, h) bounding box from base tracker, or None if lost.
        r_prev: previously accepted or seed radius (pixels).
        r_seed: initial seed radius (pixels).
        base_confidence: confidence score from the base tracker.
        frame_index: frame index used to seed deterministic RANSAC RNG.
        seed_template: grayscale patch of the seed template for appearance confidence.
        method: "circle" (Canny + RANSAC + Taubin) or "hough" (Hough ALT).
    """
    height, width = frame_bgr.shape[:2]
    if base_box is None:
        return RefinementResult(
            accepted=False,
            center_px=(0.0, 0.0),
            radius_px=r_prev,
            confidence=0.0,
            fit_attempted=False,
            reject_reasons=["base_lost"],
            base_confidence=base_confidence,
        )

    try:
        bx, by, bw, bh = (float(v) for v in base_box)
    except (TypeError, ValueError):
        return RefinementResult(
            accepted=False,
            center_px=(0.0, 0.0),
            radius_px=r_prev,
            confidence=0.0,
            fit_attempted=False,
            reject_reasons=["invalid_base_box"],
            base_confidence=base_confidence,
        )

    if not (math.isfinite(bx) and math.isfinite(by) and math.isfinite(bw) and math.isfinite(bh)) or bw <= 0 or bh <= 0:
        return RefinementResult(
            accepted=False,
            center_px=(0.0, 0.0),
            radius_px=r_prev,
            confidence=0.0,
            fit_attempted=False,
            reject_reasons=["invalid_base_box"],
            base_confidence=base_confidence,
        )

    base_cx, base_cy = box_center((bx, by, bw, bh))
    if not inside(base_cx, base_cy, width, height):
        return RefinementResult(
            accepted=False,
            center_px=(base_cx, base_cy),
            radius_px=r_prev,
            confidence=0.0,
            fit_attempted=False,
            reject_reasons=["base_box_outside"],
            base_confidence=base_confidence,
        )

    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr

    # 5.1 ROI definition: half-extents 1.3 * w/2, 1.3 * h/2 about box centre
    x0 = max(0, int(math.floor(base_cx - 0.65 * bw)))
    x1 = min(width, int(math.ceil(base_cx + 0.65 * bw)) + 1)
    y0 = max(0, int(math.floor(base_cy - 0.65 * bh)))
    y1 = min(height, int(math.ceil(base_cy + 0.65 * bh)) + 1)

    if x1 <= x0 or y1 <= y0:
        return RefinementResult(
            accepted=False,
            center_px=(base_cx, base_cy),
            radius_px=r_prev,
            confidence=round(base_confidence * 0.7, 4),
            fit_attempted=False,
            reject_reasons=["empty_roi"],
            base_confidence=base_confidence,
        )

    roi_gray = gray[y0:y1, x0:x1]

    # Helper for evaluating confidence on refined circle
    def evaluate_conf(cx: float, cy: float) -> float:
        if seed_template is None:
            return base_confidence
        tw, th = seed_template.shape[1], seed_template.shape[0]
        ref_box = (cx - (tw - 1) / 2.0, cy - (th - 1) / 2.0, float(tw), float(th))
        return appearance_confidence(gray, ref_box, seed_template)

    # Variant H: Hough
    if method == "hough":
        min_r = int(math.floor(0.85 * r_prev))
        max_r = int(math.ceil(1.15 * r_prev))
        circles = cv2.HoughCircles(
            roi_gray,
            cv2.HOUGH_GRADIENT_ALT,
            dp=1.5,
            minDist=r_prev,
            param1=300,
            param2=0.9,
            minRadius=min_r,
            maxRadius=max_r,
        )
        if circles is None or len(circles) == 0:
            return RefinementResult(
                accepted=False,
                center_px=(base_cx, base_cy),
                radius_px=r_prev,
                confidence=round(base_confidence * 0.7, 4),
                fit_attempted=True,
                reject_reasons=["no_hough_circles"],
                base_confidence=base_confidence,
            )

        c_candidates = circles[0]
        # Pick candidate whose centre is nearest the base-box centre
        best_c = min(
            c_candidates,
            key=lambda c: math.hypot((float(c[0]) + x0) - base_cx, (float(c[1]) + y0) - base_cy),
        )
        cand_cx = float(best_c[0]) + x0
        cand_cy = float(best_c[1]) + y0
        cand_r = float(best_c[2])

        reject_reasons: list[str] = []
        if abs(cand_r - r_prev) / r_prev > 0.10:
            reject_reasons.append("frame_radius")
        if abs(cand_r - r_seed) / r_seed > 0.20:
            reject_reasons.append("seed_radius")
        if not inside(cand_cx, cand_cy, width, height):
            reject_reasons.append("center_outside")

        if not reject_reasons:
            conf = evaluate_conf(cand_cx, cand_cy)
            return RefinementResult(
                accepted=True,
                center_px=(cand_cx, cand_cy),
                radius_px=cand_r,
                confidence=round(conf, 4),
                fit_attempted=True,
                reject_reasons=[],
                base_confidence=base_confidence,
            )
        else:
            return RefinementResult(
                accepted=False,
                center_px=(base_cx, base_cy),
                radius_px=r_prev,
                confidence=round(base_confidence * 0.7, 4),
                fit_attempted=True,
                reject_reasons=reject_reasons,
                base_confidence=base_confidence,
            )

    # Variant A / B: Canny + RANSAC + Taubin
    blurred = cv2.GaussianBlur(roi_gray, (0, 0), 1.5)
    v = float(np.median(roi_gray))
    lower = max(0.0, 0.66 * v)
    upper = min(255.0, 1.33 * v)
    edges = cv2.Canny(blurred, lower, upper)

    edge_rows, edge_cols = np.nonzero(edges)
    px = x0 + edge_cols
    py = y0 + edge_rows
    dists = np.hypot(px - base_cx, py - base_cy)
    annulus_mask = (dists >= 0.75 * r_prev) & (dists <= 1.25 * r_prev)
    annulus_pts = np.column_stack([px[annulus_mask], py[annulus_mask]])
    edge_count = len(annulus_pts)

    if edge_count < 3:
        return RefinementResult(
            accepted=False,
            center_px=(base_cx, base_cy),
            radius_px=r_prev,
            confidence=round(base_confidence * 0.7, 4),
            fit_attempted=True,
            reject_reasons=["insufficient_edges"],
            edge_count=edge_count,
            canny_lower=round(lower, 2),
            canny_upper=round(upper, 2),
            base_confidence=base_confidence,
        )

    fit_attempt = _fit_circle_ransac_detailed(annulus_pts, r_prev, frame_index)
    if fit_attempt.fit is None:
        return RefinementResult(
            accepted=False,
            center_px=(base_cx, base_cy),
            radius_px=r_prev,
            confidence=round(base_confidence * 0.7, 4),
            fit_attempted=True,
            reject_reasons=[fit_attempt.failure_reason or "ransac_failed"],
            edge_count=edge_count,
            inlier_count=fit_attempt.best_inlier_count,
            canny_lower=round(lower, 2),
            canny_upper=round(upper, 2),
            base_confidence=base_confidence,
        )

    refit_cx, refit_cy, refit_r, final_inliers = fit_attempt.fit
    final_inlier_count = int(len(final_inliers))

    # Coverage bins check: inliers must occupy >= 18 of 36 x 10-deg angular bins
    coverage_bins = compute_coverage_bins(final_inliers, refit_cx, refit_cy)

    # Check acceptance criteria
    reject_reasons = []
    if coverage_bins < 18:
        reject_reasons.append("coverage")
    if abs(refit_r - r_prev) / r_prev > 0.10:
        reject_reasons.append("frame_radius")
    if abs(refit_r - r_seed) / r_seed > 0.20:
        reject_reasons.append("seed_radius")
    if not inside(refit_cx, refit_cy, width, height):
        reject_reasons.append("center_outside")

    # Fit ellipse on inliers (if >= 5 inliers)
    ellipse_dict: dict[str, float] | None = None
    if final_inlier_count >= 5:
        try:
            ellipse_res = cv2.fitEllipse(final_inliers.astype(np.float32))
            (_, (d1, d2), angle) = ellipse_res
            ellipse_dict = {
                "major_px": round(float(max(d1, d2)), 3),
                "minor_px": round(float(min(d1, d2)), 3),
                "angle_deg": round(float(angle), 2),
            }
        except cv2.error:
            ellipse_dict = None

    if not reject_reasons:
        conf = evaluate_conf(refit_cx, refit_cy)
        return RefinementResult(
            accepted=True,
            center_px=(refit_cx, refit_cy),
            radius_px=refit_r,
            confidence=round(conf, 4),
            fit_attempted=True,
            reject_reasons=[],
            inlier_count=final_inlier_count,
            edge_count=edge_count,
            coverage_bins=coverage_bins,
            canny_lower=round(lower, 2),
            canny_upper=round(upper, 2),
            ellipse=ellipse_dict,
            base_confidence=base_confidence,
        )
    else:
        return RefinementResult(
            accepted=False,
            center_px=(base_cx, base_cy),
            radius_px=r_prev,
            confidence=round(base_confidence * 0.7, 4),
            fit_attempted=True,
            reject_reasons=reject_reasons,
            inlier_count=final_inlier_count,
            edge_count=edge_count,
            coverage_bins=coverage_bins,
            canny_lower=round(lower, 2),
            canny_upper=round(upper, 2),
            ellipse=ellipse_dict,
            base_confidence=base_confidence,
        )
