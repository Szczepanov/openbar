"""Independent, research-only image measurements in display pixels (+x right, +y down).

No state is updated here and loss never carries a coordinate. Confidence is an
algorithm-specific diagnostic score, not a calibrated probability. Input BGR is
uint8; registration accepts finite grayscale arrays in the intensity range 0..255.

Relative registration independently implements the matrix-multiply local inverse
DFT principle of Guizar-Sicairos, Thurman and Fienup (2008), Optics Letters 33,
156-158, https://doi.org/10.1364/OL.33.000156. No reference source code was used.
The phase estimate is refined deterministically against the real, non-wrapped
pixel overlap. The model is translation only: overlap correlation rejects
unsupported rotation, occlusion or changed texture, rather than attempting to
measure their motion. A second real-overlap ambiguity check prevents a windowed
phase peak from hiding another spatially distinct, equally supported translation.
"""
from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np


MARKER_CONFIG = {
    "hsv_lower": [35, 80, 60], "hsv_upper": [85, 255, 255],
    "roi_radius_factor": 2.0, "min_radius_factor": 0.75,
    "max_radius_factor": 1.25, "max_center_offset_factor": 0.6,
    "min_area_factor": 0.65, "min_circularity": 0.75,
    "min_purity": 0.85, "min_arc_fraction": 0.8, "min_contour_fraction": 0.9,
    "max_residual_px": 1.2, "inlier_tolerance_px": 1.5,
}
RADIAL_CONFIG = {
    "ray_count": 72, "radius_band_fraction": 0.3, "sample_step_px": 0.5,
    "min_gradient": 8.0, "min_contrast": 20.0,
    "min_radius_factor": 0.8, "max_radius_factor": 1.2,
    "max_center_offset_factor": 0.25, "inlier_tolerance_px": 1.5,
    "max_residual_px": 0.9, "min_support_fraction": 0.6,
    "min_arc_fraction": 0.65,
}
REGISTRATION_CONFIG = {
    "upsample_factor": 50, "max_shift_px": 12.0, "min_std": 2.0,
    "min_peak_ratio": 1.5, "min_correlation": 0.9,
    "min_overlap_fraction": 0.65, "max_forward_backward_px": 0.08,
    "window": True,
}
OVERLAP_REFINEMENT_STEPS_PX = (0.5, 0.2, 0.08, 0.03)
PHASE_AMBIGUITY_CANDIDATE_COUNT = 32
PHASE_MAIN_LOBE_RADIUS_PX = 2.0


def _config(default: dict, supplied: dict | None) -> dict:
    if supplied is not None and not isinstance(supplied, dict):
        raise ValueError("config must be a dictionary")
    if supplied and any(not isinstance(key, str) for key in supplied):
        raise ValueError("config keys must be strings")
    if supplied and set(supplied) - set(default):
        raise ValueError("unknown config keys: " + ", ".join(sorted(set(supplied) - set(default))))
    cfg = {**default, **(supplied or {})}
    for key, value in cfg.items():
        if key in ("hsv_lower", "hsv_upper"):
            if not isinstance(value, (list, tuple)) or len(value) != 3:
                raise ValueError(f"{key} must have three HSV integer bounds")
            if any(isinstance(v, bool) or not isinstance(v, (int, np.integer))
                   or not 0 <= v <= limit for v, limit in zip(value, (179, 255, 255))):
                raise ValueError(f"invalid {key}")
        elif key == "window":
            if not isinstance(value, bool):
                raise ValueError("window must be boolean")
        elif (isinstance(value, bool) or not isinstance(value, (int, float, np.number)) or np.iscomplexobj(value)
              or not math.isfinite(float(value)) or value <= 0):
            raise ValueError(f"{key} must be finite and positive")
        elif ("fraction" in key or key in ("min_circularity", "min_purity", "min_correlation")) and value > 1:
            raise ValueError(f"{key} must be at most one")
    if "hsv_lower" in cfg:
        if any(lo > hi for lo, hi in zip(cfg["hsv_lower"][1:], cfg["hsv_upper"][1:])):
            raise ValueError("HSV saturation/value lower bounds exceed upper bounds")
    for integer, lower, upper in (("ray_count", 12, 1024), ("upsample_factor", 2, 200)):
        if integer in cfg and (not isinstance(cfg[integer], int) or not lower <= cfg[integer] <= upper):
            raise ValueError(f"{integer} must be an integer in [{lower}, {upper}]")
    if "min_radius_factor" in cfg and not cfg["min_radius_factor"] < cfg["max_radius_factor"]:
        raise ValueError("radius factors must be ordered")
    if "roi_radius_factor" in cfg and cfg["roi_radius_factor"] <= cfg["max_radius_factor"]:
        raise ValueError("marker ROI must exceed maximum marker radius")
    if "sample_step_px" in cfg and not 0.1 <= cfg["sample_step_px"] <= 2.0:
        raise ValueError("sample_step_px must be in [0.1, 2]")
    if "radius_band_fraction" in cfg and cfg["radius_band_fraction"] >= 1:
        raise ValueError("radius band must be less than one")
    if "min_peak_ratio" in cfg and cfg["min_peak_ratio"] < 1:
        raise ValueError("min_peak_ratio must be at least one")
    return cfg


def _geometry(coarse_center: tuple, radius_px: float) -> tuple[float, float, float]:
    try:
        x, y = coarse_center
        values = (x, y, radius_px)
        if any(isinstance(v, bool) or not isinstance(v, (int, float, np.number)) or np.iscomplexobj(v)
               or not math.isfinite(float(v)) for v in values) or radius_px < 3:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("coarse center must be finite; radius_px must be finite and >= 3") from exc
    return float(x), float(y), float(radius_px)


def _lost(reason: str, diagnostics: dict | None = None, *, relative: bool = False) -> dict:
    return {"delta_px" if relative else "center_px": None, "confidence": None,
            "diagnostics": {**(diagnostics or {}), "loss_reason": reason}}


def _success(x: float, y: float, confidence: float, diagnostics: dict, *, relative: bool = False) -> dict:
    if not all(math.isfinite(v) for v in (x, y, confidence)):
        return _lost("nonfinite_estimate", diagnostics, relative=relative)
    return {"delta_px" if relative else "center_px": {"x_px": float(x), "y_px": float(y)},
            "confidence": float(np.clip(confidence, 0, 1)), "diagnostics": diagnostics}


def _bgr_valid(frame: Any) -> bool:
    return (isinstance(frame, np.ndarray) and frame.dtype == np.uint8
            and frame.ndim == 3 and frame.shape[2] == 3 and min(frame.shape[:2]) >= 8)


def _circle_lstsq(points: np.ndarray) -> np.ndarray | None:
    origin = points.mean(axis=0)
    p = points - origin
    a = np.column_stack((2 * p, np.ones(len(p))))
    solution, _, rank, _ = np.linalg.lstsq(a, (p * p).sum(axis=1), rcond=None)
    radius2 = solution[2] + (solution[:2] ** 2).sum()
    if rank < 3 or radius2 <= 0:
        return None
    return np.array([*(solution[:2] + origin), math.sqrt(radius2)])


def _robust_circle(points: np.ndarray, coarse: np.ndarray, radius: float, cfg: dict):
    """Deterministic spaced-triple consensus followed by geometric inlier refinement."""
    n = len(points)
    if n < 6:
        return None
    candidates = [_circle_lstsq(points)]
    for i in np.linspace(0, n - 1, min(n, 96), dtype=int):
        candidates.append(_circle_lstsq(points[[i, (i + n // 3) % n, (i + 2 * n // 3) % n]]))
    best = None
    best_score = None
    for candidate in candidates:
        if candidate is None or not np.isfinite(candidate).all():
            continue
        if not radius * cfg["min_radius_factor"] <= candidate[2] <= radius * cfg["max_radius_factor"]:
            continue
        if np.linalg.norm(candidate[:2] - coarse) > radius * cfg["max_center_offset_factor"]:
            continue
        residual = np.abs(np.linalg.norm(points - candidate[:2], axis=1) - candidate[2])
        inliers = residual <= cfg["inlier_tolerance_px"]
        count = int(inliers.sum())
        if count < 6:
            continue
        score = (count, -float(np.median(residual[inliers])))
        if best_score is None or score > best_score:
            best, best_score = candidate, score
    if best is None:
        return None
    for _ in range(12):
        vectors = best[:2] - points
        distances = np.linalg.norm(vectors, axis=1)
        residuals = distances - best[2]
        inliers = (np.abs(residuals) <= cfg["inlier_tolerance_px"]) & (distances > 1e-8)
        if inliers.sum() < 6:
            return None
        jacobian = np.column_stack((vectors[inliers] / distances[inliers, None], -np.ones(inliers.sum())))
        correction, _, rank, _ = np.linalg.lstsq(jacobian, -residuals[inliers], rcond=None)
        if rank < 3:
            return None
        best += correction
        if np.linalg.norm(correction) < 1e-7:
            break
    residuals = np.abs(np.linalg.norm(points - best[:2], axis=1) - best[2])
    inliers = residuals <= cfg["inlier_tolerance_px"]
    if not np.isfinite(best).all() or inliers.sum() < 6:
        return None
    if not radius * cfg["min_radius_factor"] <= best[2] <= radius * cfg["max_radius_factor"]:
        return None
    if np.linalg.norm(best[:2] - coarse) > radius * cfg["max_center_offset_factor"]:
        return None
    return best, inliers, residuals


def _arc_fraction(points: np.ndarray, center: np.ndarray) -> float:
    angle = np.mod(np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0]), 2 * np.pi)
    return len(np.unique(np.floor(angle * 24 / (2 * np.pi)).astype(int))) / 24.0


def marker_center(frame_bgr, coarse_center, radius_px, config=None) -> dict:
    """Segment an explicitly configured circular marker; ambiguous components are lost.

    radius_px is the expected *marker* radius, not the natural plate radius. HSV
    hue bounds may wrap (lower hue > upper hue). No morphology is applied.
    min_contour_fraction requires most of the complete external contour to
    support the fitted circle; sector coverage by a small subset is insufficient.
    """
    cfg = _config(MARKER_CONFIG, config)
    x, y, radius = _geometry(coarse_center, radius_px)
    if not _bgr_valid(frame_bgr):
        return _lost("invalid_bgr_image")
    h, w = frame_bgr.shape[:2]
    if not 0 <= x < w or not 0 <= y < h:
        return _lost("coarse_center_outside_image")
    extent = radius * cfg["roi_radius_factor"]
    left, top = max(0, math.floor(x - extent)), max(0, math.floor(y - extent))
    right, bottom = min(w, math.ceil(x + extent) + 1), min(h, math.ceil(y + extent) + 1)
    hsv = cv2.cvtColor(frame_bgr[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    low, high = np.array(cfg["hsv_lower"], np.uint8), np.array(cfg["hsv_upper"], np.uint8)
    if low[0] <= high[0]:
        mask = cv2.inRange(hsv, low, high)
    else:
        mask = (cv2.inRange(hsv, low, np.array([179, *high[1:]], np.uint8))
                | cv2.inRange(hsv, np.array([0, *low[1:]], np.uint8), high))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    accepted = []
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if not cfg["min_area_factor"] * math.pi * radius ** 2 <= area <= math.pi * (radius * cfg["max_radius_factor"]) ** 2 * 1.1:
            continue
        component = (labels == label).astype(np.uint8)
        # A clipped component has an artificial straight contour and is not a measurement.
        if component[0].any() or component[-1].any() or component[:, 0].any() or component[:, -1].any():
            continue
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        contour = max(contours, key=cv2.contourArea)
        perimeter = cv2.arcLength(contour, True)
        circularity = 4 * math.pi * cv2.contourArea(contour) / max(perimeter ** 2, 1)
        if circularity < cfg["min_circularity"]:
            continue
        points = contour[:, 0, :].astype(float) + [left, top]
        fit = _robust_circle(points, np.array([x, y]), radius, cfg)
        if fit is None:
            continue
        circle, inliers, residuals = fit
        rms = float(np.sqrt(np.mean(residuals[inliers] ** 2)))
        arc = _arc_fraction(points[inliers], circle[:2])
        contour_fraction = float(inliers.mean())
        # Include holes in the purity denominator: ring-shaped distractors fail.
        yy, xx = np.ogrid[:mask.shape[0], :mask.shape[1]]
        disk = (xx + left - circle[0]) ** 2 + (yy + top - circle[1]) ** 2 <= circle[2] ** 2
        purity = float(np.count_nonzero(component & disk) / max(1, np.count_nonzero(disk)))
        # Arc occupancy alone can accept squares: a circle fits their side
        # midpoints in every sector while discarding the corners as outliers.
        if (rms > cfg["max_residual_px"] or arc < cfg["min_arc_fraction"]
                or purity < cfg["min_purity"] or contour_fraction < cfg["min_contour_fraction"]):
            continue
        diagnostics = {"area_px2": area, "circularity": float(circularity),
                       "radius_px": float(circle[2]), "fit_rms_px": rms,
                       "arc_fraction": arc, "mask_purity": purity, "contour_fraction": contour_fraction,
                       "contour_count": len(points), "inlier_count": int(inliers.sum())}
        confidence = min(circularity, purity, arc, contour_fraction) * math.exp(-rms / cfg["max_residual_px"])
        accepted.append(_success(circle[0], circle[1], confidence, diagnostics))
    if len(accepted) != 1:
        return _lost("ambiguous_marker" if accepted else "no_plausible_marker",
                     {"component_count": count - 1, "accepted_count": len(accepted)})
    return accepted[0]


def _bilinear(gray: np.ndarray, xx: np.ndarray, yy: np.ndarray) -> np.ndarray:
    ix, iy = np.floor(xx).astype(int), np.floor(yy).astype(int)
    ix1 = np.minimum(ix + 1, gray.shape[1] - 1)
    iy1 = np.minimum(iy + 1, gray.shape[0] - 1)
    fx, fy = xx - ix, yy - iy
    return ((1 - fx) * (1 - fy) * gray[iy, ix] + fx * (1 - fy) * gray[iy, ix1]
            + (1 - fx) * fy * gray[iy1, ix] + fx * fy * gray[iy1, ix1])


def radial_center(frame_bgr, coarse_center, radius_px, config=None) -> dict:
    """Fixed radial rays, subpixel gradient peaks, and independently fit rim geometry."""
    cfg = _config(RADIAL_CONFIG, config)
    x, y, radius = _geometry(coarse_center, radius_px)
    if not _bgr_valid(frame_bgr):
        return _lost("invalid_bgr_image")
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY).astype(float)
    h, w = gray.shape
    if not 0 <= x < w or not 0 <= y < h:
        return _lost("coarse_center_outside_image")
    step = cfg["sample_step_px"]
    distances = np.arange(radius * (1 - cfg["radius_band_fraction"]),
                          radius * (1 + cfg["radius_band_fraction"]) + step / 2, step)
    if len(distances) < 7:
        return _lost("radius_band_too_narrow")
    points, evidence = [], []
    for angle in np.arange(cfg["ray_count"]) * (2 * np.pi / cfg["ray_count"]):
        dx, dy = math.cos(angle), math.sin(angle)
        xx, yy = x + distances * dx, y + distances * dy
        if np.any((xx < 0) | (yy < 0) | (xx >= w - 1) | (yy >= h - 1)):
            continue
        values = _bilinear(gray, xx, yy)
        gradient = np.abs(np.gradient(values, step))
        index = int(np.argmax(gradient[2:-2])) + 2
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
    diagnostics = {"ray_count": cfg["ray_count"], "edge_count": len(points), "rays": evidence}
    if len(points) < max(6, math.ceil(cfg["ray_count"] * cfg["min_support_fraction"])):
        return _lost("insufficient_rim_support", diagnostics)
    points = np.array(points)
    fit = _robust_circle(points, np.array([x, y]), radius, cfg)
    if fit is None:
        return _lost("no_plausible_circle", diagnostics)
    circle, inliers, residuals = fit
    rms = float(np.sqrt(np.mean(residuals[inliers] ** 2)))
    support = float(inliers.sum() / cfg["ray_count"])
    arc = _arc_fraction(points[inliers], circle[:2])
    diagnostics.update({"radius_px": float(circle[2]), "fit_rms_px": rms,
                        "inlier_count": int(inliers.sum()), "support_fraction": support,
                        "arc_fraction": arc})
    if support < cfg["min_support_fraction"] or arc < cfg["min_arc_fraction"] or rms > cfg["max_residual_px"]:
        return _lost("rim_geometry_gate", diagnostics)
    return _success(circle[0], circle[1], support * math.exp(-rms / cfg["max_residual_px"]), diagnostics)


def _phase_translation(previous: np.ndarray, current: np.ndarray, cfg: dict):
    h, w = previous.shape
    window = np.outer(np.hanning(h), np.hanning(w)) if cfg["window"] else 1.0
    first = np.fft.fft2((previous - previous.mean()) * window)
    second = np.fft.fft2((current - current.mean()) * window)
    # if current(x) = previous(x-d), conj(first)*second has exp(-2pi*i*k*d).
    # Its inverse DFT peaks at d, the previous->current displacement.
    cross = np.conj(first) * second
    magnitude = np.abs(cross)
    cross = np.divide(cross, magnitude, out=np.zeros_like(cross),
                      where=magnitude > max(float(magnitude.max()) * 1e-12, 1e-12))
    correlation = np.abs(np.fft.ifft2(cross))
    iy, ix = np.unravel_index(np.argmax(correlation), correlation.shape)
    y0, x0 = float(iy if iy <= h // 2 else iy - h), float(ix if ix <= w // 2 else ix - w)
    # Evaluate the inverse DFT only in a +/-1 pixel grid around the integer peak.
    # Separable complex exponent matrices avoid globally zero-padding the FFT.
    offsets = np.arange(-cfg["upsample_factor"], cfg["upsample_factor"] + 1) / cfg["upsample_factor"]
    ys, xs = y0 + offsets, x0 + offsets
    ey = np.exp(2j * np.pi * np.outer(ys, np.fft.fftfreq(h)))
    ex = np.exp(2j * np.pi * np.outer(np.fft.fftfreq(w), xs))
    fine = np.abs(ey @ cross @ ex) / (h * w)
    jy, jx = np.unravel_index(np.argmax(fine), fine.shape)
    shift = np.array([xs[jx], ys[jy]])
    # A guard region removes the main correlation lobe but retains repeated-pattern peaks.
    yy, xx = np.ogrid[:h, :w]
    dy = np.minimum(np.abs(yy - iy), h - np.abs(yy - iy))
    dx = np.minimum(np.abs(xx - ix), w - np.abs(xx - ix))
    sidelobes = correlation[(dx > PHASE_MAIN_LOBE_RADIUS_PX) | (dy > PHASE_MAIN_LOBE_RADIUS_PX)]
    ratio = float(correlation[iy, ix] / max(float(sidelobes.max()), 1e-12))

    # Hann windowing is useful for finite-crop edge effects, but it can suppress
    # otherwise meaningful repeated-texture peaks enough to make the scalar peak
    # ratio look unique. Retain a bounded set of the strongest *integer* phase
    # alternatives inside the configured motion gate. relative_shift validates
    # them independently using real, non-wrapped overlap below.
    shift_y = np.where(np.arange(h) <= h // 2, np.arange(h), np.arange(h) - h)[:, None]
    shift_x = np.where(np.arange(w) <= w // 2, np.arange(w), np.arange(w) - w)[None, :]
    candidate_mask = (((dx > PHASE_MAIN_LOBE_RADIUS_PX) | (dy > PHASE_MAIN_LOBE_RADIUS_PX))
                      & (np.hypot(shift_x, shift_y) <= cfg["max_shift_px"]))
    candidate_indices = np.flatnonzero(candidate_mask)
    candidate_scores = correlation.ravel()[candidate_indices]
    candidate_order = np.argsort(candidate_scores, kind="stable")[::-1][:PHASE_AMBIGUITY_CANDIDATE_COUNT]
    alternatives = []
    for order_index in candidate_order:
        flat_index = int(candidate_indices[order_index])
        candidate_y, candidate_x = np.unravel_index(flat_index, correlation.shape)
        delta_x = int(candidate_x if candidate_x <= w // 2 else candidate_x - w)
        delta_y = int(candidate_y if candidate_y <= h // 2 else candidate_y - h)
        alternatives.append((delta_x, delta_y, float(candidate_scores[order_index])))
    return shift, ratio, alternatives


def _overlap_correlation(previous: np.ndarray, current: np.ndarray, shift: np.ndarray):
    """Compare only real overlapping pixel-centre samples, without FFT wrap-around."""
    h, w = previous.shape
    yy, xx = np.mgrid[:h, :w]
    xx = xx.astype(float) - shift[0]
    yy = yy.astype(float) - shift[1]
    valid = (xx >= 0) & (yy >= 0) & (xx <= w - 1) & (yy <= h - 1)
    if valid.sum() < 16:
        return 0.0, float(valid.mean())
    reference = _bilinear(previous, xx[valid], yy[valid])
    observed = current[valid]
    reference = reference - reference.mean()
    observed = observed - observed.mean()
    norm = float(np.linalg.norm(reference) * np.linalg.norm(observed))
    score = float(reference @ observed / norm) if norm > 1e-12 else 0.0
    return float(np.clip(score, -1, 1)), float(valid.mean())


def _integer_overlap_correlation(previous: np.ndarray, current: np.ndarray, dx: int, dy: int):
    """Fast real-overlap correlation for an integer displacement candidate."""
    h, w = previous.shape
    x0, x1 = max(0, dx), min(w, w + dx)
    y0, y1 = max(0, dy), min(h, h + dy)
    if x1 <= x0 or y1 <= y0:
        return 0.0, 0.0
    reference = previous[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
    observed = current[y0:y1, x0:x1]
    if reference.size < 16:
        return 0.0, float(reference.size / (h * w))
    reference = reference - reference.mean()
    observed = observed - observed.mean()
    norm = float(np.linalg.norm(reference) * np.linalg.norm(observed))
    score = float(np.sum(reference * observed) / norm) if norm > 1e-12 else 0.0
    return float(np.clip(score, -1, 1)), float(reference.size / (h * w))


def _best_real_overlap_competitor(previous: np.ndarray, current: np.ndarray,
                                  candidates: list[tuple[int, int, float]], shift: np.ndarray):
    """Return the strongest spatially distinct phase proposal on real overlap."""
    best = None
    for dx, dy, phase_score in candidates:
        if np.linalg.norm(np.array([dx, dy], dtype=float) - shift) <= PHASE_MAIN_LOBE_RADIUS_PX:
            continue
        correlation, overlap = _integer_overlap_correlation(previous, current, dx, dy)
        candidate = {"delta_px": {"x_px": float(dx), "y_px": float(dy)},
                     "aligned_correlation": correlation, "overlap_fraction": overlap,
                     "phase_correlation": phase_score}
        if best is None or (correlation, overlap, phase_score) > (
                best["aligned_correlation"], best["overlap_fraction"], best["phase_correlation"]):
            best = candidate
    return best


def _refine_overlap_translation(previous: np.ndarray, current: np.ndarray, initial: np.ndarray):
    """Locally maximize non-wrapped overlap correlation around the phase estimate."""
    best = np.array(initial, dtype=float)
    best_score, best_overlap = _overlap_correlation(previous, current, best)
    for step in OVERLAP_REFINEMENT_STEPS_PX:
        center = best.copy()
        stage_best, stage_score, stage_overlap = best, best_score, best_overlap
        for oy in (-1, 0, 1):
            for ox in (-1, 0, 1):
                if ox == 0 and oy == 0:
                    continue
                candidate = center + [ox * step, oy * step]
                score, overlap = _overlap_correlation(previous, current, candidate)
                if score > stage_score + 1e-12:
                    stage_best, stage_score, stage_overlap = candidate, score, overlap
        best, best_score, best_overlap = stage_best, stage_score, stage_overlap
    return best, best_score, best_overlap


def relative_shift(previous_gray_patch, current_gray_patch, config=None) -> dict:
    """Measure canonical previous->current translation; crop movement is caller-owned.

    The local-DFT phase estimate is a coarse sub-pixel proposal. A deterministic
    local search then refines it against the real non-wrapped overlap so finite
    crop boundaries do not inherit the circular-translation assumption. A
    forward/backward estimate is still only a consistency diagnostic, not
    independent correctness evidence. Rotation is flagged by poor aligned
    texture correlation; a visually invariant spinning rim has no observable
    rotation. Windowed phase uniqueness is also checked against spatially
    distinct integer phase alternatives on real overlap.
    """
    cfg = _config(REGISTRATION_CONFIG, config)
    patches = (previous_gray_patch, current_gray_patch)
    for patch in patches:
        if (not isinstance(patch, np.ndarray) or patch.ndim != 2 or min(patch.shape) < 16
                or not np.issubdtype(patch.dtype, np.number) or np.iscomplexobj(patch)
                or not np.isfinite(patch).all() or np.any(patch < 0) or np.any(patch > 255)):
            return _lost("invalid_gray_patch", relative=True)
    if patches[0].shape != patches[1].shape:
        return _lost("patch_shape_mismatch", relative=True)
    previous, current = (p.astype(np.float64) for p in patches)
    if min(float(previous.std()), float(current.std())) < cfg["min_std"]:
        return _lost("insufficient_texture", relative=True)
    phase_forward = _phase_translation(previous, current, cfg)
    phase_reverse = _phase_translation(current, previous, cfg)
    # Some tests deliberately patch this private helper at the phase boundary;
    # accepting its historical two-item shape keeps those gate tests focused.
    phase_shift, peak_ratio = phase_forward[:2]
    reverse_phase_shift, reverse_peak_ratio = phase_reverse[:2]
    phase_candidates = phase_forward[2] if len(phase_forward) > 2 else []
    phase_fb_error = float(np.linalg.norm(phase_shift + reverse_phase_shift))
    shift, correlation, overlap = _refine_overlap_translation(previous, current, phase_shift)
    backward, _, _ = _refine_overlap_translation(current, previous, reverse_phase_shift)
    fb_error = float(np.linalg.norm(shift + backward))
    competitor = _best_real_overlap_competitor(previous, current, phase_candidates, shift)
    diagnostics = {"peak_ratio": peak_ratio, "reverse_peak_ratio": reverse_peak_ratio,
                   "phase_forward_backward_px": phase_fb_error,
                   "forward_backward_px": fb_error, "aligned_correlation": correlation,
                   "overlap_fraction": overlap, "rotation_model": "translation_only_correlation_gate",
                   "upsample_factor": cfg["upsample_factor"],
                   "phase_delta_px": {"x_px": float(phase_shift[0]), "y_px": float(phase_shift[1])},
                   "spatial_refinement_px": float(np.linalg.norm(shift - phase_shift)),
                   "reverse_spatial_refinement_px": float(np.linalg.norm(backward - reverse_phase_shift)),
                   "overlap_refinement_steps_px": list(OVERLAP_REFINEMENT_STEPS_PX),
                   "ambiguity_candidate_limit": PHASE_AMBIGUITY_CANDIDATE_COUNT,
                   "best_competing_real_overlap": competitor}
    if np.linalg.norm(shift) > cfg["max_shift_px"]:
        return _lost("shift_exceeds_gate", diagnostics, relative=True)
    if min(peak_ratio, reverse_peak_ratio) < cfg["min_peak_ratio"]:
        return _lost("ambiguous_correlation_peak", diagnostics, relative=True)
    if (competitor is not None
            and competitor["aligned_correlation"] >= cfg["min_correlation"]
            and competitor["overlap_fraction"] >= cfg["min_overlap_fraction"]):
        return _lost("ambiguous_real_overlap", diagnostics, relative=True)
    if max(phase_fb_error, fb_error) > cfg["max_forward_backward_px"]:
        return _lost("forward_backward_gate", diagnostics, relative=True)
    if overlap < cfg["min_overlap_fraction"]:
        return _lost("insufficient_overlap", diagnostics, relative=True)
    if correlation < cfg["min_correlation"]:
        return _lost("rotation_or_appearance_change", diagnostics, relative=True)
    confidence = correlation * min(1, 1 - 1 / peak_ratio)
    return _success(shift[0], shift[1], confidence, diagnostics, relative=True)
