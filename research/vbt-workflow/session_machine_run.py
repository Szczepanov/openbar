"""`vbt_session.py run-research` (#113): track and analyze machine-initialized clips. Research only.

Decision: docs/analysis/VBT_INITIALIZATION_DECISION.md. Contract: docs/validation/VBT_RESEARCH_INITIALIZATION.md
("Research run"). The input is machine-init.json, read only through session_machine_init.load_machine_init,
which binds it to the on-disk session.json and the re-supplied profile bytes. Plate diameter, stick length and
exercise come only from the profile. Every output is under <session>/machine-run/: a session-local research
manifest, machine-origin seeds, scale-reference inputs, the analyze_lift outputs, the scale report and
machine-run-record.json, which is written last. Nothing of the #95 path is written: no personal manifest,
seeds/, scale/, analyses/, packages/, session-input.csv, report.html or session-record.json. Machine values are
used unrounded and never get a status. Standard library only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import analyze_lift
import scale_reference
import schema_check
import session_ingest
import session_machine_init as smi
import session_run
from vbt_process import Runner, WorkflowError
from vbt_trackers import TRACKERS, require_cuda

RUN_DIR_NAME = "machine-run"
RUN_RECORD_NAME = "machine-run-record.json"
RUN_RECORD_FORMAT = "openbar-research-vbt-machine-run-record"
RUN_RECORD_VERSION = 1
IMPLEMENTATION = "session_machine_run"
IMPLEMENTATION_VERSION = 1
SEED_SUFFIX = ".machine-origin-seed.json"
SCALE_REPORT_NAMES = ("scale-reference-v1.json", "SCALE_REFERENCE_REPORT.md")


def rel(path: Path) -> str:
    return analyze_lift.display_path(path)


def sha(path: Path) -> str:
    return analyze_lift.file_sha256(path)


def run_paths(directory: Path) -> dict[str, Path]:
    root = directory / RUN_DIR_NAME
    return {"root": root, "manifest": root / "manifest.json", "seeds": root / "seeds", "scale": root / "scale",
            "packages": root / "scale-packages", "analyses": root / "analyses",
            "scale_report": root / "scale-report", "record": root / RUN_RECORD_NAME}


# --- Inputs (no writes) --------------------------------------------------------------------------

def input_hashes(directory: Path, profile_path: Path) -> dict[str, str]:
    return {"session_state_sha256": sha(directory / session_ingest.STATE_NAME), "profile_sha256": sha(profile_path),
            "machine_init_sha256": sha(directory / smi.RECORD_NAME)}


def load_inputs(args: argparse.Namespace) -> dict[str, Any]:
    """The verified machine-init record and the hashes of the exact bytes it is bound to."""
    profile_raw = smi.read_profile(args.profile)
    directory = session_ingest.session_dir(args.sessions_root, args.session)
    record = smi.load_machine_init(directory, profile_raw)
    state, state_raw = smi.load_session_state(directory)
    init_raw = (directory / smi.RECORD_NAME).read_bytes()
    # load_machine_init read the same files; a change between its reads and these is refused, not merged.
    if (smi.sha256_hex(state_raw) != record["inputs"]["session_state_sha256"]
            or init_raw != smi.render_record(record).encode("utf-8")):
        raise WorkflowError(f"{session_ingest.STATE_NAME} or {smi.RECORD_NAME} changed while it was being read; "
                            "run again")
    hashes = {"session_state_sha256": smi.sha256_hex(state_raw), "profile_sha256": smi.sha256_hex(profile_raw),
              "machine_init_sha256": smi.sha256_hex(init_raw)}
    return {"directory": directory, "record": record, "state": state, "hashes": hashes,
            "profile_path": args.profile}


def initialized_clips(inputs: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(session clip, machine-init clip) for every initialized clip, in session order."""
    clips = inputs["state"]["clips"]
    chosen = [(clips[clip["clip_index"]], clip) for clip in inputs["record"]["clips"]
              if clip["outcome"] == "initialized"]
    if not chosen:
        raise WorkflowError(f"{smi.RECORD_NAME} initializes no clip; nothing to run. Use the #95 confirmation page "
                            "(session.html) for every clip")
    for clip, _ in chosen:
        where = f"{session_ingest.STATE_NAME} clip {clip['fixture_id']}"
        for key in ("media_path", "package_dir"):
            if not isinstance(clip.get(key), str) or clip[key] == "":
                raise WorkflowError(f"{where} {key} must be a non-empty string")
        if type(clip.get("rotation_deg")) is not int:
            raise WorkflowError(f"{where} rotation_deg must be an integer")
    return chosen


def resolve_tracker(args: argparse.Namespace, exercise: str, runner: Runner) -> tuple[str, str | None]:
    """The profile's lift has one tracker under the policy; the GPU rules are those of the #95 run."""
    policy = session_run.TRACKER_POLICIES[args.tracker_policy]["trackers"]
    tracker = policy[exercise]
    if args.gpu_python is not None and not any(TRACKERS[name].needs_gpu_python for name in policy.values()):
        raise WorkflowError(f"--gpu-python is not used by --tracker-policy {args.tracker_policy}")
    if not TRACKERS[tracker].needs_gpu_python:
        return tracker, None
    if args.gpu_python is None:
        raise WorkflowError(f"--tracker-policy {args.tracker_policy} runs {tracker} for {exercise} and needs "
                            "--gpu-python (the GPU venv); on a machine without a CUDA GPU use csrt-all-v1")
    gpu_python = analyze_lift.resolve_gpu_python(args.gpu_python)
    try:
        require_cuda(runner, gpu_python)
    except WorkflowError as error:
        raise WorkflowError(f"{error}; or choose --tracker-policy csrt-all-v1") from error
    return tracker, gpu_python


# --- Per-clip documents (no writes) --------------------------------------------------------------

COORDINATE_KEYS = ("space", "origin", "x_direction", "y_direction", "rotation_applied")
DISPLAY_TOP_LEFT = ("decoded_display_pixels", "top_left", "right", "down", True)


def require_package_binding(metadata: dict[str, Any], clip: dict[str, Any]) -> None:
    """The ingested label package must be in ADR-0007 display pixels and belong to this video."""
    coordinate = metadata.get("coordinate_system")
    found = tuple(coordinate.get(key) for key in COORDINATE_KEYS) if isinstance(coordinate, dict) else None
    if found != DISPLAY_TOP_LEFT:
        raise WorkflowError(f"{clip['fixture_id']}: the label package coordinate system is not decoded display "
                            "pixels with a top-left origin, +X right, +Y down and rotation applied (ADR-0007); "
                            "re-run ingest")
    if metadata.get("source_video_sha256") != clip["sha256"]:
        raise WorkflowError(f"{clip['fixture_id']}: the label package is for a different video; re-run ingest")


def seed_notes(init_clip: dict[str, Any], profile: dict[str, Any], hashes: dict[str, str]) -> str:
    plate = init_clip["items"]["plate_center"]["suggestion"]
    return (f"MACHINE-ORIGIN research seed (vbt_session.py run-research, #113): not a manual selection and not "
            f"human-confirmed; research only, not consumer-eligible. machine-init.json "
            f"sha256={hashes['machine_init_sha256']}; profile {profile['profile_id']} "
            f"sha256={hashes['profile_sha256']}; plate suggestion {plate['id']} (method {plate['method']}, "
            f"suggester confidence {plate['confidence']!r}, not a selection confidence).")


def seed_document(clip: dict[str, Any], init_clip: dict[str, Any], notes: str) -> dict[str, Any]:
    """manual-target-seed-v1 is the only seed the tracker and analyzer accept; the notes carry the machine origin.

    The values are the recorded suggester numbers, unrounded. selection_confidence is left out: it means human
    confidence in a manual selection, and a suggester confidence is not that.
    """
    centre = init_clip["items"]["plate_center"]["values"]
    document = {"schema_version": 1, "fixture_id": clip["fixture_id"], "seed": {
        "timestamp_s": float(clip["timestamp_s"]), "frame_index": clip["frame_index"],
        "target": {"center": {"x_px": centre["center_x_px"], "y_px": centre["center_y_px"]},
                   "radius_px": init_clip["items"]["plate_radius"]["values"]["radius_px"]},
        "coordinate_space": "display_top_left", "source_rotation_deg": clip["rotation_deg"] % 360, "notes": notes}}
    try:
        errors = schema_check.validate_document(document, schema_check.load_schema(analyze_lift.SEED_SCHEMA))
    except (schema_check.SchemaError, OSError) as error:
        raise WorkflowError(f"cannot load the seed schema: {error}") from error
    if errors:
        raise WorkflowError(f"{clip['fixture_id']}: the machine-origin seed is not a valid manual-target-seed-v1: "
                            + "; ".join(errors))
    return document


def click_csv_text(config: dict[str, Any], init_clip: dict[str, Any]) -> str:
    """The scale_reference click CSV with the recorded stick values unrounded (repr), not at the #95 2 decimals."""
    frame = config["frames"][0]
    low, high = init_clip["items"]["stick_low"]["values"], init_clip["items"]["stick_high"]["values"]
    row = [config["fixture_id"], config["source_video_sha256"], config["package_id"], str(frame["frame_index"]),
           repr(float(frame["timestamp_s"])), str(config["width_px"]), str(config["height_px"]),
           repr(float(config["known_length_m"])), repr(float(low["low_x_px"])), repr(float(low["low_y_px"])),
           repr(float(high["high_x_px"])), repr(float(high["high_y_px"]))]
    return ",".join(scale_reference.CSV_COLUMNS) + "\n" + ",".join(row) + "\n"


def plan_clip(clip: dict[str, Any], init_clip: dict[str, Any], tracker: str, paths: dict[str, Path],
              profile: dict[str, Any], hashes: dict[str, str]) -> dict[str, Any]:
    """Everything one clip writes, computed from read-only inputs. The ingested label package is only read."""
    fixture_id = clip["fixture_id"]
    source = analyze_lift.ROOT / clip["package_dir"]
    try:
        metadata_raw = (source / "metadata.json").read_bytes()
    except OSError as error:
        raise WorkflowError(f"{fixture_id}: cannot read its label package metadata: {error}") from error
    require_package_binding(smi.parse_json_object(metadata_raw, f"{fixture_id} label package metadata.json"), clip)
    try:
        config = scale_reference.reference_config_from_label_package(source, profile["stick_length_m"])
    except scale_reference.ScaleReferenceError as error:
        raise WorkflowError(f"{fixture_id}: {error}") from error
    package_dir = paths["packages"] / fixture_id
    seed = paths["seeds"] / f"{fixture_id}{SEED_SUFFIX}"
    click_csv = paths["scale"] / f"{fixture_id}.scale-reference.csv"
    seed_text = json.dumps(seed_document(clip, init_clip, seed_notes(init_clip, profile, hashes)), indent=2,
                           sort_keys=True, allow_nan=False) + "\n"
    return {
        "clip": clip, "init": init_clip, "tracker": tracker, "media": analyze_lift.ROOT / clip["media_path"],
        "package_dir": package_dir, "seed": seed, "click_csv": click_csv,
        "outputs": analyze_lift.output_paths(paths["analyses"], fixture_id, tracker),
        "texts": {
            package_dir / "metadata.json": metadata_raw.decode("utf-8"),
            package_dir / scale_reference.REFERENCE_CONFIG_NAME:
                json.dumps(config, indent=2, sort_keys=True, allow_nan=False) + "\n",
            seed: seed_text,
            click_csv: click_csv_text(config, init_clip),
        },
    }


def check_media(plans: list[dict[str, Any]], profile: dict[str, Any]) -> None:
    """Re-probe and re-hash each video before anything is written, as the #95 run does."""
    for plan in plans:
        clip = plan["clip"]
        drafted = analyze_lift.draft_entry(plan["media"], clip["fixture_id"], profile["plate_diameter_m"],
                                           profile["exercise"])
        if str(drafted["media"].get("sha256", "")).lower() != clip["sha256"].lower():
            raise WorkflowError(f"{clip['fixture_id']} changed since it was ingested; re-run ingest and init-research")
        if (drafted["media"].get("repository_path") != clip["media_path"]
                or drafted["video"].get("rotation_deg", 0) != clip["rotation_deg"]):
            raise WorkflowError(f"{clip['fixture_id']} no longer matches its ingested media path or rotation; "
                                "re-run ingest and init-research")


# --- Planning ------------------------------------------------------------------------------------

def planned_outputs(plans: list[dict[str, Any]], paths: dict[str, Path]) -> list[Path]:
    outputs = [paths["manifest"], paths["record"], *(paths["scale_report"] / name for name in SCALE_REPORT_NAMES)]
    for plan in plans:
        outputs += [*plan["texts"], *plan["outputs"].values()]
    return outputs


def stale_outputs(plans: list[dict[str, Any]], paths: dict[str, Path]) -> list[Path]:
    """Every existing file under machine-run/ this run will not rewrite (a dropped clip, another tracker).

    The command owns the whole folder, so a clip a re-ingest dropped is covered too.
    """
    planned = set(planned_outputs(plans, paths))
    if not paths["root"].is_dir():
        return []
    return sorted(path for path in paths["root"].rglob("*") if path.is_file() and path not in planned)


def prepare(args: argparse.Namespace, runner: Runner) -> dict[str, Any]:
    """Every check that can fail, in order, before the first write. Returns the run's plan."""
    inputs = load_inputs(args)
    chosen = initialized_clips(inputs)
    profile = inputs["record"]["profile"]
    options = analyze_lift.analysis_options(args)
    openbar_cli = None if args.openbar_cli is None else analyze_lift.resolve_openbar_cli(args.openbar_cli)
    analyze_lift.require_tools(runner, ("ffmpeg", "ffprobe"))
    tracker, gpu_python = resolve_tracker(args, profile["exercise"], runner)
    paths = run_paths(inputs["directory"])
    plans = [plan_clip(clip, init_clip, tracker, paths, profile, inputs["hashes"]) for clip, init_clip in chosen]
    check_media(plans, profile)
    stale = stale_outputs(plans, paths)
    existing = [path for path in planned_outputs(plans, paths) if path.exists()] + stale
    if existing and not args.force:
        raise WorkflowError("machine-run outputs already exist (pass --force to replace them; outputs of clips this "
                            "run does not use are then removed): " + ", ".join(rel(path) for path in existing[:6])
                            + (" ..." if len(existing) > 6 else ""))
    rejected = [{"fixture_id": clip["fixture_id"], "reasons": clip["reasons"]}
                for clip in inputs["record"]["clips"] if clip["outcome"] == "rejected"]
    return {**inputs, "profile": profile, "options": options, "openbar_cli": openbar_cli, "gpu_python": gpu_python,
            "tracker": tracker, "paths": paths, "plans": plans, "stale": stale, "rejected": rejected,
            "git": analyze_lift.git_provenance(runner)}


def write_inputs(plan_set: dict[str, Any]) -> None:
    """The record goes first, so machine-run/ is marked incomplete before anything changes."""
    paths, profile = plan_set["paths"], plan_set["profile"]
    paths["record"].unlink(missing_ok=True)
    for path in plan_set["stale"]:
        path.unlink(missing_ok=True)
        print(f"removed stale output {rel(path)}")
    if paths["root"].is_dir():
        for directory in sorted((d for d in paths["root"].rglob("*") if d.is_dir()),
                                key=lambda d: len(d.parts), reverse=True):
            if not any(directory.iterdir()):
                directory.rmdir()
    # Rebuilt from the profile on every run, so a changed profile or a now-rejected clip leaves no entry behind.
    # It is never the personal manifest: a machine run registers nothing there.
    paths["manifest"].unlink(missing_ok=True)
    paths["root"].mkdir(parents=True, exist_ok=True)
    for plan in plan_set["plans"]:
        clip = plan["clip"]
        _, action = analyze_lift.register_video(plan["media"], paths["manifest"], clip["sha256"],
                                                profile["plate_diameter_m"], profile["exercise"])
        print(f"{clip['fixture_id']}: research manifest entry {action} ({profile['exercise']})")
        for path, text in plan["texts"].items():
            session_ingest.write_text(path, text)


# --- analyze_lift, the scale report and the record -----------------------------------------------

def analyze_arguments(plan: dict[str, Any], plan_set: dict[str, Any], args: argparse.Namespace,
                      show: Any) -> list[str]:
    profile, paths = plan_set["profile"], plan_set["paths"]
    argv = ["run", "--video", show(plan["media"]), "--seed", show(plan["seed"]),
            "--plate-diameter-m", repr(profile["plate_diameter_m"]), "--exercise", profile["exercise"],
            "--manifest", show(paths["manifest"]), "--output-dir", show(paths["analyses"]), "--tracker", plan["tracker"]]
    if TRACKERS[plan["tracker"]].needs_gpu_python:
        argv += ["--gpu-python", plan_set["gpu_python"]]
    argv += ["--preset", args.preset] if args.preset else analyze_lift.analysis_options(args)
    if plan_set["openbar_cli"] is not None:
        argv += ["--openbar-cli", show(plan_set["openbar_cli"])]
    return argv


def clip_entry(plan: dict[str, Any], argv: list[str]) -> dict[str, Any]:
    clip, items, outputs = plan["clip"], plan["init"]["items"], plan["outputs"]
    return {
        "fixture_id": clip["fixture_id"], "origin": smi.ORIGIN, "tracker": plan["tracker"],
        "suggestion_ids": {"plate": items["plate_center"]["suggestion"]["id"],
                           "stick": items["stick_low"]["suggestion"]["id"]},
        "video": {"path": clip["media_path"], "sha256": clip["sha256"]},
        "seed": {"path": rel(plan["seed"]), "sha256": sha(plan["seed"])},
        "scale_click_csv": {"path": rel(plan["click_csv"]), "sha256": sha(plan["click_csv"])},
        "analysis": {"path": rel(outputs["analysis"]), "sha256": sha(outputs["analysis"])},
        "analyze_lift": {"argv": ["python", "research/vbt-workflow/analyze_lift.py", *argv],
                         "run_record": {"path": rel(outputs["run_record"]), "sha256": sha(outputs["run_record"])}},
    }


def run_clips(plan_set: dict[str, Any], args: argparse.Namespace, runner: Runner) -> list[dict[str, Any]]:
    entries = []
    for plan in plan_set["plans"]:
        # Executed with absolute paths (and --force when asked); recorded repository-relative without --force,
        # so a forced re-run on the same inputs writes the same record.
        argv = analyze_arguments(plan, plan_set, args, lambda path: str(path.resolve()))
        argv += ["--force"] if args.force else []
        print(f"[analyze_lift] {plan['clip']['fixture_id']} (machine-origin, {plan_set['profile']['exercise']}, "
              f"{plan['tracker']})", flush=True)
        if analyze_lift.main(argv, runner=runner) != 0:
            raise WorkflowError(f"analyze_lift.py run failed for {plan['clip']['fixture_id']}; see the error above. "
                                f"{RUN_RECORD_NAME} was not written; run again with --force")
        entries.append(clip_entry(plan, analyze_arguments(plan, plan_set, args, rel)))
    return entries


def require_no_status_keys(value: Any, where: str = RUN_RECORD_NAME) -> None:
    """Machine values have no status at any depth, the same rule as machine-init.json."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in smi.FORBIDDEN_KEYS:
                raise WorkflowError(f"{RUN_RECORD_NAME} must not carry a status key (found at {where}.{key})")
            require_no_status_keys(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            require_no_status_keys(item, f"{where}[{index}]")


def run_record(plan_set: dict[str, Any], args: argparse.Namespace, entries: list[dict[str, Any]],
               namespace: argparse.Namespace) -> dict[str, Any]:
    paths, state = plan_set["paths"], plan_set["state"]
    policy = session_run.TRACKER_POLICIES[args.tracker_policy]
    return {
        "format": RUN_RECORD_FORMAT, "format_version": RUN_RECORD_VERSION,
        "origin": smi.ORIGIN, "human_confirmed": False, "research_only": True, "consumer_eligible": False,
        "implementation": {"name": IMPLEMENTATION, "version": IMPLEMENTATION_VERSION,
                           "source_sha256": smi.sha256_hex(Path(__file__).read_bytes())},
        "workflow_version": analyze_lift.WORKFLOW_VERSION, "session_id": state["session_id"],
        "page_id": state["page_id"], "inputs": plan_set["hashes"], "profile": plan_set["profile"],
        "configuration": {
            "stick_markers": "lowest and highest marker", "tracker_policy": args.tracker_policy,
            "tracker_policy_description": policy["description"], "tracker_policy_trackers": policy["trackers"],
            "gpu_python": plan_set["gpu_python"],
            "openbar_cli": None if plan_set["openbar_cli"] is None else rel(plan_set["openbar_cli"]),
            "preset": args.preset, "analyze_options": plan_set["options"],
        },
        "clips": entries,
        "rejected": plan_set["rejected"],
        "removed_stale_outputs": [rel(path) for path in plan_set["stale"]],
        "scale_report": {"argv": session_run.scale_report_argv(namespace),
                         "outputs": {name: sha(paths["scale_report"] / name) for name in SCALE_REPORT_NAMES}},
        "commands_note": "Run from the repository root with the research venv's python. Each analyze_lift run record "
                         "lists its own track and analyze commands. Machine-origin research evidence: never "
                         "human-confirmed and not for the recommender import.",
        "openbar": plan_set["git"],
    }


def require_inputs_unchanged(plan_set: dict[str, Any]) -> None:
    """The record is written only if session.json, machine-init.json and the profile are the bytes planned from."""
    if input_hashes(plan_set["directory"], plan_set["profile_path"]) != plan_set["hashes"]:
        raise WorkflowError(f"{session_ingest.STATE_NAME}, {smi.RECORD_NAME} or the profile changed during the "
                            f"research run; {RUN_RECORD_NAME} was not written. Re-run init-research if needed, then "
                            "run-research --force")


# --- Command -------------------------------------------------------------------------------------

def command_run_research(args: argparse.Namespace, runner: Runner) -> int:
    plan_set = prepare(args, runner)
    write_inputs(plan_set)
    entries = run_clips(plan_set, args, runner)
    paths = plan_set["paths"]
    namespace = session_run.scale_report_namespace(plan_set["plans"], paths["manifest"], paths)
    try:
        scale_reference.write_report(namespace)
    except (scale_reference.ScaleReferenceError, OSError, KeyError, ValueError) as error:
        raise WorkflowError(f"scale_reference.py report failed: {error}") from error
    record = run_record(plan_set, args, entries, namespace)
    require_no_status_keys(record)
    require_inputs_unchanged(plan_set)
    session_ingest.write_json(paths["record"], record)
    for item in plan_set["rejected"]:
        print(f"{item['fixture_id']}: not run, rejected by init-research ({', '.join(item['reasons'])}); "
              "use the #95 confirmation page")
    print(f"machine-run record: {rel(paths['record'])}")
    print("research only: machine-initialized, never human-confirmed, not consumer-eligible. Do not import "
          f"{rel(paths['analyses'])} into the recommender.")
    return 0
