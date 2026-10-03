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
            "record": directory / "session-record.json", "frames": directory / "report-frames"}


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
    """Fail before any write when a registered entry disagrees with the confirmed lift or plate."""
    fixtures = analyze_lift.load_manifest_fixtures(manifest)
    for plan in plans:
        clip, exercise = plan["clip"], plan["decision"]["exercise"]
        for fixture in fixtures:
            same_media = str(fixture.get("media", {}).get("sha256", "")).lower() == clip["sha256"]
            if same_media and fixture.get("id") != clip["fixture_id"]:
                raise WorkflowError(f"{clip['original_name']} is already registered as '{fixture.get('id')}'")
            if fixture.get("id") != clip["fixture_id"]:
                continue
            if fixture.get("exercise") != exercise:
                raise WorkflowError(
                    f"{clip['fixture_id']} ({clip['original_name']}) is registered as {fixture.get('exercise')!r} in "
                    f"{rel(manifest)}, but the page says {exercise!r}; fix the page choice or that entry deliberately "
                    "(registered exercises are never changed silently)")
            if fixture.get("load", {}).get("plate_diameter_m") != plate:
                raise WorkflowError(f"{clip['fixture_id']} is registered with plate diameter "
                                    f"{fixture.get('load', {}).get('plate_diameter_m')} m, not {plate} m")


def planned_outputs(plans: list[dict[str, Any]], paths: dict[str, Path]) -> list[Path]:
    outputs = [paths["input_csv"], paths["report"], paths["record"],
               paths["scale_report"] / "scale-reference-v1.json", paths["scale_report"] / "SCALE_REFERENCE_REPORT.md"]
    for plan in plans:
        outputs += [plan["seed"], plan["label_csv"], plan["click_csv"], *plan["outputs"].values()]
    return outputs


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
        config = scale_reference._reference_config_from_label_package(plan["package_dir"], stick_length_m)
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
                      paths: dict[str, Path], show: Any) -> list[str]:
    argv = ["run", "--video", show(plan["media"]), "--seed", show(plan["seed"]),
            "--plate-diameter-m", args.plate_diameter_m, "--exercise", plan["decision"]["exercise"],
            "--manifest", show(manifest), "--output-dir", show(paths["analyses"]), "--tracker", plan["tracker"]]
    if TRACKERS[plan["tracker"]].needs_gpu_python:
        argv += ["--gpu-python", gpu_python]
    argv += ["--preset", args.preset] if args.preset else analyze_lift.analysis_options(args)
    if args.openbar_cli:
        argv += ["--openbar-cli", args.openbar_cli]
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


def command_run(args: argparse.Namespace, runner: Runner, csv_bytes: bytes,
                cropper: session_report.Cropper | None = None) -> int:
    manifest = analyze_lift.require_personal_manifest(args.manifest)
    plate = analyze_lift.parse_plate_diameter(args.plate_diameter_m)
    stick_length = positive_number(args.stick_length_m, "--stick-length-m")
    directory = session_ingest.session_dir(args.sessions_root, args.session)
    session = session_ingest.load_session(directory)
    if session is None:
        raise WorkflowError(f"session {args.session} has no {session_ingest.STATE_NAME}; run `ingest` first")
    try:
        decisions = session_contract.parse_session_csv(csv_bytes, session)
    except session_contract.SessionCsvError as error:
        raise WorkflowError(f"session CSV refused: {error}") from error
    analyze_lift.require_tools(runner, ("ffmpeg", "ffprobe"))
    trackers, gpu_python = resolve_trackers(args, decisions, runner)
    paths = session_paths(directory)
    clips = {clip["fixture_id"]: clip for clip in session["clips"]}
    plans = [plan_clip(clips[d["fixture_id"]], d, trackers[d["fixture_id"]], paths)
             for d in decisions if d["decision"] == "confirmed"]
    skipped = [clips[d["fixture_id"]]["original_name"] for d in decisions if d["decision"] == "skipped"]
    check_registrations(plans, manifest, plate)
    existing = [path for path in planned_outputs(plans, paths) if path.exists()]
    if existing and not args.force:
        raise WorkflowError("session outputs already exist (pass --force to replace them): "
                            + ", ".join(rel(path) for path in existing[:6]) + (" ..." if len(existing) > 6 else ""))
    git = analyze_lift.git_provenance(runner)

    paths["record"].unlink(missing_ok=True)  # no record = incomplete set, from here until the end
    paths["input_csv"].parent.mkdir(parents=True, exist_ok=True)
    paths["input_csv"].write_bytes(csv_bytes)
    for plan in plans:
        register(plan, manifest, plate)
        session_ingest.write_text(plan["label_csv"], label_csv_text(plan["clip"], plan["decision"]["values"]))
        session_ingest.write_text(plan["seed"], json.dumps(build_seed_document(plan, session, manifest), indent=2,
                                                           allow_nan=False) + "\n")
        write_scale_inputs(plan, stick_length)
    records = []
    for plan in plans:
        # Executed with absolute paths (and --force when asked); recorded repository-relative without --force,
        # so a forced re-run on the same inputs writes the same record.
        argv = analyze_arguments(plan, args, manifest, gpu_python, paths, lambda path: str(path.resolve()))
        argv += ["--force"] if args.force else []
        print(f"[analyze_lift] {plan['clip']['fixture_id']} ({plan['decision']['exercise']}, {plan['tracker']})", flush=True)
        if analyze_lift.main(argv, runner=runner) != 0:
            raise WorkflowError(f"analyze_lift.py run failed for {plan['clip']['fixture_id']}; see the error above")
        records.append(clip_record(plan, analyze_arguments(plan, args, manifest, gpu_python, paths, rel)))
    namespace = scale_report_namespace(plans, manifest, paths)
    try:
        scale_reference.write_report(namespace)
    except (scale_reference.ScaleReferenceError, OSError, KeyError, ValueError) as error:
        raise WorkflowError(f"scale_reference.py report failed: {error}") from error
    configuration = {
        "plate_diameter_m": plate, "stick_length_m": stick_length, "stick_markers": "lowest and highest marker",
        "tracker_policy": args.tracker_policy, "tracker_policy_description":
            TRACKER_POLICIES[args.tracker_policy]["description"],
        "tracker_policy_trackers": TRACKER_POLICIES[args.tracker_policy]["trackers"],
        "gpu_python": gpu_python, "preset": args.preset, "analyze_options": analyze_lift.analysis_options(args),
    }
    write_report(plans, session, configuration, skipped, paths, cropper or session_report.default_cropper)
    record = {
        "format": SESSION_RECORD_FORMAT, "format_version": SESSION_RECORD_VERSION,
        "workflow_version": analyze_lift.WORKFLOW_VERSION, "session_id": session["session_id"],
        "page_id": session["page_id"],
        "inputs": {"session_csv": {"path": rel(paths["input_csv"]), "sha256": sha(paths["input_csv"])},
                   "session_state": {"path": rel(directory / session_ingest.STATE_NAME),
                                     "sha256": sha(directory / session_ingest.STATE_NAME)},
                   "manifest": {"path": rel(manifest)}},
        "configuration": configuration,
        "clips": records,
        "skipped": skipped,
        "scale_report": {"argv": scale_report_argv(namespace), "outputs": {
            name: sha(paths["scale_report"] / name) for name in ("scale-reference-v1.json", "SCALE_REFERENCE_REPORT.md")}},
        "report": {"path": rel(paths["report"]), "sha256": sha(paths["report"])},
        "commands_note": "Run from the repository root with the research venv's python. Each analyze_lift run record "
                         "lists its own track and analyze commands.",
        "openbar": git,
    }
    session_ingest.write_json(paths["record"], record)
    print(f"report: {rel(paths['report'])}\nsession record: {rel(paths['record'])}")
    return 0
