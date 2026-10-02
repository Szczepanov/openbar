from __future__ import annotations

import py_compile
import runpy
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "research" / "opencv-tracking"
SCRIPTS = (
    RESEARCH / "track.py",
    RESEARCH / "compare.py",
    RESEARCH / "visual_qa.py",
)


class ResearchOpenCvTrackingTests(unittest.TestCase):
    def test_research_scripts_are_valid_python(self) -> None:
        with tempfile.TemporaryDirectory(prefix="openbar-research-pycompile-") as tmp:
            for script in SCRIPTS:
                target = Path(tmp) / f"{script.stem}.pyc"
                py_compile.compile(str(script), cfile=str(target), doraise=True)

    def test_compare_uses_explicit_current_run_prediction_set(self) -> None:
        namespace = runpy.run_path(str(RESEARCH / "compare.py"))
        expected_prediction_paths = namespace["expected_prediction_paths"]
        require_prediction_files = namespace["require_prediction_files"]

        with tempfile.TemporaryDirectory(prefix="openbar-research-predictions-") as tmp:
            directory = Path(tmp)
            fixture_id = "fixture-a"
            expected = expected_prediction_paths(directory, fixture_id, ["opencv-csrt"])

            self.assertEqual(
                [path.name for path in expected],
                [
                    "fixture-a.template-sad-v1.prediction-v1.json",
                    "fixture-a.local-contrast-centroid-v1.prediction-v1.json",
                    "fixture-a.opencv-csrt.prediction-v1.json",
                ],
            )

            for path in expected:
                path.write_text("{}\n", encoding="utf-8")
            stale = directory / "fixture-a.old-candidate.prediction-v1.json"
            stale.write_text("{}\n", encoding="utf-8")

            self.assertEqual(require_prediction_files(expected), expected)
            self.assertNotIn(stale, expected)

    def test_compare_fails_closed_when_expected_output_is_missing(self) -> None:
        namespace = runpy.run_path(str(RESEARCH / "compare.py"))
        require_prediction_files = namespace["require_prediction_files"]

        with tempfile.TemporaryDirectory(prefix="openbar-research-predictions-") as tmp:
            missing = Path(tmp) / "missing.prediction-v1.json"
            with self.assertRaises(SystemExit):
                require_prediction_files([missing])


if __name__ == "__main__":
    unittest.main()
