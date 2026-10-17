"""Label-free selection rules and the scoring match (#233)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import selection  # noqa: E402
from selection import Candidate  # noqa: E402

TARGET = Candidate(100.0, 300.0, 50.0, 0.6)
CLUTTER = Candidate(400.0, 500.0, 40.0, 0.9)


class MatchTests(unittest.TestCase):
    def test_boundaries_are_inclusive(self) -> None:
        self.assertTrue(selection.match(Candidate(112.5, 300.0, 50.0, 1), 100.0, 300.0, 50.0))
        self.assertTrue(selection.match(Candidate(100.0, 300.0, 62.5, 1), 100.0, 300.0, 50.0))
        self.assertFalse(selection.match(Candidate(112.6, 300.0, 50.0, 1), 100.0, 300.0, 50.0))
        self.assertFalse(selection.match(Candidate(100.0, 300.0, 37.4, 1), 100.0, 300.0, 50.0))


class Top1Tests(unittest.TestCase):
    def test_highest_score_then_geometry(self) -> None:
        self.assertEqual(selection.select_top1([TARGET, CLUTTER]), CLUTTER)
        a, b = Candidate(5, 5, 5, 0.5), Candidate(4, 5, 5, 0.5)
        self.assertEqual(selection.select_top1([a, b]), b)
        self.assertEqual(selection.select_top1([b, a]), b)

    def test_empty(self) -> None:
        self.assertIsNone(selection.select_top1([]))


class PlausibleLargestTests(unittest.TestCase):
    def test_radius_window_is_inclusive(self) -> None:
        cands = [Candidate(0, 0, r, 1) for r in (43.1, 43.3, 323.9, 324.1)]  # either side of 0.04 and 0.30 of 1080
        self.assertEqual([c.radius_px for c in selection.plausible(cands, 1080, 1920)], [43.3, 323.9])

    def test_largest_among_strong_enough(self) -> None:
        small_strong, big_ok, big_weak = Candidate(1, 1, 50, 0.9), Candidate(2, 2, 90, 0.5), Candidate(3, 3, 120, 0.4)
        self.assertEqual(selection.select_largest([small_strong, big_ok, big_weak]), big_ok)
        self.assertIsNone(selection.select_largest([]))


class MotionTests(unittest.TestCase):
    def test_stationary_clutter_loses_to_moving_plate(self) -> None:
        moved = Candidate(110.0, 120.0, 50.0, 0.6)  # target lifted away from its start spot
        others = [[CLUTTER, moved]] * 5
        chosen, info = selection.select_motion([TARGET, CLUTTER], others)
        self.assertEqual(chosen, TARGET)
        self.assertEqual(info["persistence"], [1.0, 0.0])  # clutter first (higher score)

    def test_implausibly_weak_candidates_are_ignored(self) -> None:
        weak = Candidate(700.0, 100.0, 30.0, 0.3)  # below half of 0.9
        chosen, info = selection.select_motion([CLUTTER, weak], [[CLUTTER]] * 5)
        self.assertEqual(chosen, CLUTTER)
        self.assertEqual(info["plausible"], 1)

    def test_tie_on_persistence_takes_higher_score(self) -> None:
        chosen, _ = selection.select_motion([TARGET, CLUTTER], [[]] * 5)
        self.assertEqual(chosen, CLUTTER)

    def test_too_few_frames_fall_back_to_top1(self) -> None:
        chosen, info = selection.select_motion([TARGET, CLUTTER], [[]] * 3)
        self.assertEqual((chosen, info["fallback"]), (CLUTTER, "top1_too_few_frames"))

    def test_empty_decision_frame(self) -> None:
        self.assertEqual(selection.select_motion([], [[]] * 5)[0], None)

    def test_persistence_needs_frames(self) -> None:
        with self.assertRaises(ValueError):
            selection.persistence(TARGET, [])


if __name__ == "__main__":
    unittest.main()
