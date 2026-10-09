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
