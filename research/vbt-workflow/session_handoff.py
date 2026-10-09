"""Read-only completion checks and research-only outgoing files (#114). No consumer parser or writes."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Any

import analyze_lift as workflow
import assess_clip
import session_contract
import session_ingest
import session_run
from vbt_process import WorkflowError


def unchanged(observed: dict[Path, str]) -> None:
    for path, digest in observed.items():
        if workflow.file_sha256(path) != digest:
            raise WorkflowError(f"evidence changed during validation: {workflow.display_path(path)}")


def bound_bytes(path: Path, observed: dict[Path, str]) -> bytes:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if path in observed and observed[path] != digest:
        raise WorkflowError(f"copied evidence hash changed: {workflow.display_path(path)}")
    observed[path] = digest
    return data


def bound_document(data: bytes, schema: Path | None = None) -> dict:
    try:
        value = workflow.schema_check.loads_strict(data.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected JSON object")
        if schema is not None:
            errors = workflow.schema_check.validate_document(value, workflow.schema_check.load_schema(schema))
            if errors:
                raise ValueError("; ".join(errors))
        return value
    except (ValueError, workflow.schema_check.DocumentError) as error:
        raise WorkflowError(f"invalid bound document: {error}") from error


def completed_session(directory: Path) -> dict[str, Any]:
    """The final record is necessary but not sufficient: all its evidence must still match."""
    paths = session_run.session_paths(directory)
    if not paths["record"].is_file():
        machine = directory / "machine-run" / "machine-run-record.json"
        label = "; machine-origin results are research-only, not human-confirmed" if machine.exists() else ""
        raise WorkflowError(f"session incomplete: no final session-record.json{label}; use run --resume")
    observed: dict[Path, str] = {}

    def observe(path: Path) -> str:
        digest = workflow.file_sha256(path)
        if path in observed and observed[path] != digest:
            raise WorkflowError("evidence changed during validation")
        observed[path] = digest
        return digest

    def document(path: Path) -> dict:
        return bound_document(bound_bytes(path, observed))

    def binding(item: dict, expected: Path) -> None:
        assess_clip.require_digest(item["sha256"])
        if assess_clip.recorded_repo_path(item["path"]) != expected.resolve():
            raise ValueError("unexpected evidence path")
        if observe(expected) != item["sha256"].lower():
            raise ValueError(f"hash mismatch: {workflow.display_path(expected)}")

    try:
        record = document(paths["record"])
        state = document(directory / session_ingest.STATE_NAME)
        if (record["format"] != session_run.SESSION_RECORD_FORMAT
                or type(record["format_version"]) is not int
                or record["format_version"] != session_run.SESSION_RECORD_VERSION
                or record["workflow_version"] != workflow.WORKFLOW_VERSION
                or state["format"] != session_ingest.SESSION_STATE_FORMAT
                or type(state["format_version"]) is not int
                or state["format_version"] != session_ingest.SESSION_STATE_VERSION
                or record["session_id"] != directory.name or state["session_id"] != directory.name
                or record["page_id"] != state["page_id"]
                or session_ingest.compute_page_id(state) != state["page_id"]):
            raise ValueError("unrecognized session binding")
        binding(record["inputs"]["session_state"], directory / session_ingest.STATE_NAME)
        binding(record["inputs"]["session_csv"], paths["input_csv"])
        decisions = session_contract.parse_session_csv(bound_bytes(paths["input_csv"], observed), state)
        confirmed = {d["fixture_id"]: d for d in decisions if d["decision"] == "confirmed"}
        if len(confirmed) != sum(d["decision"] == "confirmed" for d in decisions):
            raise ValueError("duplicate lift")
        clips = {c["fixture_id"]: c for c in state["clips"]}
        if len(clips) != len(state["clips"]) or len({c["sha256"] for c in state["clips"]}) != len(clips):
            raise ValueError("duplicate source video")
        entries = record["clips"]
        if len(entries) != len(confirmed) or {c["fixture_id"] for c in entries} != set(confirmed):
            raise ValueError("missing or duplicate session clip records")
        manifest = workflow.require_personal_manifest(assess_clip.recorded_repo_path(record["inputs"]["manifest"]["path"]))
        manifest_document = document(manifest)
        workflow.fixture_probe.validate_manifest(manifest_document)
        fixtures = {f["id"]: f for f in manifest_document["fixtures"]}
        policy = session_run.TRACKER_POLICIES[record["configuration"]["tracker_policy"]]["trackers"]
        selected = []
        for entry in entries:
            fixture_id = entry["fixture_id"]
            clip, decision = clips[fixture_id], confirmed[fixture_id]
            assess_clip.require_digest(clip["sha256"])
            if fixture_id != workflow.fixture_id_for(clip["sha256"]):
                raise ValueError("fixture id is not the source-video identity")
            tracker = entry["tracker"]
            spec = workflow.TRACKERS[tracker]
            if (entry["decision"] != "confirmed" or entry["exercise"] != decision["exercise"]
                    or entry["item_statuses"] != decision["statuses"] or policy[entry["exercise"]] != tracker):
                raise ValueError("clip decision/tracker mismatch")
            media = workflow.require_video(assess_clip.recorded_repo_path(clip["media_path"]))
            binding(entry["video"], media)
            if entry["video"]["sha256"] != clip["sha256"]:
                raise ValueError("video identity mismatch")
            seed = paths["seeds"] / f"{fixture_id}.manual-target-seed-v1.json"
            binding(entry["seed"], seed)
            binding(entry["seed"]["label_csv"], paths["seeds"] / f"{fixture_id}.session-label.csv")
            binding(entry["scale_click_csv"], paths["scale"] / f"{fixture_id}.scale-reference.csv")
            workflow.load_bound_seed(seed, fixture_id, media)
            outputs = workflow.output_paths(paths["analyses"], fixture_id, tracker)
            run_path = outputs["run_record"]
            binding(entry["analyze_lift"]["run_record"], run_path)
            run = document(run_path)
            if (run["format"] != workflow.RUN_RECORD_FORMAT
                    or type(run["format_version"]) is not int or run["format_version"] != workflow.RUN_RECORD_FORMAT_VERSION
                    or run["workflow_version"] != workflow.WORKFLOW_VERSION or run["fixture_id"] != fixture_id
                    or run["configuration"]["tracker"] != tracker
                    or run["configuration"]["tracker_implementation"] != spec.implementation
                    or run["configuration"]["exercise"] != entry["exercise"]
                    or run["configuration"]["plate_diameter_m"] != record["configuration"]["plate_diameter_m"]
                    or run["configuration"]["preset"] != record["configuration"]["preset"]
                    or run["configuration"]["analyze_options"] != record["configuration"]["analyze_options"]
                    or set(run["outputs"]) != set(outputs) - {"run_record"}):
                raise ValueError("run provenance mismatch")
            binding(run["inputs"]["video"], media)
            binding(run["inputs"]["seed"], seed)
            if (assess_clip.recorded_repo_path(run["inputs"]["manifest"]["path"]) != manifest
                    or workflow.canonical_sha256(fixtures[fixture_id]) != run["inputs"]["manifest_entry"]["sha256"]):
                raise ValueError("manifest entry mismatch")
            for name, item in run["outputs"].items():
                assess_clip.require_digest(item["sha256"])
                if item["file"] != outputs[name].name or observe(outputs[name]) != item["sha256"].lower():
                    raise ValueError("run output hash/path mismatch")
            analysis = bound_document(bound_bytes(outputs["analysis"], observed), workflow.ANALYSIS_SCHEMA)
            if (analysis["identity"]["fixture_id"] != fixture_id
                    or analysis["identity"]["source_sha256"] != clip["sha256"]
                    or analysis["provenance"]["tracker"]["id"] != spec.implementation
                    or analysis["provenance"]["tracker"]["implementation"]["parameters"].get("prediction_sha256")
                    != run["outputs"]["prediction"]["sha256"]):
                raise ValueError("analysis provenance mismatch")
            selected.append({"entry": entry, "outputs": outputs, "analysis": analysis, "media": media, "seed": seed})
        binding(record["report"], paths["report"])
        for name in ("scale-reference-v1.json", "SCALE_REFERENCE_REPORT.md"):
            if observe(paths["scale_report"] / name) != record["scale_report"]["outputs"][name]:
                raise ValueError("scale report hash mismatch")
        unchanged(observed)
    except (KeyError, TypeError, ValueError, OSError, workflow.schema_check.DocumentError,
            session_contract.SessionCsvError, WorkflowError) as error:
        raise WorkflowError(f"session incomplete or invalid: {error}") from error
    return {"record": record, "state": state, "selected": selected, "observed": observed, "paths": paths,
            "manifest": manifest}


def command_status(args: Any) -> int:
    completed = completed_session(session_ingest.session_dir(args.sessions_root, args.session))
    print(f"session complete (hash verified): {args.session}\nreport: {workflow.display_path(completed['paths']['report'])}")
    return 0


def outgoing_files(completed: dict, policy_name: str, assessments_dir: Path) -> dict[str, bytes]:
    policy = session_run.TRACKER_POLICIES[policy_name]["trackers"]
    observed = completed["observed"]
    files = {}
    lines = ["OpenBar local handoff: research-only; consumer_eligible=false",
             "No database writes. Source switching/live-trial writes require the predeclared #79 decision and reviewed consumer policy.",
             f"session: {completed['record']['session_id']}", f"research tracker policy: {policy_name}",
             "Consumer: adaptive-training-recommender OpenBarImportPanel browser preview; shared concentric-segmentation-v2.",
             "Consumer compatibility: main 21ae95e4cb8c9d1fed53216b2bc9bbe083b3d572 rejects nonempty tracker parameters.",
             "Canonical provenance is preserved; local validation is not consumer acceptance."]
    for selected in completed["selected"]:
        entry, outputs = selected["entry"], selected["outputs"]
        fixture_id = entry["fixture_id"]
        require_unambiguous(selected)
        if policy[entry["exercise"]] != entry["tracker"]:
            raise WorkflowError(f"handoff tracker policy differs from retained run for {fixture_id}")
        assessment_path = assessments_dir / f"{fixture_id}.assessment-v1.json"
        assessment_bytes = bound_bytes(assessment_path, observed)
        assessment = bound_document(assessment_bytes, assess_clip.SCHEMA)
        if (assessment["fixture_id"] != fixture_id or assessment["processing"]["status"] != "complete"
                or any(assessment["mechanical"]["checks"][name]["status"] != "valid"
                       for name in ("run_binding", "source_binding", "canonical_analysis"))):
            raise WorkflowError(f"assessment is incomplete or does not validate provenance: {fixture_id}")
        required = {**outputs, "video": selected["media"], "seed": selected["seed"], "manifest": completed["manifest"]}
        for name, path in required.items():
            binding = assessment["sources"].get(name, {})
            if (binding.get("path") != workflow.display_path(path)
                    or binding.get("sha256") != observed[path]):
                raise WorkflowError(f"stale assessment {fixture_id}: {name} binding mismatch")
        # Also bind retained validator/manifest sources; hashes do not certify assessment authenticity.
        try:
            assess_clip.require_digest(assessment["sources"]["validator"]["sha256"])
            for binding in assessment["sources"].values():
                assess_clip.require_digest(binding["sha256"])
                path = assess_clip.recorded_repo_path(binding["path"])
                if (workflow.file_sha256(path) != binding["sha256"]
                        or path in observed and observed[path] != binding["sha256"]):
                    raise WorkflowError(f"stale assessment source for {fixture_id}")
                observed[path] = binding["sha256"]
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError(f"invalid assessment source binding for {fixture_id}: {error}") from error
        files[outputs["analysis"].name] = bound_bytes(outputs["analysis"], observed)
        files[assessment_path.name] = assessment_bytes
        lines.append(f"lift: {fixture_id}; exercise={entry['exercise']}; tracker={entry['tracker']}; "
                     f"assessment={assessment['mechanical']['status']}; suitability={assessment['experiment_suitability']['status']}; "
                     f"accuracy={assessment['accuracy']['status']}")
    for path in (completed["paths"]["report"], completed["paths"]["record"]):
        files[path.name] = bound_bytes(path, observed)
    for name, data in sorted(files.items()):
        lines.append(f"sha256 {hashlib.sha256(data).hexdigest()} {name}")
    files["HANDOFF.txt"] = ("\n".join(lines) + "\n").encode("utf-8")
    unchanged(observed)
    return files


def require_unambiguous(selected: dict) -> None:
    fixture_id, analysis = selected["entry"]["fixture_id"], selected["outputs"]["analysis"]
    if sorted(analysis.parent.glob(f"{fixture_id}*.analysis-v1.json")) != [analysis]:
        raise WorkflowError(f"ambiguous analyses for {fixture_id}; retain exactly the final-record selection")


def command_handoff(args: Any, sleep: Any) -> int:
    directory = session_ingest.session_dir(args.sessions_root, args.session)
    output = session_ingest.require_allowed(args.output_dir, "--output-dir").resolve()
    if workflow.is_within(output, directory) or workflow.is_within(directory, output):
        raise WorkflowError("handoff output must be separate from the source session")
    completed = completed_session(directory)
    files = outgoing_files(completed, args.tracker_policy, args.assessments_dir)
    # Two full hash checks, separated by a poll, prevent reading an actively replaced set.
    sleep(2.0)
    unchanged(completed["observed"])
    for selected in completed["selected"]:
        require_unambiguous(selected)
    if output.exists():
        if (not output.is_dir() or {p.name for p in output.iterdir()} != set(files)
                or any(not (output / name).is_file() or (output / name).read_bytes() != data
                       for name, data in files.items())):
            raise WorkflowError("handoff destination contains different or incomplete files; choose a new output directory")
    elif not args.dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".handoff-", dir=output.parent))
        try:
            for name, data in files.items():
                (staging / name).write_bytes(data)
            unchanged(completed["observed"])
            for selected in completed["selected"]:
                require_unambiguous(selected)
            staging.rename(output)
        finally:
            if staging.exists():
                if not workflow.is_within(staging.resolve(), output.parent):
                    raise WorkflowError("handoff staging path escaped its verified parent")
                shutil.rmtree(staging)
    print(f"{'dry-run validated' if args.dry_run else 'handoff verified'}: {len(completed['selected'])} lift(s); "
          f"research-only; policy={args.tracker_policy}\nreport: {workflow.display_path(completed['paths']['report'])}\n"
          f"outgoing: {workflow.display_path(output)}\nconsumer preview compatibility gate: nonempty tracker parameters unsupported")
    return 0
