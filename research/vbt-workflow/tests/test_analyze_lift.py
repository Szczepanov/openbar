#!/usr/bin/env python3
"""Deterministic tests for the personal VBT workflow orchestrator (#86).

Stdlib only: FFmpeg, OpenCV, cargo and git are replaced by a fake runner, and the
ffprobe-backed probe is stubbed, so these run without media tooling.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

WORKFLOW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKFLOW_DIR))
import analyze_lift  # noqa: E402

ROOT = analyze_lift.ROOT
VIDEO_BYTES = b"not really a video, but hashable"
VIDEO_SHA256 = hashlib.sha256(VIDEO_BYTES).hexdigest()
FIXTURE_ID = f"vbt-{VIDEO_SHA256[:16]}"
EXPLICIT_FILTER = [
    "--filter", "savitzky-golay", "--filter-window-s", "0.15", "--filter-polynomial-order", "2",
    "--filter-max-gap-s", "0.2", "--kinematics-max-gap-s", "0.2", "--kinematics-min-confidence", "0",
]


def fake_probe(path: Path) -> dict[str, Any]:
    return {
        "repository_path": "validation/private/vbt/media/lift.mp4",
        "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "duration_s": 10.0,
        "encoded_width_px": 1080,
        "encoded_height_px": 1920,
        "rotation_deg": 0,
        "display_width_px": 1080,
        "display_height_px": 1920,
        "nominal_fps": 60,
        "measured_fps": 59.94,
        "frame_count": 600,
    }


class FakeRunner(analyze_lift.Runner):
    """Records commands; writes deterministic stand-ins for track.py and analyze output."""

    def __init__(self, *, missing: tuple[str, ...] = (), fail_step: str | None = None) -> None:
        self.missing = set(missing)
        self.fail_step = fail_step
        self.executed: list[list[str]] = []

    def which(self, name: str) -> str | None:
        return None if name in self.missing else f"/usr/bin/{name}"

    def capture(self, argv: list[str]) -> str:
        if argv[:2] == ["git", "-C"] and "rev-parse" in argv:
            return "0123456789abcdef0123456789abcdef01234567\n"
        if argv[:2] == ["git", "-C"] and "status" in argv:
            return ""
        return f"{Path(argv[0]).name} version 1.0-test\nmore detail\n"

    def execute(self, argv: list[str]) -> None:
        self.executed.append(list(argv))
        output = Path(argv[argv.index("--output") + 1])
        if not output.is_absolute():
            output = ROOT / output
        step = "track" if any(part.endswith("track.py") for part in argv) else "analyze"
        output.parent.mkdir(parents=True, exist_ok=True)
        if step == "track":
            output.write_text(json.dumps({
                "schema_version": 1,
                "fixture_id": argv[argv.index("--fixture") + 1],
                "implementation": {"name": "opencv-csrt", "version": "spike-1",
                                   "config": {"opencv_version": "4.12.0", "numpy_version": "2.2.6"}},
                "samples": [
                    {"timestamp_s": 1.5, "state": "tracked", "center_px": {"x_px": 1, "y_px": 2}, "confidence": 1.0},
                    {"timestamp_s": 1.6, "state": "lost"},
                ],
            }, indent=2) + "\n", encoding="utf-8")
        else:
            output.write_text('{"schema_version": 1}\n', encoding="utf-8")
        if step == self.fail_step:
            raise analyze_lift.WorkflowError(f"{step} failed (fake)")


class WorkflowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="openbar-vbt-workflow-")
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.video = self.dir / "lift.mp4"
        self.video.write_bytes(VIDEO_BYTES)
        self.manifest = self.dir / "vbt" / "manifest.json"
        self.output_dir = self.dir / "analyses"
        self.seed = self.write_seed(FIXTURE_ID)
        patcher = mock.patch.object(analyze_lift.fixture_probe, "probe_video", side_effect=fake_probe)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_seed(self, fixture_id: str | None, name: str = "seed.json") -> Path:
        document: dict[str, Any] = {
            "schema_version": 1,
            "seed": {"timestamp_s": 1.5, "frame_index": 90,
                     "target": {"center": {"x_px": 1.0, "y_px": 2.0}, "radius_px": 50.0},
                     "coordinate_space": "display_top_left", "source_rotation_deg": 0},
        }
        if fixture_id is not None:
            document["fixture_id"] = fixture_id
        path = self.dir / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def run_args(self, *extra: str, filter_args: list[str] | None = None) -> list[str]:
        return [
            "run", "--video", str(self.video), "--seed", str(self.seed), "--plate-diameter-m", "0.45",
            "--exercise", "clean", "--manifest", str(self.manifest), "--output-dir", str(self.output_dir),
            *(EXPLICIT_FILTER if filter_args is None else filter_args), *extra,
        ]

    def main(self, argv: list[str], runner: FakeRunner | None = None) -> tuple[int, str, str, FakeRunner]:
        runner = runner or FakeRunner()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = analyze_lift.main(argv, runner=runner)
        return code, stdout.getvalue(), stderr.getvalue(), runner

    def outputs(self) -> list[str]:
        return sorted(path.name for path in self.output_dir.glob("*")) if self.output_dir.exists() else []


class FixtureIdTests(unittest.TestCase):
    def test_id_is_derived_from_sha256(self) -> None:
        self.assertEqual(analyze_lift.fixture_id_for("AB" * 32), "vbt-abababababababab")

    def test_id_rejects_non_sha256(self) -> None:
        with self.assertRaises(analyze_lift.WorkflowError):
            analyze_lift.fixture_id_for("xyz")


class ManifestGuardTests(WorkflowTestCase):
    def test_protected_manifest_is_refused_and_untouched(self) -> None:
        protected = ROOT / "validation" / "private" / "manifest.json"
        before = protected.read_bytes() if protected.exists() else None
        for spelling in (protected, ROOT / "validation" / "private" / "vbt" / ".." / "manifest.json"):
            argv = ["register", "--video", str(self.video), "--plate-diameter-m", "0.45",
                    "--exercise", "clean", "--manifest", str(spelling)]
            code, _, stderr, _ = self.main(argv)
            self.assertNotEqual(code, 0)
            self.assertIn("refusing", stderr)
        after = protected.read_bytes() if protected.exists() else None
        self.assertEqual(before, after)

    def test_public_manifest_is_refused(self) -> None:
        public = ROOT / "validation" / "fixtures" / "public" / "manifest.json"
        before = public.read_bytes()
        code, _, stderr, _ = self.main(["register", "--video", str(self.video), "--plate-diameter-m", "0.45",
                                        "--exercise", "clean", "--manifest", str(public)])
        self.assertNotEqual(code, 0)
        self.assertIn("refusing", stderr)
        self.assertEqual(public.read_bytes(), before)

    def test_default_manifest_is_the_separate_personal_manifest(self) -> None:
        self.assertEqual(analyze_lift.DEFAULT_MANIFEST, ROOT / "validation" / "private" / "vbt" / "manifest.json")
        self.assertNotEqual(analyze_lift.DEFAULT_MANIFEST.resolve(), analyze_lift.PROTECTED_MANIFEST.resolve())

    def test_full_run_never_touches_protected_manifest(self) -> None:
        protected = self.dir / "protected-manifest.json"
        protected.write_text('{"schema_version": 1, "fixtures": []}\n', encoding="utf-8")
        before = protected.read_bytes()
        with mock.patch.object(analyze_lift, "PROTECTED_MANIFEST", protected):
            code, _, stderr, _ = self.main(self.run_args())
        self.assertEqual(code, 0, stderr)
        self.assertEqual(protected.read_bytes(), before)


class RegistrationTests(WorkflowTestCase):
    def register(self, plate: str = "0.45", exercise: str = "clean") -> tuple[int, str, str]:
        code, stdout, stderr, _ = self.main(["register", "--video", str(self.video), "--plate-diameter-m", plate,
                                             "--exercise", exercise, "--manifest", str(self.manifest)])
        return code, stdout, stderr

    def test_register_is_idempotent(self) -> None:
        code, stdout, stderr = self.register()
        self.assertEqual(code, 0, stderr)
        self.assertIn(FIXTURE_ID, stdout)
        first = self.manifest.read_bytes()
        code, stdout, stderr = self.register()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.manifest.read_bytes(), first)
        manifest = json.loads(first)
        self.assertEqual([fixture["id"] for fixture in manifest["fixtures"]], [FIXTURE_ID])
        entry = manifest["fixtures"][0]
        self.assertEqual(entry["media"]["sha256"], VIDEO_SHA256)
        self.assertEqual(entry["load"]["plate_diameter_m"], 0.45)
        self.assertEqual(entry["purpose"], "development")
        self.assertEqual(entry["source"]["redistribution_status"], "private_only")

    def test_register_prints_seed_next_steps(self) -> None:
        _, stdout, _ = self.register()
        self.assertIn("label_package.py", stdout)
        self.assertIn("--at-s", stdout)
        self.assertIn("annotations.py seed", stdout)

    def test_register_keeps_hand_edited_conditions(self) -> None:
        self.register()
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["fixtures"][0]["conditions"]["lighting"] = "mixed"
        self.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        edited = self.manifest.read_bytes()
        code, _, stderr = self.register()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.manifest.read_bytes(), edited)

    def test_register_with_conflicting_plate_fails_closed(self) -> None:
        self.register()
        before = self.manifest.read_bytes()
        code, _, stderr = self.register(plate="0.40")
        self.assertNotEqual(code, 0)
        self.assertIn("load", stderr)
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_register_with_conflicting_exercise_fails_closed(self) -> None:
        self.register()
        code, _, stderr = self.register(exercise="snatch")
        self.assertNotEqual(code, 0)
        self.assertIn("exercise", stderr)

    def test_same_media_under_another_id_fails_closed(self) -> None:
        self.register()
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["fixtures"][0]["id"] = "hand-made-id"
        self.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        code, _, stderr = self.register()
        self.assertNotEqual(code, 0)
        self.assertIn("hand-made-id", stderr)


class FailClosedTests(WorkflowTestCase):
    def assert_nothing_happened(self, runner: FakeRunner) -> None:
        self.assertEqual(runner.executed, [])
        self.assertFalse(self.manifest.exists())
        self.assertEqual(self.outputs(), [])

    def test_seed_for_a_different_video_fails_closed(self) -> None:
        self.seed = self.write_seed("vbt-0000000000000000", "other.json")
        code, _, stderr, runner = self.main(self.run_args())
        self.assertNotEqual(code, 0)
        self.assertIn("different video", stderr)
        self.assert_nothing_happened(runner)

    def test_seed_without_fixture_id_fails_closed(self) -> None:
        self.seed = self.write_seed(None, "anonymous.json")
        code, _, stderr, runner = self.main(self.run_args())
        self.assertNotEqual(code, 0)
        self.assertIn("fixture_id", stderr)
        self.assert_nothing_happened(runner)

    def test_malformed_seed_fails_closed(self) -> None:
        self.seed.write_text("{not json", encoding="utf-8")
        code, _, stderr, runner = self.main(self.run_args())
        self.assertNotEqual(code, 0)
        self.assert_nothing_happened(runner)

    def test_missing_ffmpeg_fails_closed(self) -> None:
        for tool in ("ffmpeg", "ffprobe"):
            code, _, stderr, runner = self.main(self.run_args(), FakeRunner(missing=(tool,)))
            self.assertNotEqual(code, 0)
            self.assertIn(tool, stderr)
            self.assert_nothing_happened(runner)

    def test_register_without_ffprobe_fails_closed(self) -> None:
        code, _, stderr, _ = self.main(["register", "--video", str(self.video), "--plate-diameter-m", "0.45",
                                        "--exercise", "clean", "--manifest", str(self.manifest)],
                                       FakeRunner(missing=("ffprobe",)))
        self.assertNotEqual(code, 0)
        self.assertFalse(self.manifest.exists())

    def test_existing_output_without_force_fails_closed(self) -> None:
        self.output_dir.mkdir()
        existing = self.output_dir / f"{FIXTURE_ID}.analysis-v1.json"
        existing.write_text("previous\n", encoding="utf-8")
        code, _, stderr, runner = self.main(self.run_args())
        self.assertNotEqual(code, 0)
        self.assertIn("--force", stderr)
        self.assertEqual(runner.executed, [])
        self.assertEqual(existing.read_text(encoding="utf-8"), "previous\n")

    def test_force_replaces_existing_outputs(self) -> None:
        self.output_dir.mkdir()
        existing = self.output_dir / f"{FIXTURE_ID}.analysis-v1.json"
        existing.write_text("previous\n", encoding="utf-8")
        code, _, stderr, _ = self.main(self.run_args("--force"))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(existing.read_text(encoding="utf-8"), '{"schema_version": 1}\n')

    def test_failed_step_leaves_no_partial_outputs(self) -> None:
        for step in ("track", "analyze"):
            code, _, stderr, _ = self.main(self.run_args(), FakeRunner(fail_step=step))
            self.assertNotEqual(code, 0)
            self.assertIn(step, stderr)
            self.assertEqual(self.outputs(), [])

    def test_missing_git_commit_fails_closed(self) -> None:
        class NoGit(FakeRunner):
            def capture(self, argv: list[str]) -> str:
                if argv[0] == "git":
                    raise analyze_lift.WorkflowError("git is not on PATH")
                return super().capture(argv)

        code, _, stderr, runner = self.main(self.run_args(), NoGit())
        self.assertNotEqual(code, 0)
        self.assertIn("git", stderr)
        self.assertEqual(runner.executed, [])

    def test_invalid_plate_diameter_fails_closed(self) -> None:
        for value in ("0", "-0.45", "nan", "inf"):
            argv = self.run_args()
            argv[argv.index("--plate-diameter-m") + 1] = value
            code, _, _, runner = self.main(argv)
            self.assertNotEqual(code, 0, value)
            self.assert_nothing_happened(runner)


class ExplicitConfigurationTests(WorkflowTestCase):
    def assert_usage_error(self, argv: list[str]) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            analyze_lift.main(argv, runner=FakeRunner())
        self.assertEqual(raised.exception.code, 2)

    def test_filter_is_required(self) -> None:
        self.assert_usage_error(self.run_args(filter_args=[
            "--kinematics-max-gap-s", "0.2", "--kinematics-min-confidence", "0"]))

    def test_kinematics_thresholds_are_required(self) -> None:
        self.assert_usage_error(self.run_args(filter_args=[
            "--filter", "raw", "--kinematics-max-gap-s", "0.2"]))
        self.assert_usage_error(self.run_args(filter_args=[
            "--filter", "raw", "--kinematics-min-confidence", "0"]))

    def test_plate_diameter_is_required(self) -> None:
        argv = self.run_args()
        index = argv.index("--plate-diameter-m")
        self.assert_usage_error(argv[:index] + argv[index + 2:])

    def test_preset_cannot_be_mixed_with_explicit_flags(self) -> None:
        self.assert_usage_error(self.run_args("--preset", "vbt-sg-0.15s-v1"))

    def test_non_finite_numeric_flag_is_rejected(self) -> None:
        self.assert_usage_error(self.run_args(filter_args=[
            "--filter", "raw", "--kinematics-max-gap-s", "nan", "--kinematics-min-confidence", "0"]))

    def test_preset_expands_to_explicit_recorded_flags(self) -> None:
        code, _, stderr, runner = self.main(self.run_args("--preset", "vbt-sg-0.15s-v1", filter_args=[]))
        self.assertEqual(code, 0, stderr)
        analyze = runner.executed[1]
        for flag, value in zip(EXPLICIT_FILTER[::2], EXPLICIT_FILTER[1::2]):
            self.assertEqual(analyze[analyze.index(flag) + 1], value)
        record = json.loads((self.output_dir / f"{FIXTURE_ID}.run-record.json").read_text(encoding="utf-8"))
        self.assertEqual(record["configuration"]["preset"], "vbt-sg-0.15s-v1")
        self.assertEqual(record["configuration"]["analyze_options"], EXPLICIT_FILTER)


class RunTests(WorkflowTestCase):
    def test_run_writes_outputs_side_by_side(self) -> None:
        code, stdout, stderr, runner = self.main(self.run_args())
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outputs(), sorted([
            f"{FIXTURE_ID}.analysis-v1.json",
            f"{FIXTURE_ID}.opencv-csrt.prediction-v1.json",
            f"{FIXTURE_ID}.run-record.json",
        ]))
        track, analyze = runner.executed
        self.assertTrue(track[1].endswith("research/opencv-tracking/track.py"))
        self.assertEqual(track[track.index("--tracker") + 1], "csrt")
        self.assertNotIn("--end-s", track)
        self.assertNotIn("--allow-held-out", track)
        self.assertIn("analyze", analyze)
        self.assertEqual(analyze[analyze.index("--fixture") + 1], FIXTURE_ID)
        self.assertEqual(analyze[analyze.index("--observations") + 1], track[track.index("--output") + 1])
        self.assertEqual(analyze[analyze.index("--plate-diameter-m") + 1], "0.45")
        self.assertNotIn("--tracker", analyze)

    def test_summary_prints_seed_timestamp_prominently(self) -> None:
        _, stdout, _, _ = self.main(self.run_args())
        self.assertIn("SEED", stdout)
        self.assertIn("1.500000 s", stdout)
        self.assertIn("frame 90", stdout)
        self.assertIn("before the first rep", stdout)

    def test_run_record_lists_commands_hashes_and_commit(self) -> None:
        _, _, stderr, runner = self.main(self.run_args())
        record = json.loads((self.output_dir / f"{FIXTURE_ID}.run-record.json").read_text(encoding="utf-8"))
        self.assertEqual(record["format"], "openbar-research-vbt-run-record")
        self.assertEqual(record["fixture_id"], FIXTURE_ID)
        self.assertEqual(record["inputs"]["video"]["sha256"], VIDEO_SHA256)
        self.assertEqual(record["inputs"]["seed"]["sha256"], hashlib.sha256(self.seed.read_bytes()).hexdigest())
        self.assertEqual(record["inputs"]["seed"]["timestamp_s"], 1.5)
        self.assertEqual(record["inputs"]["manifest_entry"]["sha256"],
                         analyze_lift.canonical_sha256(json.loads(self.manifest.read_text())["fixtures"][0]))
        self.assertEqual(record["openbar"]["git_commit"], "0123456789abcdef0123456789abcdef01234567")
        self.assertFalse(record["openbar"]["tracked_changes"])
        self.assertEqual([command["step"] for command in record["commands"]], ["track", "analyze"])
        self.assertEqual(record["commands"][0]["argv"][1:], runner.executed[0][1:])
        self.assertEqual(record["commands"][1]["argv"], runner.executed[1])
        for name in ("prediction", "analysis"):
            path = self.output_dir / record["outputs"][name]["file"]
            self.assertEqual(record["outputs"][name]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(record["environment"]["opencv_version"], "4.12.0")
        self.assertIn("ffmpeg", record["environment"])

    def test_run_record_is_deterministic(self) -> None:
        self.main(self.run_args())
        record_path = self.output_dir / f"{FIXTURE_ID}.run-record.json"
        first = record_path.read_bytes()
        code, _, stderr, _ = self.main(self.run_args("--force"))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(record_path.read_bytes(), first)

    def test_run_reuses_registration_without_rewriting_manifest(self) -> None:
        self.main(["register", "--video", str(self.video), "--plate-diameter-m", "0.45", "--exercise", "clean",
                   "--manifest", str(self.manifest)])
        before = self.manifest.read_bytes()
        code, _, stderr, _ = self.main(self.run_args())
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_openbar_cli_binary_replaces_cargo(self) -> None:
        _, _, _, runner = self.main(self.run_args("--openbar-cli", "target/release/openbar-cli"))
        self.assertEqual(runner.executed[1][:2], ["target/release/openbar-cli", "analyze"])

    def test_default_analyze_uses_locked_release_cargo(self) -> None:
        _, _, _, runner = self.main(self.run_args())
        self.assertEqual(runner.executed[1][:7], ["cargo", "run", "--locked", "--release", "-p", "openbar-cli", "--"])


class SubprocessRunnerTests(unittest.TestCase):
    def test_execute_raises_on_nonzero_exit(self) -> None:
        with self.assertRaises(analyze_lift.WorkflowError):
            analyze_lift.Runner().execute([sys.executable, "-c", "import sys; sys.exit(3)"])

    def test_capture_raises_on_missing_program(self) -> None:
        with self.assertRaises(analyze_lift.WorkflowError):
            analyze_lift.Runner().capture(["definitely-not-an-openbar-program"])

    def test_capture_returns_stdout(self) -> None:
        self.assertEqual(analyze_lift.Runner().capture([sys.executable, "-c", "print('ok')"]).strip(), "ok")

    def test_completed_process_type_is_not_leaked(self) -> None:
        self.assertNotIsInstance(analyze_lift.Runner().capture([sys.executable, "-c", "print(1)"]),
                                 subprocess.CompletedProcess)


if __name__ == "__main__":
    unittest.main()
