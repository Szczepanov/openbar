from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import post_track_refusal  # noqa: E402


def _rec(
    frame_index: int,
    time_s: float,
    cx: float | None = 500.0,
    cy: float = 400.0,
    r: float = 100.0,
    status: str = "tracked",
    redetect: dict | None = None,
    seed: dict | None = None,
) -> dict:
    out: dict = {
        "fixture_id": "self-clean-side-0203-17",
        "method": "track_edgetam_sam3seed_redetect",
        "frame_index": frame_index,
        "time_s": time_s,
        "candidates": []
        if cx is None
        else [
            {
                "center_x_px": cx,
                "center_y_px": cy,
                "radius_px": r,
                "score": 0.92,
            }
        ],
        "guard": {"rule": "track-guard-v2", "status": status},
    }
    if redetect is not None:
        out["redetect"] = redetect
    if seed is not None:
        out["seed"] = seed
    return out


class PostTrackRefusalTests(unittest.TestCase):
    def test_clean_moving_track_passes_without_flags(self) -> None:
        records = [
            _rec(0, 0.0, cy=600.0, seed={"selected_index": 0, "tracked": True, "refusal": {"refused": False}}),
            _rec(2, 0.2, cy=500.0),
            _rec(4, 0.4, cy=380.0),
            _rec(6, 0.6, cy=450.0),
            _rec(8, 0.8, cy=600.0),
        ]
        diag = post_track_refusal.diagnose_clip(records)
        self.assertEqual(diag["epoch_count"], 1)
        self.assertEqual(diag["supported_time_fraction"], 1.0)
        self.assertFalse(diag["flags"]["flagged"])
        self.assertEqual(diag["flags"]["reasons"], [])

    def test_low_coverage_after_guard_stop_is_flagged(self) -> None:
        records = [
            _rec(0, 0.0, cy=600.0, seed={"selected_index": 0, "tracked": True, "refusal": {"refused": False}}),
            _rec(2, 0.2, cy=400.0),
            _rec(4, 0.8, cx=None, status="stopped_gap"),
            _rec(6, 1.0, cx=None, status="after_stop"),
            _rec(8, 1.2, cx=None, status="after_stop"),
        ]
        diag = post_track_refusal.diagnose_clip(records)
        self.assertLess(diag["supported_time_fraction"], 0.50)
        self.assertTrue(diag["flags"]["low_coverage"])
        self.assertTrue(diag["flags"]["flagged"])
        self.assertIn("insufficient_coverage", diag["flags"]["reasons"])
        self.assertEqual(diag["stopped_gap_count"], 1)

    def test_ambiguous_large_jump_restart_to_stationary_plate_is_flagged(self) -> None:
        records = [
            _rec(0, 0.0, cx=300.0, cy=600.0, seed={"selected_index": 0, "tracked": True, "refusal": {"refused": False}}),
            _rec(2, 0.2, cx=300.0, cy=400.0),
            _rec(
                10,
                0.8,
                cx=650.0,
                cy=600.0,
                status="redetected",
                redetect={
                    "selected_index": 0,
                    "matching_indices": [0],
                    "refusal": {"rule": "sam3-margin-v2", "refused": False, "margin": None},
                    "pre_restart_guard_status": "stopped_gap",
                },
            ),
            _rec(12, 1.0, cx=650.0, cy=602.0, status="tracked"),
            _rec(14, 1.2, cx=650.0, cy=601.0, status="tracked"),
        ]
        diag = post_track_refusal.diagnose_clip(records)
        self.assertEqual(diag["epoch_count"], 2)
        self.assertEqual(diag["accepted_restart_count"], 1)
        restart_info = diag["epochs"][1]["restart"]
        self.assertIsNotNone(restart_info)
        self.assertGreater(restart_info["jump_radii"], 1.5)
        self.assertTrue(restart_info["uncontested_restart"])
        self.assertTrue(restart_info["stationary_after_restart"])
        self.assertTrue(restart_info["ambiguous_restart"])
        self.assertTrue(diag["flags"]["has_ambiguous_restart"])
        self.assertIn("ambiguous_restart", diag["flags"]["reasons"])

    def test_nearby_contested_moving_restart_is_not_flagged_ambiguous(self) -> None:
        records = [
            _rec(0, 0.0, cx=300.0, cy=600.0, seed={"selected_index": 0, "tracked": True, "refusal": {"refused": False}}),
            _rec(2, 0.2, cx=300.0, cy=420.0),
            _rec(
                10,
                0.8,
                cx=330.0,
                cy=450.0,
                status="redetected",
                redetect={
                    "selected_index": 0,
                    "matching_indices": [0, 1],
                    "refusal": {"rule": "sam3-margin-v2", "refused": False, "margin": 0.22},
                    "pre_restart_guard_status": "stopped_gap",
                },
            ),
            _rec(12, 1.0, cx=330.0, cy=650.0, status="tracked"),
            _rec(14, 1.2, cx=330.0, cy=600.0, status="tracked"),
        ]
        diag = post_track_refusal.diagnose_clip(records)
        restart_info = diag["epochs"][1]["restart"]
        self.assertFalse(restart_info["large_spatial_jump"])
        self.assertFalse(restart_info["uncontested_restart"])
        self.assertFalse(restart_info["stationary_after_restart"])
        self.assertFalse(restart_info["ambiguous_restart"])
        self.assertFalse(diag["flags"]["has_ambiguous_restart"])


if __name__ == "__main__":
    unittest.main()
