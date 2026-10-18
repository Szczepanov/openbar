#!/usr/bin/env python3
"""Post-track and post-redetect refusal diagnostics (#250, #57, #232).

CPU/stdlib only. Evaluates completed guarded or redetect tracks without reading human truth labels
or opening media. Splits tracks at ``redetected`` session boundaries so motion and coverage are
measured strictly within each identity epoch, never across a tracker restart.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "validation" / "tools"))

import clips  # noqa: E402
import guard  # noqa: E402
import motion_observation  # noqa: E402
from run_phase_a import sha256_file  # noqa: E402

RULE = "post-track-diagnostics-v1"
MIN_COVERAGE_FRACTION = 0.50
MAX_RESTART_JUMP_RADII = 1.50
MAX_RESTART_SPEED_RADII_PER_S = guard.MAX_SPEED_RADII_PER_S


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _seed_refused(first_record: dict[str, Any]) -> bool:
    seed = first_record.get("seed") or {}
    refusal = seed.get("refusal")
    if seed.get("refused") is True:
        return True
    if isinstance(refusal, dict) and refusal.get("refused") is True:
        return True
    if seed.get("tracked") is False:
        return True
    return False


def diagnose_clip(
    records: Sequence[dict[str, Any]],
    *,
    min_coverage_fraction: float = MIN_COVERAGE_FRACTION,
    max_restart_jump_radii: float = MAX_RESTART_JUMP_RADII,
    max_restart_speed_radii_per_s: float = MAX_RESTART_SPEED_RADII_PER_S,
) -> dict[str, Any]:
    """Compute per-epoch and clip-level coverage, motion, and restart-ambiguity diagnostics (label-free)."""
    if not records:
        raise ValueError("records must not be empty")
    if not (0.0 < min_coverage_fraction <= 1.0) or not math.isfinite(min_coverage_fraction):
        raise ValueError("min_coverage_fraction must be in (0, 1]")
    if max_restart_jump_radii <= 0.0 or not math.isfinite(max_restart_jump_radii):
        raise ValueError("max_restart_jump_radii must be positive")
    if max_restart_speed_radii_per_s <= 0.0 or not math.isfinite(max_restart_speed_radii_per_s):
        raise ValueError("max_restart_speed_radii_per_s must be positive")

    # Validate strictly increasing timestamps across the full clip first.
    last_t: float | None = None
    for rec in records:
        t = _finite(rec["time_s"], "time_s")
        if t < 0.0 or (last_t is not None and t <= last_t):
            raise ValueError("timestamps must be nonnegative and strictly increasing")
        last_t = t

    first_t = float(records[0]["time_s"])
    observed_duration_s = float(records[-1]["time_s"]) - first_t

    # Partition records into identity epochs: a new epoch starts whenever guard.status == 'redetected'.
    epoch_slices: list[list[dict[str, Any]]] = [[]]
    restart_attempts = 0
    accepted_restarts = 0
    stopped_gaps = 0
    rejected_steps = 0

    for rec in records:
        guard_info = rec.get("guard") or {}
        status = guard_info.get("status", "tracked" if rec.get("candidates") else "lost")
        pre_status = (
            guard_info.get("pre_restart_status")
            or (rec.get("redetect") or {}).get("pre_restart_guard_status")
            or status
        )
        if pre_status == "stopped_gap":
            stopped_gaps += 1
        if pre_status == "rejected_step":
            rejected_steps += 1
        if "redetect" in rec:
            restart_attempts += 1
        if status == "redetected":
            accepted_restarts += 1
            epoch_slices.append([rec])
        else:
            epoch_slices[-1].append(rec)

    seed_radius_px: float | None = None
    for rec in records:
        cands = rec.get("candidates") or []
        if cands:
            seed_radius_px = _finite(cands[0]["radius_px"], "radius_px")
            if seed_radius_px <= 0.0:
                raise ValueError("radius_px must be positive")
            break

    epochs: list[dict[str, Any]] = []
    prev_last_valid_time: float | None = None
    prev_last_valid_pos: tuple[float, float] | None = None

    for epoch_idx, epoch_records in enumerate(epoch_slices):
        if not epoch_records:
            continue
        obs = motion_observation.observe(epoch_records)
        first_valid_rec: dict[str, Any] | None = None
        last_valid_rec: dict[str, Any] | None = None
        for r in epoch_records:
            if r.get("candidates"):
                if first_valid_rec is None:
                    first_valid_rec = r
                last_valid_rec = r

        restart_diag: dict[str, Any] | None = None
        if epoch_idx >= 1 and first_valid_rec is not None:
            cand = first_valid_rec["candidates"][0]
            cx = _finite(cand["center_x_px"], "center_x_px")
            cy = _finite(cand["center_y_px"], "center_y_px")
            r_px = _finite(cand["radius_px"], "radius_px")
            ref_r = seed_radius_px or r_px
            t_restart = float(first_valid_rec["time_s"])
            redetect_meta = first_valid_rec.get("redetect") or {}
            refusal_meta = redetect_meta.get("refusal") or {}
            margin = refusal_meta.get("margin")
            if margin is not None:
                margin = round(_finite(margin, "margin"), 4)
            matching_count = len(redetect_meta.get("matching_indices") or [])

            gap_s = (
                round(t_restart - prev_last_valid_time, 4)
                if prev_last_valid_time is not None
                else None
            )
            jump_px = (
                round(math.hypot(cx - prev_last_valid_pos[0], cy - prev_last_valid_pos[1]), 3)
                if prev_last_valid_pos is not None
                else None
            )
            jump_radii = (
                round(jump_px / ref_r, 4)
                if jump_px is not None and ref_r > 0.0
                else None
            )
            jump_speed = (
                round(jump_radii / gap_s, 4)
                if jump_radii is not None and gap_s is not None and gap_s > 0.0
                else None
            )
            uncontested = margin is None
            large_jump = jump_radii is not None and jump_radii > max_restart_jump_radii
            jump_speed_exceeded = (
                jump_speed is not None and jump_speed > max_restart_speed_radii_per_s
            )
            stationary_after = obs["observation"] != "motion_present"
            ambiguous = bool(
                jump_speed_exceeded
                or (stationary_after and (uncontested or large_jump))
            )
            restart_diag = {
                "restart_frame_index": int(first_valid_rec["frame_index"]),
                "restart_time_s": round(t_restart, 4),
                "gap_s": gap_s,
                "jump_px": jump_px,
                "jump_radii": jump_radii,
                "jump_speed_radii_per_s": jump_speed,
                "matching_candidates": matching_count,
                "margin": margin,
                "uncontested_restart": uncontested,
                "large_spatial_jump": large_jump,
                "jump_speed_exceeded": jump_speed_exceeded,
                "stationary_after_restart": stationary_after,
                "ambiguous_restart": ambiguous,
            }

        if last_valid_rec is not None:
            c = last_valid_rec["candidates"][0]
            prev_last_valid_time = _finite(last_valid_rec["time_s"], "time_s")
            prev_last_valid_pos = (
                _finite(c["center_x_px"], "center_x_px"),
                _finite(c["center_y_px"], "center_y_px"),
            )

        epochs.append(
            {
                "epoch_index": epoch_idx,
                "record_count": obs["record_count"],
                "valid_record_count": obs["valid_record_count"],
                "supported_duration_s": round(float(obs["supported_duration_s"]), 4),
                "observation": obs["observation"],
                "max_segment_p02_p98_travel_radii": (
                    round(float(obs["max_segment_p02_p98_travel_radii"]), 4)
                    if obs["max_segment_p02_p98_travel_radii"] is not None
                    else None
                ),
                "restart": restart_diag,
            }
        )

    supported_duration_s = round(math.fsum(e["supported_duration_s"] for e in epochs), 4)
    coverage_fraction = (
        round(supported_duration_s / observed_duration_s, 4)
        if observed_duration_s > 0.0
        else None
    )
    seed_is_refused = _seed_refused(records[0])
    low_coverage = (
        not seed_is_refused
        and coverage_fraction is not None
        and coverage_fraction < min_coverage_fraction
    )
    has_ambiguous_restart = any(
        bool((e.get("restart") or {}).get("ambiguous_restart")) for e in epochs
    )
    has_uncontested_restart = any(
        bool((e.get("restart") or {}).get("uncontested_restart")) for e in epochs
    )
    no_motion_evidence = (
        not seed_is_refused
        and not any(e["observation"] == "motion_present" for e in epochs)
    )
    reasons: list[str] = []
    if seed_is_refused:
        reasons.append("seed_refused")
    if low_coverage:
        reasons.append("insufficient_coverage")
    if has_ambiguous_restart:
        reasons.append("ambiguous_restart")
    if no_motion_evidence:
        reasons.append("no_motion_evidence")

    return {
        "rule": RULE,
        "seed_refused": seed_is_refused,
        "observed_duration_s": round(observed_duration_s, 4),
        "supported_duration_s": supported_duration_s,
        "supported_time_fraction": coverage_fraction,
        "epoch_count": len(epochs),
        "restart_attempt_count": restart_attempts,
        "accepted_restart_count": accepted_restarts,
        "stopped_gap_count": stopped_gaps,
        "rejected_step_count": rejected_steps,
        "flags": {
            "seed_refused": seed_is_refused,
            "low_coverage": low_coverage,
            "has_ambiguous_restart": has_ambiguous_restart,
            "has_uncontested_restart": has_uncontested_restart,
            "no_motion_evidence": no_motion_evidence,
            "flagged": bool(reasons),
            "reasons": reasons,
        },
        "epochs": epochs,
    }


def evaluate_run(
    run_dir: Path,
    manifests: Sequence[Path],
    *,
    min_coverage_fraction: float = MIN_COVERAGE_FRACTION,
    max_restart_jump_radii: float = MAX_RESTART_JUMP_RADII,
    max_restart_speed_radii_per_s: float = MAX_RESTART_SPEED_RADII_PER_S,
) -> dict[str, Any]:
    """Evaluate all methods and clips in a development run directory."""
    run_dir = run_dir.resolve()
    source_path = run_dir / "run.json"
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes.decode("utf-8"))
    manifest_hashes = [sha256_file(Path(m)) for m in manifests]
    fixtures = clips.load_fixtures(manifests)
    for name, role in (source.get("clips") or {}).items():
        if name not in fixtures or clips.development_role(fixtures[name]) != role:
            raise clips.ClipRefused(f"{name}: missing fixture or development-role mismatch")

    methods_out: dict[str, Any] = {}
    for method in sorted(source.get("methods") or {}):
        folder = run_dir / "candidates" / method
        if not folder.is_dir():
            continue
        per_clip: dict[str, Any] = {}
        flagged_clips: list[str] = []
        reason_counts: dict[str, int] = {}
        for name in sorted(source.get("clips") or {}):
            path = folder / f"{name}.jsonl"
            if not path.is_file():
                continue
            track_bytes = path.read_bytes()
            records = [
                json.loads(line)
                for line in track_bytes.decode("utf-8").splitlines()
                if line.strip()
            ]
            diag = diagnose_clip(
                records,
                min_coverage_fraction=min_coverage_fraction,
                max_restart_jump_radii=max_restart_jump_radii,
                max_restart_speed_radii_per_s=max_restart_speed_radii_per_s,
            )
            per_clip[name] = {
                "role": source["clips"][name],
                "source_sha256": hashlib.sha256(track_bytes).hexdigest(),
                **diag,
            }
            if diag["flags"]["flagged"]:
                flagged_clips.append(name)
                for r in diag["flags"]["reasons"]:
                    reason_counts[r] = reason_counts.get(r, 0) + 1
        methods_out[method] = {
            "total_clips": len(per_clip),
            "flagged_clips": flagged_clips,
            "flagged_count": len(flagged_clips),
            "reason_counts": dict(sorted(reason_counts.items())),
            "per_clip": per_clip,
        }

    return {
        "rule": RULE,
        "origin": "machine",
        "use": "development-diagnostic-only",
        "config": {
            "min_coverage_fraction": min_coverage_fraction,
            "max_restart_jump_radii": max_restart_jump_radii,
            "max_restart_speed_radii_per_s": max_restart_speed_radii_per_s,
        },
        "source_run_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "manifest_sha256": manifest_hashes,
        "source_sha256": {
            p.name: sha256_file(p)
            for p in (
                Path(__file__),
                Path(clips.__file__),
                Path(guard.__file__),
                Path(motion_observation.__file__),
            )
        },
        "methods": methods_out,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--manifest", type=Path, action="append", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--min-coverage-fraction", type=float, default=MIN_COVERAGE_FRACTION)
    p.add_argument("--max-restart-jump-radii", type=float, default=MAX_RESTART_JUMP_RADII)
    args = p.parse_args(argv)
    if args.output.resolve().is_relative_to(args.run_dir.resolve()):
        p.error("output must be outside the source run")
    report = evaluate_run(
        args.run_dir,
        args.manifest,
        min_coverage_fraction=args.min_coverage_fraction,
        max_restart_jump_radii=args.max_restart_jump_radii,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
