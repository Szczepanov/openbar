#!/usr/bin/env python3
"""Held-out refusal for the OpenCV entry points (#57): refused before any seed, media or label read."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

OPENCV_TRACKING_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPENCV_TRACKING_DIR))
import track  # noqa: E402
import visual_qa  # noqa: E402


def write_manifest(directory: Path, purpose: str, tags: list[str]) -> Path:
    path = directory / "manifest.json"
    doc = {"fixtures": [{"id": "f", "purpose": purpose, "conditions": {"challenge_tags": tags}}]}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


CASES = (("validation", []), ("development", ["split-reserved-57"]))


class HeldOutRefusalTests(unittest.TestCase):
    def test_track_load_inputs_refuses_before_reading_seed(self) -> None:
        for purpose, tags in CASES:
            with self.subTest(purpose=purpose, tags=tags), tempfile.TemporaryDirectory() as tmp:
                manifest = write_manifest(Path(tmp), purpose, tags)
                with self.assertRaisesRegex(track.SpikeError, "held-out"):
                    track.load_inputs(manifest, "f", Path(tmp) / "missing-seed.json", False)

    def test_track_explicit_allow_reaches_the_seed_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_manifest(Path(tmp), "validation", [])
            with self.assertRaises(FileNotFoundError):
                track.load_inputs(manifest, "f", Path(tmp) / "missing-seed.json", True)

    def test_visual_qa_refuses_before_reading_annotations(self) -> None:
        for purpose, tags in CASES:
            with self.subTest(purpose=purpose, tags=tags), tempfile.TemporaryDirectory() as tmp:
                manifest = write_manifest(Path(tmp), purpose, tags)
                with self.assertRaisesRegex(SystemExit, "held-out"):
                    visual_qa.extract_crops(manifest, "f", Path(tmp), False)


if __name__ == "__main__":
    unittest.main()
