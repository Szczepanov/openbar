import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import box_diagnostics as bd


class BoxDiagnosticTests(unittest.TestCase):
    def test_decomposition_rounding_loss_and_input_preservation(self):
        rows = [{'timestamp_s': 0, 'state': 'tracked', 'center_px': {'x_px': 9.6, 'y_px': 12.4}, 'confidence': 1},
                {'timestamp_s': .03, 'state': 'tracked', 'center_px': {'x_px': 15.5, 'y_px': 11.5}, 'confidence': .9},
                {'timestamp_s': .06, 'state': 'lost'}]
        boxes = [(5, 8, 10, 10), (9, 6, 14, 12), None]
        shifts = [(0, 0), (4, -2), (8, -4)]
        before = copy.deepcopy((rows, boxes, shifts))
        result = bd.decompose(boxes, rows, shifts, boxes[0])
        self.assertEqual(result[1]['top_left_residual_px'], (0, 0))
        self.assertEqual(result[1]['half_size_change_px'], (2, 1))
        self.assertAlmostEqual(result[1]['error_xy_px'][0], 1.9)
        self.assertAlmostEqual(result[1]['error_xy_px'][1], 1.1)
        self.assertEqual(result[2], {'frame_index': 2, 'timestamp_s': .06, 'state': 'lost'})
        self.assertEqual((rows, boxes, shifts), before)
        boxes[2] = (1, 2, 3, 4)
        with self.assertRaises(ValueError):
            bd.decompose(boxes, rows, shifts, boxes[0])

    def test_nonfinite_or_inconsistent_conversion_refused(self):
        rows = [{'timestamp_s': i*.03, 'state': 'tracked', 'center_px': {'x_px': 4.5, 'y_px': 4.5}, 'confidence': .9}
                for i in range(2)]
        for bad in (float('nan'), float('inf'), -float('inf'), True):
            with self.assertRaises(ValueError):
                bd.decompose([(0, 0, 10, 10), (bad, 0, 10, 10)], rows, [(0, 0)]*2, (0, 0, 10, 10))
        with self.assertRaises(ValueError):
            bd.decompose([(0, 0, 10, 10), (1, 0, 10, 10)], rows, [(0, 0)]*2, (0, 0, 10, 10))


if __name__ == '__main__':
    unittest.main()
