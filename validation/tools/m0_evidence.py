#!/usr/bin/env python3
"""Generate the reproducible public M0 evidence package for issue #14.

This tool orchestrates the authoritative Rust CLI and reads its versioned JSON outputs. It does not
reimplement tracker, calibration, filtering, kinematic, or benchmark metric logic.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ID = "synthetic-clean-side-12"
MANIFEST = ROOT / "validation/fixtures/public/manifest.json"
ANNOTATIONS = ROOT / "validation/fixtures/public/annotations/synthetic-clean-side-12.annotation-v1.json"
REPEATABILITY = ROOT / "validation/fixtures/public/annotations/synthetic-clean-side-12.repeatability.json"
SEED = ROOT / "validation/fixtures/public/seeds/synthetic-clean-side-12.manual-target-seed-v1.json"
FAILURE_ANALYSIS = ROOT / "crates/openbar-core/tests/fixtures/analysis-v1.golden.json"
TRACKERS = {
    "template-sad-v1": "template",
    "local-contrast-centroid-v1": "contrast",
}
EVIDENCE_VERSION = "m0-public-evidence-v1"


class EvidenceError(RuntimeError):
    pass


def run(command: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        raise EvidenceError(
            f"command failed with exit {completed.returncode}: {' '.join(command)}"
        )
    return completed


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise EvidenceError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_to(path: Path, base: Path) -> str:
    return os.path.relpath(path.resolve(), base.resolve()).replace(os.sep, "/")


def evaluated_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return completed.stdout.strip()
    return os.environ.get("OPENBAR_GIT_COMMIT") or os.environ.get("GITHUB_SHA") or "unknown"


def first_line(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        return "unavailable"
    return completed.stdout.splitlines()[0] if completed.stdout.splitlines() else "unavailable"


def cpu_description() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name") and ":" in line:
                return line.split(":", 1)[1].strip()
    return os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor() or "unknown"


def normalized_prediction(document: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(document)
    normalized.pop("runtime", None)
    return normalized


def build_suite(
    *,
    output: Path,
    prediction: Path,
    tracker_id: str,
    commit: str,
) -> None:
    base = output.parent
    suite = {
        "schema_version": 1,
        "pipeline_version": EVIDENCE_VERSION,
        "git_commit": commit,
        "cases": [
            {
                "id": f"{FIXTURE_ID}-{tracker_id}",
                "fixture_manifest": relative_to(MANIFEST, base),
                "fixture_id": FIXTURE_ID,
                "annotations": relative_to(ANNOTATIONS, base),
                "manual_seed": relative_to(SEED, base),
                "predictions": relative_to(prediction, base),
                "selected_range_s": {"start_s": 0.0, "end_s": 0.916667},
                "timestamp_tolerance_s": 0.0005,
                "min_confidence": 0.0,
            }
        ],
    }
    write_json(output, suite)


def tracker_run(output_dir: Path) -> None:
    run(
        [
            "cargo",
            "run",
            "--locked",
            "-p",
            "openbar-cli",
            "--",
            "tracker-run",
            "--manifest",
            str(MANIFEST),
            "--fixture",
            FIXTURE_ID,
            "--seed",
            str(SEED),
            "--output-dir",
            str(output_dir),
        ]
    )


def benchmark(suite: Path, output: Path) -> None:
    run(
        [
            "cargo",
            "run",
            "--locked",
            "-p",
            "openbar-cli",
            "--",
            "benchmark",
            "--suite",
            str(suite),
            "--output",
            str(output),
        ]
    )


def analyze(output: Path) -> None:
    run(
        [
            "cargo",
            "run",
            "--locked",
            "-p",
            "openbar-cli",
            "--",
            "analyze",
            "--manifest",
            str(MANIFEST),
            "--fixture",
            FIXTURE_ID,
            "--seed",
            str(SEED),
            "--plate-diameter-m",
            "0.45",
            "--tracker",
            "template",
            "--filter",
            "raw",
            "--kinematics-max-gap-s",
            "0.2",
            "--kinematics-min-confidence",
            "0",
            "--diagnostics",
            "quiet",
            "--output",
            str(output),
        ]
    )


def render(analysis: Path, output: Path, *, with_video: bool) -> None:
    command = [
        "cargo",
        "run",
        "--locked",
        "-p",
        "openbar-cli",
        "--",
        "render",
        "--analysis",
        str(analysis),
    ]
    if with_video:
        command.extend(
            ["--video", str(ROOT / "validation/fixtures/public/synthetic-clean-side-12.mp4")]
        )
    command.extend(["--output", str(output)])
    run(command)


def gate(metric: str, target: str, status: str, evidence: str, rationale: str) -> dict[str, str]:
    return {
        "metric": metric,
        "target": target,
        "status": status,
        "evidence": evidence,
        "rationale": rationale,
    }


def format_optional(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def build_report(evidence: dict[str, Any]) -> str:
    lines = [
        "# M0 Evidence Report — Public Reproducible Subset",
        "",
        "Issue: #14",
        "",
        "This is an engineering evidence report, not a scientific or marketing accuracy claim.",
        "The current redistribution-safe dataset contains one synthetic development fixture and no",
        "held-out real lifting footage. Tracker measurements below are useful for pipeline regression",
        "and failure inspection, but they are not sufficient to establish product-level accuracy gates.",
        "",
        "## Evaluated configuration",
        "",
        "- Trackers: template-sad-v1 and local-contrast-centroid-v1, both retained as M0 baselines.",
        "- Canonical integration probe: template tracker + raw-identity@1 filter.",
        "- Plate-diameter calibration input: 0.45 m.",
        "- Kinematics: timestamp-authoritative backward difference, max continuity gap 0.2 s.",
        "- Benchmark confidence threshold: 0.0; tracker-declared loss remains explicit.",
        "",
        "No production tracker or filter winner is selected by this report.",
        "",
        "## Dataset composition",
        "",
        f"- Public fixtures: {evidence['coverage']['fixture_count']}.",
        f"- Development fixtures: {evidence['coverage']['development_fixture_count']}.",
        f"- Held-out validation fixtures: {evidence['coverage']['held_out_fixture_count']}.",
        f"- Comparable labelled samples in the public fixture: {evidence['coverage']['comparable_labelled_samples']}.",
        "- Exercise coverage: clean only; snatch and back squat are absent.",
        "- FPS coverage: 12 fps only.",
        "- Camera: fixed side view only; camera movement and yaw are not represented.",
        "- Motion blur: none.",
        "- Occlusion: partial and full occlusion states exist, but the fully occluded frame is intentionally unlabelable and excluded from coordinate-error denominators.",
        "",
        "Annotation repeatability on the synthetic fixture is reported separately from tracker error:",
        f"mean Euclidean disagreement {evidence['annotation_repeatability']['mean_euclidean_disagreement_px']} px, "
        f"RMSE {evidence['annotation_repeatability']['rmse_euclidean_disagreement_px']} px, "
        f"maximum {evidence['annotation_repeatability']['max_euclidean_disagreement_px']} px "
        f"over {evidence['annotation_repeatability']['matching_labelled_samples']} matched labels.",
        "",
        "## Tracker results",
        "",
        "| Tracker | n tracked/comparable | Availability | X MAE/RMSE px | Y MAE/RMSE px | Centre MAE/RMSE px | p50 / p90 / p95 / max px | Max loss |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in evidence["tracker_results"]:
        m = result["metrics"]
        availability = (
            "n/a"
            if m["tracking_availability"] is None
            else f"{100 * m['tracking_availability']:.3f}%"
        )
        lines.append(
            f"| {result['implementation']} | {m['tracked_samples']}/{m['comparable_samples']} | "
            f"{availability} | "
            f"{format_optional(m.get('x_mae_px'))} / {format_optional(m.get('x_rmse_px'))} | "
            f"{format_optional(m.get('y_mae_px'))} / {format_optional(m.get('y_rmse_px'))} | "
            f"{format_optional(m['plate_center_mae_px'])} / {format_optional(m['plate_center_rmse_px'])} | "
            f"{format_optional(m.get('plate_center_p50_px'))} / {format_optional(m.get('plate_center_p90_px'))} / "
            f"{format_optional(m.get('plate_center_p95_px'))} / {format_optional(m.get('plate_center_max_px'))} | "
            f"{format_optional(m['max_consecutive_tracking_loss_samples'], 0)} samples |"
        )
    lines.extend(
        [
            "",
            "These values are from the single synthetic development fixture. They must not be generalized",
            "to ordinary phone video or the supported-condition envelope.",
            "",
            "## Provisional gate status",
            "",
            "| Gate | Target | Status | Evidence/rationale |",
            "| --- | ---: | --- | --- |",
        ]
    )
    for item in evidence["gates"]:
        lines.append(
            f"| {item['metric']} | {item['target']} | **{item['status']}** | "
            f"{item['evidence']} {item['rationale']} |"
        )
    lines.extend(
        [
            "",
            "## Determinism and performance",
            "",
            f"- Normalized tracker prediction streams identical across two complete tracker runs: "
            f"{evidence['determinism']['tracker_predictions_identical']}.",
            f"- Benchmark JSON identical across repeated evaluation of the same prediction streams: "
            f"{evidence['determinism']['benchmark_outputs_identical']}.",
            f"- Canonical analysis JSON byte-identical across two end-to-end runs: "
            f"{evidence['determinism']['canonical_analysis_identical']}.",
            "- Runtime is recorded per tracker in the machine-readable evidence, but the offline-speed gate remains",
            "  NOT MEASURABLE YET: the project has not designated stable reference hardware and a one-second",
            "  synthetic clip is not representative of the M0 recording envelope.",
            "",
            "## Failure cases and unsupported conditions",
            "",
            "- Benchmark loss is never converted into zero coordinate error.",
            "- The public fixture contains one fully occluded, deliberately unlabelable frame; it is excluded from",
            "  coordinate metrics rather than filled or interpolated.",
            "- Diagnostic SVG artifacts are generated for the public analysis and a committed loss/low-confidence",
            "  canonical-analysis fixture so the rendering path exposes gaps instead of hiding them.",
            "- No public evidence currently supports conclusions for realistic motion blur, camera motion, distance",
            "  variation, yaw, gym clutter, plate/background contrast variation, snatch, back squat, or held-out data.",
            "",
            "## Ground truth and threats to validity",
            "",
            "- Tracker reference: manual centre digitisation of a first-principles synthetic video.",
            "- Annotation repeatability is measured on two synthetic annotation passes; it is not an estimate of",
            "  annotation noise on real footage.",
            "- There is no independent calibrated physical reference for ROM or velocity in the public subset.",
            "- There is no phase-matched VBT/encoder/reference device material for mean or peak velocity.",
            "- Subgroup claims are intentionally withheld because every requested subgroup other than the single",
            "  clean/12-fps/fixed-camera development condition has zero or tiny support.",
            "",
            "## Reproduction",
            "",
            "    python3 validation/tools/m0_evidence.py --output-dir target/m0-evidence",
            "    python3 validation/tools/schema_check.py --schema validation/schema/m0-evidence-v1.schema.json target/m0-evidence/m0-evidence-v1.json",
            "",
            "The output directory also contains tracker predictions, benchmark JSON, repeated canonical analyses,",
            "filter-experiment evidence, and diagnostic SVGs. Runtime/environment provenance is retained in the JSON.",
            "",
            "## Recommended next evidence",
            "",
            "1. Add redistribution-safe or locally reproducible real clean/snatch/back-squat fixtures with held-out roles.",
            "2. Add condition coverage for realistic frame rates, distance/framing, blur, camera movement, contrast and yaw.",
            "3. Add independent calibrated position/ROM reference and definition-matched velocity reference.",
            "4. Re-run this package without changing gate semantics; only then promote tracker/kinematic gates from",
            "   NOT MEASURABLE YET to evidence-backed PASS/FAIL or justify a target revision.",
            "",
        ]
    )
    return "\n".join(lines)


def generate(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    commit = evaluated_commit()
    manifest = load_json(MANIFEST)
    annotation = load_json(ANNOTATIONS)
    repeatability = load_json(REPEATABILITY)

    run_a = output_dir / "tracker-run-a"
    run_b = output_dir / "tracker-run-b"
    tracker_run(run_a)
    tracker_run(run_b)

    tracker_results: list[dict[str, Any]] = []
    tracker_repeat_equal = True
    benchmark_repeat_equal = True

    for tracker_id in TRACKERS:
        filename = f"{FIXTURE_ID}.{tracker_id}.prediction-v1.json"
        prediction_a = run_a / filename
        prediction_b = run_b / filename
        doc_a = load_json(prediction_a)
        doc_b = load_json(prediction_b)
        tracker_repeat_equal &= normalized_prediction(doc_a) == normalized_prediction(doc_b)

        suite = output_dir / f"{tracker_id}.benchmark-v1.json"
        build_suite(output=suite, prediction=prediction_a, tracker_id=tracker_id, commit=commit)
        benchmark_a = output_dir / f"{tracker_id}.benchmark-a.json"
        benchmark_b = output_dir / f"{tracker_id}.benchmark-b.json"
        benchmark(suite, benchmark_a)
        benchmark(suite, benchmark_b)
        benchmark_repeat_equal &= benchmark_a.read_bytes() == benchmark_b.read_bytes()

        result = load_json(benchmark_a)
        if len(result.get("cases", [])) != 1:
            raise EvidenceError(f"{benchmark_a} must contain exactly one benchmark case")
        case = result["cases"][0]
        tracker_results.append(
            {
                "implementation": f"{case['implementation']['name']}@{case['implementation']['version']}",
                "metrics": case["metrics"],
                "runtime": case.get("runtime"),
                "warnings": case["warnings"],
            }
        )

    analysis_a = output_dir / "analysis-a.json"
    analysis_b = output_dir / "analysis-b.json"
    analyze(analysis_a)
    analyze(analysis_b)
    analysis_equal = analysis_a.read_bytes() == analysis_b.read_bytes()

    render(analysis_a, output_dir / "diagnostic-success.svg", with_video=True)
    render(FAILURE_ANALYSIS, output_dir / "diagnostic-failure.svg", with_video=False)

    run(
        [
            "cargo",
            "run",
            "--locked",
            "-p",
            "openbar-cli",
            "--",
            "filter-experiment",
            "--output",
            str(output_dir / "filter-experiment.json"),
        ]
    )

    comparable = sum(
        1
        for sample in annotation["samples"]
        if sample.get("annotation_state") == "labelled" and sample.get("quality") != "unusable"
    )
    fixtures = manifest["fixtures"]
    development_count = sum(1 for item in fixtures if item.get("purpose") == "development")
    held_out_count = sum(1 for item in fixtures if item.get("purpose") in {"validation", "held_out"})

    determinism_pass = tracker_repeat_equal and benchmark_repeat_equal and analysis_equal
    gates = [
        gate(
            "Plate-centre tracking MAE",
            "< 3 px",
            "NOT MEASURABLE YET",
            "The public run reports exact synthetic-fixture MAE for both retained tracker baselines.",
            "Only one synthetic development fixture exists; there is no held-out real-video sample.",
        ),
        gate(
            "Tracking availability in supported clips",
            "> 99%",
            "NOT MEASURABLE YET",
            "Availability/loss is measured on the public synthetic development fixture.",
            "The supported real recording envelope and held-out validation set do not yet exist.",
        ),
        gate(
            "Range-of-motion MAE",
            "< 0.01 m",
            "NOT MEASURABLE YET",
            "ROM semantics and deterministic synthetic implementation tests exist.",
            "The public subset has no independent calibrated physical ROM reference.",
        ),
        gate(
            "Mean velocity MAE",
            "< 0.05 m/s",
            "NOT MEASURABLE YET",
            "Mean-axis velocity semantics are implemented for explicit intervals.",
            "There is no definition-matched physical/reference velocity dataset in the public subset.",
        ),
        gate(
            "Peak velocity MAE",
            "< 0.10 m/s",
            "NOT MEASURABLE YET",
            "Peak signed-axis velocity semantics are implemented for explicit intervals.",
            "There is no definition-matched physical/reference velocity dataset in the public subset.",
        ),
        gate(
            "Repeat-analysis determinism",
            "100%",
            "PASS" if determinism_pass else "FAIL",
            "Two tracker runs, repeated benchmark evaluation, and two canonical analyze runs were compared.",
            "PASS is scoped to the current public synthetic subset and frozen configuration.",
        ),
        gate(
            "Offline processing",
            "faster than video duration on reference hardware",
            "NOT MEASURABLE YET",
            "Per-tracker wall-clock runtime and media/runtime ratio are recorded in this evidence artifact.",
            "No stable project reference-hardware target is designated, and the one-second fixture is not representative.",
        ),
    ]

    return {
        "schema_version": 1,
        "evidence_version": EVIDENCE_VERSION,
        "evaluated_commit": commit,
        "scope": "public_reproducible_subset",
        "candidate_configuration": {
            "trackers": list(TRACKERS),
            "production_tracker_selected": False,
            "integration_probe_tracker": "template-sad-v1",
            "filter": "raw-identity@1",
            "production_filter_selected": False,
            "plate_diameter_m": 0.45,
            "kinematics_method": "backward-difference@1",
            "kinematics_max_gap_s": 0.2,
            "kinematics_min_confidence": 0.0,
            "benchmark_timestamp_tolerance_s": 0.0005,
            "benchmark_min_confidence": 0.0,
        },
        "coverage": {
            "fixture_manifest": relative_to(MANIFEST, ROOT),
            "fixture_manifest_sha256": sha256(MANIFEST),
            "fixture_count": len(fixtures),
            "development_fixture_count": development_count,
            "held_out_fixture_count": held_out_count,
            "comparable_labelled_samples": comparable,
            "exercises": sorted({item["exercise"] for item in fixtures}),
            "nominal_fps": sorted({item["video"]["nominal_fps"] for item in fixtures}),
        },
        "annotation_repeatability": repeatability,
        "tracker_results": tracker_results,
        "determinism": {
            "tracker_runs": 2,
            "tracker_predictions_identical": tracker_repeat_equal,
            "benchmark_repeats_per_tracker": 2,
            "benchmark_outputs_identical": benchmark_repeat_equal,
            "canonical_analysis_runs": 2,
            "canonical_analysis_identical": analysis_equal,
        },
        "environment": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "cpu": cpu_description(),
            "python": sys.version.split()[0],
            "rustc": first_line(["rustc", "--version"]),
            "cargo": first_line(["cargo", "--version"]),
            "ffmpeg": first_line(["ffmpeg", "-hide_banner", "-version"]),
        },
        "gates": gates,
        "artifacts": {
            "tracker_run_a": "tracker-run-a/",
            "tracker_run_b": "tracker-run-b/",
            "benchmarks": [f"{tracker_id}.benchmark-a.json" for tracker_id in TRACKERS],
            "canonical_analysis": "analysis-a.json",
            "filter_experiment": "filter-experiment.json",
            "diagnostic_success": "diagnostic-success.svg",
            "diagnostic_failure": "diagnostic-failure.svg",
        },
        "limitations": [
            "one synthetic development fixture only",
            "no held-out real lifting video",
            "clean only; no snatch or back squat public evidence",
            "12 fps only",
            "fixed side camera only",
            "no realistic motion blur or camera-motion coverage",
            "no independent physical ROM reference",
            "no definition-matched velocity reference",
            "runtime environment is observational, not stable reference hardware",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "target/m0-evidence",
        help="directory for the complete evidence package",
    )
    args = parser.parse_args(argv)
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir

    try:
        evidence = generate(output_dir)
        evidence_path = output_dir / "m0-evidence-v1.json"
        report_path = output_dir / "M0_EVIDENCE_REPORT.md"
        write_json(evidence_path, evidence)
        report_path.write_text(build_report(evidence), encoding="utf-8")
    except (EvidenceError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"m0-evidence: {error}", file=sys.stderr)
        return 1

    print(f"machine evidence: {evidence_path}")
    print(f"human report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
