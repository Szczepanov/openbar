#!/usr/bin/env python3
"""Read-only first-slice VBT assessment (#111). No accuracy or experiment verdicts."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

import analyze_lift as workflow

ROOT = workflow.ROOT
SCHEMA = ROOT / "validation/schema/vbt-clip-assessment-v1.schema.json"


def check(status: str, *reasons: str) -> dict:
    return {"status": status, "reasons": list(reasons)}


def validate(cli: Path, analysis: Path, video: Path | None, seed: Path | None = None) -> dict:
    argv = [str(cli), "validate-analysis", "--analysis", str(analysis)]
    if video is not None:
        argv += ["--video", str(video)]
    if seed is not None:
        argv += ["--seed", str(seed)]
    try:
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, check=False)
    except OSError:
        return check("unknown", "validator_unavailable")
    # Media/tool failures cannot establish invalidity; invalid input and unsupported media can.
    if result.returncode == 0:
        return check("valid")
    if result.returncode == 2:
        return check("invalid", "source_pts_invalid" if video else
                     "analysis_seed_mismatch" if seed else "canonical_analysis_invalid")
    if result.returncode == 4:
        return check("invalid", "source_unsupported")
    return check("unknown", "source_probe_unavailable" if video else "validator_unavailable")


def recorded_repo_path(value: object) -> Path:
    """Recognize only the canonical repository-relative path spelling emitted by workflow-v3."""
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("recorded input path must be text")
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError("recorded input path must be repository-relative")
    resolved = workflow.safe_resolve(ROOT / relative)
    if not workflow.is_within(resolved, ROOT) or workflow.display_path(resolved) != value:
        raise ValueError("recorded input path must be canonical and stay inside the repository")
    return resolved


def assess(run_path: Path, fixture_id: str, cli: Path,
           validator: Callable[..., dict] | None = None) -> dict:
    validator = validator or validate
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]*", fixture_id) is None:
        raise workflow.WorkflowError("invalid fixture id")
    sources: dict[str, dict] = {}
    paths: dict[str, Path] = {}
    recorded_inputs: dict[str, Path] = {}

    def source(name: str, path: Path) -> str | None:
        path = path.resolve()
        paths[name] = path
        try:
            digest = workflow.file_sha256(path)
        except OSError:
            digest = None
        sources[name] = {"path": workflow.display_path(path), "sha256": digest}
        return digest

    source("run_record", run_path)
    source("validator", cli)
    checks = {name: check("unknown", "run_record_missing") for name in
              ("run_binding", "source_binding", "canonical_analysis", "decoded_pts")}
    processing = check("incomplete", "run_record_missing")
    record = None
    if sources["run_record"]["sha256"] is not None:
        try:
            record = workflow.schema_check.load_strict(run_path)
            if (record["format"] != workflow.RUN_RECORD_FORMAT
                    or type(record["format_version"]) is not int
                    or record["format_version"] != workflow.RUN_RECORD_FORMAT_VERSION
                    or record["workflow_version"] != workflow.WORKFLOW_VERSION
                    or record["fixture_id"] != fixture_id):
                raise ValueError("unrecognized run binding")
            spec = workflow.TRACKERS[record["configuration"]["tracker"]]
            if record["configuration"]["tracker_implementation"] != spec.implementation:
                raise ValueError("wrong tracker binding")
            expected_names = set(workflow.output_paths(run_path.parent, fixture_id, spec.name)) - {"run_record"}
            if not isinstance(record["outputs"], dict) or set(record["outputs"]) != expected_names:
                raise ValueError("missing or unexpected run outputs")
            for name, item in record["outputs"].items():
                if (not isinstance(item["file"], str) or Path(item["file"]).name != item["file"]
                        or ":" in item["file"] or "\\" in item["file"] or "\0" in item["file"]):
                    raise ValueError("output must be a sibling filename")
                require_digest(item["sha256"])
            for name in ("video", "seed"):
                require_digest(record["inputs"][name]["sha256"])
                recorded_inputs[name] = recorded_repo_path(record["inputs"][name]["path"])
            require_digest(record["inputs"]["manifest_entry"]["sha256"])
            recorded_inputs["manifest"] = recorded_repo_path(record["inputs"]["manifest"]["path"])
        except (KeyError, TypeError, ValueError, workflow.schema_check.DocumentError):
            record = None
            recorded_inputs.clear()
            processing = check("unknown", "run_record_invalid")
            checks = {name: check("unknown", "run_record_invalid") for name in checks}
            checks["run_binding"] = check("invalid", "run_record_invalid")
    if record is not None:
        missing, mismatched = [], []
        for name, item in record["outputs"].items():
            actual = source(name, run_path.parent / item["file"])
            if actual is None:
                missing.append("run_output_missing")
            elif actual != item["sha256"].lower():
                mismatched.append("run_output_hash_mismatch")
        processing = check("incomplete" if missing or mismatched else "complete",
                           *sorted(set(missing + mismatched)))
        checks["run_binding"] = check("invalid" if mismatched else "unknown" if missing else "valid",
                                       *sorted(set(missing + mismatched)))
        binding_reasons = []
        binding_missing = False
        for name in ("video", "seed"):
            actual = source(name, recorded_inputs[name])
            binding_missing |= actual is None
            if actual is not None and actual != record["inputs"][name]["sha256"].lower():
                binding_reasons.append("source_hash_mismatch")
        manifest = recorded_inputs["manifest"]
        source("manifest", manifest)
        binding_missing |= sources["manifest"]["sha256"] is None
        if not binding_missing:
            try:
                fixtures = workflow.load_manifest_fixtures(manifest)
                entry = next(item for item in fixtures if item["id"] == fixture_id)
                if workflow.canonical_sha256(entry) != record["inputs"]["manifest_entry"]["sha256"].lower():
                    binding_reasons.append("manifest_entry_hash_mismatch")
                if entry["media"]["sha256"].lower() != sources["video"]["sha256"]:
                    binding_reasons.append("source_hash_mismatch")
                workflow.load_bound_seed(paths["seed"], fixture_id, paths["video"])
            except (workflow.WorkflowError, StopIteration, KeyError, TypeError):
                binding_reasons.append("source_binding_invalid")
        checks["source_binding"] = check(
            "invalid" if binding_reasons else "unknown" if binding_missing else "valid",
            *sorted(set(binding_reasons + (["source_missing"] if binding_missing else []))))
        if sources["analysis"]["sha256"] is not None:
            checks["canonical_analysis"] = validator(cli, paths["analysis"], None)
            if checks["canonical_analysis"]["status"] == "valid":
                document = workflow.schema_check.load_strict(paths["analysis"])
                identity = document["identity"]
                parameters = document["provenance"]["tracker"]["implementation"].get("parameters", {})
                if (identity.get("fixture_id") != fixture_id
                        or (identity.get("source_sha256") or "").lower() != record["inputs"]["video"]["sha256"].lower()
                        or document["provenance"]["tracker"]["id"] != spec.implementation
                        or parameters.get("prediction_sha256") != record["outputs"]["prediction"]["sha256"].lower()):
                    checks["source_binding"] = check("invalid", "analysis_run_binding_mismatch")
                if checks["source_binding"]["status"] == "valid":
                    checks["source_binding"] = validator(cli, paths["analysis"], None, paths["seed"])
                    if checks["source_binding"]["status"] == "valid":
                        checks["decoded_pts"] = validator(cli, paths["analysis"], paths["video"])
                    else:
                        checks["decoded_pts"] = check("unknown", "source_binding_not_verified")
                else:
                    checks["decoded_pts"] = check("unknown", "source_binding_not_verified")
            else:
                checks["decoded_pts"] = check("unknown", "canonical_analysis_not_verified")
        else:
            checks["canonical_analysis"] = check("unknown", "run_output_missing")
            checks["decoded_pts"] = check("unknown", "canonical_analysis_not_verified")
    # A result cannot bind a moving set of inputs, including the validator binary itself.
    for name, path in paths.items():
        try:
            current = workflow.file_sha256(path)
        except OSError:
            current = None
        if current != sources[name]["sha256"]:
            checks["source_binding"] = check("invalid", "evidence_changed_during_assessment")
    states = [item["status"] for item in checks.values()]
    mechanical = "invalid" if "invalid" in states else "unknown" if "unknown" in states else "valid"
    result = {
        "schema_version": 1, "implementation": "openbar-vbt-clip-assessment", "implementation_version": 1,
        "fixture_id": fixture_id, "sources": sources, "processing": processing,
        "mechanical": {"status": mechanical, "checks": checks},
        "experiment_suitability": check("rejected", "mechanical_invalid") if mechanical == "invalid"
            else check("unknown", "experiment_criteria_not_predeclared", "recording_support_not_established"),
        "accuracy": check("not_established", "independent_accuracy_evidence_missing"),
    }
    errors = workflow.schema_check.validate_document(result, workflow.schema_check.load_schema(SCHEMA))
    if errors:
        raise workflow.WorkflowError("invalid assessment: " + "; ".join(errors))
    return result


def require_digest(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise ValueError("invalid SHA-256")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-record", type=Path, required=True)
    parser.add_argument("--fixture-id", required=True)
    parser.add_argument("--openbar-cli", type=Path, required=True, help="built current OpenBar CLI")
    parser.add_argument("--output", type=Path, required=True, help="new, separate assessment JSON")
    args = parser.parse_args(argv)
    try:
        result = assess(args.run_record.resolve(), args.fixture_id, args.openbar_cli.resolve())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n")
    except (workflow.WorkflowError, OSError, workflow.schema_check.DocumentError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())