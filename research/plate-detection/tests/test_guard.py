from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import guard  # noqa: E402


def frame(t: float, x: float | None = None, r: float = 100.0) -> dict:
    candidates = [] if x is None else [{"center_x_px": x, "center_y_px": 500.0, "radius_px": r, "score": 0.9}]
    return {"time_s": t, "frame_index": round(t * 30), "candidates": candidates}


def statuses(records: list[dict]) -> list[str]:
    return [r["guard"]["status"] for r in guard.guard_track(records)]


class GuardTests(unittest.TestCase):
    def test_smooth_track_stays_tracked(self) -> None:
        records = [frame(i / 30, 300 + 20 * i) for i in range(10)]
        self.assertEqual(statuses(records), ["tracked"] * 10)

    def test_short_loss_may_reacquire_nearby(self) -> None:
        records = [frame(0.0, 300), frame(0.1), frame(0.2), frame(0.3, 340)]
        self.assertEqual(statuses(records), ["tracked", "lost", "lost", "tracked"])

    def test_long_loss_stops_the_track(self) -> None:
        records = [frame(0.0, 300), frame(0.3), frame(0.6), frame(0.7, 300), frame(0.8, 305)]
        out = guard.guard_track(records)
        self.assertEqual([r["guard"]["status"] for r in out], ["tracked", "lost", "lost", "stopped_gap", "after_stop"])
        self.assertEqual([len(r["candidates"]) for r in out], [1, 0, 0, 0, 0])

    def test_jump_or_radius_change_stops_the_track(self) -> None:
        # 18 radii/s at dt = 1/30 s allows 0.6 + 0.25 = 0.85 radii = 85 px.
        self.assertEqual(statuses([frame(0.0, 300), frame(1 / 30, 384)]), ["tracked", "tracked"])
        self.assertEqual(statuses([frame(0.0, 300), frame(1 / 30, 386), frame(2 / 30, 390)]),
                         ["tracked", "stopped_step", "after_stop"])
        self.assertEqual(statuses([frame(0.0, 300), frame(1 / 30, 300, r=115)]), ["tracked", "tracked"])
        self.assertEqual(statuses([frame(0.0, 300), frame(1 / 30, 300, r=116)]), ["tracked", "stopped_step"])

    def test_time_order_raw_untouched_and_one_candidate(self) -> None:
        records = [frame(1 / 30, 310), frame(0.0, 300)]
        out = guard.guard_track(records)
        self.assertEqual([r["time_s"] for r in out], [0.0, 1 / 30])
        self.assertEqual(len(records[0]["candidates"]), 1)  # input not mutated
        two = frame(0.0, 300)
        two["candidates"] = two["candidates"] * 2
        with self.assertRaises(ValueError):
            guard.guard_track([two])

    def test_leading_loss_then_track(self) -> None:
        self.assertEqual(statuses([frame(0.0), frame(1.0, 300)]), ["lost", "tracked"])


if __name__ == "__main__":
    unittest.main()
