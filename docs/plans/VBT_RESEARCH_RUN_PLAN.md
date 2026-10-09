# Research run of machine-initialized VBT clips (#113, slice 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `vbt_session.py run-research` command that tracks and analyzes the clips
`machine-init.json` initialized. All of its outputs go under `<session>/machine-run/` and stay
machine-origin, research-only and consumer-ineligible. The #95 confirmation path stays unchanged.

**Architecture:** A new standard-library module, `research/vbt-workflow/session_machine_run.py`,
mirrors `session_run.py`'s structure (plan with no writes → write inputs → run `analyze_lift.py run`
per clip → scale report → record last). It reads its input only through the existing fail-closed
reader `session_machine_init.load_machine_init`. It takes plate diameter, stick length and exercise
only from the re-supplied profile, and writes nothing outside `machine-run/`. `vbt_session.py`
gains the subcommand. It also gains a shared helper for the tracker/analysis flags, extracted
without changing behavior.

**Tech Stack:** Python 3 standard library, `unittest`, the existing #95 fake harness
(`tests/session_harness.py`), and the research venv interpreter
(`research/opencv-tracking/.venv/Scripts/python.exe`).

**Spec:** GitHub issue #113 (`gh issue view 113 --repo Szczepanov/openbar`), bounded by
[`docs/analysis/VBT_INITIALIZATION_DECISION.md`](../analysis/VBT_INITIALIZATION_DECISION.md). It
extends [`docs/validation/VBT_RESEARCH_INITIALIZATION.md`](../validation/VBT_RESEARCH_INITIALIZATION.md)
(slice 1, PR #119). Read all three before Task 1.

## Context and current state

- **Slice 1 (merged, #119)** ships `init-research --profile`, which writes `machine-init.json`, plus
  the reader `load_machine_init(directory, profile_bytes)`. The reader requires a byte-exact
  recomputation from the on-disk `session.json` and the profile bytes. The PR deferred one thing:
  "Consuming the record in `run` … to a follow-up. That follow-up must keep machine outputs separate
  from human-confirmed outputs and records." This plan is that follow-up.
- The #95 `run` (`session_run.py`) does several things that must not happen to machine values:
  - It parses a human CSV whose statuses must be `accepted`/`adjusted`/`manual`.
  - It writes the label CSV with `quality=high`.
  - It rounds values to 2 decimals.
  - It registers into the **personal** manifest.
  - It writes `reference-config.json` **into the ingested label package**.
  - It writes `seeds/`, `analyses/`, `report.html` and `session-record.json`.

  Consumers read the last two: `evaluate_suggestions.py` and `plate_scale_study.py` read
  `session-record.json`, and the recommender imports `analyses/*.analysis-v1.json`. So the
  research run cannot reuse `session_run.command_run`. It reuses only side-effect-free helpers
  (`TRACKER_POLICIES`, `scale_report_namespace`, `scale_report_argv`).

## Decisions taken by this plan (owner: confirm at review)

1. **Seed carrier.** The tracker and `openbar-cli analyze` accept only `manual-target-seed-v1`, and
   the canonical `Analysis` stores that seed as `manual_seed`. Changing canonical Analysis is out of
   scope for #113. The research run therefore writes a schema-valid `manual-target-seed-v1` and
   marks the machine origin in four places:
   - its `notes`, which travel into `Analysis.manual_seed.notes` (they begin
     `MACHINE-ORIGIN research seed … not a manual selection and not human-confirmed`);
   - its file name, `<fixture>.machine-origin-seed.json`;
   - its folder, `machine-run/`;
   - `machine-run-record.json`.

   The seed has no `selection_confidence`, because the schema defines that as human confidence and a
   suggester confidence is not one. The alternative is a new seed type, which would need Rust and
   schema versioning and is outside #113. If you reject this carrier, stop after slice 1.
2. **Session-local research manifest** (`machine-run/manifest.json`), not the personal manifest.
   A machine run registering into the personal manifest would have two effects:
   - It would bind the clip's exercise for every later #95 run.
   - It would make later `ingest` calls skip the video as "already registered".

   Either would constrain the human path, which the issue says must stay usable unchanged. The
   research manifest is rebuilt on every run.
3. **Scale-reference inputs are mirrored.** `metadata.json` is copied byte for byte, and
   `reference-config.json` is written, into `machine-run/scale-packages/<fixture>/`. The ingested
   `packages/<fixture>/` is only read.
4. **No `report.html`/crops and no #111 assessment integration in this slice** (YAGNI). The
   evidence is the analyses, the scale report and the record. Feeding machine runs to
   `assess_clip.py` is a follow-up.
5. **Issue closure.** With this slice, every #113 acceptance criterion has an implementation and a
   test, so the PR can say `Closes #113`. The owner decides.

## Global Constraints

- Standard library only. No new dependency, no Rust change, no schema/`*_VERSION`/golden/fixture change.
- Leave these #95 and slice-1 modules byte-unchanged: `session_contract.py`, `session_run.py`,
  `session_ingest.py`, `session_page.html`, `vbt_suggest.py`, `session_machine_init.py`,
  `evaluate_suggestions.py`, `assess_clip.py` and `analyze_lift.py`. Of the existing workflow
  files, only `vbt_session.py` changes.
- `run-research` writes only under `<session>/machine-run/`. It never writes the personal manifest,
  `seeds/`, `scale/`, `analyses/`, `packages/`, `session.json`, `session.html`, `session-input.csv`,
  `report.html`, `session-record.json` or `machine-init.json`.
- Machine values are used exactly as recorded, never rounded. No item or record has a `status` or
  `statuses` key at any depth. No confidence threshold of any kind.
- Plate diameter, stick length and exercise come only from the profile. `run-research` refuses
  `--plate-diameter-m`, `--stick-length-m`, `--manifest`, `--csv` and `--watch`.
- Records are deterministic. They contain no timestamps, no absolute paths, no original file names
  and no random ids. A same-input `--force` rerun is byte-identical.
- The flags `origin: "machine"`, `human_confirmed: false`, `research_only: true` and
  `consumer_eligible: false` are on every record.
- No #57 held-out data, no tracker/default selection, no consumer or live-trial write.
- Commits use conventional format with the `(research)` scope and reference `#113`.

## Review Focus

1. The owner re-ingests (new suggestions) and runs `run-research` without re-running `init-research`.
   Expected: the run is refused as stale (page id or `session.json` hash) and nothing is written.
   *Task 1: `test_edited_session_state_refuses` (hash). Task 3:
   `test_clip_rejected_after_reingest_has_its_outputs_removed_as_stale` (page id).*
2. The owner later runs the human-confirmed `run` on the same session. Expected: the run works, it
   does not delete `machine-run/`, and the personal manifest holds nothing the machine run put
   there. *Task 3: `ConfirmedPathTests`.*
3. A run is interrupted on clip 2. Expected: no record is written, a rerun without `--force` is
   refused, and `--force` reproduces a clean run byte for byte with no duplicate manifest entries.
   *Task 3: `test_interrupted_run_writes_no_record_and_force_rerun_matches_a_clean_run`.*
4. A clip becomes rejected after an earlier research run. Expected: its old machine outputs are
   removed on `--force` rather than left looking current. *Task 3: the stale test above.*
5. `machine-init.json` or the profile changes while tracking runs. Expected: no record is written.
   *Task 3: `test_inputs_changed_during_the_run_write_no_record`.*

---

## File structure

| File | Responsibility |
|---|---|
| `research/vbt-workflow/session_machine_run.py` (create) | `run-research`: input verification, tracker resolution, per-clip plan and documents, writes, `analyze_lift` runs, scale report, record. |
| `research/vbt-workflow/vbt_session.py` (modify) | `run-research` subparser, shared `add_tracking` flag helper, `parse_args` preset rule, `main` dispatch, docstring. |
| `research/vbt-workflow/tests/test_session_machine_run.py` (create) | All tests for the new command, using the #95 fake harness and slice-1 test helpers. |
| `docs/validation/VBT_RESEARCH_INITIALIZATION.md` (modify) | New "Research run" contract section; update the "later slice" and "Limits" sentences. |
| `docs/plans/VBT_WORKFLOW_PLAN.md` (modify) | Update the #113 paragraph. |

Run every command from the repository root. `PY` below means
`research/opencv-tracking/.venv/Scripts/python.exe` (Windows) or `research/opencv-tracking/.venv/bin/python`.

---

### Task 1: CLI entry and fail-closed input verification

**Files:**
- Create: `research/vbt-workflow/session_machine_run.py`
- Modify: `research/vbt-workflow/vbt_session.py` (`build_parser`, `parse_args`, module docstring)
- Test: `research/vbt-workflow/tests/test_session_machine_run.py`

**Interfaces:**
- Consumes (existing):
  - `session_machine_init`: `read_profile(path) -> bytes`,
    `load_machine_init(directory, profile_bytes) -> dict`,
    `load_session_state(directory) -> (dict, bytes)`, `sha256_hex(bytes) -> str`,
    `render_record(dict) -> str`, `RECORD_NAME`, `ORIGIN`, `FORBIDDEN_KEYS`;
  - `session_ingest`: `session_dir(root, id) -> Path`, `STATE_NAME`;
  - `session_run.TRACKER_POLICIES`; `vbt_trackers.TRACKERS` and `require_cuda`;
  - `analyze_lift`: `resolve_gpu_python`, `file_sha256`, `display_path`.
- Produces:
  - `RUN_DIR_NAME = "machine-run"`, `RUN_RECORD_NAME = "machine-run-record.json"`,
    `RUN_RECORD_FORMAT = "openbar-research-vbt-machine-run-record"`, `RUN_RECORD_VERSION = 1`,
    `SEED_SUFFIX = ".machine-origin-seed.json"`,
    `SCALE_REPORT_NAMES = ("scale-reference-v1.json", "SCALE_REFERENCE_REPORT.md")`;
  - `rel(path) -> str`, `sha(path) -> str`;
  - `run_paths(directory) -> dict[str, Path]` with keys `root, manifest, seeds, scale, packages,
    analyses, scale_report, record`;
  - `input_hashes(directory, profile_path) -> dict[str, str]` with keys `session_state_sha256,
    profile_sha256, machine_init_sha256`;
  - `load_inputs(args) -> dict` with keys `directory, record, state, hashes, profile_path`;
  - `initialized_clips(inputs) -> list[tuple[state_clip, init_clip]]`;
  - `resolve_tracker(args, exercise, runner) -> tuple[str, str | None]`;
  - `vbt_session.add_tracking(parser) -> None`; the `run-research` subcommand.

- [ ] **Step 1: Write the failing tests**

Create `research/vbt-workflow/tests/test_session_machine_run.py`:

```python
#!/usr/bin/env python3
"""`vbt_session.py run-research` (#113): research run of machine-initialized clips, fail closed. Stdlib only."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_fakes as fakes  # noqa: E402
from session_harness import SessionRunner  # noqa: E402
from test_session_machine_init import MachineInitTestCase, pair, plate, profile_text, stick  # noqa: E402

import analyze_lift  # noqa: E402
import session_ingest  # noqa: E402
import session_machine_run as smr  # noqa: E402
import session_run  # noqa: E402
import vbt_session  # noqa: E402
from vbt_process import WorkflowError  # noqa: E402

ROOT = analyze_lift.ROOT
PRESET = "vbt-sg-0.15s-v1"


class MachineRunTestCase(MachineInitTestCase):
    def prepare_session(self, *pairs: dict[str, Any]) -> list[dict[str, Any]]:
        """Ingest one clip per suggestion pair (default two good clips), write the profile, init-research."""
        code, _, err = self.ingest_clips(*(pairs or (pair(), pair())))
        self.assertEqual(code, 0, err)
        code, _, err = self.init()
        self.assertEqual(code, 0, err)
        return self.state_document()["clips"]

    @property
    def run_dir(self) -> Path:
        return self.session_dir / smr.RUN_DIR_NAME

    def research_args(self, *extra: str, policy: str = "csrt-all-v1") -> list[str]:
        profile = getattr(self, "profile_file", self.dir / "profile.json")
        return ["run-research", "--session", self.session_id, "--sessions-root", str(self.sessions),
                "--profile", str(profile), "--tracker-policy", policy, "--preset", PRESET, *extra]

    def parsed(self, *extra: str, policy: str = "csrt-all-v1") -> Any:
        return vbt_session.parse_args(self.research_args(*extra, policy=policy))

    def quiet(self, action: Callable[[], Any]) -> Any:
        with contextlib.redirect_stdout(io.StringIO()):
            return action()

    def run_research(self, *extra: str, policy: str = "csrt-all-v1",
                     runner: SessionRunner | None = None) -> tuple[int, str, str]:
        return self.main(self.research_args(*extra, policy=policy), runner=runner)

    def outside_run_dir(self) -> dict[str, bytes]:
        prefix = smr.RUN_DIR_NAME + "/"
        return {name: data for name, data in self.snapshot().items() if not name.startswith(prefix)}

    def run_dir_snapshot(self) -> dict[str, bytes]:
        prefix = smr.RUN_DIR_NAME + "/"
        return {name: data for name, data in self.snapshot().items() if name.startswith(prefix)}

    def run_record(self) -> dict[str, Any]:
        return json.loads((self.run_dir / smr.RUN_RECORD_NAME).read_text(encoding="utf-8"))

    def assert_refused(self, needle: str, action: Callable[[], Any]) -> None:
        """The action fails with a message containing `needle` and writes nothing."""
        before = self.snapshot()
        with self.assertRaises(WorkflowError) as caught:
            self.quiet(action)
        self.assertIn(needle, str(caught.exception))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.run_dir.exists())


class ParserTests(MachineRunTestCase):
    def test_run_research_parses_with_a_preset(self) -> None:
        args = self.parsed()
        self.assertEqual((args.command, args.tracker_policy, args.preset, args.force),
                         ("run-research", "csrt-all-v1", PRESET, False))

    def test_lengths_manifest_and_csv_flags_are_refused(self) -> None:
        for flag, value in (("--plate-diameter-m", "0.45"), ("--stick-length-m", "1.30"),
                            ("--manifest", "manifest.json"), ("--csv", "x.csv"), ("--watch", "downloads")):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                vbt_session.parse_args([*self.research_args(), flag, value])

    def test_preset_or_explicit_filter_flags_are_required(self) -> None:
        without_preset = [part for part in self.research_args() if part not in ("--preset", PRESET)]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            vbt_session.parse_args(without_preset)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            vbt_session.parse_args([*self.research_args(), "--filter", "raw"])


class InputTests(MachineRunTestCase):
    def test_verified_record_yields_its_initialized_clips_and_input_hashes(self) -> None:
        clips = self.prepare_session()
        inputs = smr.load_inputs(self.parsed())
        chosen = smr.initialized_clips(inputs)
        self.assertEqual([clip["fixture_id"] for clip, _ in chosen], [clip["fixture_id"] for clip in clips])
        self.assertEqual([init["outcome"] for _, init in chosen], ["initialized", "initialized"])
        self.assertEqual(inputs["hashes"], {
            "session_state_sha256": analyze_lift.file_sha256(self.session_dir / session_ingest.STATE_NAME),
            "profile_sha256": analyze_lift.file_sha256(self.profile_file),
            "machine_init_sha256": analyze_lift.file_sha256(self.record_path)})
        self.assertEqual(inputs["record"]["profile"]["exercise"], "snatch")

    def test_missing_record_refuses(self) -> None:
        self.prepare_session()
        self.record_path.unlink()
        self.assert_refused("has no machine-init.json", lambda: smr.load_inputs(self.parsed()))

    def test_different_profile_refuses(self) -> None:
        self.prepare_session()
        self.write_profile(profile_text({"stick_length_m": "1.31"}))
        self.assert_refused("different profile", lambda: smr.load_inputs(self.parsed()))

    def test_promoted_record_refuses(self) -> None:
        self.prepare_session()
        original = self.record_path.read_bytes()
        for key, value in (("human_confirmed", True), ("consumer_eligible", True), ("origin", "human")):
            with self.subTest(key=key):
                self.write_record_document({**self.record(), key: value})
                self.assert_refused("must be machine origin", lambda: smr.load_inputs(self.parsed()))
                self.record_path.write_bytes(original)
        document = self.record()
        document["clips"][0]["items"]["plate_center"]["status"] = "accepted"
        self.write_record_document(document)
        self.assert_refused("must not carry a status key", lambda: smr.load_inputs(self.parsed()))

    def test_edited_session_state_refuses(self) -> None:
        self.prepare_session()
        path = self.session_dir / session_ingest.STATE_NAME
        path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=4, sort_keys=True) + "\n",
                        encoding="utf-8")
        self.assert_refused("session.json changed after it was written", lambda: smr.load_inputs(self.parsed()))

    def test_no_initialized_clip_refuses(self) -> None:
        self.prepare_session(pair(fakes.FAILED_PLATE))
        inputs = smr.load_inputs(self.parsed())
        self.assert_refused("initializes no clip", lambda: smr.initialized_clips(inputs))


class TrackerTests(MachineRunTestCase):
    def test_cpu_policy_uses_csrt(self) -> None:
        self.assertEqual(smr.resolve_tracker(self.parsed(), "snatch", SessionRunner()), ("csrt", None))

    def test_mixed_policy_uses_csrt_for_back_squat_without_a_gpu(self) -> None:
        args = self.parsed(policy="sam2-olympic-csrt-squat-v1")
        self.assertEqual(smr.resolve_tracker(args, "back_squat", SessionRunner()), ("csrt", None))

    def test_gpu_policy_without_gpu_python_refuses(self) -> None:
        args = self.parsed(policy="sam2-all-v1")
        self.assert_refused("needs --gpu-python", lambda: smr.resolve_tracker(args, "snatch", SessionRunner()))

    def test_gpu_python_with_a_cpu_policy_refuses(self) -> None:
        args = self.parsed("--gpu-python", str(self.make_gpu_python()))
        self.assert_refused("is not used by", lambda: smr.resolve_tracker(args, "snatch", SessionRunner()))

    def test_gpu_policy_without_cuda_refuses(self) -> None:
        args = self.parsed("--gpu-python", str(self.make_gpu_python()), policy="sam2-all-v1")
        self.assert_refused("or choose --tracker-policy csrt-all-v1",
                            lambda: smr.resolve_tracker(args, "snatch", SessionRunner(cuda=False)))

    def test_gpu_policy_with_cuda_runs_sam2(self) -> None:
        args = self.parsed("--gpu-python", str(self.make_gpu_python()), policy="sam2-all-v1")
        tracker, gpu_python = smr.resolve_tracker(args, "snatch", SessionRunner())
        self.assertEqual(tracker, session_run.SAM2)
        self.assertIsNotNone(gpu_python)


if __name__ == "__main__":
    unittest.main()
```

`plate` and `stick` are imported now because Task 2 uses them.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PY -m unittest discover -s research/vbt-workflow/tests -p test_session_machine_run.py -v`
Expected: ERROR, `ModuleNotFoundError: No module named 'session_machine_run'`.

- [ ] **Step 3: Create the module with the input layer**

Create `research/vbt-workflow/session_machine_run.py`:

```python
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
```

`json`, `scale_reference` and `schema_check` are unused until Task 2. If a linter complains, leave
them in; Task 2 uses them.

- [ ] **Step 4: Wire the subcommand into `vbt_session.py`**

In `research/vbt-workflow/vbt_session.py`:

(a) In the module docstring, after the `run` paragraph (the line ending `--watch <folder> waits
(bounded by --watch-timeout-s) for vbt-session-<session>.csv.`), add:

```
  init-research / run-research
          research only (#113): machine-initialize clips from a validated profile, then track and
          analyze them with every output under <session>/machine-run/; never human-confirmed and
          not consumer-eligible. See docs/validation/VBT_RESEARCH_INITIALIZATION.md.
```

(b) Add `import session_machine_run` after `import session_machine_init`.

(c) Add this function above `build_parser`:

```python
def add_tracking(parser: argparse.ArgumentParser) -> None:
    """Tracker policy and analysis options, shared by `run` and `run-research`."""
    parser.add_argument("--tracker-policy", required=True, choices=sorted(session_run.TRACKER_POLICIES),
                        help="; ".join(f"{name}: {policy['description']}"
                                       for name, policy in sorted(session_run.TRACKER_POLICIES.items())))
    parser.add_argument("--gpu-python", help="GPU venv interpreter, needed when the policy runs SAM 2")
    parser.add_argument("--openbar-cli", help="prebuilt openbar-cli binary passed to analyze_lift.py")
    parser.add_argument("--preset", choices=sorted(analyze_lift.PRESETS))
    parser.add_argument("--filter", choices=analyze_lift.FILTERS)
    for flag, kind in analyze_lift.FILTER_FLAGS:
        parser.add_argument(flag, type=kind)
    for flag in analyze_lift.KINEMATICS_FLAGS:
        parser.add_argument(flag, type=analyze_lift.finite_number)
```

(d) In `build_parser`, replace everything from `run.add_argument("--tracker-policy", ...` to the
end of the `KINEMATICS_FLAGS` loop with:

```python
    run.add_argument("--force", action="store_true", help="replace existing session outputs")
    add_tracking(run)

    research = sub.add_parser("run-research", help="research only: track and analyze the clips machine-init.json "
                                                   "initialized; outputs under machine-run/, never human-confirmed")
    research.add_argument("--session", required=True, help="session id, e.g. 2026-10-03")
    research.add_argument("--sessions-root", type=Path, default=DEFAULT_SESSIONS_ROOT,
                          help="the sessions root used by ingest and init-research "
                               "(default validation/private/vbt/sessions)")
    research.add_argument("--profile", type=Path, required=True,
                          help="the profile machine-init.json was written with; the plate diameter, stick length "
                               "and exercise come only from it")
    research.add_argument("--force", action="store_true", help="replace existing machine-run outputs")
    add_tracking(research)
```

`run` keeps exactly the same flags. Only their help order changes, because `--force` now comes
before the tracking flags.

(e) In `parse_args`, change `if args.command != "run":` to `if args.command not in ("run", "run-research"):`.

- [ ] **Step 5: Run the new tests and the existing #95 parser tests**

Run: `PY -m unittest discover -s research/vbt-workflow/tests -p test_session_machine_run.py -v`
Expected: all `ParserTests`, `InputTests` and `TrackerTests` pass.

Run: `PY -m unittest discover -s research/vbt-workflow/tests -p "test_*session*.py"`
Expected: OK. The existing `run` tests, including `test_explicit_filter_flags_are_required_without_preset`,
are unchanged and still pass.

- [ ] **Step 6: Commit**

```bash
git add research/vbt-workflow/session_machine_run.py research/vbt-workflow/vbt_session.py research/vbt-workflow/tests/test_session_machine_run.py
git commit -m "feat(research): verify machine-init inputs for run-research (#113)"
```

---

### Task 2: Per-clip machine-origin documents and input writes

**Files:**
- Modify: `research/vbt-workflow/session_machine_run.py` (append below `resolve_tracker`)
- Test: `research/vbt-workflow/tests/test_session_machine_run.py` (append test classes before `if __name__`)

**Interfaces:**
- Consumes (Task 1): `load_inputs`, `initialized_clips`, `resolve_tracker`, `run_paths`, `rel`,
  `SEED_SUFFIX`, `SCALE_REPORT_NAMES`. Existing: `analyze_lift.draft_entry`, `register_video`,
  `output_paths`, `analysis_options`, `resolve_openbar_cli`, `require_tools`, `git_provenance`,
  `SEED_SCHEMA`, `ROOT`; `scale_reference.reference_config_from_label_package`, `CSV_COLUMNS`,
  `REFERENCE_CONFIG_NAME`; `schema_check.load_schema`, `validate_document`, `SchemaError`;
  `session_ingest.write_text`; `smi.parse_json_object`.
- Produces:
  - `seed_notes(init_clip, profile, hashes) -> str`;
  - `seed_document(clip, init_clip, notes) -> dict`;
  - `click_csv_text(config, init_clip) -> str`;
  - `plan_clip(clip, init_clip, tracker, paths, profile, hashes) -> dict`, with keys `clip, init,
    tracker, media, package_dir, seed, click_csv, outputs, texts`. `texts` is an ordered
    `{Path: str}`.
  - `check_media(plans, profile) -> None`; `planned_outputs(plans, paths) -> list[Path]`;
  - `known_clip_outputs(state, paths) -> list[Path]`;
    `stale_outputs(state, plans, paths) -> list[Path]`;
  - `prepare(args, runner) -> dict`. The plan set is the `load_inputs` keys plus `profile, options,
    openbar_cli, gpu_python, tracker, paths, plans, stale, rejected, git`.
  - `write_inputs(plan_set) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session_machine_run.py`, before `if __name__ == "__main__":`. Also add
`import scale_reference  # noqa: E402` after the `import analyze_lift` line at the top:

```python
RAW_PLATE = plate(center_x_px=400.125)
RAW_STICK = stick(low_x_px=830.125)


class PlanAndInputsTests(MachineRunTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.clips = self.prepare_session(pair(RAW_PLATE, RAW_STICK), pair())
        self.runner = SessionRunner()

    def prepared(self, *extra: str) -> dict[str, Any]:
        return self.quiet(lambda: smr.prepare(self.parsed(*extra), self.runner))

    def written(self) -> dict[str, Any]:
        plan_set = self.prepared()
        self.quiet(lambda: smr.write_inputs(plan_set))
        return plan_set

    def test_prepare_plans_every_initialized_clip_without_writing(self) -> None:
        before = self.snapshot()
        plan_set = self.prepared()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([plan["clip"]["fixture_id"] for plan in plan_set["plans"]],
                         [clip["fixture_id"] for clip in self.clips])
        self.assertEqual({plan["tracker"] for plan in plan_set["plans"]}, {"csrt"})
        self.assertEqual((plan_set["stale"], plan_set["rejected"]), ([], []))

    def test_seed_is_machine_origin_unrounded_and_accepted_by_analyze_lift(self) -> None:
        self.written()
        clip = self.clips[0]
        path = self.run_dir / "seeds" / f"{clip['fixture_id']}.machine-origin-seed.json"
        seed = json.loads(path.read_text(encoding="utf-8"))
        notes = seed["seed"].pop("notes")
        self.assertEqual(seed, {"schema_version": 1, "fixture_id": clip["fixture_id"], "seed": {
            "timestamp_s": 0.0, "frame_index": 0,
            "target": {"center": {"x_px": 400.125, "y_px": 1500.0}, "radius_px": 180.25},
            "coordinate_space": "display_top_left", "source_rotation_deg": 0}})
        self.assertTrue(notes.startswith("MACHINE-ORIGIN research seed"), notes)
        for needle in ("not a manual selection and not human-confirmed", "not consumer-eligible",
                       f"machine-init.json sha256={analyze_lift.file_sha256(self.record_path)}",
                       f"plate suggestion {fakes.PLATE['id']}", "suggester confidence 0.8, not a selection confidence"):
            self.assertIn(needle, notes)
        for word in ("accepted", "adjusted", "placed by hand"):
            self.assertNotIn(word, notes)
        analyze_lift.load_bound_seed(path, clip["fixture_id"], ROOT / clip["media_path"])  # schema-valid, bound

    def test_click_csv_carries_the_unrounded_stick_values(self) -> None:
        self.written()
        clip = self.clips[0]
        data = (self.run_dir / "scale" / f"{clip['fixture_id']}.scale-reference.csv").read_bytes()
        row = ",".join([clip["fixture_id"], clip["sha256"], clip["package_id"], "0", "0.0", "1080", "1920", "1.3",
                        "830.125", "1630.0", "840.0", "580.0"])
        self.assertEqual(data.decode("utf-8"), ",".join(scale_reference.CSV_COLUMNS) + "\n" + row + "\n")
        click, _ = scale_reference.parse_click_csv(data)
        self.assertEqual(click["point_a_x_px"], 830.125)

    def test_scale_package_is_a_mirror_and_the_ingested_package_is_untouched(self) -> None:
        source = ROOT / self.clips[0]["package_dir"]
        before = {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}
        self.written()
        mirror = self.run_dir / "scale-packages" / self.clips[0]["fixture_id"]
        self.assertEqual((mirror / "metadata.json").read_bytes(), (source / "metadata.json").read_bytes())
        self.assertEqual(json.loads((mirror / scale_reference.REFERENCE_CONFIG_NAME).read_text(encoding="utf-8")),
                         scale_reference.reference_config_from_label_package(source, 1.3))
        self.assertEqual({path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}, before)
        self.assertFalse((source / scale_reference.REFERENCE_CONFIG_NAME).exists())

    def test_research_manifest_is_session_local_and_the_personal_manifest_is_untouched(self) -> None:
        before = self.outside_run_dir()
        self.written()
        self.assertEqual(self.outside_run_dir(), before)
        self.assertFalse(self.manifest.exists(), "a machine run never registers in the personal manifest")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([(entry["id"], entry["exercise"]) for entry in manifest["fixtures"]],
                         [(clip["fixture_id"], "snatch") for clip in self.clips])

    def test_package_coordinate_system_mismatch_refuses_before_writing(self) -> None:
        path = ROOT / self.clips[0]["package_dir"] / "metadata.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["coordinate_system"]["origin"] = "bottom_left"
        path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        self.assert_refused("coordinate system", lambda: smr.prepare(self.parsed(), self.runner))

    def test_existing_outputs_refuse_without_force_and_force_plans_no_stale_output(self) -> None:
        self.written()
        before = self.snapshot()
        with self.assertRaises(WorkflowError) as caught:
            self.prepared()
        self.assertIn("machine-run outputs already exist", str(caught.exception))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.prepared("--force")["stale"], [])
```

`assert_refused` asserts that `run_dir` does not exist. That holds in the coordinate test because
nothing has been written yet.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PY -m unittest discover -s research/vbt-workflow/tests -p test_session_machine_run.py -k PlanAndInputsTests -v`
Expected: ERROR, `AttributeError: module 'session_machine_run' has no attribute 'prepare'`.

- [ ] **Step 3: Implement the documents, the plan and the input writes**

Append to `research/vbt-workflow/session_machine_run.py`:

```python
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


def known_clip_outputs(state: dict[str, Any], paths: dict[str, Path]) -> list[Path]:
    """Every per-clip file name this command writes, for every clip of the session and every tracker."""
    outputs = []
    for clip in state["clips"]:
        fixture_id = clip["fixture_id"]
        package = paths["packages"] / fixture_id
        outputs += [package / "metadata.json", package / scale_reference.REFERENCE_CONFIG_NAME,
                    paths["seeds"] / f"{fixture_id}{SEED_SUFFIX}", paths["scale"] / f"{fixture_id}.scale-reference.csv"]
        for tracker in sorted(TRACKERS):
            outputs += analyze_lift.output_paths(paths["analyses"], fixture_id, tracker).values()
    return outputs


def stale_outputs(state: dict[str, Any], plans: list[dict[str, Any]], paths: dict[str, Path]) -> list[Path]:
    """Existing machine-run outputs this run will not rewrite (a clip now rejected, another tracker)."""
    planned = set(planned_outputs(plans, paths))
    return [path for path in known_clip_outputs(state, paths) if path.exists() and path not in planned]


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
    stale = stale_outputs(inputs["state"], plans, paths)
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
        path.unlink()
        print(f"removed stale output {rel(path)}")
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PY -m unittest discover -s research/vbt-workflow/tests -p test_session_machine_run.py -v`
Expected: every test in `ParserTests`, `InputTests`, `TrackerTests` and `PlanAndInputsTests` passes.

- [ ] **Step 5: Commit**

```bash
git add research/vbt-workflow/session_machine_run.py research/vbt-workflow/tests/test_session_machine_run.py
git commit -m "feat(research): plan machine-origin seeds and scale inputs for run-research (#113)"
```

---

### Task 3: Run, record, restart and isolation; contract documentation

**Files:**
- Modify: `research/vbt-workflow/session_machine_run.py` (append), `research/vbt-workflow/vbt_session.py` (`main`)
- Modify: `docs/validation/VBT_RESEARCH_INITIALIZATION.md`, `docs/plans/VBT_WORKFLOW_PLAN.md`
- Test: `research/vbt-workflow/tests/test_session_machine_run.py` (append)

**Interfaces:**
- Consumes (Tasks 1–2): `prepare`, `write_inputs`, `input_hashes`, `rel`, `sha`, `RUN_RECORD_*`,
  `SCALE_REPORT_NAMES`, plan keys `clip, init, tracker, media, seed, click_csv, outputs`, and plan-set
  keys `directory, state, hashes, profile_path, profile, options, openbar_cli, gpu_python, paths, plans,
  stale, rejected, git`. Existing: `analyze_lift.main(argv, runner) -> int`, `WORKFLOW_VERSION`;
  `session_run.scale_report_namespace(plans, manifest, paths)`, `scale_report_argv(namespace)`;
  `scale_reference.write_report(namespace)`; `session_ingest.write_json`.
- Produces:
  - `analyze_arguments(plan, plan_set, args, show) -> list[str]`;
    `clip_entry(plan, argv) -> dict`; `run_clips(plan_set, args, runner) -> list[dict]`;
  - `require_no_status_keys(value, where=RUN_RECORD_NAME) -> None`;
    `run_record(plan_set, args, entries, namespace) -> dict`;
    `require_inputs_unchanged(plan_set) -> None`;
  - `command_run_research(args, runner) -> int`; `vbt_session.main` dispatches `run-research`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session_machine_run.py`, before `if __name__ == "__main__":`:

```python
HEAD_KEYS = ("format", "format_version", "origin", "human_confirmed", "research_only", "consumer_eligible",
             "workflow_version", "session_id", "page_id", "inputs", "profile", "rejected", "removed_stale_outputs")


class FailOnFixtureRunner(SessionRunner):
    """Stops the run when the given clip is tracked, as an interrupted session would."""

    def __init__(self, fixture_id: str) -> None:
        super().__init__()
        self.fixture_id = fixture_id

    def execute(self, argv: list[str]) -> None:
        if "--fixture" in argv and argv[argv.index("--fixture") + 1] == self.fixture_id:
            raise WorkflowError("simulated interruption")
        super().execute(argv)


class EditOnceRunner(SessionRunner):
    """Appends a space to one input file after the first external step, as a concurrent edit would."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.edited = False

    def execute(self, argv: list[str]) -> None:
        super().execute(argv)
        if not self.edited:
            self.path.write_bytes(self.path.read_bytes() + b" ")
            self.edited = True


class ResearchRunTests(MachineRunTestCase):
    def test_run_tracks_each_initialized_clip_and_writes_the_record(self) -> None:
        clips = self.prepare_session()
        runner = SessionRunner()
        code, out, err = self.run_research(runner=runner)
        self.assertEqual(code, 0, err)
        self.assertEqual(len([argv for argv in runner.executed if "--fixture" in argv]), 4,
                         "one track and one analyze step per clip")
        for clip in clips:
            for path in analyze_lift.output_paths(self.run_dir / "analyses", clip["fixture_id"], "csrt").values():
                self.assertTrue(path.is_file(), path)
        for name in smr.SCALE_REPORT_NAMES:
            self.assertTrue((self.run_dir / "scale-report" / name).is_file(), name)
        self.assertTrue((self.run_dir / smr.RUN_RECORD_NAME).is_file())
        self.assertIn("research only", out)
        self.assertIn("not consumer-eligible", out)

    def test_record_contract(self) -> None:
        clips = self.prepare_session()
        self.assertEqual(self.run_research()[0], 0)
        record = self.run_record()
        self.assertEqual({key: record[key] for key in HEAD_KEYS}, {
            "format": "openbar-research-vbt-machine-run-record", "format_version": 1, "origin": "machine",
            "human_confirmed": False, "research_only": True, "consumer_eligible": False,
            "workflow_version": analyze_lift.WORKFLOW_VERSION, "session_id": self.session_id,
            "page_id": self.state_document()["page_id"],
            "inputs": {"session_state_sha256": analyze_lift.file_sha256(self.session_dir / session_ingest.STATE_NAME),
                       "profile_sha256": analyze_lift.file_sha256(self.profile_file),
                       "machine_init_sha256": analyze_lift.file_sha256(self.record_path)},
            "profile": {"profile_id": "lab-profile-1", "exercise": "snatch", "plate_diameter_m": 0.45,
                        "stick_length_m": 1.3},
            "rejected": [], "removed_stale_outputs": []})
        self.assertEqual(sorted(record), sorted([*HEAD_KEYS, "configuration", "clips", "scale_report",
                                                 "commands_note", "openbar"]))
        self.assertEqual((record["configuration"]["tracker_policy"], record["configuration"]["preset"]),
                         ("csrt-all-v1", PRESET))
        self.assertEqual([entry["fixture_id"] for entry in record["clips"]], [clip["fixture_id"] for clip in clips])
        for entry, clip in zip(record["clips"], clips):
            self.assertEqual((entry["origin"], entry["tracker"]), ("machine", "csrt"))
            self.assertEqual(entry["suggestion_ids"], {"plate": fakes.PLATE["id"], "stick": fakes.STICK["id"]})
            self.assertEqual(entry["video"], {"path": clip["media_path"], "sha256": clip["sha256"]})
            for name in ("seed", "scale_click_csv", "analysis"):
                self.assertEqual(entry[name]["sha256"], analyze_lift.file_sha256(ROOT / entry[name]["path"]), name)
            self.assertTrue(entry["analysis"]["path"].endswith(
                f"machine-run/analyses/{clip['fixture_id']}.opencv-csrt.analysis-v1.json"))
            argv = entry["analyze_lift"]["argv"]
            self.assertEqual(argv[:3], ["python", "research/vbt-workflow/analyze_lift.py", "run"])
            self.assertNotIn("--force", argv)
            self.assertEqual(argv[argv.index("--manifest") + 1], analyze_lift.display_path(self.run_dir / "manifest.json"))
            self.assertEqual(argv[argv.index("--plate-diameter-m") + 1], "0.45")
            self.assertEqual(argv[argv.index("--exercise") + 1], "snatch")
        text = (self.run_dir / smr.RUN_RECORD_NAME).read_text(encoding="utf-8")
        for needle in ('"status"', '"statuses"', '"accepted"', '"adjusted"', json.dumps(str(ROOT))[1:-1],
                       ROOT.as_posix()):
            self.assertNotIn(needle, text)
        self.assertFalse((self.session_dir / session_ingest.RECORD_NAME).exists(), "no #95 session record")

    def test_writes_only_under_machine_run(self) -> None:
        self.prepare_session()
        before = self.outside_run_dir()
        self.assertEqual(self.run_research()[0], 0)
        self.assertEqual(self.outside_run_dir(), before)
        self.assertFalse(self.manifest.exists(), "a machine run never registers in the personal manifest")

    def test_force_rerun_is_byte_identical_and_existing_outputs_refuse(self) -> None:
        self.prepare_session()
        self.assertEqual(self.run_research()[0], 0)
        first = self.snapshot()
        code, _, err = self.run_research()
        self.assertEqual(code, 1)
        self.assertIn("already exist", err)
        self.assertEqual(self.snapshot(), first)
        self.assertEqual(self.run_research("--force")[0], 0)
        self.assertEqual(self.snapshot(), first)


class RestartTests(MachineRunTestCase):
    def manifest_ids(self) -> list[str]:
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        return [entry["id"] for entry in manifest["fixtures"]]

    def test_interrupted_run_writes_no_record_and_force_rerun_matches_a_clean_run(self) -> None:
        clips = self.prepare_session()
        self.assertEqual(self.run_research()[0], 0)
        clean = self.run_dir_snapshot()
        code, _, err = self.run_research("--force", runner=FailOnFixtureRunner(clips[1]["fixture_id"]))
        self.assertEqual(code, 1)
        self.assertIn("was not written", err)
        self.assertFalse((self.run_dir / smr.RUN_RECORD_NAME).exists())
        code, _, err = self.run_research()
        self.assertEqual(code, 1)
        self.assertIn("already exist", err)
        self.assertEqual(self.run_research("--force")[0], 0)
        self.assertEqual(self.run_dir_snapshot(), clean)
        self.assertEqual(self.manifest_ids(), [clip["fixture_id"] for clip in clips])

    def test_inputs_changed_during_the_run_write_no_record(self) -> None:
        self.prepare_session()
        for path in (self.record_path, self.profile_file):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                code, _, err = self.run_research("--force", runner=EditOnceRunner(path))
                self.assertEqual(code, 1)
                self.assertIn("changed during the research run", err)
                self.assertFalse((self.run_dir / smr.RUN_RECORD_NAME).exists())
                path.write_bytes(original)

    def test_promoted_record_after_a_run_is_refused_and_outputs_are_left_alone(self) -> None:
        self.prepare_session()
        self.assertEqual(self.run_research()[0], 0)
        self.write_record_document({**self.record(), "human_confirmed": True})
        before = self.snapshot()
        code, _, err = self.run_research("--force")
        self.assertEqual(code, 1)
        self.assertIn("must be machine origin", err)
        self.assertEqual(self.snapshot(), before)

    def test_clip_rejected_after_reingest_has_its_outputs_removed_as_stale(self) -> None:
        clips = self.prepare_session()
        self.assertEqual(self.run_research()[0], 0)
        dropped = clips[1]["fixture_id"]
        self.assertEqual(self.ingest_clips(pair(), pair(fakes.FAILED_PLATE))[0], 0)
        code, _, err = self.run_research("--force")
        self.assertEqual(code, 1, "machine-init.json is stale until init-research runs again")
        self.assertIn("is stale", err)  # new suggestions change the page id, which the reader checks first
        self.assertEqual(self.init(self.profile_file, "--force")[0], 0)
        code, _, err = self.run_research()
        self.assertEqual(code, 1)
        self.assertIn("already exist", err)
        code, out, err = self.run_research("--force")
        self.assertEqual(code, 0, err)
        self.assertEqual([path for path in self.run_dir.rglob("*") if path.is_file() and dropped in path.as_posix()],
                         [])
        record = self.run_record()
        self.assertEqual(record["rejected"], [{"fixture_id": dropped, "reasons": ["plate_suggestion_missing"]}])
        self.assertTrue(record["removed_stale_outputs"])
        self.assertTrue(all(dropped in path for path in record["removed_stale_outputs"]))
        self.assertEqual(self.manifest_ids(), [clips[0]["fixture_id"]])
        self.assertIn("removed stale output", out)
        self.assertIn("not run, rejected by init-research", out)


class ConfirmedPathTests(MachineRunTestCase):
    def confirmed_rows(self) -> list[dict[str, str]]:
        state = self.state_document()
        return [fakes.accepted_row(clip, state, "snatch") for clip in state["clips"]]

    def test_confirmed_run_after_a_research_run_works_and_leaves_machine_run_alone(self) -> None:
        self.prepare_session()
        self.assertEqual(self.run_research()[0], 0)
        self.assertFalse(self.manifest.exists(), "the research run registered nothing in the personal manifest")
        machine = self.run_dir_snapshot()
        code, _, err = self.run_session(self.confirmed_rows())
        self.assertEqual(code, 0, err)
        self.assertEqual(self.run_dir_snapshot(), machine)
        record = json.loads((self.session_dir / session_ingest.RECORD_NAME).read_text(encoding="utf-8"))
        self.assertEqual({clip["item_statuses"]["plate_center"] for clip in record["clips"]}, {"accepted"})
        self.assertNotIn(smr.RUN_DIR_NAME, json.dumps(record))

    def test_research_run_after_a_confirmed_run_leaves_its_outputs_alone(self) -> None:
        self.prepare_session()
        self.assertEqual(self.run_session(self.confirmed_rows())[0], 0)
        confirmed = self.outside_run_dir()
        self.assertEqual(self.run_research()[0], 0)
        self.assertEqual(self.outside_run_dir(), confirmed)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PY -m unittest discover -s research/vbt-workflow/tests -p test_session_machine_run.py -k ResearchRunTests -v`
Expected: FAIL or ERROR. `vbt_session.main` falls through to the `run` branch and raises
`AttributeError: 'Namespace' object has no attribute 'csv'`.

- [ ] **Step 3: Implement the run, the record and the command**

Append to `research/vbt-workflow/session_machine_run.py`:

```python
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
```

- [ ] **Step 4: Dispatch `run-research` in `vbt_session.main`**

In `research/vbt-workflow/vbt_session.py` `main`, directly after the line
`return session_machine_init.command_init_research(args)`, add:

```python
        if args.command == "run-research":
            return session_machine_run.command_run_research(args, runner)
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `PY -m unittest discover -s research/vbt-workflow/tests -p test_session_machine_run.py -v`
Expected: every test passes, including `ResearchRunTests`, `RestartTests` and `ConfirmedPathTests`.

If `test_force_rerun_is_byte_identical...` fails, list the differing files. A difference in
`scale-report/` or `analyses/` means a non-deterministic input reached a writer. Find it and fix it;
do not relax the test.

- [ ] **Step 6: Document the contract**

In `docs/validation/VBT_RESEARCH_INITIALIZATION.md`:

(a) In the second paragraph, replace
`Consuming the record in `run` is a later slice and is not implemented.` with
``The separate research command `run-research` (see [Research run](#research-run)) tracks and analyzes the initialized clips.``

(b) Insert this section directly before `## Limits`:

```markdown
## Research run

`vbt_session.py run-research` is the opt-in, research-only consumer of `machine-init.json`
(`research/vbt-workflow/session_machine_run.py`, standard library only). It tracks and analyzes every
`initialized` clip and writes every output under `<session>/machine-run/`. Rejected clips are not run;
the command names them and points to the #95 confirmation page.

```powershell
research/opencv-tracking/.venv/Scripts/python.exe research/vbt-workflow/vbt_session.py run-research `
  --session 2026-10-03 --profile validation/private/vbt/profiles/snatch-lab.json `
  --tracker-policy csrt-all-v1 --preset vbt-sg-0.15s-v1
```

- **Inputs.** The record is read only through `load_machine_init`, so the profile must be the one
  the record was written with. Plate diameter, stick length and exercise come only from that profile.
  `--plate-diameter-m`, `--stick-length-m`, `--manifest`, `--csv` and `--watch` are refused.
  `--tracker-policy`, `--gpu-python`, `--openbar-cli`, `--preset` and the explicit filter/kinematics
  flags follow the same rules as `run`. A record with no initialized clip is refused.
- **Outputs**, all under `machine-run/`:
  - `manifest.json`: a session-local research manifest, rebuilt on every run.
  - `seeds/<fixture>.machine-origin-seed.json`.
  - `scale/<fixture>.scale-reference.csv` and `scale-packages/<fixture>/` (a byte copy of the label
    package `metadata.json` plus `reference-config.json`).
  - `analyses/<fixture>.<tracker>.*` from `analyze_lift.py run`.
  - `scale-report/`.
  - `machine-run-record.json`.
- **Never written:** the personal manifest, `seeds/`, `scale/`, `analyses/`, `packages/`,
  `session.json`, `session.html`, `session-input.csv`, `report.html`, `session-record.json` and
  `machine-init.json`. A machine run therefore never binds a clip's exercise in the personal manifest
  and never hides a video from a later `ingest`. The #95 `run` neither reads nor removes
  `machine-run/`.
- **Seed.** The tracker and analyzer accept only `manual-target-seed-v1`, and the canonical
  `Analysis` stores it as `manual_seed`, which #113 does not change. The research seed is therefore a
  schema-valid `manual-target-seed-v1`. Its plate centre and radius are the recorded values,
  unrounded. It has no `selection_confidence`, because that means human confidence. Its `notes`,
  which the canonical analysis carries, begin `MACHINE-ORIGIN research seed` and say it is not a
  manual selection, not human-confirmed and not consumer-eligible. The notes also give the
  `machine-init.json` and profile hashes and the plate suggestion id, method and suggester
  confidence. Any analysis whose seed notes begin that way is machine-origin. Do not import such an
  analysis into the recommender.
- **Scale clicks** carry the recorded stick values in `repr` form, not the 2-decimal #95 form.
- **Record** (`openbar-research-vbt-machine-run-record` v1, sorted keys, written last):
  - `origin: machine`, `human_confirmed: false`, `research_only: true`, `consumer_eligible: false`;
  - `inputs` with the SHA-256 of `session.json`, the profile and `machine-init.json`;
  - the profile, the tracker configuration and the per-clip seed, click, analysis and run-record
    hashes, with each clip's `origin: machine`;
  - the rejected clips with their reasons, the removed stale outputs, the scale report and the git
    state.

  It has no `status` or `statuses` key at any depth (checked before writing), no timestamp, no
  absolute path and no original file name.
- **Restart and idempotence.** All validation runs before the first write. Then:
  - The record is deleted first and written last, so a folder without it is incomplete.
  - Existing outputs are refused without `--force`.
  - `--force` removes this command's outputs for clips that are no longer initialized, and rebuilds
    the research manifest.
  - A same-input `--force` rerun is byte-identical.
  - An interrupted run leaves no record, and its `--force` rerun reproduces a clean run.
  - If `session.json`, `machine-init.json` or the profile changes during the run, no record is written.
  - A re-ingest makes `machine-init.json` stale until `init-research` runs again.
```

(c) In `## Limits`, replace the first bullet (`- No run, no tracking, no kinematics and no canonical `Analysis` change.`) with:

```markdown
- `init-research` runs no tracking or kinematics; `run-research` does, with outputs under
  `machine-run/` only. Neither changes the canonical `Analysis`.
```

and replace the bullet `- No seed, CSV, manifest, `session.html`, `session-input.csv` or `session-record.json` is written.` with:

```markdown
- Neither command writes a #95 seed, CSV, the personal manifest, `session.html`, `session-input.csv`
  or `session-record.json`.
```

(d) In `## Tests`, add a paragraph:

```markdown
`research/vbt-workflow/tests/test_session_machine_run.py` covers the research run. It runs with the
#95 fakes and checks:

- the refused flags and missing, promoted or stale inputs;
- the GPU policy rules;
- the machine-origin seed, the unrounded click CSV, the mirrored scale package and the
  session-local manifest;
- the record contract;
- that nothing is written outside `machine-run/`;
- byte-identical `--force` reruns, interruption and restart, mid-run input changes and stale-output
  removal;
- that the #95 `run` still works before and after a research run.

The synthetic public fixture has no stick, so `init-research` rejects it. There is no
`OPENBAR_VBT_E2E` test for this command.
```

In `docs/plans/VBT_WORKFLOW_PLAN.md`, in the #113 paragraph (the one starting `Research-only machine
initialization (#113)`), replace `Consuming it in `run` is a later slice.` with:

```markdown
`vbt_session.py run-research --session <id> --profile <profile.json> --tracker-policy <policy> --preset <preset>`
then tracks and analyzes the initialized clips. All of its outputs, including a session-local research
manifest and `machine-run-record.json`, go under `<session>/machine-run/`. It never writes the personal
manifest, `analyses/` or `session-record.json`, and its analyses are not for the recommender import.
```

Then run `git grep -n "later slice" docs research` and fix any remaining sentence that says the
research run does not exist.

- [ ] **Step 7: Run the full research and validation suites**

Run: `PY -m unittest discover -s research/vbt-workflow/tests -p "test_*.py"`
Expected: OK. The skip count matches main (`OPENBAR_VBT_E2E` opt-ins plus the Windows symlink skip).

Run: `python -m unittest discover -s validation/tests -p "test_*.py"`
Expected: OK.

Run: `python validation/tools/schema_check.py`
Expected: every document passes. The count is unchanged, because no committed JSON changed.

Run: `PY -m compileall -q research/vbt-workflow` and `git diff --check`
Expected: no output.

Run: `git diff --stat main -- research/vbt-workflow/session_contract.py research/vbt-workflow/session_run.py research/vbt-workflow/session_ingest.py research/vbt-workflow/session_page.html research/vbt-workflow/vbt_suggest.py research/vbt-workflow/session_machine_init.py research/vbt-workflow/evaluate_suggestions.py research/vbt-workflow/assess_clip.py research/vbt-workflow/analyze_lift.py`
Expected: empty, because the #95 and slice-1 modules are untouched.

- [ ] **Step 8: Commit**

```bash
git add research/vbt-workflow/session_machine_run.py research/vbt-workflow/vbt_session.py research/vbt-workflow/tests/test_session_machine_run.py docs/validation/VBT_RESEARCH_INITIALIZATION.md docs/plans/VBT_WORKFLOW_PLAN.md
git commit -m "feat(research): run machine-initialized clips under machine-run/ (#113)"
```

---

## Stop conditions

- If `analyze_lift.py run` or `scale_reference.py` cannot accept the research inputs without a
  change to those modules, stop and report. Changing them alters the #95 path.
- If a byte-identical rerun cannot be reached without removing a field from the record, stop and
  report which field is non-deterministic.
- If review rejects decision 1 (the `manual-target-seed-v1` carrier), stop. Do not invent a new seed
  type in this issue.

## After merge

- Open a follow-up issue for the #111 assessment of `machine-run/` analyses (decision 4) if the owner
  wants machine runs assessed.
- Machine-origin sessions stay ineligible for #114 handoff and for #79 and recommender writes unless
  a separate reviewed consumer policy permits them.
