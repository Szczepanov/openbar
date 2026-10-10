"""#79 S1-SQ-1 hand-check: synthetic temp-dir data only; bridge and git checker are injected."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

WORKFLOW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKFLOW_DIR))

import agreement79_handcheck as handcheck  # noqa: E402
import agreement79_study as study  # noqa: E402

# (timestamp_s, y_m, confidence): dyadic values so every velocity is exact in binary.
# Index 5 follows a 0.25 s gap (> max_gap_s 0.2); index 9 has confidence 0.3 (< 0.5), so 9 and 10 are null.
SAMPLES = (
    (0.0, 0.0, 1.0), (0.125, 0.0625, 1.0), (0.25, 0.1875, 1.0), (0.375, 0.28125, 1.0), (0.5, 0.25, 1.0),
    (0.75, 0.25, 1.0), (0.875, 0.3125, 1.0), (1.0, 0.4375, 1.0), (1.125, 0.53125, 1.0), (1.25, 0.5, 0.3),
    (1.375, 0.5, 1.0), (1.5, 0.5625, 1.0), (1.625, 0.71875, 1.0), (1.75, 0.8125, 1.0), (1.875, 0.78125, 1.0),
)
VY = (None, 0.5, 1.0, 0.75, -0.25, None, 0.5, 1.0, 0.75, None, None, 0.5, 1.25, 0.75, -0.25)
PARSER_REPS = (
    {'startFrame': 2, 'endFrame': 4, 'startTimeS': 0.125, 'endTimeS': 0.375,
     'meanVelocityMps': 0.75, 'peakVelocityMps': 1.0, 'romCm': 21.88},
    {'startFrame': 7, 'endFrame': 9, 'startTimeS': 0.875, 'endTimeS': 1.125,
     'meanVelocityMps': 0.75, 'peakVelocityMps': 1.0, 'romCm': 21.88},
    {'startFrame': 12, 'endFrame': 14, 'startTimeS': 1.5, 'endTimeS': 1.75,
     'meanVelocityMps': 0.833, 'peakVelocityMps': 1.25, 'romCm': 25.0},
)
METRICS = ('meanVelocityMps', 'peakVelocityMps', 'romCm')


def build_analysis(min_confidence: float = 0.5, kinematics_input: str = 'filtered') -> dict:
    filtered = [{'timestamp_s': t, 'x_m': 0.0, 'y_m': y, 'confidence': c} for t, y, c in SAMPLES]
    kinematics = []
    for index, ((t, y, c), vy) in enumerate(zip(SAMPLES, VY)):
        confidence = c if index == 0 else min(c, SAMPLES[index - 1][2])
        kinematics.append({'timestamp_s': t, 'x_m': 0.0, 'y_m': y, 'vx_mps': None if vy is None else 0.0,
                           'vy_mps': vy, 'confidence': confidence})
    return {'schema_version': 1, 'derived': {
        'filtered': {'filter': {'implementation': 'savitzky-golay', 'version': '1'}, 'samples': filtered},
        'kinematics': {'input': kinematics_input, 'samples': kinematics, 'method': {
            'implementation': 'backward-difference', 'version': '1',
            'parameters': {'max_gap_s': 0.2, 'min_confidence': min_confidence}}}}}


def parser_output(reps=PARSER_REPS) -> dict:
    full = []
    for index, rep in enumerate(reps):
        full.append({'index': index, 'frameCount': rep['endFrame'] - rep['startFrame'] + 1,
                     'durationS': study.js_round(rep['endTimeS'] - rep['startTimeS'], 1000),
                     'complete': True, 'exclusion': None, **rep})
    return {'parserVersion': study.OPENBAR_PARSER, 'segmentationRule': study.SEGMENTATION,
            'frameCount': 11, 'breaks': [], 'reps': full}


def build_report(analysis_sha: str, reps=PARSER_REPS) -> dict:
    paired = []
    for index, rep in enumerate(reps):
        row = {'label': handcheck.SLOT, 'wlIndex': index, 'openBarIndex': index, 'temporalIoU': 0.9}
        for metric in METRICS:
            row[metric] = {'wl': rep[metric] + 0.01, 'openBar': rep[metric], 'difference': -0.01, 'magnitude': 1}
        paired.append(row)
    return {'schemaVersion': study.REPORT_SCHEMA, 'segmentationRule': study.SEGMENTATION,
            'wlParserVersion': study.WL_PARSER, 'openBarParserVersion': study.OPENBAR_PARSER,
            'minOverlap': study.MIN_OVERLAP, 'videos': [
                {'label': 'S1-SQ-2', 'openBar': {'fileSha256': 'f' * 64}, 'paired': []},
                {'label': handcheck.SLOT, 'openBar': {'fileSha256': analysis_sha}, 'paired': paired}]}


class Fixture:
    """A fake repository root with an inventory, analysis and report under validation/private."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.study_dir = root / 'validation/private/vbt/study-79'
        self.analysis_path = root / 'validation/private/vbt/sessions/s1/vbt-a.opencv-csrt.analysis.json'
        self.inventory_path = self.study_dir / 'inventory.json'
        self.report_path = self.study_dir / 'runs/report-back_squat-run1.json'
        self.output = self.study_dir / 'handcheck.json'
        self.analysis = build_analysis()
        self.analyzable = True
        self.report = None
        self.parser = parser_output()
        self.git = (study.CONSUMER_COMMIT, '')
        self.bridge_calls = 0

    def write(self) -> None:
        self.analysis_path.parent.mkdir(parents=True, exist_ok=True)
        self.analysis_path.write_bytes(study.serialize(self.analysis))
        sha = study.file_sha256(self.analysis_path)
        slot = {'slot': handcheck.SLOT, 'analyzable': self.analyzable, 'files': {'analysis': {
            'path': self.analysis_path.relative_to(self.root).as_posix(), 'sha256': sha}}}
        inventory = {'format': handcheck.INVENTORY_FORMAT, 'format_version': 1, 'study_id': study.STUDY_ID,
                     'slots': [slot, {'slot': 'S1-SQ-2', 'analyzable': False, 'files': {}}]}
        self.inventory_path.parent.mkdir(parents=True, exist_ok=True)
        self.inventory_path.write_bytes(study.serialize(inventory))
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        report = self.report if self.report is not None else build_report(sha)
        self.report_path.write_bytes(study.serialize(report))

    def bridge(self, app: Path, analysis: Path) -> dict:
        self.bridge_calls += 1
        return copy.deepcopy(self.parser)

    def run(self, pairing: str = 'confirmed', seed: str = 'confirmed', output: Path | None = None) -> int:
        argv = ['--inventory', str(self.inventory_path), '--consumer-app', str(self.root / 'consumer/app'),
                '--report', str(self.report_path), '--pairing-review', pairing,
                '--seed-reference-review', seed, '--output', str(output or self.output)]
        return handcheck.main(argv, root=self.root, bridge=self.bridge, git=lambda app: self.git)

    def result(self, output: Path | None = None) -> dict:
        return json.loads((output or self.output).read_text(encoding='utf-8'))


class HandcheckTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fx = Fixture(Path(tmp.name).resolve())

    def run_result(self, **kwargs) -> dict:
        self.fx.write()
        self.assertEqual(self.fx.run(**kwargs), 0)
        return self.fx.result()

    def assert_failed(self, result: dict, *reasons: str) -> None:
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['public']['status'], 'failed')
        for reason in reasons:
            self.assertIn(reason, result['reasons'])

    def test_three_paired_reps_pass(self) -> None:
        result = self.run_result()
        self.assertEqual((result['status'], result['reasons']), ('passed', []))
        self.assertEqual(result['format'], handcheck.FORMAT)
        self.assertEqual(result['format_version'], 1)
        self.assertEqual(result['slot'], 'S1-SQ-1')
        self.assertEqual(result['consumer_commit'], study.CONSUMER_COMMIT)
        self.assertEqual(result['velocity_check'], {
            'samples': 15, 'null_count': 4, 'compared': 11, 'null_pattern_mismatches': 0,
            'max_abs_discrepancy_mps': 0.0, 'passed': True})
        self.assertEqual(result['public'], {'status': 'passed', 'reasons': [], 'values_compared': 36,
                                            'values_matched': 36, 'max_abs_discrepancy_mps': 0.0})
        self.assertEqual(len(result['reps']), 3)
        last = result['reps'][2]
        self.assertEqual(last['window'], {'start_frame': 12, 'end_frame': 14, 'start_time_s': 1.5,
                                          'end_time_s': 1.75, 'sample_count': 3})
        expected = {'meanVelocityMps': 0.833, 'peakVelocityMps': 1.25, 'romCm': 25.0}
        self.assertEqual(last['recomputed'], {'recomputed_vy': expected, 'analysis_vy': expected})
        self.assertEqual((last['openBarIndex'], last['wlIndex'], last['parser'], last['report']),
                         (2, 2, expected, expected))
        self.assertTrue(all(all(m.values()) for source in last['matches'].values() for m in source.values()))
        inputs = result['inputs']
        self.assertEqual(inputs['analysis']['path'], 'validation/private/vbt/sessions/s1/vbt-a.opencv-csrt.analysis.json')
        self.assertEqual(inputs['report']['sha256'], study.file_sha256(self.fx.report_path))
        self.assertEqual(inputs['inventory']['sha256'], study.file_sha256(self.fx.inventory_path))
        self.assertEqual(self.fx.bridge_calls, 1)

    def test_rom_tie_rounds_up_like_math_round(self) -> None:
        result = self.run_result()
        self.assertEqual(result['reps'][0]['recomputed']['recomputed_vy']['romCm'], 21.88)

    def test_slot_not_analyzable_still_writes_failure(self) -> None:
        self.fx.analyzable = False
        result = self.run_result()
        self.assertEqual(result['reasons'], ['slot_not_analyzable'])
        self.assert_failed(result)
        self.assertEqual((result['reps'], result['velocity_check']), ([], None))
        self.assertEqual(self.fx.bridge_calls, 0)

    def test_review_flags_not_done_and_failed(self) -> None:
        result = self.run_result(pairing='not_done', seed='failed')
        self.assertEqual(result['reasons'], ['pairing_review_not_done', 'seed_reference_review_failed'])
        self.assertEqual(result['reviews'], {'pairing': 'not_done', 'seed_reference': 'failed'})
        self.assert_failed(result)
        self.assertEqual(result['public']['values_matched'], 36)

    def test_pairing_failed_and_seed_not_done(self) -> None:
        result = self.run_result(pairing='failed', seed='not_done')
        self.assertEqual(result['reasons'], ['pairing_review_failed', 'seed_reference_review_not_done'])

    def test_consumer_commit_mismatch_aborts_with_exit_3(self) -> None:
        self.fx.write()
        self.fx.git = ('0' * 40, '')
        self.assertEqual(self.fx.run(), 3)
        self.assertFalse(self.fx.output.exists())
        self.assertEqual(self.fx.bridge_calls, 0)

    def test_dirty_consumer_checkout_aborts_with_exit_3(self) -> None:
        self.fx.write()
        self.fx.git = (study.CONSUMER_COMMIT, ' M app/src/observations/openBarAnalysis.ts\n')
        self.assertEqual(self.fx.run(), 3)
        self.assertFalse(self.fx.output.exists())

    def test_bridge_failure_aborts_with_exit_3(self) -> None:
        self.fx.write()

        def broken(app: Path, analysis: Path) -> dict:
            raise handcheck.InfrastructureError('node not runnable')
        argv = ['--inventory', str(self.fx.inventory_path), '--consumer-app', str(self.fx.root),
                '--report', str(self.fx.report_path), '--pairing-review', 'confirmed',
                '--seed-reference-review', 'confirmed', '--output', str(self.fx.output)]
        self.assertEqual(handcheck.main(argv, root=self.fx.root, bridge=broken, git=lambda app: self.fx.git), 3)
        self.assertFalse(self.fx.output.exists())

    def test_malformed_bridge_output_aborts_with_exit_3(self) -> None:
        self.fx.parser = {'reps': 'not a list'}
        self.fx.write()
        self.assertEqual(self.fx.run(), 3)
        self.assertFalse(self.fx.output.exists())

    def test_existing_output_is_refused(self) -> None:
        self.fx.write()
        self.fx.output.write_bytes(b'retained')
        self.assertEqual(self.fx.run(), 1)
        self.assertEqual(self.fx.output.read_bytes(), b'retained')
        self.assertEqual(self.fx.bridge_calls, 0)

    def test_output_outside_private_tree_is_refused(self) -> None:
        self.fx.write()
        self.assertEqual(self.fx.run(output=self.fx.root / 'docs/handcheck.json'), 1)
        self.assertFalse((self.fx.root / 'docs/handcheck.json').exists())

    def test_same_inputs_give_identical_bytes(self) -> None:
        self.fx.write()
        second = self.fx.study_dir / 'handcheck-2.json'
        self.assertEqual(self.fx.run(), 0)
        self.assertEqual(self.fx.run(output=second), 0)
        self.assertEqual(self.fx.output.read_bytes(), second.read_bytes())
        self.assertTrue(self.fx.output.read_bytes().endswith(b'}\n'))
        self.assertNotIn(b'\r', self.fx.output.read_bytes())

    def test_invalid_inventory_exits_1(self) -> None:
        self.fx.write()
        self.fx.inventory_path.write_text('{"format": "something-else", "format_version": 1}', encoding='utf-8')
        self.assertEqual(self.fx.run(), 1)
        self.assertFalse(self.fx.output.exists())

    def test_inventory_without_slot_exits_1(self) -> None:
        self.fx.write()
        doc = json.loads(self.fx.inventory_path.read_text(encoding='utf-8'))
        doc['slots'] = doc['slots'][1:]
        self.fx.inventory_path.write_bytes(study.serialize(doc))
        self.assertEqual(self.fx.run(), 1)

    def test_null_pattern_mismatch(self) -> None:
        self.fx.analysis['derived']['kinematics']['samples'][5]['vy_mps'] = 0.0
        result = self.run_result()
        self.assert_failed(result, 'velocity_null_pattern_mismatch')
        self.assertEqual(result['velocity_check']['null_pattern_mismatches'], 1)
        self.assertFalse(result['velocity_check']['passed'])

    def test_vy_discrepancy_above_tolerance_fails(self) -> None:
        self.fx.analysis['derived']['kinematics']['samples'][7]['vy_mps'] = 1.0 + 2e-9
        result = self.run_result()
        self.assert_failed(result, 'velocity_recompute_mismatch')
        self.assertAlmostEqual(result['velocity_check']['max_abs_discrepancy_mps'], 2e-9, delta=1e-15)
        self.assertNotIn('value_mismatch', result['reasons'])

    def test_vy_discrepancy_within_tolerance_passes(self) -> None:
        self.fx.analysis['derived']['kinematics']['samples'][7]['vy_mps'] = 1.0 + 5e-10
        result = self.run_result()
        self.assertEqual(result['status'], 'passed')
        self.assertGreater(result['public']['max_abs_discrepancy_mps'], 0.0)

    def test_kinematics_y_differs_from_filtered(self) -> None:
        self.fx.analysis['derived']['kinematics']['samples'][2]['y_m'] = 0.1875 + 1e-12
        self.assert_failed(self.run_result(), 'filtered_y_mismatch')

    def test_kinematics_sample_without_filtered_twin(self) -> None:
        self.fx.analysis['derived']['kinematics']['samples'][3]['timestamp_s'] = 0.376
        self.assert_failed(self.run_result(), 'filtered_sample_missing')

    def test_kinematics_input_must_be_filtered(self) -> None:
        self.fx.analysis = build_analysis(kinematics_input='calibrated')
        result = self.run_result()
        self.assert_failed(result, 'kinematics_input_not_filtered')
        self.assertIsNone(result['reps'][0]['recomputed']['recomputed_vy'])

    def test_window_containing_null(self) -> None:
        reps = [dict(rep) for rep in PARSER_REPS]
        reps[1] = {**reps[1], 'startFrame': 5, 'startTimeS': 0.5}
        self.fx.parser = parser_output(reps)
        result = self.run_result()
        self.assert_failed(result, 'window_contains_null')
        self.assertTrue(result['reps'][1]['window_contains_null'])
        self.assertIsNone(result['reps'][1]['recomputed']['analysis_vy'])

    def test_window_not_matching_parser_timestamps(self) -> None:
        reps = [dict(rep) for rep in PARSER_REPS]
        reps[0] = {**reps[0], 'startTimeS': 0.124}
        self.fx.parser = parser_output(reps)
        self.assert_failed(self.run_result(), 'window_mismatch')

    def test_report_value_mismatch(self) -> None:
        self.fx.write()
        report = build_report(study.file_sha256(self.fx.analysis_path))
        report['videos'][1]['paired'][0]['meanVelocityMps']['openBar'] = 0.751
        self.fx.report = report
        result = self.run_result()
        self.assert_failed(result, 'value_mismatch')
        matches = result['reps'][0]['matches']['recomputed_vy']
        self.assertEqual((matches['vs_parser']['meanVelocityMps'], matches['vs_report']['meanVelocityMps']),
                         (True, False))
        self.assertEqual(result['public']['values_matched'], 34)

    def test_parser_value_mismatch(self) -> None:
        reps = [dict(rep) for rep in PARSER_REPS]
        reps[2] = {**reps[2], 'romCm': 25.01}
        self.fx.parser = parser_output(reps)
        self.assert_failed(self.run_result(), 'value_mismatch')

    def test_parser_rep_missing_for_report_row(self) -> None:
        self.fx.parser = parser_output(PARSER_REPS[:2])
        self.assert_failed(self.run_result(), 'parser_rep_missing')

    def test_not_three_paired_reps(self) -> None:
        self.fx.write()
        report = build_report(study.file_sha256(self.fx.analysis_path))
        report['videos'][1]['paired'].pop()
        self.fx.report = report
        result = self.run_result()
        self.assertEqual(result['reasons'], ['not_three_paired_reps'])
        self.assertEqual(result['public']['values_compared'], 24)

    def test_analysis_hash_mismatch(self) -> None:
        self.fx.write()
        self.fx.analysis_path.write_bytes(self.fx.analysis_path.read_bytes() + b' ')
        self.assertEqual(self.fx.run(), 0)
        result = self.fx.result()
        self.assert_failed(result, 'analysis_hash_mismatch')
        self.assertEqual(self.fx.bridge_calls, 0)

    def test_analysis_missing(self) -> None:
        self.fx.write()
        self.fx.analysis_path.unlink()
        self.assertEqual(self.fx.run(), 0)
        self.assert_failed(self.fx.result(), 'analysis_missing')

    def test_report_contract_mismatch(self) -> None:
        self.fx.write()
        report = build_report(study.file_sha256(self.fx.analysis_path))
        report['minOverlap'] = 0.6
        report['segmentationRule'] = 'concentric-segmentation-v1'
        self.fx.report = report
        self.assert_failed(self.run_result(), 'report_contract_mismatch:minOverlap',
                           'report_contract_mismatch:segmentationRule')

    def test_report_bound_to_other_analysis(self) -> None:
        self.fx.report = build_report('e' * 64)
        self.assert_failed(self.run_result(), 'report_analysis_hash_mismatch')

    def test_report_without_slot_video(self) -> None:
        self.fx.write()
        report = build_report(study.file_sha256(self.fx.analysis_path))
        report['videos'].pop()
        self.fx.report = report
        self.assert_failed(self.run_result(), 'report_slot_missing', 'not_three_paired_reps')

    def test_parser_contract_mismatch(self) -> None:
        self.fx.parser = {**parser_output(), 'parserVersion': 'openbar-analysis-v1'}
        self.assert_failed(self.run_result(), 'parser_contract_mismatch')

    def test_public_object_is_aggregate_only(self) -> None:
        result = self.run_result()
        self.assertEqual(sorted(result['public']),
                         ['max_abs_discrepancy_mps', 'reasons', 'status', 'values_compared', 'values_matched'])

    def test_root_option_resolves_recorded_paths_in_the_data_checkout(self) -> None:
        self.fx.write()
        seen = []

        def bridge(app: Path, analysis: Path) -> dict:
            seen.append((app, analysis))
            return parser_output()
        argv = ['--root', str(self.fx.root), '--inventory', str(self.fx.inventory_path),
                '--consumer-app', 'consumer/app', '--report', str(self.fx.report_path),
                '--pairing-review', 'confirmed', '--seed-reference-review', 'confirmed', '--output', str(self.fx.output)]
        elsewhere = self.fx.root / 'not-the-data-root'
        self.assertEqual(handcheck.main(argv, root=elsewhere, bridge=bridge, git=lambda app: self.fx.git), 0)
        self.assertEqual(self.fx.result()['status'], 'passed')
        self.assertEqual(seen, [(Path('consumer/app'), self.fx.analysis_path)])  # CLI path kept as given

    def test_inventory_outside_data_root_exits_1(self) -> None:
        self.fx.write()
        argv = ['--root', str(self.fx.root / 'validation/private/vbt/sessions'), '--inventory', str(self.fx.inventory_path),
                '--consumer-app', 'app', '--report', str(self.fx.report_path), '--pairing-review', 'confirmed',
                '--seed-reference-review', 'confirmed', '--output', str(self.fx.output)]
        self.assertEqual(handcheck.main(argv, root=self.fx.root, bridge=self.fx.bridge,
                                        git=lambda app: self.fx.git), 1)
        self.assertFalse(self.fx.output.exists())

    def test_report_outside_data_root_exits_1(self) -> None:
        self.fx.write()
        outside = self.fx.root.parent / f'{self.fx.root.name}-report.json'
        outside.write_bytes(self.fx.report_path.read_bytes())
        self.addCleanup(outside.unlink)
        self.fx.report_path = outside
        self.assertEqual(self.fx.run(), 1)

    def test_non_canonical_recorded_analysis_path_exits_1(self) -> None:
        for spelling in ('./validation/private/x.json', 'validation\\private\\x.json', '../outside.json'):
            with self.subTest(spelling=spelling):
                self.fx.write()
                doc = json.loads(self.fx.inventory_path.read_text(encoding='utf-8'))
                doc['slots'][0]['files']['analysis']['path'] = spelling
                self.fx.inventory_path.write_bytes(study.serialize(doc))
                self.assertEqual(self.fx.run(), 1)
                self.assertFalse(self.fx.output.exists())

    def test_analyzable_slot_without_analysis_binding_exits_1(self) -> None:
        self.fx.write()
        doc = json.loads(self.fx.inventory_path.read_text(encoding='utf-8'))
        doc['slots'][0]['files'] = {}
        self.fx.inventory_path.write_bytes(study.serialize(doc))
        self.assertEqual(self.fx.run(), 1)

    def test_unreadable_report_exits_1(self) -> None:
        self.fx.write()
        self.fx.report_path.write_text('{"videos": [', encoding='utf-8')
        self.assertEqual(self.fx.run(), 1)
        self.assertEqual(self.fx.bridge_calls, 0)

    def test_malformed_paired_row_exits_1(self) -> None:
        self.fx.write()
        report = build_report(study.file_sha256(self.fx.analysis_path))
        report['videos'][1]['paired'][0]['romCm'] = {'openBar': None}
        self.fx.report = report
        self.fx.write()
        self.assertEqual(self.fx.run(), 1)
        self.assertFalse(self.fx.output.exists())

    def test_git_runner_failure_aborts_with_exit_3(self) -> None:
        self.fx.write()

        def git(app: Path) -> tuple[str, str]:
            raise handcheck.InfrastructureError('git not runnable')
        argv = ['--inventory', str(self.fx.inventory_path), '--consumer-app', 'app', '--report',
                str(self.fx.report_path), '--pairing-review', 'confirmed', '--seed-reference-review', 'confirmed',
                '--output', str(self.fx.output)]
        self.assertEqual(handcheck.main(argv, root=self.fx.root, bridge=self.fx.bridge, git=git), 3)
        self.assertFalse(self.fx.output.exists())

    def test_analysis_without_kinematics_parameters_is_recorded_invalid(self) -> None:
        del self.fx.analysis['derived']['kinematics']['method']['parameters']
        result = self.run_result()
        self.assert_failed(result, 'analysis_invalid')
        self.assertEqual(self.fx.bridge_calls, 0)

    def test_non_increasing_filtered_timestamps_are_recorded_invalid(self) -> None:
        self.fx.analysis['derived']['filtered']['samples'][4]['timestamp_s'] = 0.375
        self.assert_failed(self.run_result(), 'analysis_invalid')

    def test_analysis_without_filtered_output_cannot_be_recomputed(self) -> None:
        del self.fx.analysis['derived']['filtered']
        result = self.run_result()
        self.assert_failed(result, 'filtered_sample_missing')
        self.assertEqual(result['velocity_check']['max_abs_discrepancy_mps'], None)
        self.assertIsNone(result['reps'][0]['recomputed']['recomputed_vy'])

    def test_window_beyond_kinematics_samples(self) -> None:
        reps = [dict(rep) for rep in PARSER_REPS]
        reps[2] = {**reps[2], 'endFrame': 16, 'endTimeS': 2.0}
        self.fx.parser = parser_output(reps)
        result = self.run_result()
        self.assert_failed(result, 'window_out_of_range')
        self.assertIsNone(result['reps'][2]['window'])
        self.assertEqual(result['public']['values_matched'], 24)

    def test_zero_min_confidence_analysis_passes(self) -> None:
        analysis = build_analysis(min_confidence=0.0)
        samples = analysis['derived']['kinematics']['samples']
        samples[9]['vy_mps'], samples[10]['vy_mps'] = -0.25, 0.0
        self.fx.analysis = analysis
        result = self.run_result()
        self.assertEqual((result['status'], result['velocity_check']['null_count']), ('passed', 2))

    def test_low_confidence_null_kept_by_analysis_is_a_pattern_mismatch_when_threshold_is_zero(self) -> None:
        self.fx.analysis = build_analysis(min_confidence=0.0)
        result = self.run_result()
        self.assert_failed(result, 'velocity_null_pattern_mismatch')
        self.assertEqual(result['velocity_check']['null_pattern_mismatches'], 2)


class ArithmeticTests(unittest.TestCase):
    def test_js_round_ties_match_ecmascript(self) -> None:
        # Values observed from node: Math.round(x * k) / k.
        self.assertEqual(study.js_round(0.0005, 1000), 0.001)
        self.assertEqual(study.js_round(-0.0005, 1000), -0.0)  # JS yields -0; numerically equal to 0
        self.assertEqual(study.js_round(1.2345, 1000), 1.235)
        self.assertEqual(study.js_round(2.675, 100), 2.68)
        self.assertEqual(study.js_round(-2.5, 1), -2.0)
        self.assertEqual(study.js_round(0.49999999999999994, 1), 0.0)
        self.assertEqual(study.js_round(21.875, 100), 21.88)

    def test_recompute_honours_gap_and_confidence(self) -> None:
        filtered = [{'timestamp_s': t, 'y_m': y, 'confidence': c} for t, y, c in SAMPLES]
        self.assertEqual(handcheck.recompute_velocity(filtered, 0.2, 0.5), list(VY))

    def test_recompute_with_zero_min_confidence_keeps_low_confidence_pair(self) -> None:
        filtered = [{'timestamp_s': t, 'y_m': y, 'confidence': c} for t, y, c in SAMPLES]
        recomputed = handcheck.recompute_velocity(filtered, 0.2, 0.0)
        self.assertEqual((recomputed[9], recomputed[10]), (-0.25, 0.0))
        self.assertIsNone(recomputed[5])

    def test_recompute_gap_boundary_is_inclusive(self) -> None:
        filtered = [{'timestamp_s': 0.0, 'y_m': 0.0, 'confidence': 1.0},
                    {'timestamp_s': 0.25, 'y_m': 0.125, 'confidence': 1.0}]
        self.assertEqual(handcheck.recompute_velocity(filtered, 0.25, 0.0), [None, 0.5])

    def test_consumer_metrics_use_left_to_right_sum_and_relative_displacement(self) -> None:
        metrics = handcheck.consumer_metrics([0.5, 1.25, 0.75], 0.5625, 0.8125, 0.0)
        self.assertEqual(metrics, {'meanVelocityMps': 0.833, 'peakVelocityMps': 1.25, 'romCm': 25.0})

    def test_mean_is_uncompensated_left_to_right_like_js_reduce(self) -> None:
        # node: [1e16, 1, -1e16].reduce((s, v) => s + v, 0) / 3 === 0; a compensated sum would give 1/3.
        metrics = handcheck.consumer_metrics([1e16, 1.0, -1e16], 0.0, 0.0, 0.0)
        self.assertEqual(metrics['meanVelocityMps'], 0.0)

    def test_rom_is_difference_of_displacements_relative_to_first_sample(self) -> None:
        # (y_end - y0) * 100 - (y_start - y0) * 100, not (y_end - y_start) * 100.
        y0, y_start, y_end = 0.1, 0.3, 0.7
        expected = study.js_round((y_end - y0) * 100 - (y_start - y0) * 100, 100)
        self.assertEqual(handcheck.consumer_metrics([1.0], y_start, y_end, y0)['romCm'], expected)


class ProcessBoundaryTests(unittest.TestCase):
    """Default git and node runners, with subprocess replaced (no real processes)."""

    def completed(self, returncode: int, stdout: bytes = b'', stderr: bytes = b''):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def test_git_state_reads_the_app_parent_checkout(self) -> None:
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return self.completed(0, f'{study.CONSUMER_COMMIT}\n'.encode() if 'rev-parse' in argv else b'')
        with mock.patch.object(handcheck.subprocess, 'run', side_effect=run):
            self.assertEqual(handcheck.consumer_git_state(Path('c/app')), (study.CONSUMER_COMMIT, ''))
        self.assertEqual(calls[0][:3], ['git', '-C', str(Path('c/app') / '..')])
        self.assertIn('--untracked-files=no', calls[1])

    def test_git_state_failures_are_infrastructure(self) -> None:
        for effect in (OSError('no git'), self.completed(128)):
            with self.subTest(effect=effect):
                patch = (mock.patch.object(handcheck.subprocess, 'run', side_effect=effect)
                         if isinstance(effect, OSError) else
                         mock.patch.object(handcheck.subprocess, 'run', return_value=effect))
                with patch, self.assertRaises(handcheck.InfrastructureError):
                    handcheck.consumer_git_state(Path('app'))

    def test_bridge_runs_node_with_strip_types_and_parses_last_line(self) -> None:
        line = json.dumps(parser_output()).encode()
        with mock.patch.object(handcheck.subprocess, 'run', return_value=self.completed(0, b'noise\n' + line)) as run:
            self.assertEqual(handcheck.consumer_reps(Path('app'), Path('a.json')), parser_output())
        self.assertEqual(run.call_args.args[0], ['node', '--experimental-strip-types', str(handcheck.BRIDGE),
                                                 'app', 'a.json'])

    def test_bridge_failures_are_infrastructure(self) -> None:
        for effect in (OSError('no node'), self.completed(1, stderr=b'parse error\n'), self.completed(0, b'')):
            with self.subTest(effect=effect):
                patch = (mock.patch.object(handcheck.subprocess, 'run', side_effect=effect)
                         if isinstance(effect, OSError) else
                         mock.patch.object(handcheck.subprocess, 'run', return_value=effect))
                with patch, self.assertRaises(handcheck.InfrastructureError):
                    handcheck.consumer_reps(Path('app'), Path('a.json'))

    def test_bridge_script_imports_only_the_consumer_parser_modules(self) -> None:
        source = handcheck.BRIDGE.read_text(encoding='utf-8')
        self.assertIn("TextDecoder('utf-8', { fatal: true })", source)
        self.assertIn('parseOpenBarAnalysis(text, segmentation.CONCENTRIC_SEGMENTATION_V2)', source)
        self.assertNotIn('\r', source)


@unittest.skipUnless(os.environ.get('OPENBAR_CONSUMER_APP'), 'set OPENBAR_CONSUMER_APP to the pinned consumer app dir')
class ConsumerBridgeIntegrationTests(unittest.TestCase):
    """Runs the real pinned consumer parser through the bridge (node --experimental-strip-types)."""

    def test_real_parser_reps_match_independent_recomputation(self) -> None:
        app = Path(os.environ['OPENBAR_CONSUMER_APP']).resolve()
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(Path(tmp).resolve())
            fx.analysis = consumer_analysis()
            fx.write()
            parsed = handcheck.consumer_reps(app, fx.analysis_path)
            self.assertEqual((parsed['parserVersion'], parsed['segmentationRule']),
                             (study.OPENBAR_PARSER, study.SEGMENTATION))
            self.assertEqual(len(parsed['reps']), 3)
            self.assertEqual([item['reason'] for item in parsed['breaks']], ['null_velocity', 'null_velocity'])
            self.assertEqual([rep['exclusion'] for rep in parsed['reps']], [None, None, None])
            self.assertGreater(parsed['reps'][2]['startFrame'], 118)  # ordinal, not usable-frame position
            sha = study.file_sha256(fx.analysis_path)
            reps = [{key: rep[key] for key in ('startFrame', 'endFrame', 'startTimeS', 'endTimeS', *METRICS)}
                    for rep in parsed['reps']]
            fx.report = build_report(sha, reps)
            fx.write()
            argv = ['--inventory', str(fx.inventory_path), '--consumer-app', str(app),
                    '--report', str(fx.report_path), '--pairing-review', 'confirmed',
                    '--seed-reference-review', 'confirmed', '--output', str(fx.output)]
            git = lambda path: (study.CONSUMER_COMMIT, '')  # noqa: E731 - checkout state is not under test
            self.assertEqual(handcheck.main(argv, root=fx.root, git=git), 0)
            result = fx.result()
            self.assertEqual((result['status'], result['reasons']), ('passed', []), result['reasons'])
            self.assertEqual((result['public']['values_compared'], result['public']['values_matched']), (36, 36))
            self.assertEqual(result['velocity_check']['null_count'], 4)


def consumer_analysis() -> dict:
    """Synthetic analysis-v1 subset the consumer parser accepts (shape of its own fixture builder).

    Three squat-like cycles at 30 fps. The second descent holds a 0.25 s timestamp gap (index 65) and a
    0.3-confidence sample (index 70), so the kinematics carry nulls at 0, 65, 70 and 71 and later rep
    ordinals (kinematics sample index + 1) differ from positions among usable frames.
    """
    dt, gap_index, low_index = 1 / 30, 65, 70
    profile = []
    for _ in range(3):
        profile += [-0.6] * 20 + [0.0] * 3 + [0.6] * 25 + [0.025] * 7 + [0.0] * 4
    filtered, kinematics, t, y = [], [], 0.0, 0.0
    for index, velocity in enumerate(profile):
        if index > 0:
            t += dt + (0.25 if index == gap_index else 0.0)
            y += velocity * dt
        filtered.append({'timestamp_s': t, 'x_m': 0.0, 'y_m': y, 'confidence': 0.3 if index == low_index else 1.0})
    for index, sample in enumerate(filtered):
        vy, confidence = None, sample['confidence']
        if index:  # the OpenBar backward-difference rule, restated for the fixture
            previous = filtered[index - 1]
            step = sample['timestamp_s'] - previous['timestamp_s']
            confidence = min(confidence, previous['confidence'])
            if step <= 0.2 and confidence >= 0.5:
                vy = (sample['y_m'] - previous['y_m']) / step
        kinematics.append({**sample, 'vx_mps': None if vy is None else 0.0, 'vy_mps': vy, 'confidence': confidence})
    return {
        'schema_version': 1,
        'identity': {'source_id': 'synthetic-video', 'source_sha256': 'a' * 64},
        'calibration': {
            'method': 'plate_diameter', 'method_version': 1,
            'scale': {'diameter_m': 0.45, 'diameter_px': 200, 'metres_per_pixel': 0.00225},
            'coordinate_convention': 'reference_centre_x_right_y_up',
            'quality': {'status': 'unassessed', 'warnings': ['geometry_unassessed']}},
        'derived': {
            'filtered': {'filter': {'implementation': 'savitzky-golay', 'version': '1',
                                    'parameters': {'window': 9, 'order': 2}}, 'samples': filtered},
            'kinematics': {'input': 'filtered', 'samples': kinematics, 'method': {
                'implementation': 'backward-difference', 'version': '1',
                'parameters': {'max_gap_s': 0.2, 'min_confidence': 0.5}}}},
        'provenance': {
            'pipeline': {'openbar_version': '0.1.0', 'git_commit': '703c097'},
            'tracker': {'id': 'opencv-csrt', 'implementation': {
                'implementation': 'opencv-csrt', 'version': '1',
                'parameters': {'tracker': 'opencv-csrt', 'threads': 1}}}},
    }


if __name__ == '__main__':
    unittest.main()
