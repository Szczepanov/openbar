#!/usr/bin/env python3
"""Build a fail-closed recording-envelope evidence artifact for OpenBar issue #53.

The tool deliberately does not invent recording cutoffs. A study file declares candidate
boundaries and requested classifications. Non-unknown classifications are accepted only when the
referenced benchmark evidence is held-out/eligible, non-synthetic when required, produced by the
frozen tracker candidate, provenance-consistent, and satisfies preregistered evidence minima.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

ARTIFACT_TYPE = "recording-envelope-evidence-v1"


class EvidenceError(ValueError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EvidenceError(f"failed to read JSON {path}: {error}") from error


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fixture_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list):
        raise EvidenceError("fixture manifest must contain a fixtures array")
    result: dict[str, dict[str, Any]] = {}
    for fixture in fixtures:
        fixture_id = fixture.get("id") if isinstance(fixture, dict) else None
        if not isinstance(fixture_id, str) or not fixture_id:
            raise EvidenceError("fixture without a valid id")
        if fixture_id in result:
            raise EvidenceError(f"duplicate fixture id {fixture_id}")
        result[fixture_id] = fixture
    return result


def implementation_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    return (
        actual.get("name") == expected.get("name")
        and actual.get("version") == expected.get("version")
        and canonical(actual.get("config", {})) == canonical(expected.get("config", {}))
    )


def benchmark_cases(
    study_path: Path, study: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_cases: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for raw_path in study.get("benchmark_results", []):
        result_path = (study_path.parent / raw_path).resolve()
        result = load_json(result_path)
        cases = result.get("cases")
        if not isinstance(cases, list):
            raise EvidenceError(f"{raw_path}: benchmark result has no cases array")
        all_cases.extend(cases)
        provenance.append(
            {
                "path": raw_path,
                "sha256": sha256(result_path),
                "git_commit": result.get("git_commit"),
                "pipeline_version": result.get("pipeline_version"),
                "benchmark_metric_version": result.get("benchmark_metric_version"),
            }
        )
    return all_cases, provenance


def measurement_evidence(study_path: Path, study: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve matched-reference physical/kinematic evidence without assuming its private schema."""
    frozen_commit = study.get("frozen_candidate", {}).get("git_commit")
    result: list[dict[str, Any]] = []
    for item in study.get("measurement_evidence", []):
        raw_path = item["path"]
        path = (study_path.parent / raw_path).resolve()
        document = load_json(path)
        document_commit = document.get("evaluated_commit") or document.get("git_commit")
        declared_commit = item.get("git_commit")
        commit = declared_commit or document_commit
        if declared_commit and document_commit and declared_commit != document_commit:
            raise EvidenceError(
                f"measurement evidence {raw_path} declares git_commit {declared_commit!r} "
                f"but document records {document_commit!r}"
            )
        if frozen_commit and commit != frozen_commit:
            raise EvidenceError(
                f"measurement evidence {raw_path} git_commit {commit!r} "
                f"does not match frozen {frozen_commit!r}"
            )
        result.append(
            {
                "path": raw_path,
                "sha256": sha256(path),
                "git_commit": commit,
                "constructs": item.get("constructs", []),
                "note": item.get("note"),
            }
        )
    return result


_REQUIRED_METRICS = (
    "comparable_samples",
    "plate_center_mae_px",
    "plate_center_rmse_px",
    "plate_center_p50_px",
    "plate_center_p90_px",
    "plate_center_p95_px",
    "plate_center_max_px",
    "tracking_availability",
    "lost_frame_percentage",
    "max_consecutive_tracking_loss_duration_s",
)


def case_summary(case: dict[str, Any]) -> dict[str, Any]:
    metrics = case.get("metrics") or {}
    return {
        "case_id": case.get("case_id"),
        "fixture_id": case.get("fixture_id"),
        "implementation": case.get("implementation"),
        "metrics": {key: metrics.get(key) for key in _REQUIRED_METRICS},
        "warnings": case.get("warnings", []),
    }


def numeric_summary(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {"min": min(values), "median": statistics.median(values), "max": max(values)}


def aggregate_case_metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "plate_center_mae_px",
        "plate_center_rmse_px",
        "plate_center_p95_px",
        "tracking_availability",
        "lost_frame_percentage",
        "max_consecutive_tracking_loss_duration_s",
    )
    result: dict[str, Any] = {}
    for key in keys:
        values = [
            float(case["metrics"][key])
            for case in cases
            if case.get("metrics", {}).get(key) is not None
        ]
        result[key] = numeric_summary(values)
    result["comparable_samples_total"] = sum(
        int(case.get("metrics", {}).get("comparable_samples") or 0) for case in cases
    )
    return result


def assess_boundary(
    boundary: dict[str, Any],
    *,
    fixtures: dict[str, dict[str, Any]],
    cases: list[dict[str, Any]],
    study: dict[str, Any],
    provenance: list[dict[str, Any]],
    measurement_provenance: list[dict[str, Any]],
) -> dict[str, Any]:
    fixture_ids = boundary.get("fixture_ids", [])
    requested = boundary.get("requested_classification", "unknown")
    policy = study["evidence_policy"]
    frozen = study["frozen_candidate"]
    candidate = frozen.get("tracker") or {}
    frozen_commit = frozen.get("git_commit")
    blockers: list[str] = []

    selected_fixtures: list[dict[str, Any]] = []
    for fixture_id in fixture_ids:
        fixture = fixtures.get(fixture_id)
        if fixture is None:
            blockers.append(f"fixture {fixture_id} is absent from the manifest")
        else:
            selected_fixtures.append(fixture)

    selected_cases = [case for case in cases if case.get("fixture_id") in fixture_ids]
    matching_cases = [
        case
        for case in selected_cases
        if implementation_matches(case.get("implementation") or {}, candidate)
    ]

    eligible_purposes = set(policy.get("eligible_purposes", ["validation"]))
    require_non_synthetic = bool(policy.get("require_non_synthetic", True))
    eligible_fixtures: list[dict[str, Any]] = []
    for fixture in selected_fixtures:
        purpose = fixture.get("purpose")
        kind = (fixture.get("source") or {}).get("kind")
        if purpose not in eligible_purposes:
            blockers.append(f"fixture {fixture['id']} has ineligible purpose {purpose!r}")
            continue
        if require_non_synthetic and kind == "synthetic":
            blockers.append(f"fixture {fixture['id']} is synthetic")
            continue
        eligible_fixtures.append(fixture)

    eligible_ids = {fixture["id"] for fixture in eligible_fixtures}
    eligible_matching_cases = [
        case for case in matching_cases if case.get("fixture_id") in eligible_ids
    ]
    matching_by_fixture = {case.get("fixture_id") for case in eligible_matching_cases}
    for fixture in eligible_fixtures:
        if fixture["id"] not in matching_by_fixture:
            blockers.append(
                f"fixture {fixture['id']} has no benchmark case for the frozen tracker"
            )

    for case in selected_cases:
        if not implementation_matches(case.get("implementation") or {}, candidate):
            blockers.append(
                f"case {case.get('case_id')} was produced by a different tracker candidate"
            )
            continue
        if case.get("fixture_id") not in eligible_ids:
            continue
        metrics = case.get("metrics") or {}
        missing = [key for key in _REQUIRED_METRICS if metrics.get(key) is None]
        if missing:
            blockers.append(
                f"case {case.get('case_id')} lacks required metric(s): {', '.join(missing)}"
            )

    if frozen_commit:
        if not provenance:
            blockers.append("no benchmark result provenance is present")
        for item in provenance:
            if item.get("git_commit") != frozen_commit:
                blockers.append(
                    f"benchmark result {item['path']} git_commit {item.get('git_commit')!r} "
                    f"does not match frozen {frozen_commit!r}"
                )

    distinct_eligible = len({fixture["id"] for fixture in eligible_fixtures})
    comparable_total = sum(
        int(case.get("metrics", {}).get("comparable_samples") or 0)
        for case in eligible_matching_cases
    )

    if requested != "unknown":
        required_constructs = {"calibrated_position", "velocity"}
        provided_constructs = {
            construct
            for item in measurement_provenance
            for construct in item.get("constructs", [])
        }
        missing_constructs = sorted(required_constructs - provided_constructs)
        if missing_constructs:
            blockers.append(
                "matched-reference measurement evidence is missing construct(s): "
                + ", ".join(missing_constructs)
            )
        if frozen.get("status") != "frozen":
            blockers.append("tracker/filter candidate is not frozen")
        min_fixtures = policy.get("minimum_distinct_held_out_fixtures_per_boundary")
        min_samples = policy.get("minimum_comparable_samples_per_boundary")
        if min_fixtures is None or min_samples is None:
            blockers.append(
                "evidence minima are not preregistered; non-unknown classification is forbidden"
            )
        else:
            if distinct_eligible < min_fixtures:
                blockers.append(
                    f"eligible fixture count {distinct_eligible} is below preregistered minimum {min_fixtures}"
                )
            if comparable_total < min_samples:
                blockers.append(
                    f"comparable sample count {comparable_total} is below preregistered minimum {min_samples}"
                )
        if not fixture_ids:
            blockers.append("boundary references no fixtures")
        if not eligible_matching_cases:
            blockers.append(
                "boundary references no eligible benchmark cases for the frozen tracker"
            )

    if requested != "unknown" and blockers:
        raise EvidenceError(
            f"boundary {boundary.get('id')}: cannot promote to {requested}: "
            + "; ".join(blockers)
        )

    roles: dict[str, int] = {}
    source_kinds: dict[str, int] = {}
    for fixture in selected_fixtures:
        role = fixture.get("purpose", "unknown")
        roles[role] = roles.get(role, 0) + 1
        kind = (fixture.get("source") or {}).get("kind", "unknown")
        source_kinds[kind] = source_kinds.get(kind, 0) + 1

    return {
        "id": boundary["id"],
        "dimension": boundary["dimension"],
        "variable": boundary["variable"],
        "unit": boundary.get("unit"),
        "tested_values": boundary.get("tested_values", []),
        "fixture_ids": fixture_ids,
        "requested_classification": requested,
        "classification": requested,
        "decision_rationale": boundary.get("decision_rationale", ""),
        "evidence": {
            "fixture_count": len(selected_fixtures),
            "eligible_fixture_count": distinct_eligible,
            "roles": roles,
            "source_kinds": source_kinds,
            "benchmark_case_count": len(eligible_matching_cases),
            "comparable_samples": comparable_total,
            "case_metric_summary": aggregate_case_metrics(eligible_matching_cases),
            "cases": [case_summary(case) for case in eligible_matching_cases],
        },
        "blockers": blockers,
    }


def build_artifact(study_path: Path) -> dict[str, Any]:
    study = load_json(study_path)
    if study.get("schema_version") != 1:
        raise EvidenceError("study schema_version must be 1")
    manifest_path = (study_path.parent / study["fixture_manifest"]).resolve()
    manifest = load_json(manifest_path)
    fixtures = fixture_index(manifest)
    cases, provenance = benchmark_cases(study_path, study)
    measurement_provenance = measurement_evidence(study_path, study)
    boundaries = [
        assess_boundary(
            boundary,
            fixtures=fixtures,
            cases=cases,
            study=study,
            provenance=provenance,
            measurement_provenance=measurement_provenance,
        )
        for boundary in study.get("boundaries", [])
    ]

    policy = study["evidence_policy"]
    readiness_blockers: list[str] = []
    if study["frozen_candidate"].get("status") != "frozen":
        readiness_blockers.append("tracker/filter candidate is not frozen")
    if policy.get("minimum_distinct_held_out_fixtures_per_boundary") is None:
        readiness_blockers.append("minimum held-out fixture count is not preregistered")
    if policy.get("minimum_comparable_samples_per_boundary") is None:
        readiness_blockers.append("minimum comparable sample count is not preregistered")
    if not provenance:
        readiness_blockers.append("no benchmark result artifacts are referenced")
    provided_constructs = {
        construct
        for item in measurement_provenance
        for construct in item.get("constructs", [])
    }
    for construct in ("calibrated_position", "velocity"):
        if construct not in provided_constructs:
            readiness_blockers.append(
                f"no matched-reference measurement evidence covers {construct}"
            )

    eligible_purposes = set(policy.get("eligible_purposes", ["validation"]))
    eligible_real = [
        fixture
        for fixture in fixtures.values()
        if fixture.get("purpose") in eligible_purposes
        and (
            not policy.get("require_non_synthetic", True)
            or (fixture.get("source") or {}).get("kind") != "synthetic"
        )
    ]
    if not eligible_real:
        readiness_blockers.append(
            "fixture manifest contains no eligible held-out non-synthetic fixtures"
        )

    return {
        "schema_version": 1,
        "artifact_type": ARTIFACT_TYPE,
        "study_id": study["study_id"],
        "fixture_manifest": {
            "path": study["fixture_manifest"],
            "sha256": sha256(manifest_path),
        },
        "frozen_candidate": study["frozen_candidate"],
        "evidence_policy": study["evidence_policy"],
        "benchmark_results": provenance,
        "measurement_evidence": measurement_provenance,
        "readiness": {
            "can_promote_boundaries": not readiness_blockers,
            "blockers": readiness_blockers,
        },
        "boundaries": boundaries,
    }


def render_report(artifact: dict[str, Any]) -> str:
    lines = [
        "# Recording-envelope evidence study",
        "",
        f"Study: `{artifact['study_id']}`  ",
        "Issue: #53",
        "",
        "This report is evidence bookkeeping, not a marketing accuracy claim. Non-unknown boundaries are",
        "accepted only when the study's preregistered evidence policy and frozen-candidate checks pass.",
        "",
        f"Ready to promote boundaries: **{artifact['readiness']['can_promote_boundaries']}**.",
        *[
            f"- Blocker: {item}"
            for item in artifact["readiness"]["blockers"]
        ],
        "",
        "| Boundary | Dimension / variable | Tested values | Eligible fixtures | Comparable samples | Classification | Rationale |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for item in artifact["boundaries"]:
        tested = ", ".join(str(value) for value in item["tested_values"]) or "not yet tested"
        rationale = item["decision_rationale"].replace("|", "\\|") or "none"
        lines.append(
            f"| {item['id']} | {item['dimension']} / `{item['variable']}` | {tested} | "
            f"{item['evidence']['eligible_fixture_count']} | "
            f"{item['evidence']['comparable_samples']} | "
            f"**{item['classification']}** | {rationale} |"
        )
    lines.extend(
        [
            "",
            "## Frozen candidate",
            "",
            "```json",
            json.dumps(artifact["frozen_candidate"], indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        artifact = build_artifact(args.study.resolve())
    except EvidenceError as error:
        parser.error(str(error))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_report(artifact), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
