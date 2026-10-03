#!/usr/bin/env python3
"""Tests for the personal VBT workflow orchestrator (#86).

The unit tests are stdlib only: FFmpeg, OpenCV, cargo and git are replaced by a fake runner and
the ffprobe-backed probe is stubbed. `EndToEndDeterminismTests` runs the real tools and is opt-in
(OPENBAR_VBT_E2E=1); it skips when cv2, ffmpeg, ffprobe or cargo is unavailable.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
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
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def repo_temp_dir(test: unittest.TestCase, prefix: str) -> Path:
    """Temporary directory under target/, the only repository location tests may write to."""
    (ROOT / "target").mkdir(exist_ok=True)
    temp = tempfile.TemporaryDirectory(prefix=prefix, dir=ROOT / "target")
    test.addCleanup(temp.cleanup)
    return Path(temp.name)


def fake_probe(path: Path) -> dict[str, Any]:
    return {
        "repository_path": analyze_lift.display_path(Path(path)),
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

    def __init__(self, *, missing: tuple[str, ...] = (), fail_step: str | None = None,
                 bad_prediction: bool = False, ignored: bool = False) -> None:
        self.missing = set(missing)
        self.fail_step = fail_step
        self.bad_prediction = bad_prediction
        self.ignored = ignored
        self.executed: list[list[str]] = []
        self.captured: list[list[str]] = []

    def which(self, name: str) -> str | None:
        return None if name in self.missing else f"/usr/bin/{name}"

    def capture_bytes(self, argv: list[str]) -> bytes:
        self.captured.append(list(argv))
        if argv[0] == "git":
            if "rev-parse" in argv:
                return (COMMIT + "\n").encode()
            return b""
        return f"{Path(argv[0]).name} version 1.0-test\nmore detail\n".encode()

    def succeeds(self, argv: list[str]) -> bool:
        return self.ignored

    def execute(self, argv: list[str]) -> None:
        self.executed.append(list(argv))
        output = ROOT / argv[argv.index("--output") + 1]
        step = "track" if any(part.endswith("track.py") for part in argv) else "analyze"
        if output.exists():  # the real analyze opens its output with create_new
            raise analyze_lift.WorkflowError(f"{output} already exists")
        if step == "track":
            text = "{broken" if self.bad_prediction else json.dumps({
                "schema_version": 1,
                "fixture_id": argv[argv.index("--fixture") + 1],
                "implementation": {"name": "opencv-csrt", "version": "spike-1",
                                   "config": {"opencv_version": "4.12.0", "numpy_version": "2.2.6"}},
                "samples": [
                    {"timestamp_s": 1.5, "state": "tracked", "center_px": {"x_px": 1, "y_px": 2}, "confidence": 1.0},
                    {"timestamp_s": 1.6, "state": "lost"},
                ],
            }, indent=2) + "\n"
            output.write_text(text, encoding="utf-8")
        else:
            output.write_text('{"schema_version": 1}\n', encoding="utf-8")
        if step == self.fail_step:
            raise analyze_lift.WorkflowError(f"{step} failed (fake)")


class WorkflowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = repo_temp_dir(self, "vbt-workflow-test-")
        self.video = self.dir / "lift.mp4"
        self.video.write_bytes(VIDEO_BYTES)
        self.manifest = self.dir / "vbt" / "manifest.json"
        self.output_dir = self.dir / "analyses"
        self.seed = self.write_seed(FIXTURE_ID)
        patcher = mock.patch.object(analyze_lift.fixture_probe, "probe_video", side_effect=fake_probe)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_seed(self, fixture_id: str | None, name: str = "seed.json", directory: Path | None = None) -> Path:
        document: dict[str, Any] = {
            "schema_version": 1,
            "seed": {"timestamp_s": 1.5, "frame_index": 90,
                     "target": {"center": {"x_px": 1.0, "y_px": 2.0}, "radius_px": 50.0},
                     "coordinate_space": "display_top_left", "source_rotation_deg": 0},
        }
        if fixture_id is not None:
            document["fixture_id"] = fixture_id
        path = (directory or self.dir) / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def run_args(self, *extra: str, filter_args: list[str] | None = None) -> list[str]:
        return [
            "run", "--video", str(self.video), "--seed", str(self.seed), "--plate-diameter-m", "0.45",
            "--exercise", "clean", "--manifest", str(self.manifest), "--output-dir", str(self.output_dir),
            *(EXPLICIT_FILTER if filter_args is None else filter_args), *extra,
        ]

    def register_args(self, manifest: Path | str | None = None, plate: str = "0.45",
                      exercise: str = "clean") -> list[str]:
        return ["register", "--video", str(self.video), "--plate-diameter-m", plate, "--exercise", exercise,
                "--manifest", str(manifest if manifest is not None else self.manifest)]

    def main(self, argv: list[str], runner: FakeRunner | None = None) -> tuple[int, str, str, FakeRunner]:
        runner = runner or FakeRunner()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = analyze_lift.main(argv, runner=runner)
        return code, stdout.getvalue(), stderr.getvalue(), runner

    def outputs(self) -> list[str]:
        return sorted(path.name for path in self.output_dir.glob("*")) if self.output_dir.exists() else []

    def record(self) -> dict[str, Any]:
        return json.loads((self.output_dir / f"{FIXTURE_ID}.run-record.json").read_text(encoding="utf-8"))


class FixtureIdTests(unittest.TestCase):
    def test_id_is_derived_from_sha256(self) -> None:
        self.assertEqual(analyze_lift.fixture_id_for("AB" * 32), "vbt-abababababababab")

    def test_id_rejects_non_sha256(self) -> None:
        with self.assertRaises(analyze_lift.WorkflowError):
            analyze_lift.fixture_id_for("xyz")


class ManifestGuardTests(WorkflowTestCase):
    def assert_refused(self, manifest: Path | str, needle: str = "refusing") -> str:
        code, _, stderr, runner = self.main(self.register_args(manifest))
        self.assertNotEqual(code, 0, manifest)
        self.assertIn(needle, stderr, manifest)
        self.assertEqual(runner.executed, [])
        return stderr

    def test_protected_manifest_spellings_are_refused_and_untouched(self) -> None:
        protected = ROOT / "validation" / "private" / "manifest.json"
        before = protected.read_bytes() if protected.exists() else None
        spellings: list[Path | str] = [
            protected,
            ROOT / "VALIDATION" / "Private" / "MANIFEST.JSON",
            str(ROOT) + "\\validation\\private\\manifest.json",
            str(protected) + ".",
            str(protected) + " ",
            str(protected) + "::$DATA",
            ROOT / "validation" / "private" / "vbt" / ".." / "manifest.json",
            # The main checkout's #57 manifest, seen from a worktree.
            self.dir / "main-checkout" / "validation" / "private" / "manifest.json",
        ]
        for spelling in spellings:
            self.assert_refused(spelling, "#57")
        self.assertEqual(protected.read_bytes() if protected.exists() else None, before)

    def test_symlink_to_protected_manifest_is_refused(self) -> None:
        protected = self.dir / "elsewhere" / "validation" / "private" / "manifest.json"
        protected.parent.mkdir(parents=True)
        protected.write_text('{"schema_version": 1, "fixtures": []}\n', encoding="utf-8")
        before = protected.read_bytes()
        link = self.dir / "innocent.json"
        try:
            os.symlink(protected, link)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks not permitted here: {error}")
        self.assert_refused(link, "#57")
        self.assertEqual(protected.read_bytes(), before)

    def test_manifest_outside_allowed_roots_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="openbar-outside-") as outside:
            self.assert_refused(Path(outside) / "manifest.json")
        self.assert_refused(ROOT / "validation" / "private" / "other-manifest.json")
        public = ROOT / "validation" / "fixtures" / "public" / "manifest.json"
        before = public.read_bytes()
        self.assert_refused(public)
        self.assertEqual(public.read_bytes(), before)

    def test_default_manifest_is_the_separate_personal_manifest(self) -> None:
        self.assertEqual(analyze_lift.DEFAULT_MANIFEST, ROOT / "validation" / "private" / "vbt" / "manifest.json")
        analyze_lift.require_personal_manifest(analyze_lift.DEFAULT_MANIFEST)

    def test_full_run_cannot_write_a_protected_manifest(self) -> None:
        # Inside the allowed target/ tree, so only the protected-name guard stops this run.
        protected = self.dir / "checkout" / "validation" / "private" / "manifest.json"
        protected.parent.mkdir(parents=True)
        protected.write_text('{"schema_version": 1, "fixtures": []}\n', encoding="utf-8")
        before = protected.read_bytes()
        self.manifest = protected
        code, _, stderr, runner = self.main(self.run_args())
        self.assertNotEqual(code, 0)
        self.assertIn("#57", stderr)
        self.assertEqual(runner.executed, [])
        self.assertEqual(protected.read_bytes(), before)


class RegistrationTests(WorkflowTestCase):
    def register(self, plate: str = "0.45", exercise: str = "clean") -> tuple[int, str, str]:
        code, stdout, stderr, _ = self.main(self.register_args(plate=plate, exercise=exercise))
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

    def test_conflicting_plate_fails_closed_and_shows_both_values(self) -> None:
        self.register()
        before = self.manifest.read_bytes()
        code, _, stderr = self.register(plate="0.40")
        self.assertNotEqual(code, 0)
        self.assertIn('load: registered {"plate_diameter_m": 0.45}, this run {"plate_diameter_m": 0.4}', stderr)
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_conflicting_exercise_fails_closed_and_shows_both_values(self) -> None:
        self.register()
        code, _, stderr = self.register(exercise="snatch")
        self.assertNotEqual(code, 0)
        self.assertIn('exercise: registered "clean", this run "snatch"', stderr)

    def test_same_media_under_another_id_fails_closed(self) -> None:
        self.register()
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["fixtures"][0]["id"] = "hand-made-id"
        self.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        code, _, stderr = self.register()
        self.assertNotEqual(code, 0)
        self.assertIn("hand-made-id", stderr)

    def test_malformed_manifest_fails_cleanly(self) -> None:
        self.manifest.parent.mkdir(parents=True)
        for text in ('{"schema_version": 1, "fixtures": [{"id": 3}]}\n', '{"fixtures": "nope"}\n', "{broken"):
            self.manifest.write_text(text, encoding="utf-8")
            code, _, stderr = self.register()
            self.assertNotEqual(code, 0, text)
            self.assertIn("personal manifest", stderr)
            self.assertNotIn("Traceback", stderr)
            self.assertEqual(self.manifest.read_text(encoding="utf-8"), text)

    def test_video_outside_repository_points_to_personal_media(self) -> None:
        with tempfile.TemporaryDirectory(prefix="openbar-outside-") as outside:
            self.video = Path(outside) / "lift.mp4"
            self.video.write_bytes(VIDEO_BYTES)
            code, _, stderr = self.register()
        self.assertNotEqual(code, 0)
        self.assertIn("validation/private/vbt/media/", stderr)
        self.assertFalse(self.manifest.exists())

    def test_probe_outside_repository_error_points_to_personal_media(self) -> None:
        def outside_probe(path: Path) -> dict[str, Any]:
            return {**fake_probe(path), "repository_path": None}

        with mock.patch.object(analyze_lift.fixture_probe, "probe_video", side_effect=outside_probe), \
                self.assertRaisesRegex(analyze_lift.WorkflowError, "validation/private/vbt/media/"):
            analyze_lift.register_video(self.video, self.manifest, VIDEO_SHA256, 0.45, "clean")


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
        code, _, _, runner = self.main(self.run_args())
        self.assertNotEqual(code, 0)
        self.assert_nothing_happened(runner)

    def test_seed_outside_repository_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="openbar-outside-") as outside:
            self.seed = self.write_seed(FIXTURE_ID, directory=Path(outside))
            code, _, stderr, runner = self.main(self.run_args())
        self.assertNotEqual(code, 0)
        self.assertIn("--seed", stderr)
        self.assert_nothing_happened(runner)

    def test_output_dir_locations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="openbar-outside-") as outside:
            refused = [
                Path(outside) / "analyses",
                ROOT / "validation" / "fixtures" / "public" / "vbt-analyses",
                ROOT / "docs" / "vbt-analyses",
            ]
            for output_dir in refused:
                self.output_dir = output_dir
                code, _, stderr, runner = self.main(self.run_args(), FakeRunner(ignored=output_dir.name != "vbt-analyses"))
                self.assertNotEqual(code, 0, output_dir)
                self.assertIn("--output-dir", stderr)
                self.assertFalse(output_dir.exists())
                self.assertEqual(runner.executed, [])
        paths = analyze_lift.output_paths(ROOT / "docs" / "x", FIXTURE_ID)
        analyze_lift.require_output_dir(ROOT / "docs" / "x", FakeRunner(ignored=True), paths)
        with self.assertRaises(analyze_lift.WorkflowError):
            analyze_lift.require_output_dir(ROOT / "docs" / "x", FakeRunner(ignored=False), paths)
        public = ROOT / "validation" / "fixtures" / "public" / "x"
        with self.assertRaises(analyze_lift.WorkflowError):
            analyze_lift.require_output_dir(public, FakeRunner(ignored=True),
                                            analyze_lift.output_paths(public, FIXTURE_ID))

    def test_missing_ffmpeg_fails_closed(self) -> None:
        for tool in ("ffmpeg", "ffprobe"):
            code, _, stderr, runner = self.main(self.run_args(), FakeRunner(missing=(tool,)))
            self.assertNotEqual(code, 0)
            self.assertIn(tool, stderr)
            self.assert_nothing_happened(runner)

    def test_register_without_ffprobe_fails_closed(self) -> None:
        code, _, _, _ = self.main(self.register_args(), FakeRunner(missing=("ffprobe",)))
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

    def test_force_replaces_existing_outputs_on_success(self) -> None:
        self.output_dir.mkdir()
        existing = self.output_dir / f"{FIXTURE_ID}.analysis-v1.json"
        existing.write_text("previous\n", encoding="utf-8")
        code, _, stderr, _ = self.main(self.run_args("--force"))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(existing.read_text(encoding="utf-8"), '{"schema_version": 1}\n')
        self.assertFalse([name for name in self.outputs() if name.endswith(".tmp")])

    def test_failed_forced_run_keeps_previous_outputs_and_unrelated_files(self) -> None:
        code, _, stderr, _ = self.main(self.run_args())
        self.assertEqual(code, 0, stderr)
        unrelated = self.output_dir / "notes.txt"
        unrelated.write_text("keep me\n", encoding="utf-8")
        previous = {name: (self.output_dir / name).read_bytes() for name in self.outputs()}
        for runner in (FakeRunner(fail_step="track"), FakeRunner(fail_step="analyze"), FakeRunner(bad_prediction=True)):
            code, _, _, _ = self.main(self.run_args("--force"), runner)
            self.assertNotEqual(code, 0)
            self.assertEqual({name: (self.output_dir / name).read_bytes() for name in self.outputs()}, previous)

    def test_leftover_staging_files_are_removed_before_the_run(self) -> None:
        self.output_dir.mkdir()
        staged = analyze_lift.staged_paths(analyze_lift.output_paths(self.output_dir, FIXTURE_ID))
        for path in staged.values():
            path.write_text("crashed run\n", encoding="utf-8")
        code, _, stderr, _ = self.main(self.run_args())
        self.assertEqual(code, 0, stderr)
        self.assertFalse(any(path.exists() for path in staged.values()))

    def test_failing_second_rename_leaves_no_run_record(self) -> None:
        self.main(self.run_args())
        paths = analyze_lift.output_paths(self.output_dir, FIXTURE_ID)
        paths["analysis"].write_bytes(b"old analysis\n")
        old_analysis = paths["analysis"].read_bytes()
        real_replace = os.replace

        def locked_analysis(source: Any, target: Any) -> None:
            if Path(target) == paths["analysis"]:
                raise PermissionError("held open by a reader")
            real_replace(source, target)

        with mock.patch.object(analyze_lift.os, "replace", side_effect=locked_analysis),                 mock.patch.object(analyze_lift.time, "sleep") as sleep:
            code, _, stderr, _ = self.main(self.run_args("--force"))
        self.assertNotEqual(code, 0)
        self.assertIn("incomplete", stderr)
        self.assertEqual(sleep.call_count, analyze_lift.RENAME_ATTEMPTS - 1)
        self.assertFalse(paths["run_record"].exists())
        self.assertEqual(paths["analysis"].read_bytes(), old_analysis)
        self.assertFalse([name for name in self.outputs() if name.endswith(".tmp")])

    def test_rename_retries_transient_permission_errors(self) -> None:
        real_replace = os.replace
        failures = {"left": 2}

        def flaky(source: Any, target: Any) -> None:
            if failures["left"] and Path(target).parent == self.output_dir:
                failures["left"] -= 1
                raise PermissionError("antivirus scan")
            real_replace(source, target)

        with mock.patch.object(analyze_lift.os, "replace", side_effect=flaky),                 mock.patch.object(analyze_lift.time, "sleep") as sleep:
            code, _, stderr, _ = self.main(self.run_args())
        self.assertEqual(code, 0, stderr)
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(analyze_lift.output_paths(self.output_dir, FIXTURE_ID)["run_record"].exists())

    def test_cleanup_failure_does_not_hide_the_step_error(self) -> None:
        runner = FakeRunner(fail_step="analyze")
        real_unlink = Path.unlink

        def locked_unlink(path: Path, missing_ok: bool = False) -> None:
            if runner.executed and path.name.endswith(".tmp"):
                raise OSError("locked")
            real_unlink(path, missing_ok=missing_ok)

        with mock.patch.object(Path, "unlink", locked_unlink):
            code, _, stderr, _ = self.main(self.run_args(), runner)
        self.assertNotEqual(code, 0)
        self.assertIn("error: analyze step failed", stderr)
        self.assertIn("warning: could not remove staging file", stderr)

    def test_failed_step_leaves_no_outputs(self) -> None:
        for runner in (FakeRunner(fail_step="track"), FakeRunner(fail_step="analyze")):
            code, _, stderr, _ = self.main(self.run_args(), runner)
            self.assertNotEqual(code, 0)
            self.assertIn(runner.fail_step or "", stderr)
            self.assertEqual(self.outputs(), [])

    def test_unreadable_prediction_after_tool_steps_leaves_no_outputs(self) -> None:
        code, _, stderr, runner = self.main(self.run_args(), FakeRunner(bad_prediction=True))
        self.assertNotEqual(code, 0)
        self.assertIn("tracker prediction", stderr)
        self.assertEqual(len(runner.executed), 2)
        self.assertEqual(self.outputs(), [])

    def test_missing_git_state_fails_closed(self) -> None:
        class NoGit(FakeRunner):
            def capture_bytes(self, argv: list[str]) -> bytes:
                if argv[0] == "git":
                    raise analyze_lift.WorkflowError("git is not on PATH")
                return super().capture_bytes(argv)

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


class OpenbarCliTests(WorkflowTestCase):
    def make_binary(self) -> Path:
        name = "openbar-cli.exe" if sys.platform == "win32" else "openbar-cli"
        binary = self.dir / "bin" / name
        binary.parent.mkdir()
        binary.write_bytes(b"fake binary")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        return binary

    def test_relative_binary_without_exe_suffix_resolves_from_cwd(self) -> None:
        binary = self.make_binary()
        cwd = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, cwd)
        code, _, stderr, runner = self.main(self.run_args("--openbar-cli", "bin/openbar-cli"))
        self.assertEqual(code, 0, stderr)
        expected = analyze_lift.display_path(binary)
        self.assertEqual(runner.executed[1][:2], [expected, "analyze"])
        self.assertEqual(self.record()["environment"]["openbar_cli"],
                         {"path": expected, "sha256": hashlib.sha256(b"fake binary").hexdigest()})

    def test_match_in_cwd_instead_of_given_folder_is_rejected(self) -> None:
        binary = self.make_binary()
        (self.dir / "empty").mkdir()
        cwd = os.getcwd()
        os.chdir(binary.parent)
        self.addCleanup(os.chdir, cwd)
        with self.assertRaisesRegex(analyze_lift.WorkflowError, "--openbar-cli"):
            analyze_lift.resolve_openbar_cli(str(self.dir / "empty" / "openbar-cli"))

    def test_missing_binary_fails_closed(self) -> None:
        code, _, stderr, runner = self.main(self.run_args("--openbar-cli", str(self.dir / "bin" / "openbar-cli")))
        self.assertNotEqual(code, 0)
        self.assertIn("--openbar-cli", stderr)
        self.assertEqual(runner.executed, [])

    def test_default_analyze_uses_locked_release_cargo(self) -> None:
        _, _, _, runner = self.main(self.run_args())
        self.assertEqual(runner.executed[1][:7], ["cargo", "run", "--locked", "--release", "-p", "openbar-cli", "--"])
        self.assertNotIn("openbar_cli", self.record()["environment"])


class ExplicitConfigurationTests(WorkflowTestCase):
    def assert_usage_error(self, argv: list[str]) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            analyze_lift.main(argv, runner=FakeRunner())
        self.assertEqual(raised.exception.code, 2)

    def test_filter_is_required(self) -> None:
        self.assert_usage_error(self.run_args(filter_args=[
            "--kinematics-max-gap-s", "0.2", "--kinematics-min-confidence", "0"]))

    def test_kinematics_thresholds_are_required(self) -> None:
        self.assert_usage_error(self.run_args(filter_args=["--filter", "raw", "--kinematics-max-gap-s", "0.2"]))
        self.assert_usage_error(self.run_args(filter_args=["--filter", "raw", "--kinematics-min-confidence", "0"]))

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
        record = self.record()
        self.assertEqual(record["configuration"]["preset"], "vbt-sg-0.15s-v1")
        self.assertEqual(record["configuration"]["analyze_options"], EXPLICIT_FILTER)


class RunTests(WorkflowTestCase):
    def test_run_writes_outputs_side_by_side(self) -> None:
        code, _, stderr, runner = self.main(self.run_args())
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outputs(), sorted([
            f"{FIXTURE_ID}.analysis-v1.json",
            f"{FIXTURE_ID}.opencv-csrt.prediction-v1.json",
            f"{FIXTURE_ID}.run-record.json",
        ]))
        track, analyze = runner.executed
        self.assertEqual(track[1], "research/opencv-tracking/track.py")
        self.assertEqual(track[track.index("--tracker") + 1], "csrt")
        self.assertIn("--omit-runtime", track)
        self.assertNotIn("--end-s", track)
        self.assertNotIn("--allow-held-out", track)
        self.assertIn("analyze", analyze)
        self.assertEqual(analyze[analyze.index("--fixture") + 1], FIXTURE_ID)
        self.assertEqual(analyze[analyze.index("--observations") + 1], track[track.index("--output") + 1])
        self.assertTrue(analyze[analyze.index("--output") + 1].endswith(f"/.{FIXTURE_ID}.analysis-v1.json.tmp"))
        self.assertEqual(analyze[analyze.index("--plate-diameter-m") + 1], "0.45")
        self.assertNotIn("--tracker", analyze)

    def test_summary_prints_seed_timestamp_prominently(self) -> None:
        _, stdout, _, _ = self.main(self.run_args())
        self.assertIn("SEED", stdout)
        self.assertIn("1.500000 s", stdout)
        self.assertIn("frame 90", stdout)
        self.assertIn("before the first rep", stdout)

    def test_run_record_lists_commands_hashes_and_git_state(self) -> None:
        _, _, _, runner = self.main(self.run_args())
        record = self.record()
        self.assertEqual(record["format"], "openbar-research-vbt-run-record")
        self.assertEqual(record["fixture_id"], FIXTURE_ID)
        self.assertEqual(record["inputs"]["video"]["sha256"], VIDEO_SHA256)
        self.assertEqual(record["inputs"]["seed"]["sha256"], hashlib.sha256(self.seed.read_bytes()).hexdigest())
        self.assertEqual(record["inputs"]["seed"]["timestamp_s"], 1.5)
        self.assertEqual(record["inputs"]["manifest_entry"]["sha256"],
                         analyze_lift.canonical_sha256(json.loads(self.manifest.read_text())["fixtures"][0]))
        self.assertEqual(record["openbar"], {
            "git_commit": COMMIT,
            "tracked_changes": False,
            "tracked_diff_sha256": hashlib.sha256(b"").hexdigest(),
            "untracked_source_files": 0,
        })
        self.assertEqual([command["step"] for command in record["commands"]], ["track", "analyze"])
        def final(argv: list[str]) -> list[str]:
            return [arg.replace(f"/.{FIXTURE_ID}", f"/{FIXTURE_ID}").removesuffix(".tmp") for arg in argv]

        self.assertEqual(record["commands"][0]["argv"], ["python", *final(runner.executed[0][1:])])
        self.assertEqual(record["commands"][1]["argv"], final(runner.executed[1]))
        self.assertNotIn(".tmp", json.dumps(record["commands"]))
        diff = next(argv for argv in runner.captured if "diff" in argv)
        self.assertEqual(diff[-6:], ["diff", "HEAD", "--binary", "--no-ext-diff", "--no-textconv", "--no-color"])
        for name in ("prediction", "analysis"):
            path = self.output_dir / record["outputs"][name]["file"]
            self.assertEqual(record["outputs"][name]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(record["environment"]["opencv_version"], "4.12.0")
        text = json.dumps(record)
        self.assertNotIn("processing_wall_s", text)
        for path in (record["inputs"]["video"]["path"], record["inputs"]["seed"]["path"],
                     record["inputs"]["manifest"]["path"]):
            self.assertFalse(Path(path).is_absolute(), path)
            self.assertTrue(path.startswith("target/"), path)

    def test_run_record_counts_dirty_state(self) -> None:
        class Dirty(FakeRunner):
            def capture_bytes(self, argv: list[str]) -> bytes:
                if "diff" in argv:
                    return b"diff --git a/x b/x\n"
                if "ls-files" in argv:
                    return b"research/new.py\ncrates/new.rs\n"
                return super().capture_bytes(argv)

        _, stdout, _, _ = self.main(self.run_args(), Dirty())
        git = self.record()["openbar"]
        self.assertTrue(git["tracked_changes"])
        self.assertEqual(git["tracked_diff_sha256"], hashlib.sha256(b"diff --git a/x b/x\n").hexdigest())
        self.assertEqual(git["untracked_source_files"], 2)
        self.assertIn("WARNING", stdout)

    def test_run_record_is_deterministic(self) -> None:
        self.main(self.run_args())
        record_path = self.output_dir / f"{FIXTURE_ID}.run-record.json"
        first = record_path.read_bytes()
        code, _, stderr, _ = self.main(self.run_args("--force"))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(record_path.read_bytes(), first)

    def test_run_reuses_registration_without_rewriting_manifest(self) -> None:
        self.main(self.register_args())
        before = self.manifest.read_bytes()
        code, _, stderr, _ = self.main(self.run_args())
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.manifest.read_bytes(), before)


class SubprocessRunnerTests(unittest.TestCase):
    def test_execute_raises_on_nonzero_exit(self) -> None:
        with self.assertRaises(analyze_lift.WorkflowError):
            analyze_lift.Runner().execute([sys.executable, "-c", "import sys; sys.exit(3)"])

    def test_capture_raises_on_missing_program(self) -> None:
        with self.assertRaises(analyze_lift.WorkflowError):
            analyze_lift.Runner().capture(["definitely-not-an-openbar-program"])

    def test_capture_returns_stdout(self) -> None:
        self.assertEqual(analyze_lift.Runner().capture([sys.executable, "-c", "print('ok')"]).strip(), "ok")

    def test_succeeds_reports_exit_status(self) -> None:
        runner = analyze_lift.Runner()
        self.assertTrue(runner.succeeds([sys.executable, "-c", "pass"]))
        self.assertFalse(runner.succeeds([sys.executable, "-c", "import sys; sys.exit(1)"]))
        self.assertFalse(runner.succeeds(["definitely-not-an-openbar-program"]))


@unittest.skipUnless(os.environ.get("OPENBAR_VBT_E2E") == "1", "set OPENBAR_VBT_E2E=1 to run the real workflow")
class EndToEndDeterminismTests(unittest.TestCase):
    """Runs track.py and analyze for real, twice, on the public synthetic fixture."""

    FIXTURE = "synthetic-clean-side-12"
    FILTER = ["--filter", "savitzky-golay", "--filter-window", "5", "--filter-polynomial-order", "2",
              "--filter-max-gap-s", "0.2", "--kinematics-max-gap-s", "0.2", "--kinematics-min-confidence", "0"]

    def setUp(self) -> None:
        if importlib.util.find_spec("cv2") is None:
            self.skipTest("cv2 is not importable; use the research venv")
        missing = [tool for tool in ("ffmpeg", "ffprobe", "cargo") if shutil.which(tool) is None]
        if missing:
            self.skipTest(f"SKIPPED: {', '.join(missing)} not on PATH")
        self.dir = repo_temp_dir(self, "vbt-e2e-")

    def main(self, argv: list[str]) -> None:
        completed = subprocess.run([sys.executable, str(WORKFLOW_DIR / "analyze_lift.py"), *argv], cwd=ROOT,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_two_runs_give_byte_identical_outputs(self) -> None:
        video = ROOT / "validation" / "fixtures" / "public" / f"{self.FIXTURE}.mp4"
        manifest = self.dir / "manifest.json"
        fixture_id = analyze_lift.fixture_id_for(analyze_lift.file_sha256(video))
        self.main(["register", "--video", str(video), "--plate-diameter-m", "0.45", "--exercise", "clean",
                   "--manifest", str(manifest)])
        public_seed = ROOT / "validation" / "fixtures" / "public" / "seeds" / f"{self.FIXTURE}.manual-target-seed-v1.json"
        seed = self.dir / "seed.json"
        seed.write_text(json.dumps({**json.loads(public_seed.read_text(encoding="utf-8")),
                                    "fixture_id": fixture_id}, indent=2) + "\n", encoding="utf-8")
        common = ["run", "--video", str(video), "--seed", str(seed), "--plate-diameter-m", "0.45",
                  "--exercise", "clean", "--manifest", str(manifest), *self.FILTER]
        outputs = {}
        for run in ("run1", "run2"):
            self.main([*common, "--output-dir", str(self.dir / run)])
            outputs[run] = analyze_lift.output_paths(self.dir / run, fixture_id)
        for name in ("prediction", "analysis"):
            self.assertEqual(outputs["run1"][name].read_bytes(), outputs["run2"][name].read_bytes(), name)
        prediction = json.loads(outputs["run1"]["prediction"].read_text(encoding="utf-8"))
        self.assertNotIn("runtime", prediction)
        first_record = outputs["run1"]["run_record"].read_bytes()
        self.main([*common, "--output-dir", str(self.dir / "run1"), "--force"])
        self.assertEqual(outputs["run1"]["run_record"].read_bytes(), first_record)


if __name__ == "__main__":
    unittest.main()
