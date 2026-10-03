"""`vbt_session.py run` (#95): confirmed session CSV -> seeds, scale clicks, analyses, reports, record.

Orchestration only. Seeds come from annotations.build_seed, scale-reference evidence from
scale_reference.py, tracking and analysis from analyze_lift.py `run` (#86/#94), each with its own
validation. Every input is validated before anything is written: the CSV against the session
state, the tracker policy (and CUDA, when it needs SAM 2), registration conflicts, and existing
outputs (refused without --force). The session record is written last; no record means the set is
incomplete. Standard library only, apart from the report crops (OpenCV, imported lazily).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import analyze_lift
import annotations
import label_package
import scale_reference
import session_contract
import session_ingest
import session_report
from vbt_process import Runner, WorkflowError
from vbt_trackers import TRACKERS, require_cuda

SESSION_RECORD_FORMAT = "openbar-research-vbt-session-record"
SESSION_RECORD_VERSION = 1
SAM2 = "sam2.1-bplus-circle"
TRACKER_POLICIES: dict[str, dict[str, Any]] = {
    "csrt-all-v1": {
        "description": "CSRT (CPU) for every lift; the policy for machines without a CUDA GPU.",
        "trackers": {exercise: "csrt" for exercise in session_contract.EXERCISES},
    },
    "sam2-all-v1": {
        "description": "SAM 2.1 base-plus circle fit (CUDA GPU) for every lift.",
        "trackers": {exercise: SAM2 for exercise in session_contract.EXERCISES},
    },
    "sam2-olympic-csrt-squat-v1": {
        "description": "SAM 2.1 base-plus circle fit for snatch and clean (it removed CSRT's first-rep peak "
                       "artefact there, #94); CSRT for back squat and other lifts.",
        "trackers": {"snatch": SAM2, "clean": SAM2, "back_squat": "csrt", "other": "csrt"},
    },
}
LABEL_HEADER = ("timestamp_s,requested_timestamp_s,frame_index,annotation_state,visibility,quality,x_px,y_px,"
                "radius_px,diameter_px,left_px,top_px,right_px,bottom_px,notes")
# A labelled row needs a quality; the per-clip Confirm on the session page stands in for the label
# page's quality step. Quality never reaches the seed (manual-target-seed-v1 has no such field).
LABEL_QUALITY = "high"


def positive_number(text: str, flag: str) -> float:
    try:
        value = float(text)
    except ValueError as error:
        raise WorkflowError(f"{flag} must be a number, got {text!r}") from error
    if not math.isfinite(value) or value <= 0:
        raise WorkflowError(f"{flag} must be a positive finite number, got {text!r}")
    return value


def rel(path: Path) -> str:
    return analyze_lift.display_path(path)


# --- Planning (no writes) ------------------------------------------------------------------------

def session_paths(directory: Path) -> dict[str, Path]:
    return {"input_csv": directory / "session-input.csv", "seeds": directory / "seeds",
            "scale": directory / "scale", "analyses": directory / "analyses",
            "scale_report": directory / "scale-report", "report": directory / "report.html",
            "record": directory / session_ingest.RECORD_NAME, "frames": directory / "report-frames"}


def plan_clip(clip: dict[str, Any], decision: dict[str, Any], tracker: str, paths: dict[str, Path]) -> dict[str, Any]:
    fixture_id = clip["fixture_id"]
    return {
        "clip": clip, "decision": decision, "tracker": tracker,
        "seed": paths["seeds"] / f"{fixture_id}.manual-target-seed-v1.json",
        "label_csv": paths["seeds"] / f"{fixture_id}.session-label.csv",
        "click_csv": paths["scale"] / f"{fixture_id}.scale-reference.csv",
        "outputs": analyze_lift.output_paths(paths["analyses"], fixture_id, tracker),
        "package_dir": analyze_lift.ROOT / clip["package_dir"],
        "media": analyze_lift.ROOT / clip["media_path"],
    }


def check_registrations(plans: list[dict[str, Any]], manifest: Path, plate: float) -> None:
    """Fail before any write when registration would conflict, exactly as analyze_lift.register_video would.

    Each clip's entry is drafted read-only (`draft_entry`, which probes the video) and compared on every
    analyze_lift.BINDING_FIELDS field (exercise, media, video, load) with an existing entry of that id.
    """
    fixtures = analyze_lift.load_manifest_fixtures(manifest)
    for plan in plans:
        clip, exercise = plan["clip"], plan["decision"]["exercise"]
        for fixture in fixtures:
            same_media = str(fixture.get("media", {}).get("sha256", "")).lower() == clip["sha256"]
            if same_media and fixture.get("id") != clip["fixture_id"]:
                raise WorkflowError(f"{clip['original_name']} is already registered as '{fixture.get('id')}'")
        existing = next((fixture for fixture in fixtures if fixture.get("id") == clip["fixture_id"]), None)
        if existing is None:
            continue
        drafted = analyze_lift.draft_entry(plan["media"], clip["fixture_id"], plate, exercise)
        conflicts = [field for field in analyze_lift.BINDING_FIELDS if existing.get(field) != drafted[field]]
        if conflicts:
            raise WorkflowError(
                f"{clip['fixture_id']} ({clip['original_name']}) is registered as {existing.get('exercise')!r} in "
                f"{rel(manifest)}, and this run differs in {', '.join(conflicts)} "
                f"({analyze_lift.describe_conflicts(existing, drafted, conflicts)}); fix the page choice or that "
                "entry deliberately (registered entries are never changed silently)")


def planned_outputs(plans: list[dict[str, Any]], paths: dict[str, Path]) -> list[Path]:
    outputs = [paths["input_csv"], paths["report"], paths["record"],
               paths["scale_report"] / "scale-reference-v1.json", paths["scale_report"] / "SCALE_REFERENCE_REPORT.md"]
    for plan in plans:
        outputs += [plan["seed"], plan["label_csv"], plan["click_csv"], *plan["outputs"].values()]
    return outputs


def known_clip_outputs(session: dict[str, Any], paths: dict[str, Path]) -> list[Path]:
    """Every per-clip file name this tool writes, for every clip of the session and every tracker."""
    outputs = []
    for clip in session["clips"]:
        fixture_id = clip["fixture_id"]
        outputs += [paths["seeds"] / f"{fixture_id}.manual-target-seed-v1.json",
                    paths["seeds"] / f"{fixture_id}.session-label.csv",
                    paths["scale"] / f"{fixture_id}.scale-reference.csv"]
        for tracker in sorted(TRACKERS):
            outputs += analyze_lift.output_paths(paths["analyses"], fixture_id, tracker).values()
        outputs += sorted((paths["frames"] / fixture_id).glob("frame_*.png"))
    return outputs


def stale_outputs(session: dict[str, Any], plans: list[dict[str, Any]], paths: dict[str, Path]) -> list[Path]:
    """Existing outputs of an earlier run that this run will not rewrite (dropped clips, other trackers)."""
    planned = set(planned_outputs(plans, paths))
    kept_frames = {paths["frames"] / plan["clip"]["fixture_id"] for plan in plans}
    return [path for path in known_clip_outputs(session, paths)
            if path.exists() and path not in planned and path.parent not in kept_frames]


def resolve_trackers(args: argparse.Namespace, decisions: list[dict[str, Any]],
                     runner: Runner) -> tuple[dict[str, str], str | None]:
    policy = TRACKER_POLICIES[args.tracker_policy]["trackers"]
    used = {decision["fixture_id"]: policy[decision["exercise"]] for decision in decisions
            if decision["decision"] == "confirmed"}
    needs_gpu = any(TRACKERS[tracker].needs_gpu_python for tracker in used.values())
    policy_has_gpu = any(TRACKERS[tracker].needs_gpu_python for tracker in policy.values())
    if args.gpu_python is not None and not policy_has_gpu:
        raise WorkflowError(f"--gpu-python is not used by --tracker-policy {args.tracker_policy}")
    if not needs_gpu:
        return used, None
    if args.gpu_python is None:
        raise WorkflowError(f"--tracker-policy {args.tracker_policy} runs {SAM2} for this session's lifts and needs "
                            "--gpu-python (the GPU venv); on a machine without a CUDA GPU use csrt-all-v1")
    gpu_python = analyze_lift.resolve_gpu_python(args.gpu_python)
    try:
        require_cuda(runner, gpu_python)
    except WorkflowError as error:
        raise WorkflowError(f"{error}; or choose --tracker-policy csrt-all-v1") from error
    return used, gpu_python


# --- Seeds and scale clicks ----------------------------------------------------------------------

def label_csv_text(clip: dict[str, Any], values: dict[str, float]) -> str:
    row = [repr(float(clip["timestamp_s"])), "", str(clip["frame_index"]), "labelled", "visible", LABEL_QUALITY,
           f"{values['plate_center_x_px']:.2f}", f"{values['plate_center_y_px']:.2f}",
           f"{values['plate_radius_px']:.2f}", "", "", "", "", "", "vbt session page confirmation"]
    return LABEL_HEADER + "\n" + ",".join(row) + "\n"


def seed_provenance(session: dict[str, Any], clip: dict[str, Any], statuses: dict[str, str]) -> str:
    plate = clip["suggestions"]["plate"]
    head = (f"VBT session {session['session_id']} page {session['page_id']} (vbt_session.py, #95): "
            f"plate centre {statuses['plate_center']}, plate radius {statuses['plate_radius']}")
    if plate.get("status") == "suggested":
        return f"{head}; suggestion {plate['id']} (method {plate['method']}, confidence {plate['confidence']})."
    return f"{head}; no suggestion ({plate.get('method')} failed: {plate.get('reason')}), placed by hand."


def build_seed_document(plan: dict[str, Any], session: dict[str, Any], manifest: Path) -> dict[str, Any]:
    metadata = session_ingest.read_json(plan["package_dir"] / "metadata.json")
    try:
        seed = annotations.build_seed(metadata, plan["label_csv"], annotations.load_json(manifest))
    except annotations.AnnotationError as error:
        raise WorkflowError(f"{plan['clip']['fixture_id']}: seed refused by annotations.py seed: {error}") from error
    notes = f"{seed['seed']['notes']} {seed_provenance(session, plan['clip'], plan['decision']['statuses'])}"
    return {**seed, "seed": {**seed["seed"], "notes": notes}}


def click_csv_text(clip: dict[str, Any], config: dict[str, Any], values: dict[str, float]) -> str:
    frame = config["frames"][0]
    row = [config["fixture_id"], config["source_video_sha256"], config["package_id"], str(frame["frame_index"]),
           repr(float(frame["timestamp_s"])), str(config["width_px"]), str(config["height_px"]),
           repr(float(config["known_length_m"])),
           f"{values['stick_low_x_px']:.2f}", f"{values['stick_low_y_px']:.2f}",
           f"{values['stick_high_x_px']:.2f}", f"{values['stick_high_y_px']:.2f}"]
    return ",".join(scale_reference.CSV_COLUMNS) + "\n" + ",".join(row) + "\n"


def write_scale_inputs(plan: dict[str, Any], stick_length_m: float) -> None:
    try:
        config = scale_reference.reference_config_from_label_package(plan["package_dir"], stick_length_m)
    except scale_reference.ScaleReferenceError as error:
        raise WorkflowError(f"{plan['clip']['fixture_id']}: {error}") from error
    session_ingest.write_text(plan["package_dir"] / scale_reference.REFERENCE_CONFIG_NAME,
                              json.dumps(config, indent=2, sort_keys=True, allow_nan=False) + "\n")
    session_ingest.write_text(plan["click_csv"], click_csv_text(plan["clip"], config, plan["decision"]["values"]))


def register(plan: dict[str, Any], manifest: Path, plate: float) -> None:
    clip = plan["clip"]
    entry, action = analyze_lift.register_video(plan["media"], manifest, clip["sha256"], plate,
                                                plan["decision"]["exercise"])
    video = entry["video"]
    if (entry["media"]["repository_path"] != clip["media_path"] or video.get("rotation_deg", 0) != clip["rotation_deg"]):
        raise WorkflowError(f"{clip['fixture_id']}: registered media or rotation differs from the session frame")
    print(f"{clip['fixture_id']}: manifest entry {action} ({plan['decision']['exercise']})")


# --- analyze_lift and the scale report -----------------------------------------------------------

def analyze_arguments(plan: dict[str, Any], args: argparse.Namespace, manifest: Path, gpu_python: str | None,
                      openbar_cli: Path | None, paths: dict[str, Path], show: Any) -> list[str]:
    argv = ["run", "--video", show(plan["media"]), "--seed", show(plan["seed"]),
            "--plate-diameter-m", args.plate_diameter_m, "--exercise", plan["decision"]["exercise"],
            "--manifest", show(manifest), "--output-dir", show(paths["analyses"]), "--tracker", plan["tracker"]]
    if TRACKERS[plan["tracker"]].needs_gpu_python:
        argv += ["--gpu-python", gpu_python]
    argv += ["--preset", args.preset] if args.preset else analyze_lift.analysis_options(args)
    if openbar_cli is not None:
        argv += ["--openbar-cli", show(openbar_cli)]
    return argv


def scale_report_namespace(plans: list[dict[str, Any]], manifest: Path, paths: dict[str, Path]) -> argparse.Namespace:
    return argparse.Namespace(manifest=manifest, analysis=[plan["outputs"]["analysis"] for plan in plans],
                              package_dir=[plan["package_dir"] for plan in plans],
                              csv=[plan["click_csv"] for plan in plans], output_dir=paths["scale_report"])


def scale_report_argv(namespace: argparse.Namespace) -> list[str]:
    argv = ["python", "validation/tools/scale_reference.py", "report", "--manifest", rel(namespace.manifest)]
    for analysis, package, click in zip(namespace.analysis, namespace.package_dir, namespace.csv):
        argv += ["--analysis", rel(analysis), "--package-dir", rel(package), "--csv", rel(click)]
    return argv + ["--output-dir", rel(namespace.output_dir)]


# --- Report --------------------------------------------------------------------------------------

def clip_crops(plan: dict[str, Any], analysis: dict[str, Any], seed: dict[str, Any], frame_entry: dict[str, Any],
               paths: dict[str, Path], cropper: session_report.Cropper) -> list[dict[str, Any]]:
    clip = plan["clip"]
    try:
        probed = label_package.probe(plan["media"])
        indices = session_report.crop_frames(analysis, len(probed["pts"]))
        if not indices:
            return []
        frames = label_package.extract(plan["media"], indices, [probed["pts"][i] for i in indices],
                                       paths["frames"] / clip["fixture_id"], frame_entry)
    except label_package.PackageError as error:
        raise WorkflowError(f"{clip['fixture_id']}: cannot extract tracking-check frames: {error}") from error
    size = (clip["width_px"], clip["height_px"])
    return [session_report.make_crop(frame, index, analysis, seed, size, cropper) for frame, index in zip(frames, indices)]


def write_report(plans: list[dict[str, Any]], session: dict[str, Any], configuration: dict[str, Any],
                 skipped: list[str], paths: dict[str, Path], cropper: session_report.Cropper) -> None:
    rows = {row["fixture_id"]: row for row in session_ingest.read_json(
        paths["scale_report"] / "scale-reference-v1.json")["rows"]}
    entries = {entry["id"]: entry for entry in session_ingest.read_json(
        paths["frames"].parent / session_ingest.FRAME_MANIFEST_NAME)["fixtures"]}
    clips = []
    for plan in plans:
        clip = plan["clip"]
        analysis = session_ingest.read_json(plan["outputs"]["analysis"])
        seed = session_ingest.read_json(plan["seed"])
        clips.append({
            "fixture_id": clip["fixture_id"], "original_name": clip["original_name"],
            "exercise": plan["decision"]["exercise"], "tracker": plan["tracker"], "statuses": plan["decision"]["statuses"],
            "analysis": analysis, "prediction": session_ingest.read_json(plan["outputs"]["prediction"]), "seed": seed,
            "scale_row": rows.get(clip["fixture_id"]),
            "crops": clip_crops(plan, analysis, seed, entries[clip["fixture_id"]], paths, cropper),
        })
    session_ingest.write_text(paths["report"], session_report.render_report(
        session["session_id"], configuration, clips, skipped))


# --- Command -------------------------------------------------------------------------------------

def sha(path: Path) -> str:
    return analyze_lift.file_sha256(path)


def clip_record(plan: dict[str, Any], argv: list[str]) -> dict[str, Any]:
    clip, decision = plan["clip"], plan["decision"]
    return {
        "fixture_id": clip["fixture_id"], "original_name": clip["original_name"], "decision": "confirmed",
        "exercise": decision["exercise"], "tracker": plan["tracker"], "item_statuses": decision["statuses"],
        "suggestion_ids": {kind: clip["suggestions"][kind].get("id") for kind in ("plate", "stick")},
        "video": {"path": clip["media_path"], "sha256": clip["sha256"]},
        "seed": {"path": rel(plan["seed"]), "sha256": sha(plan["seed"]),
                 "via": "annotations.build_seed on the label CSV, notes extended with the page provenance",
                 "label_csv": {"path": rel(plan["label_csv"]), "sha256": sha(plan["label_csv"])}},
        "scale_click_csv": {"path": rel(plan["click_csv"]), "sha256": sha(plan["click_csv"])},
        "analyze_lift": {"argv": ["python", "research/vbt-workflow/analyze_lift.py", *argv],
                         "run_record": {"path": rel(plan["outputs"]["run_record"]),
                                        "sha256": sha(plan["outputs"]["run_record"])}},
    }


def prepare(args: argparse.Namespace, runner: Runner, csv_bytes: bytes) -> dict[str, Any]:
    """Every check that can fail, in order, before the first write. Returns the run's plan."""
    manifest = analyze_lift.require_personal_manifest(args.manifest)
    plate = analyze_lift.parse_plate_diameter(args.plate_diameter_m)
    stick_length = positive_number(args.stick_length_m, "--stick-length-m")
    directory = session_ingest.session_dir(args.sessions_root, args.session)
    session = session_ingest.load_session(directory)
    if session is None:
        raise WorkflowError(f"session {args.session} has no {session_ingest.STATE_NAME}; run `ingest` first")
    if "template_sha256" not in session or session_ingest.compute_page_id(session) != session["page_id"]:
        raise WorkflowError(f"{session_ingest.STATE_NAME} does not match its page id; it was edited after ingest. "
                            "Re-run `ingest --force` and confirm the clips again")
    try:
        decisions = session_contract.parse_session_csv(csv_bytes, session)
    except session_contract.SessionCsvError as error:
        raise WorkflowError(f"session CSV refused: {error}") from error
    openbar_cli = None if args.openbar_cli is None else analyze_lift.resolve_openbar_cli(args.openbar_cli)
    analyze_lift.require_tools(runner, ("ffmpeg", "ffprobe"))
    trackers, gpu_python = resolve_trackers(args, decisions, runner)
    paths = session_paths(directory)
    clips = {clip["fixture_id"]: clip for clip in session["clips"]}
    plans = [plan_clip(clips[d["fixture_id"]], d, trackers[d["fixture_id"]], paths)
             for d in decisions if d["decision"] == "confirmed"]
    check_registrations(plans, manifest, plate)
    stale = stale_outputs(session, plans, paths)
    existing = [path for path in planned_outputs(plans, paths) if path.exists()] + stale
    if existing and not args.force:
        raise WorkflowError("session outputs already exist (pass --force to replace them; outputs of clips or "
                            "trackers this run does not use are then removed): "
                            + ", ".join(rel(path) for path in existing[:6]) + (" ..." if len(existing) > 6 else ""))
    return {
        "manifest": manifest, "plate": plate, "stick_length": stick_length, "directory": directory,
        "session": session, "paths": paths, "plans": plans, "stale": stale, "openbar_cli": openbar_cli,
        "gpu_python": gpu_python, "git": analyze_lift.git_provenance(runner),
        "skipped": [clips[d["fixture_id"]]["original_name"] for d in decisions if d["decision"] == "skipped"],
    }


def write_inputs(plan_set: dict[str, Any], csv_bytes: bytes) -> None:
    """The record goes first, so the folder is marked incomplete before anything changes."""
    paths, session, manifest = plan_set["paths"], plan_set["session"], plan_set["manifest"]
    paths["record"].unlink(missing_ok=True)
    # Outputs of clips or trackers this run does not use would otherwise sit next to the new set looking
    # current (for example an analysis the recommender could import). Only names this tool writes are removed.
    for path in plan_set["stale"]:
        path.unlink()
        print(f"removed stale output {rel(path)}")
    paths["input_csv"].parent.mkdir(parents=True, exist_ok=True)
    paths["input_csv"].write_bytes(csv_bytes)
    for plan in plan_set["plans"]:
        register(plan, manifest, plan_set["plate"])
        session_ingest.write_text(plan["label_csv"], label_csv_text(plan["clip"], plan["decision"]["values"]))
        session_ingest.write_text(plan["seed"], json.dumps(build_seed_document(plan, session, manifest), indent=2,
                                                           allow_nan=False) + "\n")
        write_scale_inputs(plan, plan_set["stick_length"])


def run_clips(plan_set: dict[str, Any], args: argparse.Namespace, runner: Runner) -> list[dict[str, Any]]:
    records = []
    common = (args, plan_set["manifest"], plan_set["gpu_python"], plan_set["openbar_cli"], plan_set["paths"])
    for plan in plan_set["plans"]:
        # Executed with absolute paths (and --force when asked); recorded repository-relative without --force,
        # so a forced re-run on the same inputs writes the same record.
        argv = analyze_arguments(plan, *common, lambda path: str(path.resolve()))
        argv += ["--force"] if args.force else []
        print(f"[analyze_lift] {plan['clip']['fixture_id']} ({plan['decision']['exercise']}, {plan['tracker']})",
              flush=True)
        if analyze_lift.main(argv, runner=runner) != 0:
            raise WorkflowError(f"analyze_lift.py run failed for {plan['clip']['fixture_id']}; see the error above")
        records.append(clip_record(plan, analyze_arguments(plan, *common, rel)))
    return records


def session_record(plan_set: dict[str, Any], configuration: dict[str, Any], records: list[dict[str, Any]],
                   namespace: argparse.Namespace) -> dict[str, Any]:
    paths, session, directory = plan_set["paths"], plan_set["session"], plan_set["directory"]
    return {
        "format": SESSION_RECORD_FORMAT, "format_version": SESSION_RECORD_VERSION,
        "workflow_version": analyze_lift.WORKFLOW_VERSION, "session_id": session["session_id"],
        "page_id": session["page_id"],
        "inputs": {"session_csv": {"path": rel(paths["input_csv"]), "sha256": sha(paths["input_csv"])},
                   "session_state": {"path": rel(directory / session_ingest.STATE_NAME),
                                     "sha256": sha(directory / session_ingest.STATE_NAME)},
                   "manifest": {"path": rel(plan_set["manifest"])}},
        "configuration": configuration,
        "clips": records,
        "skipped": plan_set["skipped"],
        "removed_stale_outputs": [rel(path) for path in plan_set["stale"]],
        "scale_report": {"argv": scale_report_argv(namespace), "outputs": {
            name: sha(paths["scale_report"] / name) for name in ("scale-reference-v1.json", "SCALE_REFERENCE_REPORT.md")}},
        "report": {"path": rel(paths["report"]), "sha256": sha(paths["report"])},
        "commands_note": "Run from the repository root with the research venv's python. Each analyze_lift run record "
                         "lists its own track and analyze commands.",
        "openbar": plan_set["git"],
    }


def command_run(args: argparse.Namespace, runner: Runner, csv_bytes: bytes,
                cropper: session_report.Cropper | None = None) -> int:
    plan_set = prepare(args, runner, csv_bytes)
    write_inputs(plan_set, csv_bytes)
    records = run_clips(plan_set, args, runner)
    paths = plan_set["paths"]
    namespace = scale_report_namespace(plan_set["plans"], plan_set["manifest"], paths)
    try:
        scale_reference.write_report(namespace)
    except (scale_reference.ScaleReferenceError, OSError, KeyError, ValueError) as error:
        raise WorkflowError(f"scale_reference.py report failed: {error}") from error
    configuration = {
        "plate_diameter_m": plan_set["plate"], "stick_length_m": plan_set["stick_length"],
        "stick_markers": "lowest and highest marker", "tracker_policy": args.tracker_policy,
        "tracker_policy_description": TRACKER_POLICIES[args.tracker_policy]["description"],
        "tracker_policy_trackers": TRACKER_POLICIES[args.tracker_policy]["trackers"],
        "gpu_python": plan_set["gpu_python"],
        "openbar_cli": None if plan_set["openbar_cli"] is None else rel(plan_set["openbar_cli"]),
        "preset": args.preset, "analyze_options": analyze_lift.analysis_options(args),
    }
    write_report(plan_set["plans"], plan_set["session"], configuration, plan_set["skipped"], paths,
                 cropper or session_report.default_cropper)
    session_ingest.write_json(paths["record"], session_record(plan_set, configuration, records, namespace))
    print(f"report: {rel(paths['report'])}\nsession record: {rel(paths['record'])}")
    return 0
