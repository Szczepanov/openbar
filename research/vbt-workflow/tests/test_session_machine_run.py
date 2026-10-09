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


if __name__ == "__main__":
    unittest.main()
