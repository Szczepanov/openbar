#!/usr/bin/env python3
"""`vbt_session.py run-research` (#113): research run of machine-initialized clips, fail closed. Stdlib only."""
from __future__ import annotations

import contextlib
import hashlib
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
import scale_reference  # noqa: E402
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
        self.assertEqual(sorted(record), sorted([*HEAD_KEYS, "implementation", "configuration", "clips",
                                                 "scale_report", "commands_note", "openbar"]))
        self.assertEqual(record["implementation"], {
            "name": "session_machine_run", "version": 1,
            "source_sha256": hashlib.sha256(Path(smr.__file__).read_bytes()).hexdigest()})
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
        for path in (self.record_path, self.profile_file, self.session_dir / session_ingest.STATE_NAME):
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

    def test_a_file_of_a_clip_dropped_by_reingest_is_stale_and_removed_with_force(self) -> None:
        self.prepare_session()
        self.assertEqual(self.run_research()[0], 0)
        stray = self.run_dir / "seeds" / "vbt-0000000000000000.machine-origin-seed.json"
        stray.write_text("{}", encoding="utf-8")
        code, _, err = self.run_research()
        self.assertEqual(code, 1)
        self.assertIn("already exist", err)
        self.assertTrue(stray.exists())
        code, _, err = self.run_research("--force")
        self.assertEqual(code, 0, err)
        self.assertFalse(stray.exists())
        self.assertEqual(self.run_record()["removed_stale_outputs"], [analyze_lift.display_path(stray)])

    def test_empty_directories_of_removed_outputs_are_pruned_but_not_the_root(self) -> None:
        self.prepare_session()
        self.assertEqual(self.run_research()[0], 0)
        stray = self.run_dir / "scale-packages" / "vbt-0000000000000000" / "metadata.json"
        stray.parent.mkdir(parents=True)
        stray.write_text("{}", encoding="utf-8")
        self.assertEqual(self.run_research("--force")[0], 0)
        self.assertFalse(stray.parent.exists())
        self.assertTrue(self.run_dir.is_dir())


class StatusKeyGuardTests(unittest.TestCase):
    def test_status_key_is_refused_at_depth_and_machine_origin_is_accepted(self) -> None:
        with self.assertRaisesRegex(WorkflowError, "must not carry a status key"):
            smr.require_no_status_keys({"clips": [{"items": {"plate_center": {"status": "accepted"}}}]})
        smr.require_no_status_keys({"clips": [{"origin": "machine"}]})


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
        manifest = self.manifest.read_bytes()
        self.assertEqual(self.run_research()[0], 0)
        self.assertEqual(self.outside_run_dir(), confirmed)
        self.assertEqual(self.manifest.read_bytes(), manifest)


if __name__ == "__main__":
    unittest.main()
