#!/usr/bin/env python3
"""Build a reproducible M0 evidence preflight from existing OpenBar validation artifacts.

This tool is reporting-only. It never recomputes tracker/calibration/filter/kinematic measurements;
authoritative measurement semantics remain in Rust. It inventories evidence, checks provenance and
reports which provisional M0 gates are supported or still not measurable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
EVIDENCE_VERSION = "m0-evidence-preflight-v1"
PASS = "PASS"
FAIL = "FAIL"
NOT_MEASURABLE = "NOT_MEASURABLE_YET"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read JSON '{path}': {error}") from error


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ValueError(f"cannot hash '{path}': {error}") from error
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def require_v1(document: Any, path: Path) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError(f"'{path}' must contain a JSON object")
    if document.get("schema_version") != 1:
        raise ValueError(
            f"'{path}' has unsupported schema_version {document.get('schema_version')!r}; expected 1"
        )
    return document


def analysis_commit(analysis: dict[str, Any]) -> str | None:
    value = analysis.get("provenance", {}).get("pipeline", {}).get("git_commit")
    return value if isinstance(value, str) and value.strip() else None


def fixture_summary(manifest: dict[str, Any], annotation_paths: list[Path]) -> dict[str, Any]:
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list):
        raise ValueError("fixture manifest must contain a fixtures array")
    fixture_by_id = {fixture.get("id"): fixture for fixture in fixtures if isinstance(fixture, dict)}
    if len(fixture_by_id) != len(fixtures) or None in fixture_by_id:
        raise ValueError("fixture manifest contains a fixture without a unique id")

    annotated_ids: set[str] = set()
    for path in annotation_paths:
        annotation = require_v1(load_json(path), path)
        fixture_id = annotation.get("fixture_id")
        if not isinstance(fixture_id, str) or fixture_id not in fixture_by_id:
            raise ValueError(f"annotation '{path}' references unknown fixture {fixture_id!r}")
        annotated_ids.add(fixture_id)

    synthetic = [f for f in fixtures if f.get("source", {}).get("kind") == "synthetic"]
    non_synthetic = [f for f in fixtures if f.get("source", {}).get("kind") != "synthetic"]
    held_out_real = [
        f
        for f in fixtures
        if f.get("source", {}).get("kind") != "synthetic" and f.get("purpose") == "validation"
    ]
    held_out_real_annotated = [f for f in held_out_real if f["id"] in annotated_ids]
    fps_values = sorted(
        {
            float(f["video"].get("measured_fps", f["video"].get("nominal_fps")))
            for f in fixtures
            if isinstance(f.get("video"), dict)
            and isinstance(
                f["video"].get("measured_fps", f["video"].get("nominal_fps")), (int, float)
            )
        }
    )
    distance_values = sorted(
        {
            float(f["camera"]["distance_m"])
            for f in fixtures
            if isinstance(f.get("camera", {}).get("distance_m"), (int, float))
        }
    )
    yaw_values = sorted(
        {
            float(f["camera"]["approx_yaw_deg"])
            for f in fixtures
            if isinstance(f.get("camera", {}).get("approx_yaw_deg"), (int, float))
        }
    )
    metadata_gaps = []
    missing_distance = sum(1 for f in fixtures if "distance_m" not in f.get("camera", {}))
    missing_yaw = sum(1 for f in fixtures if "approx_yaw_deg" not in f.get("camera", {}))
    if missing_distance:
        metadata_gaps.append(f"camera.distance_m missing for {missing_distance} fixture(s)")
    if missing_yaw:
        metadata_gaps.append(f"camera.approx_yaw_deg missing for {missing_yaw} fixture(s)")
    metadata_gaps.append(
        "plate/background contrast is not a first-class fixture-manifest field; use challenge tags "
        "or future versioned metadata rather than inferring a numeric contrast value"
    )

    return {
        "fixture_count": len(fixtures),
        "synthetic_fixture_count": len(synthetic),
        "non_synthetic_fixture_count": len(non_synthetic),
        "annotated_fixture_count": len(annotated_ids),
        "development_fixture_count": sum(1 for f in fixtures if f.get("purpose") == "development"),
        "validation_fixture_count": sum(1 for f in fixtures if f.get("purpose") == "validation"),
        "held_out_real_validation_fixture_count": len(held_out_real),
        "held_out_real_annotated_fixture_count": len(held_out_real_annotated),
        "exercises": sorted({str(f.get("exercise")) for f in fixtures}),
        "camera_views": sorted({str(f.get("camera", {}).get("view")) for f in fixtures}),
        "camera_movements": sorted({str(f.get("camera", {}).get("movement")) for f in fixtures}),
        "motion_blur_levels": sorted({str(f.get("conditions", {}).get("motion_blur")) for f in fixtures}),
        "occlusion_levels": sorted({str(f.get("conditions", {}).get("occlusion")) for f in fixtures}),
        "lighting_conditions": sorted({str(f.get("conditions", {}).get("lighting")) for f in fixtures}),
        "camera_distance_m_values": distance_values,
        "approx_yaw_deg_values": yaw_values,
        "challenge_tags": sorted(
            {
                str(tag)
                for f in fixtures
                for tag in f.get("conditions", {}).get("challenge_tags", [])
            }
        ),
        "fps_values": fps_values,
        "purposes": sorted({str(f.get("purpose")) for f in fixtures}),
        "metadata_gaps": metadata_gaps,
    }


def artifact(kind: str, path: Path, scope: str) -> dict[str, Any]:
    return {"kind": kind, "path": display_path(path), "sha256": sha256(path), "scope": scope}


def tracker_diagnostics(benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for case in benchmark.get("cases", []):
        implementation = case.get("implementation", {})
        metrics = case.get("metrics", {})
        runtime = case.get("runtime") or {}
        rows.append(
            {
                "case_id": str(case.get("case_id", "")),
                "fixture_id": str(case.get("fixture_id", "")),
                "implementation": str(implementation.get("name", "")),
                "plate_center_mae_px": metrics.get("plate_center_mae_px"),
                "plate_center_rmse_px": metrics.get("plate_center_rmse_px"),
                "tracking_availability": metrics.get("tracking_availability"),
                "lost_frame_percentage": metrics.get("lost_frame_percentage"),
                "max_consecutive_tracking_loss_samples": metrics.get(
                    "max_consecutive_tracking_loss_samples"
                ),
                "media_seconds_per_wall_second": runtime.get("media_seconds_per_wall_second"),
            }
        )
    return sorted(rows, key=lambda row: (row["implementation"], row["case_id"]))


def gate(gate_id: str, target: str, status: str, rationale: str, evidence: list[str]) -> dict[str, Any]:
    return {
        "id": gate_id,
        "target": target,
        "status": status,
        "rationale": rationale,
        "evidence": evidence,
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    manifest = require_v1(load_json(args.manifest), args.manifest)
    benchmark = require_v1(load_json(args.decoded_tracker_benchmark), args.decoded_tracker_benchmark)
    analysis = require_v1(load_json(args.analysis), args.analysis)
    repeat = require_v1(load_json(args.analysis_repeat), args.analysis_repeat)
    load_json(args.tracker_experiment)
    load_json(args.filter_experiment)

    bench_commit = benchmark.get("git_commit")
    bench_commit = bench_commit if isinstance(bench_commit, str) and bench_commit.strip() else None
    commits = {
        value
        for value in (analysis_commit(analysis), analysis_commit(repeat), bench_commit)
        if value is not None
    }
    if len(commits) > 1:
        raise ValueError(f"evidence artifacts disagree on evaluated git commit: {sorted(commits)}")
    evaluated_commit = next(iter(commits), None)

    coverage = fixture_summary(manifest, args.annotation)
    first_hash = sha256(args.analysis)
    repeat_hash = sha256(args.analysis_repeat)
    deterministic = first_hash == repeat_hash

    artifacts = [
        artifact("fixture_manifest", args.manifest, "public_metadata"),
        *[
            artifact("annotation", path, "public_ground_truth")
            for path in sorted(args.annotation, key=lambda p: p.as_posix())
        ],
        artifact(
            "decoded_tracker_benchmark",
            args.decoded_tracker_benchmark,
            "synthetic_integration",
        ),
        artifact("tracker_experiment", args.tracker_experiment, "procedural_synthetic"),
        artifact(
            "filter_experiment",
            args.filter_experiment,
            "synthetic_development_and_held_out",
        ),
        artifact("analysis", args.analysis, "synthetic_integration"),
        artifact("analysis_repeat", args.analysis_repeat, "synthetic_integration"),
    ]

    no_real_reference = coverage["held_out_real_annotated_fixture_count"] == 0
    tracking_reason = (
        "No annotated non-synthetic fixture with purpose=validation is present in the supplied "
        "public evidence; synthetic integration results remain diagnostics and are excluded from "
        "real-world gate decisions."
        if no_real_reference
        else "This preflight does not promote real/reference measurements to a gate decision; "
        "#14 must freeze the candidate configuration and evaluate the held-out reference set."
    )
    gates = [
        gate(
            "plate_center_tracking_mae",
            "< 3 px",
            NOT_MEASURABLE,
            tracking_reason,
            [display_path(args.decoded_tracker_benchmark), display_path(args.manifest)],
        ),
        gate(
            "tracking_availability",
            "> 99%",
            NOT_MEASURABLE,
            tracking_reason,
            [display_path(args.decoded_tracker_benchmark), display_path(args.manifest)],
        ),
        gate(
            "range_of_motion_mae",
            "< 0.01 m",
            NOT_MEASURABLE,
            "No matched non-synthetic calibrated position/ROM reference artifact is supplied.",
            [display_path(args.analysis)],
        ),
        gate(
            "mean_velocity_mae",
            "< 0.05 m/s",
            NOT_MEASURABLE,
            "No matched non-synthetic velocity reference with equivalent interval/filter semantics is supplied.",
            [display_path(args.analysis)],
        ),
        gate(
            "peak_velocity_mae",
            "< 0.10 m/s",
            NOT_MEASURABLE,
            "No matched non-synthetic signed-axis peak-velocity reference with equivalent semantics is supplied.",
            [display_path(args.analysis)],
        ),
        gate(
            "repeat_analysis_determinism",
            "100%",
            PASS if deterministic else FAIL,
            (
                "Two independently executed canonical analyze outputs are byte-identical."
                if deterministic
                else "Repeated canonical analyze outputs differ byte-for-byte."
            ),
            [display_path(args.analysis), display_path(args.analysis_repeat)],
        ),
        gate(
            "offline_processing",
            "faster than video duration on reference hardware",
            NOT_MEASURABLE,
            "CI smoke runtime is environment-sensitive and no M0 reference-hardware run is supplied; it is not promoted to the product performance gate.",
            [display_path(args.decoded_tracker_benchmark)],
        ),
    ]

    blockers = []
    if no_real_reference:
        blockers.append(
            {
                "id": "real_annotated_validation_corpus",
                "description": "Add annotated non-synthetic validation fixtures across the intended M0 recording conditions and benchmark frozen tracker configurations on them.",
                "blocks": ["tracking_accuracy_gates", "recording_envelope", "go_no_go"],
            }
        )
    blockers.extend(
        [
            {
                "id": "calibrated_reference_metrics",
                "description": "Add matched reference position/ROM and semantically equivalent velocity evidence before deciding calibrated/kinematic gates.",
                "blocks": ["rom_gate", "velocity_gates", "recording_envelope", "go_no_go"],
            },
            {
                "id": "reference_hardware_runtime",
                "description": "Run the frozen candidate on documented reference hardware before deciding the offline-processing gate.",
                "blocks": ["offline_processing_gate", "go_no_go"],
            },
        ]
    )

    result = {
        "schema_version": SCHEMA_VERSION,
        "evidence_version": EVIDENCE_VERSION,
        "scope": "public_m0_preflight",
        "overall_status": "BLOCKED_ON_REFERENCE_EVIDENCE" if deterministic else "PRECHECK_FAILED",
        "dataset_coverage": coverage,
        "analysis_determinism": {
            "first_sha256": first_hash,
            "repeat_sha256": repeat_hash,
            "byte_identical": deterministic,
        },
        "synthetic_tracker_diagnostics": tracker_diagnostics(benchmark),
        "gate_statuses": gates,
        "blockers": blockers,
        "artifacts": artifacts,
    }
    if evaluated_commit:
        result["evaluated_commit"] = evaluated_commit
    return result


def render_markdown(evidence: dict[str, Any]) -> str:
    coverage = evidence["dataset_coverage"]
    lines = [
        "# M0 evidence preflight",
        "",
        "This report is generated from versioned validation artifacts. It is an engineering evidence inventory, not a scientific or marketing accuracy claim.",
        "",
        f"- Scope: `{evidence['scope']}`",
        f"- Overall status: **{evidence['overall_status']}**",
    ]
    if evidence.get("evaluated_commit"):
        lines.append(f"- Evaluated commit: `{evidence['evaluated_commit']}`")
    lines += [
        "",
        "## Dataset coverage",
        "",
        f"- Fixtures: {coverage['fixture_count']} total; {coverage['synthetic_fixture_count']} synthetic; {coverage['non_synthetic_fixture_count']} non-synthetic.",
        f"- Annotated fixtures supplied: {coverage['annotated_fixture_count']}.",
        f"- Annotated held-out non-synthetic validation fixtures: {coverage['held_out_real_annotated_fixture_count']}.",
        f"- Exercises: {', '.join(coverage['exercises']) or 'none'}.",
        f"- Camera views: {', '.join(coverage['camera_views']) or 'none'}.",
        f"- Camera movement: {', '.join(coverage['camera_movements']) or 'none'}.",
        f"- Motion blur: {', '.join(coverage['motion_blur_levels']) or 'none'}; occlusion: {', '.join(coverage['occlusion_levels']) or 'none'}.",
        f"- Lighting: {', '.join(coverage['lighting_conditions']) or 'none'}.",
        f"- FPS values: {', '.join(str(value) for value in coverage['fps_values']) or 'none'}.",
        f"- Camera distances (m): {', '.join(str(value) for value in coverage['camera_distance_m_values']) or 'not recorded'}.",
        f"- Approximate yaw (deg): {', '.join(str(value) for value in coverage['approx_yaw_deg_values']) or 'not recorded'}.",
        f"- Development vs validation fixtures: {coverage['development_fixture_count']} / {coverage['validation_fixture_count']}.",
        f"- Challenge tags: {', '.join(coverage['challenge_tags']) or 'none'}.",
        "",
        "Metadata gaps:",
        *[f"- {gap}" for gap in coverage["metadata_gaps"]],
        "",
        "The public synthetic fixture is useful for integration/regression evidence but is not treated as a substitute for real lifting-video validation.",
        "",
        "## Decoded tracker diagnostics (synthetic integration only)",
        "",
        "| Implementation | MAE (px) | RMSE (px) | Availability | Lost % | Media sec / wall sec |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]

    def fmt(value: Any) -> str:
        return "—" if value is None else f"{value:.6g}" if isinstance(value, float) else str(value)

    for row in evidence["synthetic_tracker_diagnostics"]:
        lines.append(
            f"| `{row['implementation']}` | {fmt(row['plate_center_mae_px'])} | "
            f"{fmt(row['plate_center_rmse_px'])} | {fmt(row['tracking_availability'])} | "
            f"{fmt(row['lost_frame_percentage'])} | {fmt(row['media_seconds_per_wall_second'])} |"
        )
    lines += [
        "",
        "These values exercise the real decode → tracker → benchmark path on the committed synthetic video. They are diagnostic only and do not decide the real-world tracking gates.",
        "",
        "## Provisional engineering gates",
        "",
        "| Gate | Target | Status | Rationale |",
        "| --- | --- | --- | --- |",
    ]
    for item in evidence["gate_statuses"]:
        rationale = item["rationale"].replace("|", "\\|")
        lines.append(
            f"| `{item['id']}` | {item['target']} | **{item['status']}** | {rationale} |"
        )
    lines += ["", "## Remaining evidence blockers", ""]
    for blocker in evidence["blockers"]:
        lines.append(f"- **{blocker['id']}** — {blocker['description']}")
    lines += [
        "",
        "## Interpretation",
        "",
        "The implementation chain is ready to generate evidence, but M0 is not ready for a go/no-go decision from the public corpus alone. Issue #14 remains open until the missing real/reference evidence is supplied; #15 and #16 remain downstream of that evidence.",
        "",
    ]
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    result.add_argument("--manifest", required=True, type=Path)
    result.add_argument("--annotation", action="append", default=[], type=Path)
    result.add_argument("--decoded-tracker-benchmark", required=True, type=Path)
    result.add_argument("--tracker-experiment", required=True, type=Path)
    result.add_argument("--filter-experiment", required=True, type=Path)
    result.add_argument("--analysis", required=True, type=Path)
    result.add_argument("--analysis-repeat", required=True, type=Path)
    result.add_argument("--output-json", required=True, type=Path)
    result.add_argument("--output-markdown", required=True, type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        evidence = build(args)
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        args.output_markdown.write_text(render_markdown(evidence), encoding="utf-8")
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        f"M0 evidence preflight: {evidence['overall_status']} "
        f"({sum(item['status'] == PASS for item in evidence['gate_statuses'])} PASS, "
        f"{sum(item['status'] == FAIL for item in evidence['gate_statuses'])} FAIL, "
        f"{sum(item['status'] == NOT_MEASURABLE for item in evidence['gate_statuses'])} NOT_MEASURABLE_YET)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
