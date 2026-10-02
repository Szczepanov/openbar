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
        if allow_held_out:
            cmd.append("--allow-held-out")
        execute(cmd)


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
) -> dict[str, Any]:
    matches = tfs._match_timestamped_samples(references, prediction_samples, tolerance_s)
    errors: list[float] = []
    avail_count = 0
    max_consec_loss = 0
    cur_consec = 0

    for ref, act in matches:
        if act is not None and act.get("state") == "tracked":
            avail_count += 1
            cur_consec = 0
            center = act.get("center_px")
            if center:
                dx = float(center["x_px"]) - float(ref["center_px"]["x_px"])
                dy = float(center["y_px"]) - float(ref["center_px"]["y_px"])
                errors.append(math.hypot(dx, dy))
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

    return {
        "comparable_samples": comparable,
        "tracked_samples": avail_count,
        "lost_samples": comparable - avail_count,
        "availability": avail,
        "mae_px": mae,
        "p50_px": p50,
        "p90_px": p90,
        "max_px": max_err,
        "max_consecutive_loss": max_consec_loss,
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
        seed_s = json.loads(seed_path.read_text(encoding="utf-8"))["seed"]["timestamp_s"]

        tol = float(annotation.get("timebase", {}).get("decoder_match_tolerance_s", TIMESTAMP_TOLERANCE_S))
        all_refs = [
            s for s in annotation.get("samples", [])
            if s.get("annotation_state") == "labelled" and s.get("quality") != "unusable"
        ]
        no_seed_refs = [
            s for s in all_refs
            if abs(float(s["timestamp_s"]) - seed_s) > tol
        ]

        ann_no_seed = {
            **annotation,
            "samples": [
                s for s in annotation.get("samples", [])
                if abs(float(s["timestamp_s"]) - seed_s) > tol
            ],
        }

        predictions_dir = predictions_base_dir / fid
        pred_files = require_prediction_files(
            expected_prediction_paths(predictions_dir, fid, candidate_names)
        )
        tracker_summaries = []

        for p_file in pred_files:
            pred = json.loads(p_file.read_text(encoding="utf-8"))
            t_name = pred.get("implementation", {}).get("name") or p_file.stem.removeprefix(f"{fid}.").removesuffix(".prediction-v1")
            pred_samples = pred.get("samples", [])

            m_all = compute_metrics(all_refs, pred_samples, tol)
            m_no_seed = compute_metrics(no_seed_refs, pred_samples, tol)

            diag_all = tfs.false_track_diagnostics(annotation, pred)
            diag_no_seed = tfs.false_track_diagnostics(ann_no_seed, pred)

            m_all["false_track_count"] = diag_all["high_confidence_false_track_samples"]
            m_no_seed["false_track_count"] = diag_no_seed["high_confidence_false_track_samples"]

            tracker_summaries.append({
                "tracker": t_name,
                "all_samples": m_all,
                "seed_excluded": m_no_seed,
            })

        summary_fixtures.append({
            "fixture_id": fid,
            "seed_timestamp_s": seed_s,
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
        "| Clip | Tracker | Availability | MAE px | p50 px | p90 px | Max px | Max Loss | False Tracks |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
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
                f"| `{f['fixture_id']}` | {t['tracker']} | {avail_str} | {mae_str} | {p50_str} | {p90_str} | {max_str} | {m['max_consecutive_loss']} | {m['false_track_count']} |"
            )

    md_lines.extend([
        "",
        "## 2. Seed-Excluded Results (Fair tracking performance)",
        "",
        "| Clip | Tracker | Availability | MAE px | p50 px | p90 px | Max px | Max Loss | False Tracks |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for f in summary_fixtures:
        for t in f["trackers"]:
            m = t["seed_excluded"]
            avail_str = f"{m['availability']*100:.1f} %" if m['availability'] is not None else "N/A"
            mae_str = f"{m['mae_px']:.1f}" if m['mae_px'] is not None else "N/A"
            p50_str = f"{m['p50_px']:.1f}" if m['p50_px'] is not None else "N/A"
            p90_str = f"{m['p90_px']:.1f}" if m['p90_px'] is not None else "N/A"
            max_str = f"{m['max_px']:.1f}" if m['max_px'] is not None else "N/A"
            md_lines.append(
                f"| `{f['fixture_id']}` | {t['tracker']} | {avail_str} | {mae_str} | {p50_str} | {p90_str} | {max_str} | {m['max_consecutive_loss']} | {m['false_track_count']} |"
            )

    md_lines.extend([
        "",
        "## 3. Seed Impact (MAE Difference)",
        "",
        "| Clip | Tracker | MAE All px | MAE No-Seed px | Delta px | Delta % |",
        "|---|---|---:|---:|---:|---:|",
    ])
    for f in summary_fixtures:
        for t in f["trackers"]:
            m_all = t["all_samples"]
            m_no = t["seed_excluded"]
            if m_all["mae_px"] is not None and m_no["mae_px"] is not None:
                delta = m_no["mae_px"] - m_all["mae_px"]
                pct = (delta / m_all["mae_px"]) * 100 if m_all["mae_px"] > 0 else 0.0
                md_lines.append(
                    f"| `{f['fixture_id']}` | {t['tracker']} | {m_all['mae_px']:.2f} | {m_no['mae_px']:.2f} | +{delta:.2f} | +{pct:.1f} % |"
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

        # Run registered candidate producers
        for producer in selected_producers:
            output_file = predictions_dir / f"{fixture_id}.{producer.name}.prediction-v1.json"
            producer.run(
                manifest_path=args.manifest,
                fixture_id=fixture_id,
                seed_path=seed,
                end_s=end_s_tolerant,
                output_path=output_file,
                allow_held_out=args.allow_held_out,
            )

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
