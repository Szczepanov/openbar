#!/usr/bin/env python3
"""Tests for track_gpu.py's output writing and the opt-in --omit-runtime / --geometry-output flags.

The tracker itself is stubbed: these cover only how main() serialises a prediction and its geometry
sidecar. torch is replaced by an empty stand-in module when it is not installed (CI has no GPU stack),
so these tests need only the OpenCV research requirements.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

GPU_TRACKING_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GPU_TRACKING_DIR))

if importlib.util.find_spec("torch") is None:
    # track_gpu imports torch at module level but main()'s writing path never touches it.
    torch_stub = types.ModuleType("torch")
    torch_stub.nn = types.ModuleType("torch.nn")  # type: ignore[attr-defined]
    torch_stub.nn.functional = types.ModuleType("torch.nn.functional")  # type: ignore[attr-defined]
    sys.modules.setdefault("torch", torch_stub)
    sys.modules.setdefault("torch.nn", torch_stub.nn)  # type: ignore[attr-defined]
    sys.modules.setdefault("torch.nn.functional", torch_stub.nn.functional)  # type: ignore[attr-defined]

import track_gpu  # noqa: E402

CIRCLE = "sam2.1-bplus-circle"
CENTROID = "sam2.1-bplus-centroid"


def prediction_for(name: str) -> dict:
    return {
        "schema_version": 1,
        "fixture_id": "fixture-a",
        "source_video_sha256": "a" * 64,
        "coordinate_space": "decoded_display_pixels",
        "implementation": {"name": name, "version": "gpu-spike-3", "config": {"candidate": name}},
        "runtime": {"processing_wall_s": 0.25},
        "samples": [
            {"timestamp_s": 0.0, "state": "tracked", "center_px": {"x_px": 1.0, "y_px": 2.0}, "confidence": 1.0},
            {"timestamp_s": 0.1, "state": "lost"},
        ],
    }


def sidecar_for(name: str) -> dict:
    return {
        "format": "openbar-research-geometry-sidecar",
        "format_version": 0,
        "fixture_id": "fixture-a",
        "implementation": {"name": name, "version": "gpu-spike-3", "config": {"candidate": name}},
        "samples": [{"timestamp_s": 0.0, "fit_attempted": False, "accepted": True, "reject_reasons": [],
                     "base_confidence": 1.0}],
    }


def platform_text(document: dict) -> bytes:
    """What Path.write_text(..., encoding='utf-8') produces on this platform (the default mode)."""
    text = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    return text.replace(b"\n", b"\r\n") if sys.platform == "win32" else text


def lf_text(document: dict) -> bytes:
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def without_runtime(document: dict) -> dict:
    return {key: value for key, value in document.items() if key != "runtime"}


class TrackGpuOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="openbar-track-gpu-output-")
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.output = self.dir / f"fixture-a.{CIRCLE}.prediction-v1.json"
        self.default_sidecar = self.dir / f"fixture-a.{CIRCLE}.geometry.json"

    def run_main(self, *extra: str, candidate: str = CIRCLE, documents: list | None = None) -> tuple[int, str]:
        if documents is None:
            documents = [(prediction_for(candidate), sidecar_for(candidate) if candidate.endswith("-circle") else None)]
        stdout = io.StringIO()
        copied = json.loads(json.dumps(documents))
        with mock.patch.object(track_gpu, "track", return_value=[tuple(pair) for pair in copied]) as track, \
                contextlib.redirect_stdout(stdout):
            code = track_gpu.main(["--manifest", "m.json", "--fixture", "fixture-a", "--seed", "s.json",
                                   "--candidate", candidate, "--output", str(self.output), *extra])
        self.track_calls = track.call_args_list
        return code, stdout.getvalue()

    def assert_usage_error(self, *argv: str) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            track_gpu.main(["--manifest", "m.json", "--fixture", "fixture-a", "--seed", "s.json",
                            "--output", str(self.output), *argv])
        self.assertEqual(raised.exception.code, 2)

    def test_default_output_is_unchanged(self) -> None:
        # Default: runtime kept, platform text mode, sidecar named after --output (pre-existing behaviour).
        code, stdout = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(self.output.read_bytes(), platform_text(prediction_for(CIRCLE)))
        self.assertEqual(self.default_sidecar.read_bytes(), platform_text(sidecar_for(CIRCLE)))
        self.assertEqual(sorted(path.name for path in self.dir.iterdir()),
                         sorted([self.output.name, self.default_sidecar.name]))
        self.assertIn("1/2 tracked, 0.25 s", stdout)
        self.assertNotIn("runtime omitted", stdout)

    def test_omit_runtime_drops_runtime_and_writes_lf(self) -> None:
        code, stdout = self.run_main("--omit-runtime")
        self.assertEqual(code, 0)
        self.assertEqual(self.output.read_bytes(), lf_text(without_runtime(prediction_for(CIRCLE))))
        self.assertEqual(self.default_sidecar.read_bytes(), lf_text(sidecar_for(CIRCLE)))
        self.assertIn("1/2 tracked, 0.25 s", stdout)
        self.assertIn("runtime omitted from prediction", stdout)

    def test_geometry_output_redirects_the_sidecar(self) -> None:
        geometry = self.dir / "staged" / ".geometry.json.tmp"
        code, _ = self.run_main("--omit-runtime", "--geometry-output", str(geometry))
        self.assertEqual(code, 0)
        self.assertEqual(geometry.read_bytes(), lf_text(sidecar_for(CIRCLE)))
        self.assertFalse(self.default_sidecar.exists())

    def test_geometry_output_applies_only_to_the_primary_candidate(self) -> None:
        sibling = self.dir / f"fixture-a.{CENTROID}.prediction-v1.json"
        geometry = self.dir / "primary.geometry.json"
        documents = [(prediction_for(CIRCLE), sidecar_for(CIRCLE)), (prediction_for(CENTROID), None)]
        code, _ = self.run_main("--sibling-output", str(sibling), "--geometry-output", str(geometry),
                                documents=documents)
        self.assertEqual(code, 0)
        self.assertEqual(geometry.read_bytes(), platform_text(sidecar_for(CIRCLE)))
        self.assertEqual(sibling.read_bytes(), platform_text(prediction_for(CENTROID)))
        self.assertEqual(self.track_calls[0].args[3], [CIRCLE, CENTROID])

    def test_geometry_output_requires_a_circle_candidate(self) -> None:
        self.assert_usage_error("--candidate", CENTROID, "--geometry-output", str(self.dir / "g.json"))

    def test_summary_tolerates_prediction_without_runtime(self) -> None:
        line = track_gpu.summary_line(without_runtime(prediction_for(CIRCLE)), self.output, None)
        self.assertIn("1/2 tracked", line)
        self.assertIn("unknown", line)

    def test_tracker_error_writes_nothing(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(track_gpu, "track", side_effect=track_gpu.GpuTrackerError("checkpoint missing")), \
                contextlib.redirect_stderr(stderr):
            code = track_gpu.main(["--manifest", "m.json", "--fixture", "fixture-a", "--seed", "s.json",
                                   "--candidate", CIRCLE, "--output", str(self.output), "--omit-runtime"])
        self.assertEqual(code, 1)
        self.assertIn("error: checkpoint missing", stderr.getvalue())
        self.assertEqual(list(self.dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
