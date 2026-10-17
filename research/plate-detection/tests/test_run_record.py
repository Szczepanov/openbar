"""run.json is created once per configuration and only extended by identical reruns (#233)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_phase_a  # noqa: E402

RECORD = {"harness": "h", "methods": {"hough": {"p": 1}}}


class RunRecordTests(unittest.TestCase):
    def test_identical_rerun_extends_clips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            run_phase_a.write_record(path, RECORD, {"b": "exploration"})
            run_phase_a.write_record(path, RECORD, {"a": "exploration"})
            self.assertEqual(json.loads(path.read_text())["clips"], {"a": "exploration", "b": "exploration"})

    def test_changed_provenance_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            run_phase_a.write_record(path, RECORD, {"a": "exploration"})
            with self.assertRaises(SystemExit):
                run_phase_a.write_record(path, {**RECORD, "methods": {"sam3": {}}}, {"a": "exploration"})
            self.assertEqual(json.loads(path.read_text())["methods"], RECORD["methods"])

    def test_changed_role_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            run_phase_a.write_record(path, RECORD, {"a": "exploration"})
            with self.assertRaises(SystemExit):
                run_phase_a.write_record(path, RECORD, {"a": "scored-development-only"})


if __name__ == "__main__":
    unittest.main()
