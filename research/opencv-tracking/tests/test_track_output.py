#!/usr/bin/env python3
"""Tests for track.py's prediction writing and the opt-in --omit-runtime flag (#86).

The tracker itself is stubbed: these cover only how main() serialises and summarises a prediction.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

OPENCV_TRACKING_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPENCV_TRACKING_DIR))
import track  # noqa: E402

PREDICTION = {
    "schema_version": 1,
    "fixture_id": "fixture-a",
    "source_video_sha256": "a" * 64,
    "coordinate_space": "decoded_display_pixels",
    "implementation": {"name": "opencv-csrt", "version": track.SPIKE_VERSION, "config": {"tracker": "csrt"}},
    "runtime": {"processing_wall_s": 0.25},
    "samples": [
        {"timestamp_s": 0.0, "state": "tracked", "center_px": {"x_px": 1.0, "y_px": 2.0}, "confidence": 1.0},
        {"timestamp_s": 0.1, "state": "lost"},
    ],
}


class TrackOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="openbar-track-output-")
        self.addCleanup(temp.cleanup)
        self.output = Path(temp.name) / "out.prediction-v1.json"

    def run_main(self, *extra: str) -> str:
        stdout = io.StringIO()
        prediction = json.loads(json.dumps(PREDICTION))
        with mock.patch.object(track, "track", return_value=(prediction, None)), contextlib.redirect_stdout(stdout):
            code = track.main(["--manifest", "m.json", "--fixture", "fixture-a", "--seed", "s.json",
                               "--tracker", "csrt", "--output", str(self.output), *extra])
        self.assertEqual(code, 0)
        return stdout.getvalue()

    def test_default_output_keeps_runtime_and_platform_text_mode(self) -> None:
        stdout = self.run_main()
        written = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(written, PREDICTION)
        expected = (json.dumps(PREDICTION, indent=2) + "\n").encode("utf-8")
        if sys.platform == "win32":
            expected = expected.replace(b"\n", b"\r\n")
        self.assertEqual(self.output.read_bytes(), expected)
        self.assertIn("1/2 tracked, 0.25 s", stdout)

    def test_omit_runtime_drops_runtime_and_writes_lf(self) -> None:
        stdout = self.run_main("--omit-runtime")
        expected_doc = {key: value for key, value in PREDICTION.items() if key != "runtime"}
        self.assertEqual(self.output.read_bytes(), (json.dumps(expected_doc, indent=2) + "\n").encode("utf-8"))
        self.assertNotIn(b"\r\n", self.output.read_bytes())
        self.assertIn("1/2 tracked, 0.25 s", stdout)
        self.assertIn("runtime omitted from prediction", stdout)

    def test_summary_tolerates_prediction_without_runtime(self) -> None:
        line = track.summary_line({**PREDICTION, "runtime": None}, Path("x.json"), None)
        self.assertIn("1/2 tracked", line)
        no_runtime = {key: value for key, value in PREDICTION.items() if key != "runtime"}
        self.assertIn("1/2 tracked", track.summary_line(no_runtime, Path("x.json"), None))


if __name__ == "__main__":
    unittest.main()
