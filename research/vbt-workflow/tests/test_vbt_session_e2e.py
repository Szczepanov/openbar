#!/usr/bin/env python3
"""End to end on the public synthetic fixture (#95), with real FFmpeg, OpenCV and cargo.

Opt-in (OPENBAR_VBT_E2E=1); skips when cv2, ffmpeg, ffprobe or cargo is unavailable. The session CSV
is written by the test, as the owner's browser would: plate suggestion accepted, stick clicked by hand
(the synthetic frame has no stick, so the stick suggestion fails and the scale ratio is meaningless).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_fakes as fakes  # noqa: E402
import test_analyze_lift  # noqa: E402
from session_fakes import WORKFLOW_DIR, session_contract  # noqa: E402

import analyze_lift  # noqa: E402
import schema_check  # noqa: E402

ROOT = analyze_lift.ROOT
FIXTURE = "synthetic-clean-side-12"
FILTER = ["--filter", "savitzky-golay", "--filter-window", "5", "--filter-polynomial-order", "2",
          "--filter-max-gap-s", "0.2", "--kinematics-max-gap-s", "0.2", "--kinematics-min-confidence", "0"]
SCHEMAS = ROOT / "validation" / "schema"


@unittest.skipUnless(os.environ.get("OPENBAR_VBT_E2E") == "1", "set OPENBAR_VBT_E2E=1 to run the real workflow")
class SessionEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        if importlib.util.find_spec("cv2") is None:
            self.skipTest("cv2 is not importable; use the research venv")
        missing = [tool for tool in ("ffmpeg", "ffprobe", "cargo") if shutil.which(tool) is None]
        if missing:
            self.skipTest(f"SKIPPED: {', '.join(missing)} not on PATH")
        self.dir = test_analyze_lift.repo_temp_dir(self, "vbt-session-e2e-")
        analyze_lift.PERSONAL_ROOT.mkdir(parents=True, exist_ok=True)
        sessions = tempfile.TemporaryDirectory(prefix="e2e-sessions-", dir=analyze_lift.PERSONAL_ROOT)
        self.addCleanup(sessions.cleanup)
        self.sessions = Path(sessions.name)
        self.inbox = self.dir / "inbox"
        self.inbox.mkdir()
        shutil.copyfile(ROOT / "validation" / "fixtures" / "public" / f"{FIXTURE}.mp4", self.inbox / f"{FIXTURE}.mp4")
        self.common = ["--session", "e2e", "--sessions-root", str(self.sessions),
                       "--manifest", str(self.dir / "vbt" / "manifest.json")]

    def main(self, argv: list[str]) -> str:
        completed = subprocess.run([sys.executable, str(WORKFLOW_DIR / "vbt_session.py"), *argv], cwd=ROOT,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return completed.stdout

    def hand_written_csv(self) -> Path:
        state = json.loads((self.sessions / "e2e" / "session.json").read_text(encoding="utf-8"))
        [clip] = state["clips"]
        self.assertEqual(clip["suggestions"]["plate"]["status"], "suggested")
        self.assertEqual(clip["suggestions"]["stick"]["status"], "failed")
        row = {**fakes.accepted_row(clip, state, "clean"), "stick_suggestion_id": "",
               "stick_low_x_px": "20.00", "stick_low_y_px": "200.00", "stick_high_x_px": "20.00",
               "stick_high_y_px": "40.00", "stick_low_status": "manual", "stick_high_status": "manual"}
        path = self.dir / "vbt-session-e2e.csv"
        path.write_bytes(fakes.csv_bytes([row]))
        return path

    def assert_schema(self, path: Path, schema: str) -> None:
        errors = schema_check.validate_document(schema_check.load_strict(path),
                                                schema_check.load_schema(SCHEMAS / schema))
        self.assertEqual(errors, [], path)

    def test_ingest_page_csv_run_and_byte_identical_rerun(self) -> None:
        self.main(["ingest", *self.common, "--inbox", str(self.inbox), "--media-dir", str(self.dir / "media")])
        csv = self.hand_written_csv()
        run = ["run", *self.common, "--csv", str(csv), "--plate-diameter-m", "0.45", "--stick-length-m", "1.30",
               "--tracker-policy", "csrt-all-v1", *FILTER]
        self.main(run)
        session = self.sessions / "e2e"
        fixture_id = json.loads((session / "session.json").read_text(encoding="utf-8"))["clips"][0]["fixture_id"]
        seed = session / "seeds" / f"{fixture_id}.manual-target-seed-v1.json"
        analyses = session / "analyses"
        self.assert_schema(seed, "manual-target-seed-v1.schema.json")
        self.assert_schema(analyses / f"{fixture_id}.opencv-csrt.prediction-v1.json", "tracker-prediction-v1.schema.json")
        self.assert_schema(analyses / f"{fixture_id}.opencv-csrt.analysis-v1.json", "analysis-v1.schema.json")
        self.assertIn("plate centre accepted, plate radius accepted",
                      json.loads(seed.read_text(encoding="utf-8"))["seed"]["notes"])
        self.assertTrue((session / "report.html").is_file())
        self.assertTrue((session / "scale-report" / "scale-reference-v1.json").is_file())
        first = {path.relative_to(session).as_posix(): path.read_bytes()
                 for path in sorted(session.rglob("*")) if path.is_file()}
        self.main([*run, "--force"])
        second = {path.relative_to(session).as_posix(): path.read_bytes()
                  for path in sorted(session.rglob("*")) if path.is_file()}
        self.assertEqual(sorted(first), sorted(second))
        for name in first:
            self.assertEqual(first[name], second[name], name)
        self.main(["status", *self.common])
        self.main([*run, "--resume"])
        self.assertEqual({path.relative_to(session).as_posix(): path.read_bytes()
                          for path in sorted(session.rglob("*")) if path.is_file()}, second)
        assessments = self.dir / "assessments"
        assessments.mkdir()
        assessment = assessments / f"{fixture_id}.assessment-v1.json"
        cli = ROOT / "target" / "release" / ("openbar-cli.exe" if os.name == "nt" else "openbar-cli")
        result = subprocess.run([sys.executable, str(WORKFLOW_DIR / "assess_clip.py"), "--run-record",
                                 str(analyses / f"{fixture_id}.opencv-csrt.run-record.json"), "--fixture-id", fixture_id,
                                 "--openbar-cli", str(cli), "--output", str(assessment)], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        outgoing = self.dir / "outgoing"
        handoff = ["handoff", *self.common, "--tracker-policy", "csrt-all-v1", "--assessments-dir", str(assessments),
                   "--output-dir", str(outgoing)]
        self.main([*handoff, "--dry-run"])
        self.assertFalse(outgoing.exists())
        self.main(handoff)
        before = {path.name: path.read_bytes() for path in outgoing.iterdir()}
        self.main(handoff)
        self.assertEqual({path.name: path.read_bytes() for path in outgoing.iterdir()}, before)
        analysis = analyses / f"{fixture_id}.opencv-csrt.analysis-v1.json"
        self.assertEqual((outgoing / analysis.name).read_bytes(), analysis.read_bytes())
        self.assertEqual((outgoing / assessment.name).read_bytes(), assessment.read_bytes())
        self.assertEqual(session_contract.FORMAT, "openbar-vbt-session-v1")


if __name__ == "__main__":
    unittest.main()
