#!/usr/bin/env python3
"""Generate the reproducible M0 evidence package for issue #14.

This tool orchestrates the authoritative Rust CLI and reads its versioned JSON outputs. It does not
reimplement tracker, calibration, filtering, kinematic, or benchmark metric logic.

The public subset is always evaluated. ``--private-manifest`` additionally evaluates local,
non-redistributable fixtures (see ``m0_private_evidence.py``) and reports aggregates only.
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
REQUIRED_EXERCISES = ("clean", "snatch", "back_squat")
HELD_OUT_PURPOSES = frozenset({"validation"})  # fixture-manifest-v1 purpose enum
# Frozen canonical end-to-end probe configuration (docs/validation/M0_EVIDENCE.md).
PROBE_TRACKER = "template"
PROBE_FILTER = "raw"
KINEMATICS_MAX_GAP_S = "0.2"
KINEMATICS_MIN_CONFIDENCE = "0"
# Owner decision recorded 2026-10-01: the offline-speed gate targets phone-class hardware.
REFERENCE_HARDWARE_POLICY = (
    "The phone-class reference target is Google Pixel 8 under ADR-0009, but no gate-eligible "
    "reference-phone run over representative supported real clips is committed yet; desktop and "
    "non-reference timings are diagnostic only."
)


class EvidenceError(RuntimeError):
    pass


def run(command: list[str], *, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
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
    if check and completed.returncode != 0:
        raise EvidenceError(
            f"command failed with exit {completed.returncode}: {' '.join(command)}"
        )
    return completed


def cli(arguments: list[str], *, release: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    profile = ["--release"] if release else []
    return run(["cargo", "run", "--locked", *profile, "-p", "openbar-cli", "--", *arguments], check=check)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise EvidenceError(f"{path} must contain a JSON object")
    return value


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 with LF endings on every OS so generated evidence is byte-comparable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


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
    if sys.platform == "win32":
        import winreg  # noqa: PLC0415 - Windows-only stdlib module

        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor() or "unknown"


def environment() -> dict[str, str]:
    return {
        "os": platform.platform(),
        "machine": platform.machine(),
        "cpu": cpu_description(),
        "python": sys.version.split()[0],
        "rustc": first_line(["rustc", "--version"]),
        "cargo": first_line(["cargo", "--version"]),
        "ffmpeg": first_line(["ffmpeg", "-hide_banner", "-version"]),
    }


def normalized_prediction(document: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(document)
    normalized.pop("runtime", None)
    return normalized


def comparable_labelled_samples(annotation: dict[str, Any]) -> int:
    return sum(
        1
        for sample in annotation.get("samples", [])
        if sample.get("annotation_state") == "labelled" and sample.get("quality") != "unusable"
    )


def dataset_coverage(
    manifest: dict[str, Any], labelled_samples_by_fixture: dict[str, int]
) -> dict[str, Any]:
    """Summarise recording-condition coverage straight from manifest metadata (no inference)."""
    fixtures = manifest["fixtures"]

    def values(getter: Any) -> list[Any]:
        return sorted({value for item in fixtures if (value := getter(item)) is not None})

    def is_synthetic(item: dict[str, Any]) -> bool:
        return item.get("source", {}).get("kind") == "synthetic"

    annotated = {fixture_id for fixture_id, count in labelled_samples_by_fixture.items() if count > 0}
    exercises = values(lambda item: item.get("exercise"))
    return {
        "fixture_count": len(fixtures),
        "synthetic_fixture_count": sum(1 for item in fixtures if is_synthetic(item)),
        "non_synthetic_fixture_count": sum(1 for item in fixtures if not is_synthetic(item)),
        "development_fixture_count": sum(1 for item in fixtures if item.get("purpose") == "development"),
        "held_out_fixture_count": sum(1 for item in fixtures if item.get("purpose") in HELD_OUT_PURPOSES),
        "annotated_fixture_count": sum(1 for item in fixtures if item["id"] in annotated),
        "held_out_real_annotated_fixture_count": sum(
            1
            for item in fixtures
            if item["id"] in annotated
            and item.get("purpose") in HELD_OUT_PURPOSES
            and not is_synthetic(item)
        ),
        "comparable_labelled_samples": sum(labelled_samples_by_fixture.values()),
        "exercises": exercises,
        "missing_exercises": [name for name in REQUIRED_EXERCISES if name not in exercises],
        "nominal_fps": values(lambda item: item.get("video", {}).get("nominal_fps")),
        "camera_views": values(lambda item: item.get("camera", {}).get("view")),
        "camera_movements": values(lambda item: item.get("camera", {}).get("movement")),
        "motion_blur_levels": values(lambda item: item.get("conditions", {}).get("motion_blur")),
        "occlusion_levels": values(lambda item: item.get("conditions", {}).get("occlusion")),
        "lighting_conditions": values(lambda item: item.get("conditions", {}).get("lighting")),
    }


def require_commit_agreement(evaluated: str, recorded: list[str | None]) -> None:
    """Fail closed when evidence artifacts name a different commit than the one evaluated.

    Artifacts may omit the commit (for example a locally built CLI); only conflicts are errors.
    """
    conflicting = sorted({value for value in recorded if value and value != evaluated})
    if conflicting:
        raise EvidenceError(
            f"evidence artifacts disagree on evaluated git commit {evaluated}: {conflicting}"
        )


def analysis_commit(analysis: dict[str, Any]) -> str | None:
    return analysis.get("provenance", {}).get("pipeline", {}).get("git_commit")


def gate(metric: str, target: str, status: str, evidence: str, rationale: str) -> dict[str, str]:
    return {
        "metric": metric,
        "target": target,
        "status": status,
        "evidence": evidence,
        "rationale": rationale,
    }


def accuracy_preconditions(coverage: dict[str, Any], production_tracker_selected: bool) -> str:
    missing = []
    if coverage["held_out_real_annotated_fixture_count"] == 0:
        missing.append("no annotated held-out non-synthetic fixture exists")
    if not production_tracker_selected:
        missing.append("no production tracker is selected (#16)")
    return "; ".join(missing).capitalize() + "." if missing else ""


def assess_gates(
    *,
    coverage: dict[str, Any],
    determinism_pass: bool,
    determinism_scope: str,
    runtime_diagnostics: list[str],
    production_tracker_selected: bool = False,
) -> list[dict[str, str]]:
    """Assign a status to every provisional gate from evidence facts only."""
    blocked = accuracy_preconditions(coverage, production_tracker_selected)
    if not blocked:
        raise EvidenceError(
            "held-out accuracy evaluation of a selected production tracker is not implemented yet"
        )
    samples = coverage["comparable_labelled_samples"]
    runtime_evidence = (
        "Diagnostic runs: " + "; ".join(runtime_diagnostics) + "."
        if runtime_diagnostics
        else "Per-tracker wall-clock runtime is recorded in this evidence artifact."
    )
    return [
        gate(
            "Plate-centre tracking MAE",
            "< 3 px",
            "NOT MEASURABLE YET",
            f"{samples} comparable labelled samples exist across evaluated fixtures; per-tracker MAE is "
            "reported per fixture.",
            blocked,
        ),
        gate(
            "Tracking availability in supported clips",
            "> 99%",
            "NOT MEASURABLE YET",
            "Availability/loss is reported per tracker and fixture.",
            blocked + " The supported recording envelope (#15) is not defined yet.",
        ),
        gate(
            "Range-of-motion MAE",
            "< 0.01 m",
            "NOT MEASURABLE YET",
            "ROM semantics and deterministic synthetic implementation tests exist.",
            "No fixture has an independent calibrated physical ROM reference.",
        ),
        gate(
            "Mean velocity MAE",
            "< 0.05 m/s",
            "NOT MEASURABLE YET",
            "Mean-axis velocity semantics are implemented for explicit intervals.",
            "No fixture has a definition-matched physical/reference velocity source.",
        ),
        gate(
            "Peak velocity MAE",
            "< 0.10 m/s",
            "NOT MEASURABLE YET",
            "Peak signed-axis velocity semantics are implemented for explicit intervals.",
            "No fixture has a definition-matched physical/reference velocity source.",
        ),
        gate(
            "Repeat-analysis determinism",
            "100%",
            "PASS" if determinism_pass else "FAIL",
            "Public subset: repeated tracker runs, benchmark evaluations and canonical analyze runs were "
            "compared; local clips: repeated canonical analyze runs and benchmark evaluations.",
            f"Scoped to {determinism_scope} and the frozen configuration.",
        ),
        gate(
            "Offline processing",
            "faster than video duration on reference hardware",
            "NOT MEASURABLE YET",
            runtime_evidence,
            REFERENCE_HARDWARE_POLICY,
        ),
    ]


def build_suite(
    *,
    output: Path,
    manifest: Path,
    fixture_id: str,
    annotations: Path,
    seed: Path,
    prediction: Path,
    tracker_id: str,
    selected_range_s: tuple[float, float],
    commit: str,
) -> None:
    base = output.parent
    suite = {
        "schema_version": 1,
        "pipeline_version": EVIDENCE_VERSION,
        "git_commit": commit,
        "cases": [
            {
                "id": f"{fixture_id}-{tracker_id}",
                "fixture_manifest": relative_to(manifest, base),
                "fixture_id": fixture_id,
                "annotations": relative_to(annotations, base),
                "manual_seed": relative_to(seed, base),
                "predictions": relative_to(prediction, base),
                "selected_range_s": {"start_s": selected_range_s[0], "end_s": selected_range_s[1]},
                "timestamp_tolerance_s": 0.0005,
                "min_confidence": 0.0,
            }
        ],
    }
    write_json(output, suite)


def benchmark_case(suite: Path, output_a: Path, output_b: Path, *, release: bool = False) -> tuple[dict[str, Any], bool]:
    """Benchmark one single-case suite twice; return the case and whether both runs matched."""
    cli(["benchmark", "--suite", str(suite), "--output", str(output_a)], release=release)
    cli(["benchmark", "--suite", str(suite), "--output", str(output_b)], release=release)
    result = load_json(output_a)
    if len(result.get("cases", [])) != 1:
        raise EvidenceError(f"{output_a} must contain exactly one benchmark case")
    case = result["cases"][0]
    return {
        "implementation": f"{case['implementation']['name']}@{case['implementation']['version']}",
        "metrics": case["metrics"],
        "runtime": case.get("runtime"),
        "warnings": case["warnings"],
        "git_commit": result.get("git_commit"),
    }, output_a.read_bytes() == output_b.read_bytes()


def analyze_command(
    *, manifest: Path, fixture_id: str, seed: Path, plate_diameter_m: float, output: Path
) -> list[str]:
    return [
        "analyze",
        "--manifest",
        str(manifest),
        "--fixture",
        fixture_id,
        "--seed",
        str(seed),
        "--plate-diameter-m",
        f"{plate_diameter_m}",
        "--tracker",
        PROBE_TRACKER,
        "--filter",
        PROBE_FILTER,
        "--kinematics-max-gap-s",
        KINEMATICS_MAX_GAP_S,
        "--kinematics-min-confidence",
        KINEMATICS_MIN_CONFIDENCE,
        "--diagnostics",
        "quiet",
        "--output",
        str(output),
    ]


def render(analysis: Path, output: Path, *, video: Path | None) -> None:
    command = ["render", "--analysis", str(analysis)]
    if video is not None:
        command.extend(["--video", str(video)])
    command.extend(["--output", str(output)])
    cli(command)


def format_optional(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def joined(values: list[Any], empty: str = "none") -> str:
    return ", ".join(str(value) for value in values) or empty


def coverage_lines(coverage: dict[str, Any]) -> list[str]:
    return [
        f"- Fixtures: {coverage['fixture_count']} ({coverage['synthetic_fixture_count']} synthetic, "
        f"{coverage['non_synthetic_fixture_count']} non-synthetic).",
        f"- Development / held-out validation fixtures: {coverage['development_fixture_count']} / "
        f"{coverage['held_out_fixture_count']}.",
        f"- Annotated fixtures: {coverage['annotated_fixture_count']}; annotated held-out non-synthetic: "
        f"{coverage['held_out_real_annotated_fixture_count']}.",
        f"- Comparable labelled samples: {coverage['comparable_labelled_samples']}.",
        f"- Exercises: {joined(coverage['exercises'])}; absent: {joined(coverage['missing_exercises'])}.",
        f"- Nominal FPS: {joined(coverage['nominal_fps'])}.",
        f"- Camera view / movement: {joined(coverage['camera_views'])} / {joined(coverage['camera_movements'])}.",
        f"- Motion blur: {joined(coverage['motion_blur_levels'])}; occlusion: {joined(coverage['occlusion_levels'])}; "
        f"lighting: {joined(coverage['lighting_conditions'])}.",
    ]


def tracker_table(results: list[dict[str, Any]], label: str = "Tracker") -> list[str]:
    lines = [
        f"| {label} | n tracked/comparable | Availability | X MAE/RMSE px | Y MAE/RMSE px | Centre MAE/RMSE px | p50 / p90 / p95 / max px | Max loss |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        m = result["metrics"]
        availability = (
            "n/a"
            if m["tracking_availability"] is None
            else f"{100 * m['tracking_availability']:.3f}%"
        )
        lines.append(
            f"| {result.get('label', result['implementation'])} | {m['tracked_samples']}/{m['comparable_samples']} | "
            f"{availability} | "
            f"{format_optional(m.get('x_mae_px'))} / {format_optional(m.get('x_rmse_px'))} | "
            f"{format_optional(m.get('y_mae_px'))} / {format_optional(m.get('y_rmse_px'))} | "
            f"{format_optional(m['plate_center_mae_px'])} / {format_optional(m['plate_center_rmse_px'])} | "
            f"{format_optional(m.get('plate_center_p50_px'))} / {format_optional(m.get('plate_center_p90_px'))} / "
            f"{format_optional(m.get('plate_center_p95_px'))} / {format_optional(m.get('plate_center_max_px'))} | "
            f"{format_optional(m['max_consecutive_tracking_loss_samples'], 0)} samples |"
        )
    return lines


def gate_table(gates: list[dict[str, str]]) -> list[str]:
    lines = ["| Gate | Target | Status | Evidence/rationale |", "| --- | ---: | --- | --- |"]
    for item in gates:
        lines.append(
            f"| {item['metric']} | {item['target']} | **{item['status']}** | "
            f"{item['evidence']} {item['rationale']} |"
        )
    return lines


def build_report(evidence: dict[str, Any]) -> str:
    repeatability = evidence["annotation_repeatability"]
    lines = [
        "# M0 Evidence Report — Public Reproducible Subset",
        "",
        "Issue: #14",
        "",
        "This is an engineering evidence report, not a scientific or marketing accuracy claim.",
        "Tracker measurements below come from redistribution-safe fixtures only. They are useful for",
        "pipeline regression and failure inspection, but they are not sufficient to establish",
        "product-level accuracy gates.",
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
        *coverage_lines(evidence["coverage"]),
        "",
        "Annotation repeatability on the synthetic fixture is reported separately from tracker error:",
        f"mean Euclidean disagreement {repeatability['mean_euclidean_disagreement_px']} px, "
        f"RMSE {repeatability['rmse_euclidean_disagreement_px']} px, "
        f"maximum {repeatability['max_euclidean_disagreement_px']} px "
        f"over {repeatability['matching_labelled_samples']} matched labels.",
        "",
        "## Tracker results",
        "",
        *tracker_table(evidence["tracker_results"]),
        "",
        "These values are from synthetic development material. They must not be generalized",
        "to ordinary phone video or the supported-condition envelope.",
        "",
        "## Provisional gate status",
        "",
        *gate_table(evidence["gates"]),
        "",
        "## Determinism and performance",
        "",
        f"- Normalized tracker prediction streams identical across two complete tracker runs: "
        f"{evidence['determinism']['tracker_predictions_identical']}.",
        f"- Benchmark JSON identical across repeated evaluation of the same prediction streams: "
        f"{evidence['determinism']['benchmark_outputs_identical']}.",
        f"- Canonical analysis JSON byte-identical across two end-to-end runs: "
        f"{evidence['determinism']['canonical_analysis_identical']}.",
        "- Runtime is recorded per tracker in the machine-readable evidence. The offline-speed gate remains",
        "  NOT MEASURABLE YET until #59 records gate-eligible runs on the Pixel 8 reference runtime.",
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
        "- Local real-video evidence, when generated, is reported separately in",
        "  [M0_PRIVATE_EVIDENCE_REPORT.md](M0_PRIVATE_EVIDENCE_REPORT.md) as aggregates only.",
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
        "1. Annotate the local real snatch clips and add clean/back-squat fixtures with held-out roles.",
        "2. Add condition coverage for realistic frame rates, distance/framing, blur, camera movement, contrast and yaw.",
        "3. Add independent calibrated position/ROM reference and definition-matched velocity reference.",
        "4. Re-run this package without changing gate semantics; only then promote tracker/kinematic gates from",
        "   NOT MEASURABLE YET to evidence-backed PASS/FAIL or justify a target revision.",
        "",
    ]
    return "\n".join(lines)


def generate_public(output_dir: Path, commit: str) -> dict[str, Any]:
    """Run the public subset; returns evidence facts without gate statuses."""
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_json(MANIFEST)
    annotation = load_json(ANNOTATIONS)

    run_a = output_dir / "tracker-run-a"
    run_b = output_dir / "tracker-run-b"
    for target in (run_a, run_b):
        cli(["tracker-run", "--manifest", str(MANIFEST), "--fixture", FIXTURE_ID,
             "--seed", str(SEED), "--output-dir", str(target)])

    tracker_results: list[dict[str, Any]] = []
    tracker_repeat_equal = True
    benchmark_repeat_equal = True
    for tracker_id in TRACKERS:
        filename = f"{FIXTURE_ID}.{tracker_id}.prediction-v1.json"
        tracker_repeat_equal &= normalized_prediction(load_json(run_a / filename)) == normalized_prediction(
            load_json(run_b / filename)
        )
        suite = output_dir / f"{tracker_id}.benchmark-v1.json"
        build_suite(output=suite, manifest=MANIFEST, fixture_id=FIXTURE_ID, annotations=ANNOTATIONS,
                    seed=SEED, prediction=run_a / filename, tracker_id=tracker_id,
                    selected_range_s=(0.0, 0.916667), commit=commit)
        result, identical = benchmark_case(
            suite, output_dir / f"{tracker_id}.benchmark-a.json", output_dir / f"{tracker_id}.benchmark-b.json"
        )
        benchmark_repeat_equal &= identical
        tracker_results.append(result)

    analyses = [output_dir / "analysis-a.json", output_dir / "analysis-b.json"]
    for target in analyses:
        cli(analyze_command(manifest=MANIFEST, fixture_id=FIXTURE_ID, seed=SEED,
                            plate_diameter_m=0.45, output=target))
    analysis_equal = analyses[0].read_bytes() == analyses[1].read_bytes()
    require_commit_agreement(
        commit,
        [result.pop("git_commit") for result in tracker_results]
        + [analysis_commit(load_json(path)) for path in analyses],
    )

    render(analyses[0], output_dir / "diagnostic-success.svg",
           video=ROOT / "validation/fixtures/public/synthetic-clean-side-12.mp4")
    render(FAILURE_ANALYSIS, output_dir / "diagnostic-failure.svg", video=None)
    cli(["filter-experiment", "--output", str(output_dir / "filter-experiment.json")])

    coverage = dataset_coverage(manifest, {FIXTURE_ID: comparable_labelled_samples(annotation)})
    return {
        "coverage": {
            "fixture_manifest": relative_to(MANIFEST, ROOT),
            "fixture_manifest_sha256": sha256(MANIFEST),
            **coverage,
        },
        "tracker_results": tracker_results,
        "determinism": {
            "tracker_runs": 2,
            "tracker_predictions_identical": tracker_repeat_equal,
            "benchmark_repeats_per_tracker": 2,
            "benchmark_outputs_identical": benchmark_repeat_equal,
            "canonical_analysis_runs": 2,
            "canonical_analysis_identical": analysis_equal,
        },
    }


def assemble(commit: str, public: dict[str, Any], private: dict[str, Any] | None) -> dict[str, Any]:
    determinism = public["determinism"]
    determinism_pass = (
        determinism["tracker_predictions_identical"]
        and determinism["benchmark_outputs_identical"]
        and determinism["canonical_analysis_identical"]
    )
    scope = "the public synthetic subset"
    runtime_diagnostics: list[str] = []
    coverage = public["coverage"]
    if private is not None:
        determinism_pass = determinism_pass and private["determinism_pass"]
        scope += f" plus {private['analysed_fixture_count']} local real-video fixture(s)"
        runtime_diagnostics = private["runtime_diagnostics"]
        coverage = {
            **coverage,
            "held_out_real_annotated_fixture_count": coverage["held_out_real_annotated_fixture_count"]
            + private["coverage"]["held_out_real_annotated_fixture_count"],
            "comparable_labelled_samples": coverage["comparable_labelled_samples"]
            + private["coverage"]["comparable_labelled_samples"],
        }
    production_tracker_selected = False
    evidence = {
        "schema_version": 1,
        "evidence_version": EVIDENCE_VERSION,
        "evaluated_commit": commit,
        "scope": "public_reproducible_subset",
        "candidate_configuration": {
            "trackers": list(TRACKERS),
            "production_tracker_selected": production_tracker_selected,
            "integration_probe_tracker": "template-sad-v1",
            "filter": "raw-identity@1",
            "production_filter_selected": False,
            "plate_diameter_m": 0.45,
            "kinematics_method": "backward-difference@1",
            "kinematics_max_gap_s": float(KINEMATICS_MAX_GAP_S),
            "kinematics_min_confidence": float(KINEMATICS_MIN_CONFIDENCE),
            "benchmark_timestamp_tolerance_s": 0.0005,
            "benchmark_min_confidence": 0.0,
        },
        "coverage": public["coverage"],
        "annotation_repeatability": load_json(REPEATABILITY),
        "tracker_results": public["tracker_results"],
        "determinism": determinism,
        "environment": environment(),
        "gates": assess_gates(
            coverage=coverage,
            determinism_pass=determinism_pass,
            determinism_scope=scope,
            runtime_diagnostics=runtime_diagnostics,
            production_tracker_selected=production_tracker_selected,
        ),
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
            "one synthetic development fixture in the public subset",
            "no held-out real lifting video",
            "no public snatch or back squat evidence",
            "12 fps only in the public subset",
            "fixed side camera only",
            "no realistic motion blur or camera-motion coverage in the public subset",
            "no independent physical ROM reference",
            "no definition-matched velocity reference",
            "runtime environment is observational, not reference hardware",
        ],
    }
    if private is not None:
        evidence["private_subset"] = private
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "target/m0-evidence",
        help="directory for the complete evidence package",
    )
    parser.add_argument(
        "--private-manifest",
        type=Path,
        help="also evaluate local non-redistributable fixtures from this manifest (aggregates only)",
    )
    args = parser.parse_args(argv)
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir

    try:
        commit = evaluated_commit()
        public = generate_public(output_dir, commit)
        private = None
        if args.private_manifest is not None:
            # Share this module instance so both modules raise and catch the same EvidenceError.
            sys.modules["m0_evidence"] = sys.modules[__name__]
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import m0_private_evidence  # noqa: PLC0415 - optional, local-only mode

            manifest = args.private_manifest
            manifest = manifest if manifest.is_absolute() else ROOT / manifest
            private = m0_private_evidence.generate(manifest, output_dir / "private", commit)
        evidence = assemble(commit, public, private)
        evidence_path = output_dir / "m0-evidence-v1.json"
        report_path = output_dir / "M0_EVIDENCE_REPORT.md"
        write_json(evidence_path, evidence)
        write_text(report_path, build_report(evidence))
        if private is not None:
            private_report = output_dir / "M0_PRIVATE_EVIDENCE_REPORT.md"
            write_text(private_report, m0_private_evidence.build_report(evidence))
            print(f"private aggregate report: {private_report}")
    except (EvidenceError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"m0-evidence: {error}", file=sys.stderr)
        return 1

    print(f"machine evidence: {evidence_path}")
    print(f"human report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
