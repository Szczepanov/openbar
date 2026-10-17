"""Model-free detector helpers: box validity and cross-prompt NMS (#233). No torch needed."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import detectors  # noqa: E402


class BoxTests(unittest.TestCase):
    def test_valid_box(self) -> None:
        self.assertTrue(detectors.valid_box([-5.0, 0.0, 10.0, 10.0]))  # past the frame edge is allowed
        for box in ([0, 0, 0, 5], [0, 5, 5, 5], [0, 0, math.nan, 5], [0, 0, math.inf, 5], [0, 0, 5]):
            with self.subTest(box=box):
                self.assertFalse(detectors.valid_box(box))

    def test_iou(self) -> None:
        self.assertEqual(detectors.box_iou([0, 0, 10, 10], [0, 0, 10, 10]), 1.0)
        self.assertEqual(detectors.box_iou([0, 0, 10, 10], [20, 20, 30, 30]), 0.0)
        self.assertAlmostEqual(detectors.box_iou([0, 0, 10, 10], [5, 0, 15, 10]), 1 / 3)


class DedupeTests(unittest.TestCase):
    def test_keeps_best_of_overlapping_and_drops_invalid(self) -> None:
        boxes = [[0, 0, 10, 10], [0, 0, 10, 10.5], [50, 50, 60, 60], [5, 5, 5, 9], [70, 70, 80, 80]]
        scores = [0.4, 0.6, 0.5, 0.9, math.nan]
        self.assertEqual(detectors.dedupe(boxes, scores), [1, 2])

    def test_order_does_not_depend_on_input_order(self) -> None:
        boxes = [[0, 0, 10, 10], [50, 50, 60, 60]]
        self.assertEqual(detectors.dedupe(boxes, [0.5, 0.5]), [0, 1])
        self.assertEqual(detectors.dedupe(list(reversed(boxes)), [0.5, 0.5]), [1, 0])

    def test_caps_candidates(self) -> None:
        boxes = [[i * 20.0, 0, i * 20.0 + 10, 10] for i in range(detectors.MAX_CANDIDATES + 5)]
        self.assertEqual(len(detectors.dedupe(boxes, [0.5] * len(boxes))), detectors.MAX_CANDIDATES)


if __name__ == "__main__":
    unittest.main()
