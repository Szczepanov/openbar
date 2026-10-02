#!/usr/bin/env python3
"""Held-out tracker/filter selection study for issue #57.

The tool orchestrates OpenBar's authoritative Rust CLI and existing validation
contracts. It never tunes on held-out fixtures and never treats synthetic or
development clips as production selection evidence.

Typical flow:

  freeze   -> lock candidate configurations and the validation manifest hash
  preflight -> prove held-out real annotations/seeds/repeatability are present
  evaluate -> run the frozen candidates and emit aggregate evidence
  finalize -> record an explicit select/reject decision without weakening gates
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path
from typing import Any

import m0_evidence as evidence_tool
import m0_private_evidence as private_evidence

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 1
STUDY_VERSION = "m0-tracker-filter-selection-v1"
REQUIRED_EXERCISES = ("clean", "snatch", "back_squat")
MAE_GATE_PX = 3.0
AVAILABILITY_GATE = 0.99
HIGH_CONFIDENCE = 0.8
FALSE_TRACK_ERROR_PX = 3.0

TRACKER_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "id": "template-sad-v1",
        "cli_name": "template",
        "version": "1",
        "expected_config": {
            "search_radius_px": "12",
            "low_confidence_normalized_mean_absolute_difference": "0.1",
            "max_normalized_mean_absolute_difference": "0.2",
            "seed_timestamp_tolerance_s": "0.0005",
        },
    },
    {
        "id": "local-contrast-centroid-v1",
        "cli_name": "contrast",
        "version": "1",
        "expected_config": {
            "search_radius_px": "12",
            "min_seed_contrast": "12",
            "min_mass_ratio": "0.3",
            "low_confidence_mass_ratio": "0.6",
            "seed_timestamp_tolerance_s": "0.0005",
        },
    },
)

FILTER_CLI_NAMES = {
    "raw-identity": "raw",
    "centered-moving-average": "moving-average",
    "timestamp-aware-savitzky-golay": "savitzky-golay",
    "constant-velocity-kalman": "kalman",
}

FILTER_PARAMETER_FLAGS = {
    "window": "--filter-window",
    "polynomial_order": "--filter-polynomial-order",
    "max_gap_s": "--filter-max-gap-s",
    "acceleration_variance_m2_s4": "--filter-acceleration-variance-m2-s4",
    "measurement_variance_m2": "--filter-measurement-variance-m2",
    "initial_velocity_variance_m2_s2": "--filter-initial-velocity-variance-m2-s2",
    "confidence_window_samples": "--filter-confidence-window-samples",
}


class SelectionError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    return evidence_tool.load_json(path)


def write_json(path: Path, value: Any) -> None:
    evidence_tool.write_json(path, value)


def sha256(path: Path) -> str:
    return evidence_tool.sha256(path)


def validation_fixtures(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return held-out non-synthetic side/fixed fixtures, failing on scope violations."""
    fixtures: list[dict[str, Any]] = []
    for item in manifest.get("fixtures", []):
        if item.get("purpose") != "validation":
            continue
        if item.get("source", {}).get("kind") == "synthetic":
            raise SelectionError(
                f"{item.get('id', '<unknown>')}: validation fixture is synthetic; "
                "issue #57 requires held-out non-synthetic evidence"
            )
        camera = item.get("camera", {})
        if camera.get("view") != "side" or camera.get("movement") != "fixed":
            raise SelectionError(
                f"{item.get('id', '<unknown>')}: held-out selection fixture must be side/fixed"
            )
        fixtures.append(item)
    return fixtures


def exercise_coverage(fixtures: list[dict[str, Any]]) -> dict[str, int]:
    return {
        exercise: sum(1 for fixture in fixtures if fixture.get("exercise") == exercise)
        for exercise in REQUIRED_EXERCISES
    }


def _private_path(manifest: Path, subdir: str, fixture_id: str, suffix: str) -> Path:
    return manifest.parent / subdir / f"{fixture_id}.{suffix}"


def _labelled_count(annotation: dict[str, Any]) -> int:
    return evidence_tool.comparable_labelled_samples(annotation)


def _require_media(fixture: dict[str, Any]) -> tuple[str, str]:
    media = fixture.get("media") or {}
    repository_path = media.get("repository_path")
    media_sha = media.get("sha256")
    if not repository_path or not media_sha:
        raise SelectionError(
            f"{fixture['id']}: held-out fixture requires media.repository_path and media.sha256"
        )
    return str(repository_path), str(media_sha)


def preflight_inputs(manifest_path: Path, freeze: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    if manifest.get("schema_version") != 1:
        raise SelectionError("fixture manifest must use schema_version 1")
    if freeze is not None and freeze.get("manifest_sha256") != sha256(manifest_path):
        raise SelectionError(
            "validation manifest changed after candidate freeze; create a new freeze before evaluation"
        )

    fixtures = validation_fixtures(manifest)
    coverage = exercise_coverage(fixtures)
    missing = [name for name, count in coverage.items() if count == 0]
    if missing:
        raise SelectionError(
            "held-out non-synthetic validation coverage is missing required exercise(s): "
            + ", ".join(missing)
        )

    rows = []
    for fixture in fixtures:
        fixture_id = fixture["id"]
        repository_path, expected_sha = _require_media(fixture)
        media_path = ROOT / repository_path
        if not media_path.is_file():
            raise SelectionError(f"{fixture_id}: media file is missing at {repository_path}")
        if sha256(media_path).lower() != expected_sha.lower():
            raise SelectionError(f"{fixture_id}: media sha256 does not match manifest")

        seed_path = _private_path(
            manifest_path, "seeds", fixture_id, "manual-target-seed-v1.json"
        )
        annotation_path = _private_path(
            manifest_path, "annotations", fixture_id, "annotation-v1.json"
        )
        repeatability_path = _private_path(
            manifest_path, "annotations", fixture_id, "repeatability.json"
        )
        for label, path in (
            ("manual seed", seed_path),
            ("annotation", annotation_path),
            ("repeatability report", repeatability_path),
        ):
            if not path.is_file():
                raise SelectionError(f"{fixture_id}: {label} is missing at {path}")

        seed = load_json(seed_path)
        annotation = load_json(annotation_path)
        repeatability = load_json(repeatability_path)
        if seed.get("fixture_id") not in (None, fixture_id):
            raise SelectionError(f"{fixture_id}: seed fixture_id does not match")
        if annotation.get("fixture_id") != fixture_id:
            raise SelectionError(f"{fixture_id}: annotation fixture_id does not match")
        source_sha = annotation.get("source_video_sha256")
        if source_sha and source_sha.lower() != expected_sha.lower():
            raise SelectionError(f"{fixture_id}: annotation source sha256 does not match manifest")
        labelled = _labelled_count(annotation)
        if labelled == 0:
            raise SelectionError(f"{fixture_id}: annotation has no comparable labelled samples")
        if repeatability.get("fixture_id") != fixture_id:
            raise SelectionError(f"{fixture_id}: repeatability fixture_id does not match")
        if int(repeatability.get("matching_labelled_samples", 0)) <= 0:
            raise SelectionError(
                f"{fixture_id}: repeatability report has no matching labelled samples"
            )

        rows.append(
            {
                "fixture_id": fixture_id,
                "exercise": fixture["exercise"],
                "labelled_samples": labelled,
                "repeatability_matching_samples": int(
                    repeatability["matching_labelled_samples"]
                ),
                "conditions": {
                    "nominal_fps": fixture.get("video", {}).get("nominal_fps"),
                    "measured_fps": fixture.get("video", {}).get("measured_fps"),
                    "motion_blur": fixture.get("conditions", {}).get("motion_blur"),
                    "occlusion": fixture.get("conditions", {}).get("occlusion"),
                    "plate_visibility": fixture.get("conditions", {}).get("plate_visibility"),
                    "lighting": fixture.get("conditions", {}).get("lighting"),
                    "challenge_tags": fixture.get("conditions", {}).get(
                        "challenge_tags", []
                    ),
                },
            }
        )

    return {
        "ready": True,
        "fixture_count": len(fixtures),
        "exercise_coverage": coverage,
        "fixtures": rows,
        "manifest_sha256": sha256(manifest_path),
    }


def extract_filter_candidates(filter_experiment: dict[str, Any]) -> list[dict[str, Any]]:
    if filter_experiment.get("schema_version") != 3:
        raise SelectionError("filter experiment must use schema_version 3")
    candidates = []
    for family in filter_experiment.get("development", []):
        selected = family.get("selected") or {}
        implementation = selected.get("implementation")
        if implementation not in FILTER_CLI_NAMES:
            raise SelectionError(
                f"unsupported selected filter implementation: {implementation!r}"
            )
        parameters = dict(selected.get("parameters") or {})
        unknown = sorted(set(parameters) - set(FILTER_PARAMETER_FLAGS))
        if unknown:
            raise SelectionError(
                f"{implementation}: unsupported frozen filter parameter(s): {unknown}"
            )
        args: list[str] = ["--filter", FILTER_CLI_NAMES[implementation]]
        for name in sorted(parameters):
            args.extend([FILTER_PARAMETER_FLAGS[name], str(parameters[name])])
        candidates.append(
            {
                "family": family["family"],
                "implementation": selected,
                "cli_args": args,
            }
        )
    if {item["family"] for item in candidates} != {
        "raw",
        "moving_average",
        "savitzky_golay",
        "kalman",
    }:
        raise SelectionError("filter experiment did not freeze all four M0 filter families")
    return candidates


def freeze_study(manifest_path: Path, output: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    fixtures = validation_fixtures(manifest)
    coverage = exercise_coverage(fixtures)
    missing = [name for name, count in coverage.items() if count == 0]
    if missing:
        raise SelectionError(
            "cannot freeze final held-out study: validation manifest is missing "
            + ", ".join(missing)
        )

    with tempfile.TemporaryDirectory(prefix="openbar-filter-freeze-") as tmp:
        filter_path = Path(tmp) / "filter-experiment.json"
        evidence_tool.cli(
            ["filter-experiment", "--output", str(filter_path)], release=True
        )
        filter_candidates = extract_filter_candidates(load_json(filter_path))

    document = {
        "schema_version": SCHEMA_VERSION,
        "study_version": STUDY_VERSION,
        "evaluated_commit": evidence_tool.evaluated_commit(),
        "manifest_sha256": sha256(manifest_path),
        "validation_fixture_ids": [fixture["id"] for fixture in fixtures],
        "gate_targets": {
            "plate_center_mae_px": {"operator": "<", "value": MAE_GATE_PX},
            "tracking_availability": {"operator": ">", "value": AVAILABILITY_GATE},
        },
        "false_track_diagnostic": {
            "confidence_gte": HIGH_CONFIDENCE,
            "position_error_px_gt": FALSE_TRACK_ERROR_PX,
            "note": (
                "Diagnostic only: tracker confidence is algorithm-specific and is not "
                "treated as a calibrated probability."
            ),
        },
        "tracker_candidates": list(TRACKER_CANDIDATES),
        "filter_candidates": filter_candidates,
        "selection_policy": {
            "tracker": (
                "A tracker can be selected only after the original MAE and availability "
                "gates are evaluated on this frozen held-out set. False-track and condition "
                "breakdowns remain decision evidence even when aggregate gates pass."
            ),
            "filter": (
                "The #10 per-family configurations are frozen here. Final family selection "
                "must consider real annotation position error together with the already "
                "recorded synthetic velocity/peak/lag trade-offs; do not retune on this set."
            ),
        },
    }
    write_json(output, document)
    return document


def _nearest_prediction(
    samples: list[dict[str, Any]], timestamp_s: float, tolerance_s: float
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_delta = math.inf
    for sample in samples:
        delta = abs(float(sample["timestamp_s"]) - timestamp_s)
        if delta < best_delta:
            best, best_delta = sample, delta
    return best if best is not None and best_delta <= tolerance_s else None


def false_track_diagnostics(
    annotation: dict[str, Any],
    prediction: dict[str, Any],
    *,
    confidence_threshold: float = HIGH_CONFIDENCE,
    error_threshold_px: float = FALSE_TRACK_ERROR_PX,
) -> dict[str, Any]:
    tolerance = float(annotation["timebase"]["decoder_match_tolerance_s"])
    prediction_samples = list(prediction.get("samples") or [])
    compared = 0
    high_confidence = 0
    false_tracks = 0
    worst_error = None
    examples: list[dict[str, float]] = []

    for reference in annotation.get("samples", []):
        if (
            reference.get("annotation_state") != "labelled"
            or reference.get("quality") == "unusable"
        ):
            continue
        actual = _nearest_prediction(
            prediction_samples, float(reference["timestamp_s"]), tolerance
        )
        if actual is None or actual.get("state") != "tracked":
            continue
        center = actual.get("center_px")
        confidence = actual.get("confidence")
        if center is None or confidence is None:
            continue
        dx = float(center["x_px"]) - float(reference["center_px"]["x_px"])
        dy = float(center["y_px"]) - float(reference["center_px"]["y_px"])
        error = math.hypot(dx, dy)
        compared += 1
        worst_error = error if worst_error is None else max(worst_error, error)
        if float(confidence) >= confidence_threshold:
            high_confidence += 1
            if error > error_threshold_px:
                false_tracks += 1
                if len(examples) < 10:
                    examples.append(
                        {
                            "timestamp_s": float(reference["timestamp_s"]),
                            "confidence": float(confidence),
                            "error_px": error,
                        }
                    )

    return {
        "comparable_tracked_samples": compared,
        "high_confidence_samples": high_confidence,
        "high_confidence_false_track_samples": false_tracks,
        "high_confidence_false_track_fraction": (
            false_tracks / high_confidence if high_confidence else None
        ),
        "max_tracked_error_px": worst_error,
        "examples": examples,
    }


def gate_status(metrics: dict[str, Any]) -> dict[str, str]:
    mae = metrics.get("plate_center_mae_px")
    availability = metrics.get("tracking_availability")
    return {
        "plate_center_mae": (
            "PASS" if mae is not None and float(mae) < MAE_GATE_PX else "FAIL"
        ),
        "tracking_availability": (
            "PASS"
            if availability is not None and float(availability) > AVAILABILITY_GATE
            else "FAIL"
        ),
    }


def filter_cli_args(candidate: dict[str, Any]) -> list[str]:
    return [str(value) for value in candidate["cli_args"]]


def analyze_with_filter(
    *,
    manifest: Path,
    fixture: dict[str, Any],
    seed: Path,
    tracker_cli_name: str,
    filter_candidate: dict[str, Any],
    output: Path,
) -> dict[str, Any]:
    command = [
        "analyze",
        "--manifest",
        str(manifest),
        "--fixture",
        fixture["id"],
        "--seed",
        str(seed),
        "--plate-diameter-m",
        str(fixture["load"]["plate_diameter_m"]),
        "--tracker",
        tracker_cli_name,
        *filter_cli_args(filter_candidate),
        "--kinematics-max-gap-s",
        evidence_tool.KINEMATICS_MAX_GAP_S,
        "--kinematics-min-confidence",
        evidence_tool.KINEMATICS_MIN_CONFIDENCE,
        "--diagnostics",
        "quiet",
        "--output",
        str(output),
    ]
    evidence_tool.cli(command, release=True)
    return load_json(output)


def _match_metric_sample(
    samples: list[dict[str, Any]], timestamp_s: float, tolerance_s: float
) -> dict[str, Any] | None:
    best = None
    best_delta = math.inf
    for sample in samples:
        delta = abs(float(sample["timestamp_s"]) - timestamp_s)
        if delta < best_delta:
            best, best_delta = sample, delta
    return best if best is not None and best_delta <= tolerance_s else None


def filtered_position_metrics(
    annotation: dict[str, Any], analysis: dict[str, Any]
) -> dict[str, Any]:
    calibration = analysis["calibration"]
    scale = float(calibration["scale"]["metres_per_pixel"])
    reference_center = calibration["reference"]["geometry"]["center_px"]
    reference_x = float(reference_center["x_px"])
    reference_y = float(reference_center["y_px"])
    filtered = analysis.get("derived", {}).get("filtered")
    if not filtered:
        raise SelectionError("canonical analysis is missing the filtered layer")
    samples = list(filtered.get("samples") or [])
    tolerance = float(annotation["timebase"]["decoder_match_tolerance_s"])
    errors: list[float] = []

    for reference in annotation.get("samples", []):
        if (
            reference.get("annotation_state") != "labelled"
            or reference.get("quality") == "unusable"
        ):
            continue
        actual = _match_metric_sample(
            samples, float(reference["timestamp_s"]), tolerance
        )
        if actual is None:
            continue
        center = reference["center_px"]
        truth_x = (float(center["x_px"]) - reference_x) * scale
        truth_y = (reference_y - float(center["y_px"])) * scale
        errors.append(
            math.hypot(
                float(actual["x_m"]) - truth_x,
                float(actual["y_m"]) - truth_y,
            )
        )

    return {
        "comparable_samples": len(errors),
        "position_mae_m": sum(errors) / len(errors) if errors else None,
        "position_rmse_m": (
            math.sqrt(sum(value * value for value in errors) / len(errors))
            if errors
            else None
        ),
        "position_max_error_m": max(errors) if errors else None,
        "filter": filtered["filter"],
    }


def evaluate_study(
    manifest_path: Path, freeze_path: Path, output_dir: Path
) -> dict[str, Any]:
    freeze = load_json(freeze_path)
    if freeze.get("schema_version") != SCHEMA_VERSION:
        raise SelectionError("freeze uses an unsupported schema_version")
    if freeze.get("study_version") != STUDY_VERSION:
        raise SelectionError("freeze uses an unsupported study_version")
    if freeze.get("evaluated_commit") != evidence_tool.evaluated_commit():
        raise SelectionError(
            "code commit changed after candidate freeze; regenerate the freeze before evaluation"
        )

    readiness = preflight_inputs(manifest_path, freeze)
    manifest = load_json(manifest_path)
    fixtures = validation_fixtures(manifest)
    output_dir.mkdir(parents=True, exist_ok=True)

    evaluated_fixtures = []
    for fixture in fixtures:
        fixture_id = fixture["id"]
        fixture_dir = output_dir / "fixtures" / fixture_id
        facts = private_evidence.evaluate_fixture(
            manifest_path,
            fixture,
            output_dir / "tracker-evidence",
            freeze["evaluated_commit"],
        )
        annotation_path = _private_path(
            manifest_path, "annotations", fixture_id, "annotation-v1.json"
        )
        seed_path = _private_path(
            manifest_path, "seeds", fixture_id, "manual-target-seed-v1.json"
        )
        annotation = load_json(annotation_path)

        false_tracks = {}
        for tracker in freeze["tracker_candidates"]:
            prediction_path = (
                output_dir
                / "tracker-evidence"
                / fixture_id
                / "tracker-run"
                / f"{fixture_id}.{tracker['id']}.prediction-v1.json"
            )
            if not prediction_path.exists():
                false_tracks[tracker["id"]] = {
                    "status": "unavailable",
                    "reason": "tracker did not produce a prediction stream",
                }
                continue
            prediction = load_json(prediction_path)
            actual_config = (
                prediction.get("implementation", {})
                .get("config", {})
                .get("tracker_config", {})
            )
            if actual_config != tracker["expected_config"]:
                raise SelectionError(
                    f"{fixture_id}: {tracker['id']} effective config differs from frozen config"
                )
            false_tracks[tracker["id"]] = {
                "status": "evaluated",
                **false_track_diagnostics(annotation, prediction),
            }

        filter_results = []
        for tracker in freeze["tracker_candidates"]:
            tracker_id = tracker["id"]
            tracker_fact = next(
                (
                    item
                    for item in facts.get("trackers", [])
                    if item["implementation"] == tracker_id
                ),
                None,
            )
            if tracker_fact is None or tracker_fact.get("status") != "ok":
                continue
            for filter_candidate in freeze["filter_candidates"]:
                output = (
                    fixture_dir
                    / f"{tracker_id}.{filter_candidate['family']}.analysis-v1.json"
                )
                analysis = analyze_with_filter(
                    manifest=manifest_path,
                    fixture=fixture,
                    seed=seed_path,
                    tracker_cli_name=tracker["cli_name"],
                    filter_candidate=filter_candidate,
                    output=output,
                )
                filter_results.append(
                    {
                        "tracker_id": tracker_id,
                        "filter_family": filter_candidate["family"],
                        **filtered_position_metrics(annotation, analysis),
                    }
                )

        evaluated_fixtures.append(
            {
                "fixture_id": fixture_id,
                "exercise": fixture["exercise"],
                "conditions": readiness["fixtures"][
                    [row["fixture_id"] for row in readiness["fixtures"]].index(fixture_id)
                ]["conditions"],
                "tracker_benchmarks": facts.get("benchmarks", []),
                "false_track_diagnostics": false_tracks,
                "filter_position_results": filter_results,
                "runtime": facts.get("trackers", []),
                "deterministic": facts.get("benchmark_outputs_identical"),
            }
        )

    tracker_summary = summarize_trackers(evaluated_fixtures, freeze)
    filter_summary = summarize_filters(evaluated_fixtures, freeze)
    document = {
        "schema_version": SCHEMA_VERSION,
        "study_version": STUDY_VERSION,
        "evaluated_commit": freeze["evaluated_commit"],
        "manifest_sha256": freeze["manifest_sha256"],
        "freeze_sha256": sha256(freeze_path),
        "readiness": readiness,
        "tracker_results": tracker_summary,
        "filter_results": filter_summary,
        "fixture_results": evaluated_fixtures,
        "decision": {
            "status": "pending_review",
            "selected_tracker": None,
            "selected_filter_family": None,
            "rationale": (
                "Evaluation is complete; selection/rejection must be recorded explicitly "
                "with the finalize command."
            ),
        },
    }
    write_json(output_dir / "tracker-filter-selection-evidence-v1.json", document)
    return document


def summarize_trackers(
    fixtures: list[dict[str, Any]], freeze: dict[str, Any]
) -> list[dict[str, Any]]:
    summaries = []
    for tracker in freeze["tracker_candidates"]:
        tracker_id = tracker["id"]
        metrics = []
        false_track_samples = 0
        high_confidence_samples = 0
        for fixture in fixtures:
            benchmark = next(
                (
                    item
                    for item in fixture["tracker_benchmarks"]
                    if item["implementation"].startswith(f"{tracker_id}@")
                ),
                None,
            )
            if benchmark is not None:
                metrics.append(benchmark["metrics"])
            diagnostic = fixture["false_track_diagnostics"].get(tracker_id, {})
            if diagnostic.get("status") == "evaluated":
                false_track_samples += int(
                    diagnostic["high_confidence_false_track_samples"]
                )
                high_confidence_samples += int(diagnostic["high_confidence_samples"])

        total_comparable = sum(int(item["comparable_samples"]) for item in metrics)
        total_tracked = sum(int(item["tracked_samples"]) for item in metrics)
        total_lost = sum(int(item["lost_samples"]) for item in metrics)
        errors_weighted = [
            (float(item["plate_center_mae_px"]), int(item["tracked_samples"]))
            for item in metrics
            if item.get("plate_center_mae_px") is not None and int(item["tracked_samples"]) > 0
        ]
        mae = (
            sum(value * count for value, count in errors_weighted)
            / sum(count for _, count in errors_weighted)
            if errors_weighted
            else None
        )
        availability = (
            total_tracked / total_comparable if total_comparable else None
        )
        aggregate = {
            "comparable_samples": total_comparable,
            "tracked_samples": total_tracked,
            "lost_samples": total_lost,
            "plate_center_mae_px": mae,
            "tracking_availability": availability,
        }
        summaries.append(
            {
                "tracker_id": tracker_id,
                "aggregate": aggregate,
                "gate_status": gate_status(aggregate),
                "high_confidence_false_track_samples": false_track_samples,
                "high_confidence_samples": high_confidence_samples,
                "high_confidence_false_track_fraction": (
                    false_track_samples / high_confidence_samples
                    if high_confidence_samples
                    else None
                ),
            }
        )
    return summaries


def summarize_filters(
    fixtures: list[dict[str, Any]], freeze: dict[str, Any]
) -> list[dict[str, Any]]:
    summaries = []
    for tracker in freeze["tracker_candidates"]:
        for filter_candidate in freeze["filter_candidates"]:
            rows = [
                result
                for fixture in fixtures
                for result in fixture["filter_position_results"]
                if result["tracker_id"] == tracker["id"]
                and result["filter_family"] == filter_candidate["family"]
                and result["position_mae_m"] is not None
            ]
            total = sum(int(row["comparable_samples"]) for row in rows)
            summaries.append(
                {
                    "tracker_id": tracker["id"],
                    "filter_family": filter_candidate["family"],
                    "implementation": filter_candidate["implementation"],
                    "comparable_samples": total,
                    "position_mae_m": (
                        sum(
                            float(row["position_mae_m"])
                            * int(row["comparable_samples"])
                            for row in rows
                        )
                        / total
                        if total
                        else None
                    ),
                    "position_rmse_m": (
                        math.sqrt(
                            sum(
                                float(row["position_rmse_m"]) ** 2
                                * int(row["comparable_samples"])
                                for row in rows
                            )
                            / total
                        )
                        if total
                        else None
                    ),
                }
            )
    return summaries


def finalize_decision(
    evidence_path: Path,
    output: Path,
    *,
    tracker: str | None,
    filter_family: str | None,
    reject: bool,
    rationale: str,
) -> dict[str, Any]:
    document = load_json(evidence_path)
    if document.get("study_version") != STUDY_VERSION:
        raise SelectionError("evidence uses an unsupported study_version")
    if reject:
        if tracker is not None or filter_family is not None:
            raise SelectionError("--reject cannot be combined with --tracker/--filter-family")
        decision = {
            "status": "rejected",
            "selected_tracker": None,
            "selected_filter_family": None,
            "rationale": rationale,
        }
    else:
        if not tracker or not filter_family:
            raise SelectionError(
                "selection requires both --tracker and --filter-family, or use --reject"
            )
        tracker_result = next(
            (item for item in document["tracker_results"] if item["tracker_id"] == tracker),
            None,
        )
        if tracker_result is None:
            raise SelectionError(f"unknown evaluated tracker: {tracker}")
        if set(tracker_result["gate_status"].values()) != {"PASS"}:
            raise SelectionError(
                f"{tracker}: cannot select a tracker that does not pass both original tracking gates"
            )
        if not any(
            item["tracker_id"] == tracker
            and item["filter_family"] == filter_family
            and item["comparable_samples"] > 0
            for item in document["filter_results"]
        ):
            raise SelectionError(
                f"{tracker}/{filter_family}: no held-out filter-position evidence exists"
            )
        decision = {
            "status": "selected",
            "selected_tracker": tracker,
            "selected_filter_family": filter_family,
            "rationale": rationale,
        }

    finalized = {**document, "decision": decision}
    write_json(output, finalized)
    return finalized


def render_report(document: dict[str, Any]) -> str:
    lines = [
        "# Held-out tracker/filter selection evidence",
        "",
        f"- Study: \`{document['study_version']}\`",
        f"- Evaluated commit: \`{document['evaluated_commit']}\`",
        f"- Held-out fixtures: {document['readiness']['fixture_count']}",
        "",
        "## Tracker gates",
        "",
        "| Tracker | MAE | Availability | MAE gate | Availability gate | High-confidence false tracks |",
        "| --- | ---: | ---: | --- | --- | ---: |",
    ]
    for item in document["tracker_results"]:
        aggregate = item["aggregate"]
        mae = aggregate["plate_center_mae_px"]
        availability = aggregate["tracking_availability"]
        lines.append(
            f"| {item['tracker_id']} | "
            f"{'n/a' if mae is None else f'{mae:.3f} px'} | "
            f"{'n/a' if availability is None else f'{availability:.3%}'} | "
            f"{item['gate_status']['plate_center_mae']} | "
            f"{item['gate_status']['tracking_availability']} | "
            f"{item['high_confidence_false_track_samples']}/{item['high_confidence_samples']} |"
        )
    lines.extend(
        [
            "",
            "## Filter position diagnostics",
            "",
            "| Tracker | Filter family | Comparable samples | Position MAE | Position RMSE |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )
    for item in document["filter_results"]:
        mae = item["position_mae_m"]
        rmse = item["position_rmse_m"]
        lines.append(
            f"| {item['tracker_id']} | {item['filter_family']} | "
            f"{item['comparable_samples']} | "
            f"{'n/a' if mae is None else f'{mae:.5f} m'} | "
            f"{'n/a' if rmse is None else f'{rmse:.5f} m'} |"
        )
    decision = document["decision"]
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- Status: **{decision['status']}**",
            f"- Selected tracker: {decision.get('selected_tracker') or 'none'}",
            f"- Selected filter family: {decision.get('selected_filter_family') or 'none'}",
            f"- Rationale: {decision['rationale']}",
            "",
            "## Interpretation limits",
            "",
            "- Plate-centre MAE and tracking availability use the existing Rust benchmark semantics.",
            "- High-confidence false-track analysis is diagnostic; tracker confidence is not a calibrated probability.",
            "- Filter metrics here compare filtered position with manually digitised plate-centre reference. "
            "Independent physical ROM/velocity validation remains issue #58.",
            "- Condition and exercise breakdowns must be reviewed before treating aggregate gate results as a supported envelope; issue #53 owns envelope promotion.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--manifest", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--manifest", type=Path, required=True)
    preflight.add_argument("--freeze", type=Path, required=True)
    preflight.add_argument("--output", type=Path)

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--manifest", type=Path, required=True)
    evaluate.add_argument("--freeze", type=Path, required=True)
    evaluate.add_argument("--output-dir", type=Path, required=True)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--evidence", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True)
    finalize.add_argument("--report", type=Path)
    finalize.add_argument("--tracker")
    finalize.add_argument("--filter-family")
    finalize.add_argument("--reject", action="store_true")
    finalize.add_argument("--rationale", required=True)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "freeze":
            freeze_study(args.manifest, args.output)
        elif args.command == "preflight":
            freeze = load_json(args.freeze)
            result = preflight_inputs(args.manifest, freeze)
            if args.output:
                write_json(args.output, result)
            else:
                print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "evaluate":
            result = evaluate_study(args.manifest, args.freeze, args.output_dir)
            evidence_tool.write_text(
                args.output_dir / "TRACKER_FILTER_SELECTION_REPORT.md",
                render_report(result),
            )
        elif args.command == "finalize":
            result = finalize_decision(
                args.evidence,
                args.output,
                tracker=args.tracker,
                filter_family=args.filter_family,
                reject=args.reject,
                rationale=args.rationale,
            )
            if args.report:
                evidence_tool.write_text(args.report, render_report(result))
        else:  # pragma: no cover - argparse enforces this.
            raise SelectionError(f"unsupported command: {args.command}")
    except (SelectionError, evidence_tool.EvidenceError, OSError, ValueError, KeyError) as error:
        print(f"selection-study error: {error}", file=__import__("sys").stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
