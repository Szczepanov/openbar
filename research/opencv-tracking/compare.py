#!/usr/bin/env python3
"""Score OpenCV spike trackers against OpenBar's M0 trackers on the same fixtures (#57 research).

For each fixture it runs, from the fixture's manual seed to its last annotated frame:
  - `openbar-cli tracker-run` (template-sad-v1 and local-contrast-centroid-v1), and
  - registered candidate tracker producers (e.g. OpenCV CSRT, KCF, neural-network trackers, etc.),
then writes one benchmark-suite-v1 file and scores every prediction with `openbar-cli benchmark`.

Also generates:
  - comparison-summary.json: per-clip and per-tracker metrics, false-track counts, and seed-excluded metrics
  - comparison-summary.md: markdown summary tables
  - (optional) visual QA overlay images via --visual-qa

`min_confidence` is 0 for every case: confidence semantics differ per tracker, so cases are compared by
their declared tracked/lost states. Held-out validation fixtures are refused unless --allow-held-out.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TRACK = Path(__file__).with_name("track.py")
TIMESTAMP_TOLERANCE_S = 0.0005
OPENBAR_BASELINES = ("template-sad-v1", "local-contrast-centroid-v1")

sys.path.insert(0, str(ROOT / "validation" / "tools"))
import tracker_filter_selection as tfs  # noqa: E402


@dataclass(frozen=True)
class CandidateProducer:
    """Configurable candidate tracker producer that runs in its own environment."""
    name: str
    interpreter: str | Path
    script: Path
    extra_args: list[str] = field(default_factory=list)

    def run(
        self,
        manifest_path: Path,
        fixture_id: str,
        seed_path: Path,
        end_s: float,
        output_path: Path,
        allow_held_out: bool = False,
        sibling_output_path: Path | None = None,
    ) -> None:
        cmd = [
            str(self.interpreter),
            str(self.script),
            "--manifest",
            str(manifest_path),
            "--fixture",
            fixture_id,
            "--seed",
            str(seed_path),
            "--end-s",
            f"{end_s:.6f}",
            "--output",
            str(output_path),
            *self.extra_args,
        ]
        if sibling_output_path is not None:
            cmd += ["--sibling-output", str(sibling_output_path)]
        if allow_held_out:
            cmd.append("--allow-held-out")
        execute(cmd)


def mask_sibling(name: str) -> str | None:
    """The other centre method of a GPU mask candidate (centroid <-> circle), or None."""
    if not name.startswith(("sam2.1-", "cutie-base-")):
        return None
    if name.endswith("-centroid"):
        return name.removesuffix("-centroid") + "-circle"
    if name.endswith("-circle"):
        return name.removesuffix("-circle") + "-centroid"
    return None


# Default candidate registry for OpenCV trackers
CANDIDATE_REGISTRY: dict[str, CandidateProducer] = {
    "opencv-csrt": CandidateProducer(
        name="opencv-csrt",
        interpreter=sys.executable,
        script=TRACK,
        extra_args=["--tracker", "csrt"],
    ),
    "opencv-kcf": CandidateProducer(
        name="opencv-kcf",
        interpreter=sys.executable,
        script=TRACK,
        extra_args=["--tracker", "kcf"],
    ),
    "opencv-vit": CandidateProducer(
        name="opencv-vit",
        interpreter=sys.executable,
        script=TRACK,
        extra_args=["--tracker", "vit"],
    ),
    "opencv-nano": CandidateProducer(
        name="opencv-nano",
        interpreter=sys.executable,
        script=TRACK,
        extra_args=["--tracker", "nano"],
    ),
    "opencv-dasiamrpn": CandidateProducer(
        name="opencv-dasiamrpn",
        interpreter=sys.executable,
        script=TRACK,
        extra_args=["--tracker", "dasiamrpn"],
    ),
    "opencv-csrt+circle-a": CandidateProducer(
        name="opencv-csrt+circle-a",
        interpreter=sys.executable,
        script=TRACK,
        extra_args=["--tracker", "csrt+circle-a"],
    ),
    "opencv-csrt+circle-b1": CandidateProducer(
        name="opencv-csrt+circle-b1",
        interpreter=sys.executable,
        script=TRACK,
        extra_args=["--tracker", "csrt+circle-b1"],
    ),
    "opencv-csrt+circle-b5": CandidateProducer(
        name="opencv-csrt+circle-b5",
        interpreter=sys.executable,
        script=TRACK,
        extra_args=["--tracker", "csrt+circle-b5"],
    ),
    "opencv-csrt+hough": CandidateProducer(
        name="opencv-csrt+hough",
        interpreter=sys.executable,
        script=TRACK,
        extra_args=["--tracker", "csrt+hough"],
    ),
    "opencv-lk-affine": CandidateProducer(
        name="opencv-lk-affine",
        interpreter=sys.executable,
        script=TRACK,
        extra_args=["--tracker", "lk-affine"],
    ),
    "opencv-csrt+lk": CandidateProducer(
        name="opencv-csrt+lk",
        interpreter=sys.executable,
        script=TRACK,
        extra_args=["--tracker", "csrt+lk"],
    ),
    "sam2.1-small-centroid": CandidateProducer(
        name="sam2.1-small-centroid",
        interpreter=ROOT / "research" / "gpu-tracking" / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python"),
        script=ROOT / "research" / "gpu-tracking" / "track_gpu.py",
        extra_args=["--candidate", "sam2.1-small-centroid"],
    ),
    "sam2.1-small-circle": CandidateProducer(
        name="sam2.1-small-circle",
        interpreter=ROOT / "research" / "gpu-tracking" / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python"),
        script=ROOT / "research" / "gpu-tracking" / "track_gpu.py",
        extra_args=["--candidate", "sam2.1-small-circle"],
    ),
    "sam2.1-bplus-centroid": CandidateProducer(
        name="sam2.1-bplus-centroid",
        interpreter=ROOT / "research" / "gpu-tracking" / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python"),
        script=ROOT / "research" / "gpu-tracking" / "track_gpu.py",
        extra_args=["--candidate", "sam2.1-bplus-centroid"],
    ),
    "sam2.1-bplus-circle": CandidateProducer(
        name="sam2.1-bplus-circle",
        interpreter=ROOT / "research" / "gpu-tracking" / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python"),
        script=ROOT / "research" / "gpu-tracking" / "track_gpu.py",
        extra_args=["--candidate", "sam2.1-bplus-circle"],
    ),
    "cutie-base-centroid": CandidateProducer(
        name="cutie-base-centroid",
        interpreter=ROOT / "research" / "gpu-tracking" / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python"),
        script=ROOT / "research" / "gpu-tracking" / "track_gpu.py",
        extra_args=["--candidate", "cutie-base-centroid"],
    ),
    "cutie-base-circle": CandidateProducer(
        name="cutie-base-circle",
        interpreter=ROOT / "research" / "gpu-tracking" / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python"),
        script=ROOT / "research" / "gpu-tracking" / "track_gpu.py",
        extra_args=["--candidate", "cutie-base-circle"],
    ),
    "bootstapir-affine": CandidateProducer(
        name="bootstapir-affine",
        interpreter=ROOT / "research" / "gpu-tracking" / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python"),
        script=ROOT / "research" / "gpu-tracking" / "track_gpu.py",
        extra_args=["--candidate", "bootstapir-affine"],
    ),
    "cotracker3-affine": CandidateProducer(
        name="cotracker3-affine",
        interpreter=ROOT / "research" / "gpu-tracking" / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python"),
        script=ROOT / "research" / "gpu-tracking" / "track_gpu.py",
        extra_args=["--candidate", "cotracker3-affine"],
    ),
}

TRACKER_LICENSES: dict[str, str] = {
    "template-sad-v1": "PolyForm Shield 1.0.0",
    "local-contrast-centroid-v1": "PolyForm Shield 1.0.0",
    "opencv-csrt": "Apache-2.0",
    "opencv-kcf": "Apache-2.0",
    "opencv-vit": "Apache-2.0",
    "opencv-dasiamrpn": "MIT",
    "opencv-nano": "Unconfirmed / all rights reserved (not shippable without confirmation)",
    "opencv-csrt+circle-a": "Apache-2.0",
    "opencv-csrt+circle-b1": "Apache-2.0",
    "opencv-csrt+circle-b5": "Apache-2.0",
    "opencv-csrt+hough": "Apache-2.0",
    "opencv-lk-affine": "Apache-2.0",
    "opencv-csrt+lk": "Apache-2.0",
    "sam2.1-small-centroid": "Apache-2.0 / Apache-2.0",
    "sam2.1-small-circle": "Apache-2.0 / Apache-2.0",
    "sam2.1-bplus-centroid": "Apache-2.0 / Apache-2.0",
    "sam2.1-bplus-circle": "Apache-2.0 / Apache-2.0",
    "cutie-base-centroid": "MIT / unconfirmed weights",
    "cutie-base-circle": "MIT / unconfirmed weights",
    "bootstapir-affine": "Apache-2.0 / Apache-2.0",
    "cotracker3-affine": "CC-BY-NC-4.0 / CC-BY-NC-4.0",
}


def fixture_inputs(
    manifest_path: Path,
    fixture_id: str,
    allow_held_out: bool,
) -> tuple[Path, Path, float, float, bool]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fixture = next((f for f in manifest["fixtures"] if f["id"] == fixture_id), None)
    if fixture is None:
        raise SystemExit(f"error: {fixture_id} is not in {manifest_path}")
    if fixture["purpose"] == "validation" and not allow_held_out:
        raise SystemExit(f"error: {fixture_id} is a held-out validation fixture")
    annotations = manifest_path.parent / "annotations" / f"{fixture_id}.annotation-v1.json"
    seed = manifest_path.parent / "seeds" / f"{fixture_id}.manual-target-seed-v1.json"
    for path in (annotations, seed):
        if not path.is_file():
            raise SystemExit(f"error: missing {path}")
    seed_s = json.loads(seed.read_text(encoding="utf-8"))["seed"]["timestamp_s"]
    labelled = [s["timestamp_s"] for s in json.loads(annotations.read_text(encoding="utf-8"))["samples"]
                if s["annotation_state"] == "labelled"]
    if not labelled:
        raise SystemExit(f"error: {fixture_id} has no labelled annotation samples")
    is_private = fixture.get("source", {}).get("redistribution_status") != "allowed"
    return annotations, seed, seed_s, max(labelled), is_private


def expected_prediction_paths(
    predictions_dir: Path,
    fixture_id: str,
    candidate_names: list[str],
) -> list[Path]:
    """Return exactly the prediction files that this invocation is expected to produce."""
    names = [*OPENBAR_BASELINES, *candidate_names]
    return [predictions_dir / f"{fixture_id}.{name}.prediction-v1.json" for name in names]


def require_prediction_files(paths: list[Path]) -> list[Path]:
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise SystemExit("error: expected tracker output was not produced: " + ", ".join(map(str, missing)))
    return paths


def execute(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        raise SystemExit(f"error: {' '.join(command[:4])} failed:\n{completed.stderr[-2000:]}")
    print(completed.stdout.strip() or completed.stderr.strip()[-400:])


def nearest_rank_percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    rank = math.ceil(quantile * len(s))
    return s[max(0, min(rank - 1, len(s) - 1))]


def compute_metrics(
    references: list[dict[str, Any]],
    prediction_samples: list[dict[str, Any]],
    tolerance_s: float,
    scale_mm: float | None = None,
    sidecar_samples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    matches = tfs._match_timestamped_samples(references, prediction_samples, tolerance_s)
    sidecar_by_time: dict[float, dict[str, Any]] = {}
    if sidecar_samples:
        for s in sidecar_samples:
            sidecar_by_time[round(float(s["timestamp_s"]), 4)] = s

    errors: list[float] = []
    sample_errors: dict[float, float] = {}
    avail_count = 0
    max_consec_loss = 0
    cur_consec = 0
    high_error_count = 0
    false_track_count = 0
    false_track_base_conf_count = 0

    for ref, act in matches:
        ref_t = float(ref["timestamp_s"])
        if act is not None and act.get("state") == "tracked":
            avail_count += 1
            cur_consec = 0
            center = act.get("center_px")
            if center:
                dx = float(center["x_px"]) - float(ref["center_px"]["x_px"])
                dy = float(center["y_px"]) - float(ref["center_px"]["y_px"])
                err = math.hypot(dx, dy)
                errors.append(err)
                sample_errors[ref_t] = err

                if err > 3.0:
                    high_error_count += 1
                    conf = float(act.get("confidence", 0.0))
                    if conf >= 0.8:
                        false_track_count += 1

                    sc = None
                    if sidecar_by_time:
                        sc_key = round(float(act.get("timestamp_s", ref_t)), 4)
                        sc = sidecar_by_time.get(sc_key)
                    base_conf = float(sc.get("base_confidence", conf)) if sc else conf
                    if base_conf >= 0.8:
                        false_track_base_conf_count += 1
        else:
            cur_consec += 1
            if cur_consec > max_consec_loss:
                max_consec_loss = cur_consec

    comparable = len(references)
    mae = sum(errors) / len(errors) if errors else None
    p50 = nearest_rank_percentile(errors, 0.50)
    p90 = nearest_rank_percentile(errors, 0.90)
    max_err = max(errors) if errors else None
    avail = avail_count / comparable if comparable else None

    mae_mm = mae * scale_mm if (mae is not None and scale_mm is not None) else None
    p50_mm = p50 * scale_mm if (p50 is not None and scale_mm is not None) else None
    p90_mm = p90 * scale_mm if (p90 is not None and scale_mm is not None) else None
    max_mm = max_err * scale_mm if (max_err is not None and scale_mm is not None) else None

    return {
        "comparable_samples": comparable,
        "tracked_samples": avail_count,
        "lost_samples": comparable - avail_count,
        "availability": avail,
        "mae_px": mae,
        "p50_px": p50,
        "p90_px": p90,
        "max_px": max_err,
        "mae_mm_nominal": mae_mm,
        "p50_mm_nominal": p50_mm,
        "p90_mm_nominal": p90_mm,
        "max_mm_nominal": max_mm,
        "max_consecutive_loss": max_consec_loss,
        "high_error_count": high_error_count,
        "false_track_count": false_track_count,
        "false_track_base_conf_count": false_track_base_conf_count,
        "sample_errors": sample_errors,
    }


def generate_summary(
    manifest_path: Path,
    fixtures: list[str],
    predictions_base_dir: Path,
    candidate_names: list[str],
) -> tuple[dict[str, Any], str]:
    """Generate structured summary JSON and markdown tables including seed-excluded metrics."""
    summary_fixtures = []

    for fid in fixtures:
        ann_path = manifest_path.parent / "annotations" / f"{fid}.annotation-v1.json"
        annotation = json.loads(ann_path.read_text(encoding="utf-8"))
        seed_path = manifest_path.parent / "seeds" / f"{fid}.manual-target-seed-v1.json"
        seed_doc = json.loads(seed_path.read_text(encoding="utf-8"))
        seed_s = seed_doc["seed"]["timestamp_s"]
        r_seed_px = float(seed_doc["seed"]["target"]["radius_px"])
        scale_mm = 225.0 / r_seed_px  # nominal 450 mm plate radius 225 mm

        tol = float(annotation.get("timebase", {}).get("decoder_match_tolerance_s", TIMESTAMP_TOLERANCE_S))
        all_refs = [
            s for s in annotation.get("samples", [])
            if s.get("annotation_state") == "labelled" and s.get("quality") != "unusable"
        ]
        no_seed_refs = [
            s for s in all_refs
            if abs(float(s["timestamp_s"]) - seed_s) > tol
        ]

        predictions_dir = predictions_base_dir / fid
        pred_files = require_prediction_files(
            expected_prediction_paths(predictions_dir, fid, candidate_names)
        )
        tracker_summaries = []

        for p_file in pred_files:
            pred = json.loads(p_file.read_text(encoding="utf-8"))
            t_name = pred.get("implementation", {}).get("name") or p_file.stem.removeprefix(f"{fid}.").removesuffix(".prediction-v1")
            t_license = (
                pred.get("implementation", {}).get("config", {}).get("license")
                or TRACKER_LICENSES.get(t_name, "unknown")
            )
            runtime_s = pred.get("runtime", {}).get("processing_wall_s")
            peak_gpu_mem_mb = pred.get("implementation", {}).get("config", {}).get("peak_gpu_memory_mb")
            pred_samples = pred.get("samples", [])

            # Check for geometry sidecar
            sidecar_file = predictions_dir / f"{fid}.{t_name}.geometry.json"
            if not sidecar_file.is_file():
                alt_sidecar = p_file.with_name(p_file.name.replace(".prediction-v1.json", ".geometry.json"))
                if alt_sidecar.is_file():
                    sidecar_file = alt_sidecar

            sidecar_samples = None
            sidecar_summary = None
            if sidecar_file.is_file():
                sc_doc = json.loads(sidecar_file.read_text(encoding="utf-8"))
                sidecar_samples = sc_doc.get("samples", [])
                fit_attempted = sum(1 for s in sidecar_samples if s.get("fit_attempted"))
                # The seed entry is marked accepted without a fit attempt; count accepted fits only.
                fit_accepted = sum(1 for s in sidecar_samples if s.get("fit_attempted") and s.get("accepted"))
                overall_rate = fit_accepted / fit_attempted if fit_attempted else 0.0

                # Match sidecar samples to labelled annotations
                lab_attempted = 0
                lab_accepted = 0
                for ref in all_refs:
                    rt = float(ref["timestamp_s"])
                    matched_sc = next(
                        (s for s in sidecar_samples if abs(float(s["timestamp_s"]) - rt) <= tol),
                        None,
                    )
                    if matched_sc and matched_sc.get("fit_attempted"):
                        lab_attempted += 1
                        if matched_sc.get("accepted"):
                            lab_accepted += 1
                lab_rate = lab_accepted / lab_attempted if lab_attempted else 0.0

                sidecar_summary = {
                    "overall_fit_attempted": fit_attempted,
                    "overall_fit_accepted": fit_accepted,
                    "overall_acceptance_rate": overall_rate,
                    "labelled_fit_attempted": lab_attempted,
                    "labelled_fit_accepted": lab_accepted,
                    "labelled_acceptance_rate": lab_rate,
                }

            m_all = compute_metrics(all_refs, pred_samples, tol, scale_mm, sidecar_samples)
            m_no_seed = compute_metrics(no_seed_refs, pred_samples, tol, scale_mm, sidecar_samples)

            tracker_summaries.append({
                "tracker": t_name,
                "license": t_license,
                "runtime_s": runtime_s,
                "peak_gpu_memory_mb": peak_gpu_mem_mb,
                "all_samples": m_all,
                "seed_excluded": m_no_seed,
                "geometry_sidecar": sidecar_summary,
            })

        # Find CSRT baseline for paired comparisons and runtime overhead
        csrt_t = next((t for t in tracker_summaries if t["tracker"] == "opencv-csrt"), None)
        csrt_errors = csrt_t["seed_excluded"]["sample_errors"] if csrt_t else {}
        csrt_runtime_s = csrt_t.get("runtime_s") if csrt_t else None

        for t in tracker_summaries:
            t["runtime_overhead_vs_csrt"] = (
                t["runtime_s"] / csrt_runtime_s if (t.get("runtime_s") and csrt_runtime_s) else None
            )
            cand_errors = t["seed_excluded"]["sample_errors"]
            common_ts = sorted(set(cand_errors.keys()) & set(csrt_errors.keys()))
            if common_ts:
                deltas = [cand_errors[ts] - csrt_errors[ts] for ts in common_ts]
                improved = sum(1 for d in deltas if d < -0.001)
                worsened = sum(1 for d in deltas if d > 0.001)
                tied = sum(1 for d in deltas if abs(d) <= 0.001)
                med_delta_px = nearest_rank_percentile(deltas, 0.50)
                med_delta_mm = med_delta_px * scale_mm if med_delta_px is not None else None
                t["paired_vs_csrt"] = {
                    "compared_labels": len(common_ts),
                    "improved": improved,
                    "worsened": worsened,
                    "tied": tied,
                    "median_delta_px": med_delta_px,
                    "median_delta_mm": med_delta_mm,
                }
            else:
                t["paired_vs_csrt"] = None

        summary_fixtures.append({
            "fixture_id": fid,
            "seed_timestamp_s": seed_s,
            "seed_radius_px": r_seed_px,
            "scale_mm_per_px": scale_mm,
            "total_labelled_samples": len(all_refs),
            "trackers": tracker_summaries,
        })

    summary_doc = {
        "schema_version": 1,
        "summary_version": "m0-tracker-comparison-summary-v1",
        "fixtures": summary_fixtures,
    }

    # Format Markdown tables
    md_lines = [
        "# Tracker Comparison Summary",
        "",
        "## 1. Overall Results (All labelled frames)",
        "",
        "| Clip | Tracker | License | Availability | MAE px | p50 px | p90 px | Max px | Max Loss | False Tracks |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for f in summary_fixtures:
        for t in f["trackers"]:
            m = t["all_samples"]
            avail_str = f"{m['availability']*100:.1f} %" if m['availability'] is not None else "N/A"
            mae_str = f"{m['mae_px']:.1f}" if m['mae_px'] is not None else "N/A"
            p50_str = f"{m['p50_px']:.1f}" if m['p50_px'] is not None else "N/A"
            p90_str = f"{m['p90_px']:.1f}" if m['p90_px'] is not None else "N/A"
            max_str = f"{m['max_px']:.1f}" if m['max_px'] is not None else "N/A"
            md_lines.append(
                f"| `{f['fixture_id']}` | {t['tracker']} | {t['license']} | {avail_str} | {mae_str} | {p50_str} | {p90_str} | {max_str} | {m['max_consecutive_loss']} | {m['false_track_count']} |"
            )

    md_lines.extend([
        "",
        "## 2. Seed-Excluded Results (Fair tracking performance & #57 Gates)",
        "",
        "| Clip | Tracker | License | Availability | MAE px | MAE mm (nom) | p90 px | p90 mm (nom) | Max px | False Tracks | FT (Base Conf) | High Errors (>3px) |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for f in summary_fixtures:
        for t in f["trackers"]:
            m = t["seed_excluded"]
            avail_str = f"{m['availability']*100:.1f} %" if m['availability'] is not None else "N/A"
            mae_str = f"{m['mae_px']:.2f}" if m['mae_px'] is not None else "N/A"
            mae_mm_str = f"{m['mae_mm_nominal']:.2f}" if m.get('mae_mm_nominal') is not None else "N/A"
            p90_str = f"{m['p90_px']:.2f}" if m['p90_px'] is not None else "N/A"
            p90_mm_str = f"{m['p90_mm_nominal']:.2f}" if m.get('p90_mm_nominal') is not None else "N/A"
            max_str = f"{m['max_px']:.2f}" if m['max_px'] is not None else "N/A"
            ft_base_str = str(m.get('false_track_base_conf_count', m['false_track_count']))
            high_err_str = str(m.get('high_error_count', 'N/A'))
            md_lines.append(
                f"| `{f['fixture_id']}` | {t['tracker']} | {t['license']} | {avail_str} | {mae_str} | {mae_mm_str} | {p90_str} | {p90_mm_str} | {max_str} | {m['false_track_count']} | {ft_base_str} | {high_err_str} |"
            )

    md_lines.extend([
        "",
        "## 3. Paired Comparison vs CSRT (Seed-excluded labels)",
        "",
        "| Clip | Candidate | Compared | Improved | Worsened | Tied | Median Delta px | Median Delta mm (nom) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for f in summary_fixtures:
        for t in f["trackers"]:
            if t["tracker"] in ("opencv-csrt", "template-sad-v1", "local-contrast-centroid-v1"):
                continue
            p = t.get("paired_vs_csrt")
            if p:
                d_px_str = f"{p['median_delta_px']:+.3f}" if p['median_delta_px'] is not None else "N/A"
                d_mm_str = f"{p['median_delta_mm']:+.3f}" if p['median_delta_mm'] is not None else "N/A"
                md_lines.append(
                    f"| `{f['fixture_id']}` | {t['tracker']} | {p['compared_labels']} | {p['improved']} | {p['worsened']} | {p['tied']} | {d_px_str} | {d_mm_str} |"
                )

    md_lines.extend([
        "",
        "## 4. Geometry Fit Acceptance & Runtime Overhead",
        "",
        "| Clip | Candidate | Overall Acceptance | Labelled Acceptance | Runtime s | vs CSRT | Peak GPU MB |",
        "|---|---|---:|---:|---:|---:|---:|",
    ])
    for f in summary_fixtures:
        for t in f["trackers"]:
            sc = t.get("geometry_sidecar")
            if sc:
                oa_str = f"{sc['overall_acceptance_rate']*100:.1f} % ({sc['overall_fit_accepted']}/{sc['overall_fit_attempted']})"
                la_str = f"{sc['labelled_acceptance_rate']*100:.1f} % ({sc['labelled_fit_accepted']}/{sc['labelled_fit_attempted']})"
            else:
                oa_str = "N/A"
                la_str = "N/A"
            rt_str = f"{t['runtime_s']:.2f} s" if t.get('runtime_s') is not None else "N/A"
            ovh_str = f"{t['runtime_overhead_vs_csrt']:.2f}x" if t.get('runtime_overhead_vs_csrt') is not None else "1.00x"
            peak_str = f"{t['peak_gpu_memory_mb']:.1f} MB" if t.get('peak_gpu_memory_mb') is not None else "N/A"
            md_lines.append(
                f"| `{f['fixture_id']}` | {t['tracker']} | {oa_str} | {la_str} | {rt_str} | {ovh_str} | {peak_str} |"
            )

    md_lines.extend([
        "",
        "## 5. Seed Impact (MAE Difference)",
        "",
        "| Clip | Tracker | License | MAE All px | MAE No-Seed px | Delta px | Delta % |",
        "|---|---|---|---:|---:|---:|---:|",
    ])
    for f in summary_fixtures:
        for t in f["trackers"]:
            m_all = t["all_samples"]
            m_no = t["seed_excluded"]
            if m_all["mae_px"] is not None and m_no["mae_px"] is not None:
                delta = m_no["mae_px"] - m_all["mae_px"]
                pct = (delta / m_all["mae_px"]) * 100 if m_all["mae_px"] > 0 else 0.0
                md_lines.append(
                    f"| `{f['fixture_id']}` | {t['tracker']} | {t['license']} | {m_all['mae_px']:.2f} | {m_no['mae_px']:.2f} | +{delta:.2f} | +{pct:.1f} % |"
                )

    md_lines.append("")
    return summary_doc, "\n".join(md_lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--fixture", action="append", required=True, dest="fixtures")
    parser.add_argument("--candidate", action="append", dest="candidates", help="specific candidates to run (default: all registered)")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--visual-qa", action="store_true", help="generate tiled visual QA overlays per clip")
    parser.add_argument("--allow-held-out", action="store_true")
    args = parser.parse_args(argv)

    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    selected_producers: list[CandidateProducer] = []
    if args.candidates:
        for c in args.candidates:
            if c not in CANDIDATE_REGISTRY:
                raise SystemExit(f"error: unknown candidate {c!r}; registered: {list(CANDIDATE_REGISTRY)}")
            selected_producers.append(CANDIDATE_REGISTRY[c])
    else:
        selected_producers = list(CANDIDATE_REGISTRY.values())

    if len(set(args.fixtures)) != len(args.fixtures):
        raise SystemExit("error: --fixture values must be unique")
    candidate_names = [producer.name for producer in selected_producers]
    if len(set(candidate_names)) != len(candidate_names):
        raise SystemExit("error: --candidate values must be unique")

    cases = []
    for fixture_id in args.fixtures:
        annotations, seed, seed_s, end_s, is_private = fixture_inputs(
            args.manifest, fixture_id, args.allow_held_out
        )
        start_s = max(0.0, seed_s - TIMESTAMP_TOLERANCE_S)
        end_s_tolerant = end_s + TIMESTAMP_TOLERANCE_S
        predictions_dir = out / fixture_id

        # Always run OpenBar baseline trackers
        execute(["cargo", "run", "--locked", "--release", "-q", "-p", "openbar-cli", "--", "tracker-run",
                 "--manifest", str(args.manifest), "--fixture", fixture_id, "--seed", str(seed),
                 "--start-s", f"{start_s:.6f}", "--end-s", f"{end_s_tolerant:.6f}", "--output-dir", str(predictions_dir)])

        # Run registered candidate producers. When both centre methods of a mask model are selected, the
        # model runs once and writes both predictions from the same masks.
        produced: set[str] = set()
        for producer in selected_producers:
            if producer.name in produced:
                continue
            output_file = predictions_dir / f"{fixture_id}.{producer.name}.prediction-v1.json"
            sibling = mask_sibling(producer.name)
            sibling_file = None
            if sibling in candidate_names:
                sibling_file = predictions_dir / f"{fixture_id}.{sibling}.prediction-v1.json"
                produced.add(sibling)
            producer.run(
                manifest_path=args.manifest,
                fixture_id=fixture_id,
                seed_path=seed,
                end_s=end_s_tolerant,
                output_path=output_file,
                allow_held_out=args.allow_held_out,
                sibling_output_path=sibling_file,
            )
            produced.add(producer.name)

        # Collect only outputs expected from this invocation. Reusing an output directory must not
        # silently pull stale predictions from older candidate sets into the benchmark.
        prediction_files = require_prediction_files(
            expected_prediction_paths(predictions_dir, fixture_id, candidate_names)
        )
        execute([
            sys.executable,
            str(ROOT / "validation" / "tools" / "schema_check.py"),
            "--schema",
            str(ROOT / "validation" / "schema" / "tracker-prediction-v1.schema.json"),
            *map(str, prediction_files),
        ])
        for prediction in prediction_files:
            name = prediction.name.removeprefix(f"{fixture_id}.").removesuffix(".prediction-v1.json")
            cases.append({
                "id": f"{fixture_id}.{name}",
                "fixture_manifest": str(args.manifest.resolve()),
                "fixture_id": fixture_id,
                "annotations": str(annotations.resolve()),
                "manual_seed": str(seed.resolve()),
                "predictions": str(prediction),
                "selected_range_s": {"start_s": round(start_s, 6), "end_s": round(end_s_tolerant, 6)},
                "timestamp_tolerance_s": TIMESTAMP_TOLERANCE_S,
                "min_confidence": 0,
            })

        # Optionally generate visual QA
        if args.visual_qa:
            qa_output = (
                ROOT / "validation" / "private" / "diagnostics" / "visual-qa"
                / f"{fixture_id}.visual-qa.png"
                if is_private
                else out / f"{fixture_id}.visual-qa.png"
            )
            qa_cmd = [
                sys.executable,
                str(Path(__file__).with_name("visual_qa.py")),
                "--manifest", str(args.manifest),
                "--fixture", fixture_id,
                "--predictions-dir", str(predictions_dir),
                "--output", str(qa_output),
            ]
            for prediction in prediction_files:
                qa_cmd.extend(["--prediction", str(prediction)])
            if args.allow_held_out:
                qa_cmd.append("--allow-held-out")
            execute(qa_cmd)

    suite = out / "comparison.benchmark-v1.json"
    suite.write_text(json.dumps({"schema_version": 1, "pipeline_version": "m0-benchmark-v1", "cases": cases},
                                indent=2) + "\n", encoding="utf-8")
    benchmark_res = out / "comparison.benchmark-result-v1.json"
    execute(["cargo", "run", "--locked", "--release", "-q", "-p", "openbar-cli", "--", "benchmark",
             "--suite", str(suite), "--output", str(benchmark_res)])

    # Generate summary JSON and Markdown tables
    summary_doc, summary_md = generate_summary(args.manifest, args.fixtures, out, candidate_names)
    (out / "comparison-summary.json").write_text(json.dumps(summary_doc, indent=2) + "\n", encoding="utf-8")
    (out / "comparison-summary.md").write_text(summary_md, encoding="utf-8")

    print("\n" + summary_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
