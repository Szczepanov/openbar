from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import controlled_motion as cm
import background_motion as bg


class ControlledMotionTests(unittest.TestCase):
    def test_paired_background_foreground_identity_and_no_ghost(self):
        source = np.full((300, 300, 3), 27, dtype=np.uint8)
        source[140:161, 140:161] = 240
        before = source.copy()
        target = (150, 150, 12)
        shifts = [(0, 0), (64, -32)]
        initial, background, layer, mask = bg.background_layers(source, target, shifts)
        np.testing.assert_array_equal(initial[mask], source[mask])
        np.testing.assert_array_equal(bg.target_on_background(background, layer, mask, (0, 0), target), initial)
        shift = shifts[1]
        moving = cm.transform(initial, shift, target, 'translation', 1)
        stationary = bg.target_on_background(background, layer, mask, shift, target)
        shifted_mask = cm.transform(mask, shift, target, 'translation', 1)
        np.testing.assert_array_equal(moving[shifted_mask], stationary[shifted_mask])
        np.testing.assert_array_equal(stationary[~shifted_mask], background[~shifted_mask])
        self.assertTrue((stationary[mask] == 128).all())
        corridor = mask | shifted_mask
        np.testing.assert_array_equal(background[~corridor], source[~corridor])
        np.testing.assert_array_equal(source, before)
        with self.assertRaises(ValueError):
            bg.background_layers(source.astype(float), target, shifts)
        with self.assertRaises(ValueError):
            bg.background_layers(source, (float('nan'), 150, 12), shifts)
        with self.assertRaises(ValueError):
            bg.background_layers(source, target, [(200, 0)])

    def rows(self):
        return [{'timestamp_s': round(i/30, 6), 'state': 'tracked',
                 'center_px': {'x_px': 100+dx, 'y_px': 100+dy}, 'confidence': .9}
                for i, (dx, dy) in enumerate(cm.program())]

    def test_nonwrapped_transform_target_margin_and_input_preservation(self):
        source = np.arange(120*140*3, dtype=np.uint8).reshape(120, 140, 3)
        before = source.copy()
        out = cm.transform(source, (4, -2), (70, 60, 20), 'translation', 1)
        np.testing.assert_array_equal(out[:-2, 4:], source[2:, :-4])
        self.assertFalse(out[-2:].any())
        self.assertFalse(out[:, :4].any())
        np.testing.assert_array_equal(source, before)
        np.testing.assert_array_equal(cm.transform(source, (0, 0), (70, 60, 20), 'translation', 0), source)
        occluded = cm.transform(source, (4, -2), (70, 60, 20), 'occlusion', 12)
        self.assertFalse(occluded[30:87, 46:103].any())
        with self.assertRaises(ValueError):
            cm.transform(source, (70, 0), (70, 60, 20), 'translation', 1)
        with self.assertRaises(ValueError):
            cm.transform(source, (.5, 0), (70, 60, 20), 'translation', 1)

    def test_exact_error_loss_recovery_confidence_and_common_support(self):
        rows = self.rows()
        times = [r['timestamp_s'] for r in rows]
        pristine = cm.score(rows, times, cm.program(), 'translation')
        self.assertEqual(pristine['displacement_error']['max_px'], 0)
        rows[12] = {'timestamp_s': times[12], 'state': 'lost'}
        rows[16]['center_px']['x_px'] += 5
        before = copy.deepcopy(rows)
        result = cm.score(rows, times, cm.program(), 'occlusion')
        self.assertEqual(rows, before)
        self.assertEqual(result['lost'], 1)
        self.assertEqual(result['high_confidence_wrong'], 1)
        self.assertEqual(result['wrong_confidence_range'], [.9, .9])
        self.assertEqual(result['adjacent_error']['count'], 30)
        self.assertEqual(result['recovery_three_frames']['frame_index'], 17)
        self.assertEqual(cm.common_support(pristine, result)['count'], 31)
        self.assertNotIn('error_px', result['frames'][12])
        rows[12]['center_px'] = {'x_px': 1, 'y_px': 1}
        with self.assertRaises(ValueError):
            cm.score(rows, times, cm.program(), 'occlusion')

    def test_finite_values_and_timestamp_binding_fail_closed(self):
        for bad in (float('nan'), float('inf'), -float('inf')):
            for field in ('confidence', 'timestamp_s'):
                rows = self.rows()
                rows[4][field] = bad
                with self.assertRaises(ValueError):
                    cm.score(rows, [round(i/30, 6) for i in range(33)], cm.program(), 'translation')
            rows = self.rows()
            rows[4]['center_px']['x_px'] = bad
            with self.assertRaises(ValueError):
                cm.score(rows, [round(i/30, 6) for i in range(33)], cm.program(), 'translation')
        times = [i/30 for i in range(33)]
        times[4] += .001
        with self.assertRaises(ValueError):
            cm.require_timestamps(times)
        rows = self.rows()
        rows.pop(5)
        with self.assertRaises(ValueError):
            cm.score(rows, [i/30 for i in range(33)], cm.program(), 'translation')


if __name__ == '__main__':
    unittest.main()
