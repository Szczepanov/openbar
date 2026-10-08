"""Frozen retained-input suggestion evaluation: stdlib, no new media or labels."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import session_fakes as fakes
import session_ingest
import evaluate_suggestions as evaluation


class EvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sessions = self.root / 'validation/private/vbt/sessions'
        self.directory = self.sessions / 'retained'
        self.directory.mkdir(parents=True)
        self.media = self.root / 'validation/private/vbt/media/source.mp4'
        self.media.parent.mkdir(parents=True)
        self.media.write_bytes(b'owner source bytes, not real video')
        self.clip = fakes.clip(sha=hashlib.sha256(self.media.read_bytes()).hexdigest())
        self.clip['media_path'] = self.media.relative_to(self.root).as_posix()
        self.state = {**fakes.session([self.clip], 'retained'),
                      'format': session_ingest.SESSION_STATE_FORMAT, 'format_version': 1,
                      'workflow_version': 'vbt-workflow-3', 'template_sha256': 'cd' * 32,
                      'suggester_environment': {'opencv_version': '4.12.0', 'numpy_version': '2.2.6'}}
        self.rows = [fakes.accepted_row(self.clip, self.state)]
        self.manifest = self.root / 'validation/private/vbt/manifest.json'
        self.manifest_doc = json.loads((evaluation.workflow.ROOT / 'validation/fixtures/public/manifest.json').read_text())
        self.manifest_doc['fixtures'] = self.manifest_doc['fixtures'][:1]
        self.manifest_doc['fixtures'][0]['id'] = self.clip['fixture_id']
        self.manifest.write_text(json.dumps(self.manifest_doc))

    def save(self) -> None:
        self.state['page_id'] = session_ingest.compute_page_id(self.state)
        for row in self.rows:
            row['page_id'] = self.state['page_id']
        state_path = self.directory / 'session.json'
        csv_path = self.directory / 'session-input.csv'
        state_path.write_text(json.dumps(self.state), encoding='utf-8')
        csv_path.write_bytes(fakes.csv_bytes(self.rows))
        decisions = evaluation.session_contract.parse_session_csv(csv_path.read_bytes(), self.state)
        prototype = self.manifest_doc['fixtures'][0]
        self.manifest_doc['fixtures'] = [{**prototype, 'id': c['fixture_id'],
            'media': {'repository_path': c['media_path'], 'sha256': c['sha256']}} for c in self.state['clips']]
        self.manifest.write_text(json.dumps(self.manifest_doc))
        record = {'format': 'openbar-research-vbt-session-record', 'format_version': 1,
                  'workflow_version': 'vbt-workflow-3', 'session_id': 'retained',
                  'page_id': self.state['page_id'], 'inputs': {
                      name: {'path': path.relative_to(self.root).as_posix(),
                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                      for name, path in [('session_state', state_path), ('session_csv', csv_path)]},
                  'clips': [], 'skipped': []}
        record['inputs']['manifest'] = {'path': self.manifest.relative_to(self.root).as_posix()}
        for clip, decision in zip(self.state['clips'], decisions):
            if decision['decision'] == 'skipped':
                record['skipped'].append(clip['fixture_id'])
            else:
                record['clips'].append({'fixture_id': clip['fixture_id'], 'decision': 'confirmed',
                    'exercise': decision['exercise'], 'item_statuses': decision['statuses'],
                    'video': {'path': clip['media_path'], 'sha256': clip['sha256']},
                    'suggestion_ids': {k: clip['suggestions'][k].get('id') for k in ('plate', 'stick')}})
        (self.directory / 'session-record.json').write_text(json.dumps(record), encoding='utf-8')

    def report(self) -> dict:
        return evaluation.evaluate_session(self.root, self.directory)

    def test_accepted_zero_separate_from_adjusted_and_identity_unknown(self) -> None:
        self.rows[0].update(plate_center_x_px='403.50', plate_center_y_px='1504.00',
                            plate_center_status='adjusted', plate_radius_px='200.00',
                            plate_radius_status='adjusted')
        self.save()
        result = self.report()
        center = result['clips'][0]['items']['plate_center']
        radius = result['clips'][0]['items']['plate_radius']
        self.assertEqual(center['discrepancy_px'], 5.0)
        self.assertEqual(radius['signed_discrepancy_px'], -19.75)
        self.assertEqual(radius['signed_relative_percent'], -9.875)
        self.assertEqual(center['target_identity'], 'not_derivable')
        summary = evaluation.summarize([result])
        self.assertEqual(summary['items']['plate_center']['discrepancy_px']['adjusted']['mean'], 5.0)
        self.assertEqual(summary['items']['plate_center']['discrepancy_px']['accepted']['n'], 0)
        self.assertEqual(summary['items']['stick_low']['discrepancy_px']['accepted']['max'], 0.0)
        self.assertIsNone(summary['items']['plate_center']['target_mismatch_count'])
        self.assertEqual(summary['assessment_counts']['unknown'], 1)

    def test_failed_proposals_manual_and_skipped_remain_in_denominator(self) -> None:
        self.clip['suggestions'] = fakes.suggestions(fakes.FAILED_PLATE, fakes.FAILED_STICK)
        other = fakes.clip(1, self.clip['sha256'])
        other['fixture_id'] += '-other-frame'
        other['media_path'] = self.clip['media_path']
        self.state['clips'].append(other)
        self.rows = [fakes.accepted_row(self.clip, self.state), fakes.skipped_row(other, self.state)]
        self.save()
        summary = evaluation.summarize([self.report()])
        center = summary['items']['plate_center']
        self.assertEqual(center['inventory_n'], 2)
        self.assertEqual(center['statuses'], {'accepted': 0, 'adjusted': 0, 'manual': 1, 'skipped': 1,
                                              'unavailable_confirmation': 0})
        self.assertEqual(center['suggestions'], {'suggested': 1, 'no_suggestion': 1, 'not_derivable': 0})
        self.assertEqual(center['discrepancy_px']['manual']['n'], 0)
        self.assertIsNone(center['rejected_suggestion_count'])

    def test_missing_confirmation_and_changed_source_are_explicit_exclusions(self) -> None:
        self.save()
        self.media.write_bytes(b'changed')
        result = self.report()
        self.assertIn('source_hash_mismatch', result['clips'][0]['exclusions'])
        summary = evaluation.summarize([result])
        self.assertEqual(summary['items']['plate_center']['excluded_n'], 1)
        self.assertEqual(summary['items']['plate_center']['discrepancy_px']['accepted']['n'], 0)
        (self.directory / 'session-input.csv').unlink()
        result = self.report()
        self.assertIn('confirmation_missing', result['exclusions'])
        self.assertEqual(evaluation.summarize([result])['items']['plate_center']['inventory_n'], 1)

    def test_wrong_suggestion_id_is_excluded_not_object_mismatch(self) -> None:
        self.save()
        path = self.directory / 'session-input.csv'
        path.write_bytes(path.read_bytes().replace(b'plate-hough-edge-v1:111111111111', b'wrong-object-id'))
        result = self.report()
        self.assertIn('confirmation_invalid', result['exclusions'])
        self.assertIsNone(evaluation.summarize([result])['items']['plate_center']['target_mismatch_count'])

    def test_missing_suggestion_record_is_not_derivable(self) -> None:
        self.clip['suggestions']['plate'] = {}
        self.rows = [fakes.accepted_row(self.clip, self.state)]
        self.save()
        summary = evaluation.summarize([self.report()])
        self.assertEqual(summary['items']['plate_radius']['suggestions']['not_derivable'], 1)

    def test_non_development_and_escaping_paths_are_excluded(self) -> None:
        self.save()
        self.manifest_doc['fixtures'][0]['purpose'] = 'validation'
        self.manifest.write_text(json.dumps(self.manifest_doc))
        self.assertIn('not_development', self.report()['clips'][0]['exclusions'])
        self.clip['media_path'] = '../outside.mp4'
        self.save()
        self.assertIn('source_binding_invalid', self.report()['clips'][0]['exclusions'])

    def test_deterministic_output_and_no_paths_or_original_names(self) -> None:
        self.save()
        before = {p: p.read_bytes() for p in self.directory.iterdir()}
        first = evaluation.serialize(self.report())
        self.assertEqual(first, evaluation.serialize(self.report()))
        self.assertNotIn('VID_', first)
        self.assertNotIn(str(self.root), first)
        self.assertEqual(before, {p: p.read_bytes() for p in self.directory.iterdir()})

    def test_empty_statistics_and_nonfinite_values(self) -> None:
        self.assertEqual(evaluation.statistics([]), {'n': 0, 'mean': None, 'median': None, 'min': None, 'max': None})
        with self.assertRaises(ValueError):
            evaluation.statistics([math.nan])

    def test_rejected_assessment_and_wrong_source_binding(self) -> None:
        self.save()
        path = self.directory / 'session-record.json'
        record = json.loads(path.read_text())
        bindings = {'video': record['clips'][0]['video']}
        for name in ('seed', 'run_record'):
            source = self.directory / (name + '.json')
            source.write_bytes(b'bound original evidence')
            bindings[name] = {'path': source.relative_to(self.root).as_posix(),
                              'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
        record['clips'][0].update(seed=bindings['seed'], analyze_lift={'run_record': bindings['run_record']})
        path.write_text(json.dumps(record))
        assessment = json.loads((evaluation.workflow.ROOT / 'validation/examples/vbt-clip-assessment.example.json').read_text())
        assessment['fixture_id'] = self.clip['fixture_id']
        assessment['sources'].update(bindings)
        assessment['mechanical']['status'] = 'invalid'
        assessment['mechanical']['checks']['source_binding'] = {'status': 'invalid', 'reasons': ['source_hash_mismatch']}
        assessment['experiment_suitability'] = {'status': 'rejected', 'reasons': ['mechanical_invalid']}
        assessments = self.directory / 'assessments'
        assessments.mkdir()
        item = assessments / (self.clip['fixture_id'] + '.assessment-v1.json')
        item.write_text(json.dumps(assessment))
        result = evaluation.evaluate_session(self.root, self.directory, assessments)
        self.assertEqual(result['clips'][0]['assessment']['mechanical'], 'invalid')
        self.assertIn('assessment_rejected', result['clips'][0]['exclusions'])
        self.assertEqual(evaluation.summarize([result])['items']['plate_center']['discrepancy_px']['accepted']['n'], 0)
        assessment['sources']['video']['sha256'] = 'ab' * 32
        item.write_text(json.dumps(assessment))
        result = evaluation.evaluate_session(self.root, self.directory, assessments)
        self.assertIn('assessment_binding_invalid', result['clips'][0]['exclusions'])

    def test_cli_refuses_overwrite_and_short_freeze_hash(self) -> None:
        self.save()
        output = self.root / 'target/report.json'
        with mock.patch.object(evaluation.workflow, 'ROOT', self.root), mock.patch.object(
                evaluation, 'frozen_rules', return_value='cd' * 32):
            args = ['--data-root', str(self.root), '--sessions-root', str(self.sessions),
                    '--rules-commit', 'ab' * 20, '--output', str(output)]
            self.assertEqual(evaluation.main(args), 0)
            original = output.read_bytes()
            self.assertEqual(evaluation.main(args), 1)
            self.assertEqual(output.read_bytes(), original)
        with self.assertRaises(ValueError):
            evaluation.frozen_rules('abc123')

    def test_development_manifest_must_bind_actual_source(self) -> None:
        self.save()
        self.manifest_doc['fixtures'][0]['media']['sha256'] = 'ab' * 32
        self.manifest.write_text(json.dumps(self.manifest_doc))
        result = self.report()
        self.assertIn('manifest_source_binding_invalid', result['clips'][0]['exclusions'])
        self.assertEqual(evaluation.summarize([result])['items']['plate_center']['discrepancy_px']['accepted']['n'], 0)

    def test_unsupported_version_preserves_known_inventory(self) -> None:
        self.save()
        state_path = self.directory / 'session.json'
        state = json.loads(state_path.read_text())
        state['format_version'] = 2
        state_path.write_text(json.dumps(state))
        result = self.report()
        self.assertIn('session_evidence_invalid', result['exclusions'])
        self.assertEqual(evaluation.summarize([result])['items']['plate_center']['inventory_n'], 1)

    def test_out_of_range_suggestion_confidence_excludes_clip(self) -> None:
        self.clip['suggestions']['plate']['confidence'] = 2.0
        self.save()
        result = self.report()
        self.assertIn('suggestion_provenance_invalid', result['clips'][0]['exclusions'])
        self.assertEqual(evaluation.summarize([result])['items']['plate_center']['discrepancy_px']['accepted']['n'], 0)

    def test_arbitrary_private_provenance_is_hashed_not_copied(self) -> None:
        self.state['suggester_environment']['private_path'] = 'C:/Users/person/original-video.mp4'
        self.clip['suggestions']['plate']['parameters'] = {'file': 'C:/Users/person/original-video.mp4'}
        self.save()
        text = evaluation.serialize(self.report())
        self.assertNotIn('original-video.mp4', text)
        self.assertNotIn('private_path', text)
        self.assertIn('parameters_sha256', text)

    def test_unrecognized_suggestion_is_not_derivable_without_aborting(self) -> None:
        self.save()
        path = self.directory / 'session.json'
        state = json.loads(path.read_text())
        state['clips'][0]['suggestions']['plate'] = None
        path.write_text(json.dumps(state))
        result = self.report()
        self.assertIn('session_evidence_invalid', result['exclusions'])
        self.assertEqual(evaluation.summarize([result])['items']['plate_center']['suggestions']['not_derivable'], 1)


if __name__ == '__main__':
    unittest.main()
