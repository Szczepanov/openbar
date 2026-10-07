from __future__ import annotations

import copy
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import plate_scale_study as study


def observations():
    rows = [{'timestamp_s': i / 10, 'state': 'tracked',
             'center_px': {'x_px': 200.0, 'y_px': 200.0}, 'confidence': .9}
            for i in range(11)]
    geometry = [{'timestamp_s': 0.0, 'fit_attempted': False, 'accepted': True,
                 'reject_reasons': [], 'base_confidence': 1.0}]
    geometry += [{'timestamp_s': i / 10, 'fit_attempted': True, 'accepted': True,
                  'reject_reasons': [], 'base_confidence': .9, 'radius_px': 99.0 + i,
                  'inlier_count': 100, 'coverage_bins': 24} for i in range(1, 11)]
    return rows, geometry


class PlateScaleStudyTests(unittest.TestCase):
    def estimate(self, rows=None, geometry=None):
        original_rows, original_geometry = observations()
        return study.estimate(rows if rows is not None else original_rows,
                              geometry if geometry is not None else original_geometry,
                              seed_timestamp_s=0.0, seed_radius_px=100.0, diameter_m=.45)

    def test_known_scale_spread_reciprocal_band_time_trend_and_preservation(self):
        rows, geometry = observations()
        before = copy.deepcopy((rows, geometry))
        result = self.estimate(rows, geometry)
        self.assertEqual(result['status'], 'supported')
        self.assertEqual(result['support']['eligible'], 10)
        self.assertEqual(result['diameter']['median_px'], 209.0)
        self.assertEqual(result['diameter']['p10_px'], 200.0)
        self.assertEqual(result['diameter']['p90_px'], 216.0)
        self.assertAlmostEqual(result['diameter']['time_slope_px_per_s'], 20.0)
        self.assertAlmostEqual(result['scale_m_per_px'], .45 / 209)
        self.assertEqual(result['empirical_band_m_per_px'], [.45 / 216, .45 / 200])
        self.assertAlmostEqual(result['seed_radius_deviation_pct'], 100 * (100 / 104.5 - 1))
        self.assertEqual((rows, geometry), before)
        self.assertEqual(result, self.estimate(rows, geometry))

    def test_rejected_fallback_loss_and_low_support_never_supply_scale(self):
        rows, geometry = observations()
        rows[1] = {'timestamp_s': .1, 'state': 'lost'}
        geometry[1].update(accepted=False, reject_reasons=['mask_invalid_area'])
        geometry[2].update(accepted=False, reject_reasons=['coverage'])
        # Real GPU failure rows carry unavailable inlier/coverage values as null.
        geometry[2].update(radius_px=None, inlier_count=None, coverage_bins=None)
        geometry[3]['coverage_bins'] = 17
        result = self.estimate(rows, geometry)
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['support']['eligible'], 7)
        self.assertEqual(result['support']['lost'], 1)
        self.assertEqual(result['support']['fallback'], 1)
        self.assertEqual(result['support']['insufficient_geometry'], 1)
        self.assertIsNone(result['scale_m_per_px'])
        self.assertIsNone(result['empirical_band_m_per_px'])
        self.assertEqual(self.estimate(rows[:1], geometry[:1])['status'], 'unsupported')
        with self.assertRaises(ValueError):
            self.estimate([], [])
        # Count alone is insufficient: accepted support must cover half the timeline and 0.5 s.
        rows, geometry = observations()
        for i in range(11, 32):
            rows.append({'timestamp_s': i / 10, 'state': 'lost'})
            geometry.append({'timestamp_s': i / 10, 'fit_attempted': False, 'accepted': False,
                             'reject_reasons': ['frame_missing'], 'base_confidence': 0.0})
        self.assertIn('eligible_fraction_below_half', self.estimate(rows, geometry)['reasons'])
        rows, geometry = observations()
        for i in range(11):
            rows[i]['timestamp_s'] = geometry[i]['timestamp_s'] = i / 100
        self.assertIn('eligible_span_below_0_5_s', self.estimate(rows, geometry)['reasons'])

    def test_finite_loss_timestamp_and_geometry_validation(self):
        for field in ('radius_px', 'base_confidence', 'timestamp_s'):
            for bad in (math.nan, math.inf, -math.inf):
                rows, geometry = observations()
                geometry[4][field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    self.estimate(rows, geometry)
        for field, bad in (('fit_attempted', 1), ('accepted', 'true'),
                           ('coverage_bins', True), ('coverage_bins', 37),
                           ('inlier_count', -1), ('radius_px', 0),
                           ('base_confidence', 1.1), ('unknown', 1)):
            rows, geometry = observations()
            geometry[4][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                self.estimate(rows, geometry)
        rows, geometry = observations()
        geometry[4]['timestamp_s'] += .001
        with self.assertRaises(ValueError):
            self.estimate(rows, geometry)
        rows[4] = {'timestamp_s': .4, 'state': 'lost', 'confidence': .9}
        with self.assertRaises(ValueError):
            self.estimate(rows)
        for parameter in ('seed_radius_px', 'diameter_m'):
            with self.subTest(parameter=parameter), self.assertRaises(ValueError):
                study.estimate(*observations(), seed_timestamp_s=0.0,
                               **{parameter: 0, 'diameter_m' if parameter == 'seed_radius_px' else 'seed_radius_px': 1})
        rows, geometry = observations()
        for fit in geometry[1:]:
            fit['radius_px'] = 1e308
        with self.assertRaises((ValueError, OverflowError)):
            self.estimate(rows, geometry)

    def test_reference_interval_errors_band_coverage_and_decision(self):
        result = self.estimate()
        ref = {'reference_scale_m_per_px': .0022, 'reference_scale_lower_m_per_px': .00219,
               'reference_scale_upper_m_per_px': .00221}
        before = copy.deepcopy(ref)
        compared = study.compare(result, .0025, ref)
        self.assertEqual(ref, before)
        self.assertTrue(compared['band_contains_reference_interval'])
        self.assertTrue(compared['band_contains_reference_point'])
        self.assertTrue(compared['band_overlaps_reference_interval'])
        self.assertLess(compared['multi_absolute_relative_error_pct'], compared['seed_absolute_relative_error_pct'])
        self.assertLessEqual(compared['multi_signed_error_range_pct'][0], compared['multi_signed_relative_error_pct'])
        rows = [{'fixture_id': str(i), 'estimate': result, 'comparison': compared} for i in range(8)]
        self.assertEqual(study.decision(rows)['outcome'], 'retain_research')
        rows[-1] = copy.deepcopy(rows[-1])
        rows[-1]['comparison']['band_contains_reference_interval'] = False
        self.assertEqual(study.decision(rows)['outcome'], 'reject')
        rows[-1]['estimate']['status'] = 'unsupported'
        rows[-1]['comparison'] = study.compare(rows[-1]['estimate'], .0025, ref)
        self.assertIsNone(rows[-1]['comparison']['multi_absolute_relative_error_pct'])
        self.assertEqual(study.decision(rows)['supported'], 7)
        for bad in (0, math.nan, math.inf):
            with self.assertRaises(ValueError):
                study.compare(result, bad, ref)

    def test_identity_seed_provenance_and_authoritative_pts_binding(self):
        rows, geometry_rows = observations()
        item = {'id': 'case', 'video': {'width_px': 640, 'height_px': 480, 'rotation_deg': 0}}
        impl = {'name': 'sam2.1-bplus-circle', 'version': 'gpu-spike-3',
                'config': {'end_s': 1.0, 'seed_timestamp_s': 0.0}}
        prediction = {'implementation': impl, 'samples': rows}
        geometry = {'format': 'openbar-research-geometry-sidecar', 'format_version': 0,
                    'fixture_id': 'case', 'implementation': copy.deepcopy(impl), 'samples': geometry_rows}
        seed = {'timestamp_s': 0.0, 'frame_index': 0,
                'target': {'center': {'x_px': 200.0, 'y_px': 200.0}, 'radius_px': 100.0},
                'coordinate_space': 'display_top_left', 'source_rotation_deg': 0}
        analysis = {'manual_seed': seed, 'provenance': {'tracker': {'implementation': {
            'implementation': impl['name'], 'version': impl['version'],
            'parameters': {'prediction_sha256': 'sha'}}}}, 'raw_observations': [
                {'timestamp_s': r['timestamp_s'], 'frame_index': i, 'tracking_state': 'tracked',
                 'measurement': {'timestamp_s': r['timestamp_s'], **r['center_px'], 'confidence': r['confidence']}}
                for i, r in enumerate(rows)]}
        probed = {'width_px': 640, 'height_px': 480, 'rotation_deg': 0,
                  'timestamps_s': [r['timestamp_s'] for r in rows]}
        click = {'width_px': 640, 'height_px': 480, 'frame_index': 0, 'timestamp_s': 0.0}
        args = [item, prediction, geometry, analysis, {'seed': copy.deepcopy(seed)}, probed, click, 'sha']
        before = copy.deepcopy(args)
        study.bind(*args)
        self.assertEqual(args, before)
        mutations = [(2, ['fixture_id'], 'wrong'), (2, ['format_version'], True),
                     (2, ['implementation', 'version'], 'wrong'),
                     (3, ['provenance', 'tracker', 'implementation', 'parameters', 'prediction_sha256'], 'wrong'),
                     (4, ['seed', 'target', 'radius_px'], 101),
                     (5, ['rotation_deg'], 90), (6, ['timestamp_s'], .001),
                     (3, ['raw_observations', 4, 'measurement', 'x_px'], 201)]
        for index, keys, value in mutations:
            modified = copy.deepcopy(args)
            target = modified[index]
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
            with self.subTest(keys=keys), self.assertRaises((ValueError, study.label_package.PackageError)):
                study.bind(*modified)
        modified = copy.deepcopy(args)
        modified[5]['timestamps_s'][4] += .001
        with self.assertRaises(ValueError):
            study.bind(*modified)

    def test_frozen_report_determinism_preservation_and_failed_case_set(self):
        rows, geometry = observations()
        cases = [dict(fixture_id=i, prediction={'samples': rows, 'implementation': {}},
                      geometry={'samples': geometry}, seed={'timestamp_s': 0.0, 'target': {'radius_px': 100}},
                      diameter_m=.45, seed_scale_m_per_px=.0025,
                      reference={'reference_scale_m_per_px': .0022,
                                 'reference_scale_lower_m_per_px': .00219,
                                 'reference_scale_upper_m_per_px': .00221}) for i in study.FIXTURES]
        bundle = dict(format='openbar-research-plate-scale-freeze', format_version=1,
                      commit='test', inputs_sha256={}, cases=cases)
        before = copy.deepcopy(bundle)
        self.assertEqual(study.score(bundle), study.score(bundle))
        self.assertEqual(bundle, before)
        bundle['cases'][0]['fixture_id'] = 'held-out'
        with self.assertRaises(ValueError):
            study.score(bundle)

    def test_partial_write_cleanup_retry_and_existing_output_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unrelated = root / 'original.json'
            unrelated.write_bytes(b'original')
            output = root / 'report.json'

            def failed_write(path, value):
                path.write_bytes(b'{partial')
                raise OSError('injected write failure')

            with mock.patch.object(study.io, 'write', side_effect=failed_write), self.assertRaises(OSError):
                study.write_new(output, {'value': 1})
            self.assertEqual(list(root.iterdir()), [unrelated])
            study.write_new(output, {'value': 1})
            before = output.read_bytes()
            with self.assertRaises(ValueError):
                study.write_new(output, {'value': 2})
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(unrelated.read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
