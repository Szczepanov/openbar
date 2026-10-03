#!/usr/bin/env python3
"""Review fixes for #95 (PR #96): pre-write registration checks, --openbar-cli, complete-set guarantees,
suggester failures, page escaping, --at-s conflicts, reserved ids and page-id binding. Stdlib only."""
from __future__ import annotations

import json
import shutil
import stat
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_fakes as fakes  # noqa: E402
import session_harness  # noqa: E402
from session_harness import ROOT, SessionRunner, SessionTestCase  # noqa: E402

import analyze_lift  # noqa: E402
import session_contract  # noqa: E402
import session_ingest  # noqa: E402


class TwoClipCase(SessionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.add_video("VID_1.mp4", b"video one")
        self.add_video("VID_2.mp4", b"video two")
        self.assertEqual(self.ingest()[0], 0)
        self.clips = self.state()["clips"]

    def rows(self, *exercises: str | None) -> list[dict[str, str]]:
        state = self.state()
        return [fakes.skipped_row(clip, state) if exercise is None else fakes.accepted_row(clip, state, exercise)
                for clip, exercise in zip(self.clips, exercises)]

    def untouched(self, code: int, err: str, needle: str, manifest_before: bytes | None) -> None:
        self.assertEqual(code, 1, err)
        self.assertIn(needle, err)
        after = self.manifest.read_bytes() if self.manifest.exists() else None
        self.assertEqual(after, manifest_before)
        for name in ("seeds", "analyses", "scale", "scale-report", "session-input.csv", "report.html",
                     "session-record.json"):
            self.assertFalse((self.session_dir / name).exists(), name)

    def register_first(self, video: Path | None = None, plate: float = 0.45, exercise: str = "snatch") -> bytes:
        clip = self.clips[0]
        analyze_lift.register_video(video or ROOT / clip["media_path"], self.manifest, clip["sha256"], plate, exercise)
        return self.manifest.read_bytes()


class RegistrationConflictTests(TwoClipCase):
    """M1: every binding field (exercise, media, video, load) is compared before anything is written."""

    def test_plate_conflict(self) -> None:
        before = self.register_first(plate=0.40)
        code, _, err = self.run_session(self.rows("snatch", "clean"))
        self.untouched(code, err, "load", before)

    def test_media_path_conflict(self) -> None:
        elsewhere = self.dir / "elsewhere" / "same.mp4"
        elsewhere.parent.mkdir()
        shutil.copyfile(ROOT / self.clips[0]["media_path"], elsewhere)
        before = self.register_first(video=elsewhere)
        code, _, err = self.run_session(self.rows("snatch", "clean"))
        self.untouched(code, err, "media", before)

    def test_video_metadata_conflict(self) -> None:
        def other_fps(path: Path) -> dict[str, Any]:
            return {**session_harness.fake_probe(path), "nominal_fps": 30, "measured_fps": 29.97}
        with mock.patch.object(analyze_lift.fixture_probe, "probe_video", side_effect=other_fps):
            before = self.register_first()
        code, _, err = self.run_session(self.rows("snatch", "clean"))
        self.untouched(code, err, "video", before)

    def test_conflict_on_the_second_clip_writes_nothing_for_the_first(self) -> None:
        clip = self.clips[1]
        analyze_lift.register_video(ROOT / clip["media_path"], self.manifest, clip["sha256"], 0.45, "back_squat")
        before = self.manifest.read_bytes()
        code, _, err = self.run_session(self.rows("snatch", "clean"))
        self.untouched(code, err, "registered as 'back_squat'", before)


class OpenbarCliTests(TwoClipCase):
    """M2: --openbar-cli is resolved before any write and recorded repository-relative."""

    def fake_binary(self) -> Path:
        binary = self.dir / "bin" / ("openbar-cli.exe" if sys.platform == "win32" else "openbar-cli")
        binary.parent.mkdir()
        binary.write_bytes(b"fake binary")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        return binary

    def test_missing_binary_fails_before_any_write(self) -> None:
        code, _, err = self.run_session(self.rows("snatch", None), "--openbar-cli", str(self.dir / "nope"))
        self.untouched(code, err, "--openbar-cli", None)

    def test_binary_is_recorded_repository_relative(self) -> None:
        binary = self.fake_binary()
        runner = SessionRunner()
        code, _, err = self.run_session(self.rows("snatch", None), "--openbar-cli", str(binary.with_suffix("")),
                                        runner=runner)
        self.assertEqual(code, 0, err)
        record = json.loads((self.session_dir / "session-record.json").read_text(encoding="utf-8"))
        expected = analyze_lift.display_path(binary)
        self.assertEqual(record["configuration"]["openbar_cli"], expected)
        argv = record["clips"][0]["analyze_lift"]["argv"]
        self.assertEqual(argv[argv.index("--openbar-cli") + 1], expected)
        self.assertNotIn(str(Path.home()), json.dumps(record))
        analyze = next(a for a in runner.executed if "analyze" in a and "--observations" in a)
        self.assertEqual(Path(ROOT / analyze[0]).resolve(), binary.resolve())


class CompleteSetTests(TwoClipCase):
    """M3: a session record always describes exactly the current files."""

    def outputs_of(self, clip: dict[str, Any]) -> list[Path]:
        fixture_id = clip["fixture_id"]
        return [self.session_dir / "seeds" / f"{fixture_id}.manual-target-seed-v1.json",
                self.session_dir / "seeds" / f"{fixture_id}.session-label.csv",
                self.session_dir / "scale" / f"{fixture_id}.scale-reference.csv",
                *analyze_lift.output_paths(self.session_dir / "analyses", fixture_id, "csrt").values()]

    def test_reingest_after_a_run_is_refused_without_force(self) -> None:
        self.assertEqual(self.run_session(self.rows("snatch", "clean"))[0], 0)
        before = self.snapshot()
        code, _, err = self.ingest()
        self.assertEqual(code, 1)
        self.assertIn("session-record.json", err)
        self.assertEqual(self.snapshot(), before)
        code, _, err = self.ingest("--force")
        self.assertEqual(code, 0, err)
        self.assertFalse((self.session_dir / "session-record.json").exists(), "the old run is marked incomplete")

    def test_force_rerun_with_fewer_clips_removes_the_dropped_clips_outputs(self) -> None:
        self.assertEqual(self.run_session(self.rows("snatch", "clean"))[0], 0)
        dropped = self.outputs_of(self.clips[1])
        self.assertTrue(all(path.exists() for path in dropped))
        (self.session_dir / "analyses" / "notes.txt").write_text("mine", encoding="utf-8")
        frame = self.session_dir / "report-frames" / self.clips[1]["fixture_id"] / "frame_000003.png"
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"old crop frame")  # the fake analyses have no velocity peak, so make one by hand
        dropped.append(frame)
        code, out, err = self.run_session(self.rows("snatch", None), "--force")
        self.assertEqual(code, 0, err)
        self.assertEqual([path.name for path in dropped if path.exists()], [])
        self.assertTrue((self.session_dir / "analyses" / "notes.txt").exists(), "unknown files are left alone")
        self.assertIn("removed", out)
        record = json.loads((self.session_dir / "session-record.json").read_text(encoding="utf-8"))
        self.assertEqual([c["fixture_id"] for c in record["clips"]], [self.clips[0]["fixture_id"]])
        removed = record["removed_stale_outputs"]
        self.assertLessEqual({analyze_lift.display_path(path) for path in dropped}, set(removed))
        self.assertTrue(all(self.clips[1]["fixture_id"] in path for path in removed), removed)
        self.assertTrue(any("report-frames" in path for path in removed), "its tracking-check frames too")

    def test_stale_outputs_require_force(self) -> None:
        self.assertEqual(self.run_session(self.rows("snatch", "clean"))[0], 0)
        (self.session_dir / "session-record.json").unlink()
        for name in ("session-input.csv", "report.html"):
            (self.session_dir / name).unlink()
        shutil.rmtree(self.session_dir / "scale-report")
        for path in self.outputs_of(self.clips[0]):
            path.unlink()
        code, _, err = self.run_session(self.rows("snatch", None))
        self.assertEqual(code, 1)
        self.assertIn("already exist", err)

    def test_record_is_removed_before_anything_else_is_written(self) -> None:
        self.assertEqual(self.run_session(self.rows("snatch", "clean"))[0], 0)
        code, _, _ = self.run_session(self.rows("snatch", "clean"), "--force",
                                      runner=SessionRunner(fail_step="analyze"))
        self.assertEqual(code, 1)
        self.assertFalse((self.session_dir / "session-record.json").exists())


class SuggesterTests(SessionTestCase):
    """L4: a raising suggester falls back to manual; a missing OpenCV is a clear error."""

    def test_raising_suggester_falls_back_to_manual(self) -> None:
        self.add_video("VID_1.mp4", b"video one")

        def broken(frame: Path) -> dict[str, Any]:
            raise RuntimeError("bad frame")
        argv = ["ingest", *self.common(), "--inbox", str(self.inbox), "--media-dir", str(self.media)]
        code, _, err = self.main(argv, suggester=broken)
        self.assertEqual(code, 0, err)
        suggestions = self.state()["clips"][0]["suggestions"]
        for kind in ("plate", "stick"):
            self.assertEqual(suggestions[kind]["status"], "failed")
            self.assertIn("bad frame", suggestions[kind]["reason"])

    def test_missing_opencv_is_a_clear_error_before_copying(self) -> None:
        self.add_video("VID_1.mp4", b"video one")
        argv = ["ingest", *self.common(), "--inbox", str(self.inbox), "--media-dir", str(self.media)]
        with mock.patch.dict(sys.modules, {"vbt_suggest": None}):
            code, _, err = self.main(argv, suggester=None)
        self.assertEqual(code, 1)
        self.assertIn("OpenCV", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse((self.media / "VID_1.mp4").exists())


class IngestInputTests(SessionTestCase):
    def test_name_and_id_overrides_for_one_clip_conflict(self) -> None:
        sha = self.add_video("VID_1.mp4", b"video one")
        code, _, err = self.ingest("--at-s", "VID_1.mp4=1", "--at-s", f"vbt-{sha[:16]}=2")
        self.assertEqual(code, 1)
        self.assertIn("both", err)

    def test_reserved_session_ids(self) -> None:
        for reserved in ("media", "seeds", "analyses", "sam2", "sessions", "scale-report", "Media"):
            with self.subTest(reserved=reserved), self.assertRaises(session_contract.SessionCsvError):
                session_contract.require_session_id(reserved)

    def test_page_config_escapes_every_less_than(self) -> None:
        clip = {**fakes.clip(), "original_name": "<!--<script>alert(1)</script>.mp4"}
        state = {"session_id": "s", "page_id": "0" * 16, "clips": [clip]}
        page = session_ingest.render_page(state, ["data:image/png;base64,"],
                                          session_ingest.PAGE_TEMPLATE.read_text(encoding="utf-8"))
        script = page[page.index("const CONFIG ="):page.index("const IMAGES")]
        self.assertNotIn("<", script)
        self.assertIn("\\u003c!--\\u003cscript", script)


class PageBindingTests(TwoClipCase):
    """L12: run recomputes the page id from session.json and the page template."""

    def test_edited_session_state_is_refused(self) -> None:
        path = self.session_dir / "session.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        rows = self.rows("snatch", None)
        state["clips"][0]["suggestions"]["plate"]["radius_px"] = 1.0
        path.write_text(json.dumps(state), encoding="utf-8")
        code, _, err = self.run_session(rows)
        self.assertEqual(code, 1)
        self.assertIn("page id", err)

    def test_state_records_the_template_hash(self) -> None:
        self.assertEqual(len(self.state()["template_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
