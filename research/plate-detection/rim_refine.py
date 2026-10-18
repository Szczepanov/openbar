#!/usr/bin/env python3
"""Deterministic full-resolution sub-pixel rim circle refinement (#249, #233, #57, #88).

Given a full-resolution decoded frame (BGR or grayscale) and a coarse circle prior (from a 256x256
upscaled tracker mask ``geometry.circle_from_mask`` or a detector bounding box
``geometry.circle_from_box``), samples radial intensity gradients in a narrow annulus around the
prior rim, localizes sub-pixel gradient peaks via parabolic interpolation, and fits an inlier circle
with seeded RANSAC + Huber-weighted least squares.

Measurement invariants:
- ADR-0007 pixel-centre coordinates: integer ``(i, j)`` is the centre of pixel ``(i, j)``.
- Raw is never replaced: ``refine_circle`` returns both ``raw`` and ``refined`` circles, and
  ``refine_candidate_row`` keeps ``raw_circle`` alongside any refined coordinates.
- Explicit fallback: when angular support (``arc_coverage``) or inlier count is insufficient,
  ``used_refined`` is ``False`` and the prior is returned unchanged with an explicit ``reason``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import geometry
import numpy as np

RULE = "rim-subpixel-v1"
DEFAULT_BAND_FRACTION = 0.12
MIN_BAND_PX = 4.0
DEFAULT_NUM_ANGLES = 72
RADIAL_STEP_PX = 0.5
MIN_GRADIENT_PX = 5.0
MIN_ARC_COVERAGE = 0.45
MAX_FIT_RMS_PX = 2.5
BORDER_MARGIN_PX = 2.0
HUBER_DELTA_PX = 1.0
HUBER_ITERATIONS = 3


@dataclass(frozen=True)
class RefinementResult:
    refined: geometry.Circle
    raw: geometry.Circle
    used_refined: bool
    reason: str
    inlier_count: int


def _to_smoothed_luma(image: np.ndarray) -> np.ndarray:
    """Convert 2D grayscale or 3D BGR uint8/float image to a 3x3 binomial-smoothed float64 luma grid."""
    if image.ndim == 2:
        luma = image.astype(np.float64)
    elif image.ndim == 3 and image.shape[2] >= 3:
        b = image[:, :, 0].astype(np.float64)
        g = image[:, :, 1].astype(np.float64)
        r = image[:, :, 2].astype(np.float64)
        luma = 0.114 * b + 0.587 * g + 0.299 * r
    else:
        raise ValueError(f"unsupported image shape {image.shape!r}")
    if luma.shape[0] < 5 or luma.shape[1] < 5:
        raise ValueError(f"image too small for rim refinement: {luma.shape!r}")
    # Separable [0.25, 0.5, 0.25] zero-phase smoothing preserves edge centres to sub-pixel precision.
    padded_x = np.pad(luma, ((0, 0), (1, 1)), mode="edge")
    smooth_x = 0.25 * padded_x[:, :-2] + 0.50 * padded_x[:, 1:-1] + 0.25 * padded_x[:, 2:]
    padded_y = np.pad(smooth_x, ((1, 1), (0, 0)), mode="edge")
    return 0.25 * padded_y[:-2, :] + 0.50 * padded_y[1:-1, :] + 0.25 * padded_y[2:, :]


def _sample_bilinear(grid: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Bilinear interpolation at continuous pixel-centre coordinates (xs, ys)."""
    h, w = grid.shape
    x0 = np.floor(xs).astype(np.int64)
    y0 = np.floor(ys).astype(np.int64)
    x1 = np.clip(x0 + 1, 0, w - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)
    x0_c = np.clip(x0, 0, w - 1)
    y0_c = np.clip(y0, 0, h - 1)
    wx = xs - x0
    wy = ys - y0
    v00 = grid[y0_c, x0_c]
    v10 = grid[y0_c, x1]
    v01 = grid[y1, x0_c]
    v11 = grid[y1, x1]
    return (1.0 - wy) * ((1.0 - wx) * v00 + wx * v10) + wy * ((1.0 - wx) * v01 + wx * v11)


def _fit_circle_huber(
    xs: np.ndarray,
    ys: np.ndarray,
    init_cx: float,
    init_cy: float,
    init_r: float,
) -> tuple[float, float, float]:
    """Iteratively reweighted algebraic circle fit using Huber weights on radial residuals."""
    cx, cy, r = init_cx, init_cy, init_r
    for _ in range(HUBER_ITERATIONS):
        residuals = np.hypot(xs - cx, ys - cy) - r
        abs_res = np.abs(residuals)
        weights = np.where(abs_res <= HUBER_DELTA_PX, 1.0, HUBER_DELTA_PX / np.maximum(abs_res, 1e-9))
        sqrt_w = np.sqrt(weights)
        a = np.column_stack([xs * sqrt_w, ys * sqrt_w, sqrt_w])
        b = (xs * xs + ys * ys) * sqrt_w
        solution, _, rank, _ = np.linalg.lstsq(a, b, rcond=None)
        if rank < 3:
            break
        new_cx = float(solution[0] / 2.0)
        new_cy = float(solution[1] / 2.0)
        r_sq = float(solution[2]) + new_cx * new_cx + new_cy * new_cy
        if not math.isfinite(r_sq) or r_sq <= 0.0:
            break
        cx, cy, r = new_cx, new_cy, math.sqrt(r_sq)
    return cx, cy, r


def refine_circle(
    image: np.ndarray,
    prior: geometry.Circle,
    *,
    band_fraction: float = DEFAULT_BAND_FRACTION,
    num_angles: int = DEFAULT_NUM_ANGLES,
    min_gradient_px: float = MIN_GRADIENT_PX,
    min_arc_coverage: float = MIN_ARC_COVERAGE,
    max_fit_rms_px: float = MAX_FIT_RMS_PX,
) -> RefinementResult:
    """Refine ``prior`` on ``image`` using sub-pixel radial gradient peaks in an annulus around ``prior.radius_px``."""
    for name, val in (
        ("center_x_px", prior.center_x_px),
        ("center_y_px", prior.center_y_px),
        ("radius_px", prior.radius_px),
        ("band_fraction", band_fraction),
        ("min_gradient_px", min_gradient_px),
        ("min_arc_coverage", min_arc_coverage),
        ("max_fit_rms_px", max_fit_rms_px),
    ):
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            raise ValueError(f"{name} must be finite")
    if prior.radius_px < 6.0:
        return RefinementResult(prior, prior, False, "radius_too_small", 0)
    if band_fraction <= 0.0 or band_fraction >= 0.5:
        raise ValueError("band_fraction must be in (0, 0.5)")
    if num_angles < geometry.MIN_FIT_POINTS:
        raise ValueError(f"num_angles must be at least {geometry.MIN_FIT_POINTS}")

    luma = _to_smoothed_luma(image)
    h, w = luma.shape
    cx0, cy0, r0 = float(prior.center_x_px), float(prior.center_y_px), float(prior.radius_px)

    half_band = max(MIN_BAND_PX, band_fraction * r0)
    r_min = max(2.0, r0 - half_band)
    r_max = r0 + half_band
    num_steps = int(round((r_max - r_min) / RADIAL_STEP_PX)) + 1
    if num_steps < 5:
        return RefinementResult(prior, prior, False, "annulus_too_narrow", 0)

    radii = np.linspace(r_min, r_max, num_steps, dtype=np.float64)
    step = float(radii[1] - radii[0])
    angles = np.linspace(0.0, 2.0 * math.pi, num_angles, endpoint=False, dtype=np.float64)
    cos_a = np.cos(angles)[:, None]
    sin_a = np.sin(angles)[:, None]

    xs_grid = cx0 + radii[None, :] * cos_a
    ys_grid = cy0 + radii[None, :] * sin_a

    in_bounds = (
        (xs_grid >= BORDER_MARGIN_PX)
        & (xs_grid <= (w - 1.0 - BORDER_MARGIN_PX))
        & (ys_grid >= BORDER_MARGIN_PX)
        & (ys_grid <= (h - 1.0 - BORDER_MARGIN_PX))
    )
    valid_rays = np.all(in_bounds, axis=1)
    if int(np.count_nonzero(valid_rays)) < geometry.MIN_FIT_POINTS:
        return RefinementResult(prior, prior, False, "insufficient_in_frame_rays", 0)

    profiles = _sample_bilinear(luma, xs_grid, ys_grid)
    # Central difference dI/dr on interior radial samples [1 .. num_steps - 2]
    grad = (profiles[:, 2:] - profiles[:, :-2]) / (2.0 * step)
    abs_grad = np.abs(grad)
    interior_radii = radii[1:-1]
    # Gentle proximity weight toward r0 so outer rim beats a distant background edge of equal strength
    sigma_r = max(MIN_BAND_PX, 0.75 * half_band)
    proximity = np.exp(-0.5 * ((interior_radii - r0) / sigma_r) ** 2)[None, :]
    weighted_grad = abs_grad * proximity

    peak_xs: list[float] = []
    peak_ys: list[float] = []
    for ray_idx in np.flatnonzero(valid_rays):
        row_w = weighted_grad[ray_idx]
        row_g = abs_grad[ray_idx]
        best_m = int(np.argmax(row_w))
        if row_g[best_m] < min_gradient_px:
            continue
        # Sub-pixel parabolic refinement on raw gradient magnitude |dI/dr| around best_m
        delta = 0.0
        if 0 < best_m < (row_g.size - 1):
            g_left = float(row_g[best_m - 1])
            g_mid = float(row_g[best_m])
            g_right = float(row_g[best_m + 1])
            denom = g_left - 2.0 * g_mid + g_right
            if denom < -1e-9:
                delta = float(np.clip(0.5 * (g_left - g_right) / denom, -0.5, 0.5))
        r_peak = float(interior_radii[best_m]) + delta * step
        theta = float(angles[ray_idx])
        peak_xs.append(cx0 + r_peak * math.cos(theta))
        peak_ys.append(cy0 + r_peak * math.sin(theta))

    if len(peak_xs) < geometry.MIN_FIT_POINTS:
        return RefinementResult(prior, prior, False, "insufficient_gradient_peaks", len(peak_xs))

    xs_arr = np.asarray(peak_xs, dtype=np.float64)
    ys_arr = np.asarray(peak_ys, dtype=np.float64)
    keep = geometry.ransac_inliers(xs_arr, ys_arr)
    inlier_count = int(np.count_nonzero(keep))
    if inlier_count < geometry.MIN_FIT_POINTS:
        return RefinementResult(prior, prior, False, "insufficient_ransac_inliers", inlier_count)

    in_xs = xs_arr[keep]
    in_ys = ys_arr[keep]
    try:
        cx_ls, cy_ls, r_ls = geometry.fit_circle(in_xs, in_ys)
        cx, cy, radius = _fit_circle_huber(in_xs, in_ys, cx_ls, cy_ls, r_ls)
    except ValueError:
        return RefinementResult(prior, prior, False, "degenerate_circle_fit", inlier_count)

    if not all(math.isfinite(v) for v in (cx, cy, radius)) or radius <= 0.0:
        return RefinementResult(prior, prior, False, "non_finite_fit", inlier_count)

    rms = float(np.sqrt(np.mean((np.hypot(in_xs - cx, in_ys - cy) - radius) ** 2)))
    cov = geometry.arc_coverage(in_xs, in_ys, cx, cy)
    if cov < min_arc_coverage:
        return RefinementResult(prior, prior, False, "low_arc_coverage", inlier_count)
    if rms > max_fit_rms_px:
        return RefinementResult(prior, prior, False, "high_fit_rms", inlier_count)
    if math.hypot(cx - cx0, cy - cy0) > half_band:
        return RefinementResult(prior, prior, False, "centre_shift_exceeded", inlier_count)
    if abs(radius - r0) > half_band:
        return RefinementResult(prior, prior, False, "radius_shift_exceeded", inlier_count)

    refined = geometry.Circle(
        center_x_px=cx,
        center_y_px=cy,
        radius_px=radius,
        fit="rim_subpixel",
        fit_rms_px=rms,
        arc_coverage=cov,
    )
    return RefinementResult(refined, prior, True, "accepted", inlier_count)


def refine_candidate_row(
    image: np.ndarray,
    row: dict[str, Any],
    *,
    band_fraction: float = DEFAULT_BAND_FRACTION,
) -> dict[str, Any]:
    """Return a copy of ``row`` with sub-pixel rim refinement applied, preserving raw geometry in ``raw_circle``."""
    raw_source = row.get("raw_circle") or row
    prior = geometry.Circle(
        center_x_px=float(raw_source["center_x_px"]),
        center_y_px=float(raw_source["center_y_px"]),
        radius_px=float(raw_source["radius_px"]),
        fit=str(raw_source.get("fit", "unknown")),
        fit_rms_px=None if raw_source.get("fit_rms_px") is None else float(raw_source["fit_rms_px"]),
        arc_coverage=None if raw_source.get("arc_coverage") is None else float(raw_source["arc_coverage"]),
    )
    res = refine_circle(image, prior, band_fraction=band_fraction)
    raw_meta = row.get("raw_circle") or {
        "center_x_px": round(prior.center_x_px, 3),
        "center_y_px": round(prior.center_y_px, 3),
        "radius_px": round(prior.radius_px, 3),
        "fit": prior.fit,
        "fit_rms_px": prior.fit_rms_px,
        "arc_coverage": prior.arc_coverage,
    }
    c = res.refined
    return {
        **row,
        "center_x_px": round(c.center_x_px, 3),
        "center_y_px": round(c.center_y_px, 3),
        "radius_px": round(c.radius_px, 3),
        "fit": c.fit,
        "fit_rms_px": None if c.fit_rms_px is None else round(c.fit_rms_px, 3),
        "arc_coverage": None if c.arc_coverage is None else round(c.arc_coverage, 4),
        "raw_circle": raw_meta,
        "rim_refinement": {
            "rule": RULE,
            "used_refined": res.used_refined,
            "reason": res.reason,
            "inlier_count": res.inlier_count,
        },
    }


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))], 4)


def _summarize_errors(rows: list[dict[str, Any]]) -> dict[str, Any]:
    raw_c = [r["raw_centre_error_rel"] for r in rows]
    ref_c = [r["refined_centre_error_rel"] for r in rows]
    raw_r = [r["raw_radius_abs_error_rel"] for r in rows]
    ref_r = [r["refined_radius_abs_error_rel"] for r in rows]
    used = sum(1 for r in rows if r["used_refined"])
    return {
        "frames": len(rows),
        "used_refined_frames": used,
        "raw_centre_error_rel_median": _quantile(raw_c, 0.5),
        "raw_centre_error_rel_p90": _quantile(raw_c, 0.9),
        "refined_centre_error_rel_median": _quantile(ref_c, 0.5),
        "refined_centre_error_rel_p90": _quantile(ref_c, 0.9),
        "raw_radius_abs_error_rel_median": _quantile(raw_r, 0.5),
        "raw_radius_abs_error_rel_p90": _quantile(raw_r, 0.9),
        "refined_radius_abs_error_rel_median": _quantile(ref_r, 0.5),
        "refined_radius_abs_error_rel_p90": _quantile(ref_r, 0.9),
    }


def evaluate_run_on_labelled_frames(
    *,
    run_dir: Any,
    repo_root: Any,
    output_path: Any,
    manifests: list[Any] | None = None,
) -> dict[str, Any]:
    """Evaluate rim-subpixel-v1 on matched labelled frames of a development track run."""
    import json
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    import cv2

    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    sys.path.insert(0, str(here.parents[1] / "validation" / "tools"))
    import clips
    import label_package
    import run_phase_a
    import score_phase_a
    import selection
    import truth

    run_dir = Path(run_dir).resolve()
    repo_root = Path(repo_root).resolve()
    output_path = Path(output_path).resolve()
    if not output_path.is_relative_to(label_package.PRIVATE_ROOT.resolve()):
        raise SystemExit(f"output must be under {label_package.PRIVATE_ROOT}")

    manifest_paths = manifests or [
        repo_root / "validation" / "private" / "manifest.json",
        repo_root / "validation" / "private" / "vbt" / "manifest.json",
    ]
    fixtures = clips.load_fixtures(manifest_paths)
    run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    for name, role in (run_json.get("clips") or {}).items():
        if name not in fixtures or clips.development_role(fixtures[name]) != role:
            raise clips.ClipRefused(f"{name}: missing fixture or development-role mismatch")

    private_root = repo_root / "validation" / "private"
    methods_report: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(dir=private_root, prefix="rim-refine-eval-") as tmp_str:
        tmp_root = Path(tmp_str)
        for method in sorted(run_json.get("methods") or {}):
            cand_dir = run_dir / "candidates" / method
            if not cand_dir.is_dir():
                continue
            all_rows: list[dict[str, Any]] = []
            by_source: dict[str, list[dict[str, Any]]] = {}
            per_clip: dict[str, Any] = {}

            for clip_id in sorted(run_json.get("clips") or {}):
                jsonl_path = cand_dir / f"{clip_id}.jsonl"
                if not jsonl_path.is_file():
                    continue
                records = score_phase_a.load_records(jsonl_path)
                points = truth.load_truth(clip_id, private_root)
                pairs = score_phase_a.labelled_frames(records, points)
                matched_pairs: list[tuple[dict[str, Any], truth.TruthPoint, selection.Candidate]] = []
                for rec, pt in pairs:
                    cands = score_phase_a.candidates_of(rec)
                    hit = score_phase_a.best_match(cands, pt)
                    if hit is not None:
                        matched_pairs.append((rec, pt, hit))
                if not matched_pairs:
                    continue

                fixture = fixtures[clip_id]
                media = repo_root / fixture["media"]["repository_path"]
                label_package.require_media_hash(fixture, media)
                probed = label_package.probe(media)
                label_package.require_fixture_probe_match(fixture, probed)
                indices = [int(rec["frame_index"]) for rec, _, _ in matched_pairs]
                pts_list = [int(rec["pts"]) for rec, _, _ in matched_pairs]
                frames_dir = tmp_root / clip_id
                paths = label_package.extract(media, indices, pts_list, frames_dir, fixture)

                clip_rows: list[dict[str, Any]] = []
                try:
                    for (rec, pt, hit), frame_path in zip(matched_pairs, paths):
                        img = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
                        if img is None:
                            raise RuntimeError(f"cannot read {frame_path}")
                        raw_row = next(
                            r
                            for r in rec["candidates"]
                            if selection.Candidate.from_dict(r) == hit
                        )
                        refined_row = refine_candidate_row(img, raw_row)
                        raw_c_err = (
                            math.hypot(
                                hit.center_x_px - pt.center_x_px,
                                hit.center_y_px - pt.center_y_px,
                            )
                            / pt.radius_px
                        )
                        raw_r_err = abs(hit.radius_px - pt.radius_px) / pt.radius_px
                        ref_c_err = (
                            math.hypot(
                                float(refined_row["center_x_px"]) - pt.center_x_px,
                                float(refined_row["center_y_px"]) - pt.center_y_px,
                            )
                            / pt.radius_px
                        )
                        ref_r_err = (
                            abs(float(refined_row["radius_px"]) - pt.radius_px) / pt.radius_px
                        )
                        entry = {
                            "fixture_id": clip_id,
                            "frame_index": int(rec["frame_index"]),
                            "truth_source": pt.source,
                            "used_refined": refined_row["rim_refinement"]["used_refined"],
                            "reason": refined_row["rim_refinement"]["reason"],
                            "raw_centre_error_rel": round(raw_c_err, 5),
                            "refined_centre_error_rel": round(ref_c_err, 5),
                            "raw_radius_abs_error_rel": round(raw_r_err, 5),
                            "refined_radius_abs_error_rel": round(ref_r_err, 5),
                        }
                        clip_rows.append(entry)
                        all_rows.append(entry)
                        by_source.setdefault(pt.source, []).append(entry)
                finally:
                    shutil.rmtree(frames_dir, ignore_errors=True)

                per_clip[clip_id] = _summarize_errors(clip_rows)

            methods_report[method] = {
                "all": _summarize_errors(all_rows),
                "by_truth_source": {
                    src: _summarize_errors(rows) for src, rows in sorted(by_source.items())
                },
                "per_clip": per_clip,
            }

    report = {
        "rule": RULE,
        "origin": "machine",
        "use": "development-evaluation-only",
        "source_run_sha256": run_phase_a.sha256_file(run_dir / "run.json"),
        "methods": methods_report,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from pathlib import Path

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--repo-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--manifest", type=Path, action="append")
    args = p.parse_args(argv)
    report = evaluate_run_on_labelled_frames(
        run_dir=args.run_dir,
        repo_root=args.repo_root,
        output_path=args.output,
        manifests=args.manifest,
    )
    print(json.dumps({m: v["all"] for m, v in report["methods"].items()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
