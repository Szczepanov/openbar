#!/usr/bin/env python3
"""report.html pieces (#95): rep preview, plot, crops and byte-for-byte determinism. Stdlib only."""
from __future__ import annotations

import base64
import re
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import session_report  # noqa: E402


def sample(t: float, y: float, vy: float | None) -> dict[str, Any]:
    return {"timestamp_s": t, "x_m": 0.0, "y_m": y, "vx_mps": None if vy is None else 0.0, "vy_mps": vy,
            "confidence": 1.0}


def two_reps() -> list[dict[str, Any]]:
    """Rise 0.5 m over 0.5 s, small 0.05 m bump, gap, then 0.4 m over 0.4 s."""
    samples = [sample(0.0, 0.0, None)]
    t, y = 0.0, 0.0
    for vy in (0.6, 1.0, 1.4, 1.0, 1.0):  # 0.1 s steps
        t, y = round(t + 0.1, 6), y + vy * 0.1
        samples.append(sample(t, y, vy))
    for vy in (-1.0, -1.0, 0.5, -0.5):
        t, y = round(t + 0.1, 6), y + vy * 0.1
        samples.append(sample(t, y, vy))
    t = round(t + 0.5, 6)
    samples.append(sample(t, y, None))  # after a gap: no velocity
    for vy in (1.0, 1.0, 1.0, 1.0):
        t, y = round(t + 0.1, 6), y + vy * 0.1
        samples.append(sample(t, y, vy))
    return samples


def analysis(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "calibration": {"scale": {"metres_per_pixel": 0.0025}},
        "derived": {"kinematics": {"samples": samples}},
        "raw_observations": [
            {"timestamp_s": s["timestamp_s"], "frame_index": index, "tracking_state": "tracked",
             "measurement": {"timestamp_s": s["timestamp_s"], "x_px": 500.0 + index, "y_px": 900.0, "confidence": 1.0}}
            for index, s in enumerate(samples)
        ],
    }


SEED = {"seed": {"timestamp_s": 0.0, "frame_index": 0, "target": {"center": {"x_px": 500.0, "y_px": 900.0},
                                                                    "radius_px": 100.0}}}


def clip(samples: list[dict[str, Any]], crops: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "fixture_id": "vbt-0123456789abcdef", "original_name": "VID <1>.mp4", "exercise": "snatch",
        "tracker": "csrt", "statuses": {"plate_center": "accepted", "plate_radius": "adjusted"},
        "analysis": analysis(samples), "seed": SEED,
        "prediction": {"samples": [{"state": "tracked"}, {"state": "tracked"}, {"state": "lost"}]},
        "scale_row": {"plate_scale_m_per_px": 0.0025,
                      "reference": {"reference_scale_m_per_px": 0.00255},
                      "reference_to_plate_ratio": {"value": 1.02, "lower": 1.01, "upper": 1.03,
                                                   "consistent_with_1": False}},
        "crops": crops or [],
    }


class PreviewRepTests(unittest.TestCase):
    def test_two_reps_and_the_small_bump_is_ignored(self) -> None:
        reps = session_report.preview_reps(two_reps())
        self.assertEqual(len(reps), 2)
        first, second = reps
        self.assertEqual((first["start_s"], first["end_s"]), (0.0, 0.5))
        self.assertAlmostEqual(first["rise_m"], 0.5)
        self.assertAlmostEqual(first["mean_mps"], 1.0)
        self.assertAlmostEqual(first["peak_mps"], 1.4)
        self.assertAlmostEqual(second["rise_m"], 0.4)
        self.assertAlmostEqual(second["mean_mps"], 1.0)

    def test_gap_ends_a_run_and_empty_input(self) -> None:
        self.assertEqual(session_report.preview_reps([]), [])
        self.assertEqual(session_report.preview_reps([sample(0.0, 0.0, None), sample(0.1, 0.0, None)]), [])

    def test_threshold_is_configurable(self) -> None:
        self.assertEqual(len(session_report.preview_reps(two_reps(), min_rise_m=0.45)), 1)


class PeakAndCropTests(unittest.TestCase):
    def test_crop_frames_surround_the_peak(self) -> None:
        self.assertEqual(session_report.crop_frames(analysis(two_reps()), 100), [1, 3, 5])
        self.assertEqual(session_report.crop_frames(analysis(two_reps()), 4), [1, 3])
        self.assertEqual(session_report.crop_frames(analysis([sample(0.0, 0.0, None)]), 10), [])

    def test_crop_box_is_centred_on_the_tracked_plate_and_clamped(self) -> None:
        boxes = []

        def cropper(frame: Path, left: int, top: int, right: int, bottom: int) -> bytes:
            boxes.append((left, top, right, bottom))
            return b"png"
        crop = session_report.make_crop(Path("f.png"), 3, analysis(two_reps()), SEED, (1080, 1920), cropper)
        self.assertEqual(boxes, [(343, 740, 664, 1061)])
        self.assertEqual(crop["centre"], (503.0, 900.0))
        self.assertEqual(base64.b64decode(crop["png"]), b"png")
        session_report.make_crop(Path("f.png"), 0, analysis(two_reps()), SEED, (520, 1000), cropper)
        self.assertEqual(boxes[-1][2], 520)


class RenderTests(unittest.TestCase):
    def render(self) -> str:
        crop = session_report.make_crop(Path("f.png"), 3, analysis(two_reps()), SEED, (1080, 1920),
                                        lambda *args: b"png")
        return session_report.render_report("2026-10-03", {"tracker_policy": "csrt-all-v1", "plate_diameter_m": 0.45},
                                            [clip(two_reps(), [crop])], ["VID_2.mp4"])

    def test_report_is_deterministic_and_self_contained(self) -> None:
        first, second = self.render(), self.render()
        self.assertEqual(first, second)
        self.assertNotRegex(first, r"(?i)(src|href)=['\"]https?:")
        self.assertNotRegex(first, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")  # no wall-clock timestamps

    def test_report_content(self) -> None:
        html = self.render()
        self.assertIn("VID &lt;1&gt;.mp4", html, "names are escaped")
        self.assertIn("Per-rep PREVIEW (not authoritative)", html)
        self.assertIn("<td>1.000</td><td>1.020</td>", html, "mean plate and stick-corrected")
        self.assertIn("2 tracked, 1 lost, 3 total", html)
        self.assertIn("1.0200 (1.0100–1.0300)", html)
        self.assertIn("Skipped on the page: VID_2.mp4", html)
        self.assertEqual(len(re.findall(r"<polyline", html)), 2, "the gap splits the velocity line")
        self.assertIn("data:image/png;base64,cG5n", html)

    def test_clip_without_scale_row(self) -> None:
        html = session_report.render_report("s", {}, [{**clip(two_reps()), "scale_row": None}], [])
        self.assertIn("<td>1.000</td><td>—</td>", html)


if __name__ == "__main__":
    unittest.main()
