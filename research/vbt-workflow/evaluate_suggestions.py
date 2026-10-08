#!/usr/bin/env python3
"""Evaluate retained #95 confirmations under frozen #112 rules; no decoding, writes or tuning."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import statistics as stats
import subprocess
import sys

import analyze_lift as workflow
import session_contract
import session_ingest

RULES = workflow.ROOT / 'docs/analysis/VBT_SUGGESTION_EVALUATION_RULES.md'
ASSESSMENT_SCHEMA = workflow.ROOT / 'validation/schema/vbt-clip-assessment-v1.schema.json'
FORMAT = 'openbar-research-vbt-suggestion-evaluation'
VERSION = 1
STATUSES = ('accepted', 'adjusted', 'manual')


def serialize(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n'


def statistics(values: list[float]) -> dict:
    if any(not math.isfinite(value) for value in values):
        raise ValueError('non-finite discrepancy')
    values = sorted(values)
    return {'n': len(values), 'mean': math.fsum(values) / len(values) if values else None,
            'median': stats.median(values) if values else None,
            'min': min(values) if values else None, 'max': max(values) if values else None}


def personal_path(root: Path, recorded: str) -> Path:
    if not isinstance(recorded, str):
        raise ValueError('invalid recorded path')
    path = (root / recorded).resolve()
    if not path.is_relative_to(root / 'validation/private/vbt') or path.relative_to(root).as_posix() != recorded:
        raise ValueError('non-canonical personal input path')
    return path


def suggestion_provenance(proposal: dict, kind: str) -> tuple[dict, bool]:
    expected = 'plate-hough-edge-v1' if kind == 'plate' else 'stick-yellow-markers-v1'
    provenance = {}
    valid = proposal.get('method') == expected
    if valid:
        provenance['method'] = expected
    if isinstance(proposal.get('id'), str) and re.fullmatch(expected + r':[0-9a-f]{12}', proposal['id']):
        provenance['id'] = proposal['id']
    elif proposal.get('status') == 'suggested':
        valid = False
    confidence = proposal.get('confidence')
    if type(confidence) in (int, float) and 0 <= confidence <= 1 and math.isfinite(confidence):
        provenance['confidence'] = confidence
    elif proposal.get('status') == 'suggested':
        valid = False
    if 'parameters' in proposal:
        parameters = proposal['parameters']
        provenance['parameters_sha256'] = workflow.canonical_sha256(parameters)
        if isinstance(parameters, dict) and all(re.fullmatch(r'[a-z][a-z0-9_]*', key)
                and type(value) in (int, float, bool) for key, value in parameters.items()):
            provenance['parameters'] = parameters
    if 'reason' in proposal:
        provenance['recorded_reason_sha256'] = workflow.canonical_sha256(proposal['reason'])
    return provenance, valid


def evaluate_session(root: Path, directory: Path, assessments: Path | None = None) -> dict:
    observed: dict[Path, str] = {}
    sources: dict[str, str] = {}

    def read(path: Path, role: str) -> bytes:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        observed[path] = digest
        sources[role] = digest
        return data

    def document(path: Path, role: str) -> dict:
        return workflow.schema_check.loads_strict(read(path, role).decode('utf-8'))

    result = {'session_id': directory.name, 'page_id': None, 'sources': sources,
              'confirmation_provenance': 'retained #95 session CSV, bound to completed session record',
              'inventory_status': 'unknown', 'exclusions': [], 'clips': []}
    try:
        if not directory.resolve().is_relative_to(root / 'validation/private/vbt'):
            raise ValueError('session outside personal inputs')
        state = document(directory / 'session.json', 'session_state')
        result['page_id'] = state['page_id']
        environment = state.get('suggester_environment')
        result['suggester_environment_sha256'] = workflow.canonical_sha256(environment)
        result['suggester_environment'] = {key: value for key, value in (environment or {}).items()
            if key in ('opencv_version', 'numpy_version') and isinstance(value, str)
            and re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', value)} if isinstance(environment, dict) else None
        for clip in state['clips']:
            row = {'fixture_id': clip['fixture_id'], 'source_video_sha256': clip['sha256'],
                   'package_id': clip['package_id'], 'frame_index': clip['frame_index'],
                   'timestamp_s': clip['timestamp_s'], 'decision': 'unavailable_confirmation',
                   'exclusions': [], 'assessment': {'processing': 'unknown', 'mechanical': 'unknown',
                                                  'experiment_suitability': 'unknown',
                                                  'accuracy': 'not_established', 'reason': 'assessment_missing'},
                   'items': {}, 'stick_length': None}
            for item, (kind, _, _, _) in session_contract.ITEMS.items():
                proposals = clip.get('suggestions')
                proposal = proposals.get(kind) if isinstance(proposals, dict) else None
                proposal = proposal if isinstance(proposal, dict) else {}
                status = proposal.get('status')
                provenance, valid = suggestion_provenance(proposal, kind)
                if status == 'suggested' and not valid:
                    row['exclusions'].append('suggestion_provenance_invalid')
                row['items'][item] = {'status': 'unavailable_confirmation', 'target_identity': 'not_derivable',
                    'suggestion': 'suggested' if status == 'suggested' else 'no_suggestion' if status == 'failed'
                        else 'not_derivable',
                    'provenance': provenance, 'discrepancy_px': None}
            result['clips'].append(row)
        result['inventory_status'] = 'known'
        if (state['format'] != session_ingest.SESSION_STATE_FORMAT or type(state['format_version']) is not int
                or state['format_version'] != 1 or state['session_id'] != directory.name
                or len({c['fixture_id'] for c in state['clips']}) != len(state['clips'])):
            raise ValueError('unsupported or duplicate session clips')
        if session_ingest.compute_page_id(state) != state['page_id']:
            result['exclusions'].append('page_binding_invalid')
        try:
            decisions = session_contract.parse_session_csv(read(directory / 'session-input.csv', 'session_csv'), state)
        except OSError:
            decisions = []
            result['exclusions'].append('confirmation_missing')
        except (ValueError, KeyError, TypeError, OverflowError):
            decisions = []
            result['exclusions'].append('confirmation_invalid')
        for row, decision in zip(result['clips'], decisions):
            row['decision'] = decision['decision']
            for item, value in row['items'].items():
                value['status'] = decision.get('statuses', {}).get(item, 'skipped')
        record = document(directory / 'session-record.json', 'session_record')
        if (record['format'] != 'openbar-research-vbt-session-record' or type(record['format_version']) is not int
                or record['format_version'] != 1 or record['workflow_version'] != 'vbt-workflow-3'
                or record['session_id'] != state['session_id'] or record['page_id'] != state['page_id']):
            raise ValueError('unrecognized session record')
        for name, filename in (('session_state', 'session.json'), ('session_csv', 'session-input.csv')):
            binding = record['inputs'][name]
            if (personal_path(root, binding['path']) != directory / filename
                    or binding['sha256'] != sources.get(name)):
                result['exclusions'].append('confirmation_binding_invalid')
        manifest = document(personal_path(root, record['inputs']['manifest']['path']), 'manifest')
        workflow.fixture_probe.validate_manifest(manifest)
        fixtures = {entry['id']: entry for entry in manifest['fixtures']}
        records = {entry['fixture_id']: entry for entry in record['clips']}
        confirmed = {d['fixture_id'] for d in decisions if d['decision'] == 'confirmed'}
        skipped = [d['fixture_id'] for d in decisions if d['decision'] == 'skipped']
        if (len(records) != len(record['clips']) or set(records) != confirmed or record['skipped'] != skipped):
            result['exclusions'].append('session_decisions_binding_invalid')
        for row, clip, decision in zip(result['clips'], state['clips'], decisions):
            if decision['decision'] == 'skipped':
                continue
            entry = records.get(clip['fixture_id'], {})
            try:
                if (entry['decision'] != 'confirmed' or entry['exercise'] != decision['exercise']
                        or entry['item_statuses'] != decision['statuses']
                        or entry['suggestion_ids'] != {k: clip['suggestions'][k].get('id') for k in ('plate', 'stick')}
                        or entry['video'] != {'path': clip['media_path'], 'sha256': clip['sha256']}):
                    raise ValueError('clip record mismatch')
                fixture = fixtures.get(clip['fixture_id'], {})
                if fixture.get('purpose') != 'development':
                    row['exclusions'].append('not_development')
                if fixture.get('media') != {'repository_path': clip['media_path'], 'sha256': clip['sha256']}:
                    row['exclusions'].append('manifest_source_binding_invalid')
                media = personal_path(root, clip['media_path'])
                digest = workflow.file_sha256(media)
                observed[media] = sources[clip['fixture_id'] + ':video'] = digest
                if digest != clip['sha256']:
                    row['exclusions'].append('source_hash_mismatch')
            except OSError:
                row['exclusions'].append('source_missing')
            except (KeyError, ValueError, TypeError):
                row['exclusions'].append('source_binding_invalid')
            if assessments is not None:
                path = assessments / (clip['fixture_id'] + '.assessment-v1.json')
                if path.exists():
                    try:
                        assessment = document(path, clip['fixture_id'] + ':assessment')
                        errors = workflow.schema_check.validate_document(
                            assessment, workflow.schema_check.load_schema(ASSESSMENT_SCHEMA))
                        if errors or assessment['fixture_id'] != clip['fixture_id']:
                            raise ValueError('invalid assessment')
                        bindings = {'video': entry['video'], 'seed': entry['seed'],
                                    'run_record': entry['analyze_lift']['run_record']}
                        for name, binding in bindings.items():
                            evidence = personal_path(root, binding['path'])
                            observed[evidence] = sources[clip['fixture_id'] + ':' + name] = workflow.file_sha256(evidence)
                            if (sources[clip['fixture_id'] + ':' + name] != binding['sha256']
                                    or assessment['sources'][name]['sha256'] != binding['sha256']):
                                raise ValueError('assessment binding mismatch')
                        row['assessment'] = {name: assessment[name]['status'] for name in
                                             ('processing', 'mechanical', 'experiment_suitability', 'accuracy')}
                        if assessment['experiment_suitability']['status'] == 'rejected':
                            row['exclusions'].append('assessment_rejected')
                    except (OSError, KeyError, ValueError, TypeError):
                        row['exclusions'].append('assessment_binding_invalid')
            if row['exclusions'] or result['exclusions']:
                continue
            for item, (_, columns, keys, _) in session_contract.ITEMS.items():
                value = row['items'][item]
                if value['suggestion'] != 'suggested':
                    continue
                proposal = clip['suggestions'][session_contract.ITEMS[item][0]]
                suggested = [round(float(proposal[key]), 2) for key in keys]
                confirmed_values = [decision['values'][column] for column in columns]
                value['discrepancy_px'] = math.dist(suggested, confirmed_values)
                if item == 'plate_radius':
                    delta = suggested[0] - confirmed_values[0]
                    value.update(signed_discrepancy_px=delta,
                                 signed_relative_percent=100 * delta / confirmed_values[0])
            if all(row['items'][item]['suggestion'] == 'suggested' for item in ('stick_low', 'stick_high')):
                proposal = clip['suggestions']['stick']
                points = [[round(float(proposal[key]), 2) for key in session_contract.ITEMS[item][2]]
                          for item in ('stick_low', 'stick_high')]
                confirmed_points = [[decision['values'][column] for column in session_contract.ITEMS[item][1]]
                                    for item in ('stick_low', 'stick_high')]
                length = math.dist(*confirmed_points)
                delta = math.dist(*points) - length
                row['stick_length'] = {'status': 'accepted' if all(decision['statuses'][item] == 'accepted'
                    for item in ('stick_low', 'stick_high')) else 'adjusted',
                    'signed_discrepancy_px': delta, 'signed_relative_percent': 100 * delta / length}
    except OSError:
        result['exclusions'].append('session_evidence_missing')
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, workflow.fixture_probe.ProbeError):
        result['exclusions'].append('session_evidence_invalid')
    for path, digest in observed.items():
        try:
            if workflow.file_sha256(path) != digest:
                result['exclusions'].append('evidence_changed_during_evaluation')
        except OSError:
            result['exclusions'].append('evidence_changed_during_evaluation')
    result['exclusions'] = sorted(set(result['exclusions']))
    if result['exclusions']:
        for row in result['clips']:
            for value in row['items'].values():
                value['discrepancy_px'] = None
                value.pop('signed_discrepancy_px', None)
                value.pop('signed_relative_percent', None)
            row['stick_length'] = None
    return result


def summarize(sessions: list[dict]) -> dict:
    rows = [(session, row) for session in sessions for row in session['clips']]
    summary = {'sessions_n': len(sessions), 'clips_n': len(rows),
               'unavailable_inventory_sessions_n': sum(s['inventory_status'] != 'known' for s in sessions),
               'excluded_sessions_n': sum(bool(s['exclusions']) for s in sessions),
               'assessment_counts': {state: sum(row['assessment']['mechanical'] == state for _, row in rows)
                                     for state in ('valid', 'invalid', 'unknown')}, 'items': {}}
    summary['processing_counts'] = {state: sum(row['assessment']['processing'] == state for _, row in rows)
                                    for state in ('complete', 'incomplete', 'unknown')}
    summary['experiment_suitability_counts'] = {state: sum(row['assessment']['experiment_suitability'] == state
                                                          for _, row in rows) for state in ('unknown', 'rejected')}
    summary['accuracy_counts'] = {'not_established': len(rows)}
    for item in session_contract.ITEMS:
        values = [(session, row, row['items'][item]) for session, row in rows]
        eligible = [value for session, row, value in values if not session['exclusions'] and not row['exclusions']]
        metrics = ('discrepancy_px', 'signed_discrepancy_px', 'signed_relative_percent') if item == 'plate_radius' else (
            'discrepancy_px',)
        summary['items'][item] = {
            'inventory_n': len(values), 'excluded_n': sum(bool(s['exclusions'] or r['exclusions']) for s, r, _ in values),
            'statuses': {status: sum(v['status'] == status for _, _, v in values) for status in
                         (*STATUSES, 'skipped', 'unavailable_confirmation')},
            'suggestions': {status: sum(v['suggestion'] == status for _, _, v in values)
                            for status in ('suggested', 'no_suggestion', 'not_derivable')},
            'target_identity': 'not_derivable', 'target_mismatch_count': None,
            'rejected_suggestion_count': None,
            **{metric: {status: statistics([v[metric] for v in eligible if v['status'] == status
                                           and v.get(metric) is not None]) for status in STATUSES} for metric in metrics}}
    lengths = [row['stick_length'] for session, row in rows if not session['exclusions'] and not row['exclusions']
               and row['stick_length'] is not None]
    summary['stick_length'] = {metric: {status: statistics([v[metric] for v in lengths if v['status'] == status])
                                      for status in ('accepted', 'adjusted')}
                               for metric in ('signed_discrepancy_px', 'signed_relative_percent')}
    return summary


def frozen_rules(commit: str) -> str:
    if re.fullmatch(r'[0-9a-f]{40}', commit) is None:
        raise ValueError('--rules-commit must be a full commit hash')
    relative = RULES.relative_to(workflow.ROOT).as_posix()
    subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'HEAD'], cwd=workflow.ROOT,
                   check=True, capture_output=True)
    frozen = subprocess.run(['git', 'show', f'{commit}:{relative}'], cwd=workflow.ROOT, check=True,
                            capture_output=True).stdout
    current = subprocess.run(['git', 'show', f'HEAD:{relative}'], cwd=workflow.ROOT, check=True,
                             capture_output=True).stdout
    if frozen != current or frozen.decode('utf-8') != RULES.read_text(encoding='utf-8'):
        raise ValueError('current rules differ from the frozen commit')
    return hashlib.sha256(frozen).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=workflow.ROOT, help='checkout retaining personal inputs')
    parser.add_argument('--sessions-root', type=Path, required=True)
    parser.add_argument('--assessments-dir', type=Path, help='optional bound #111 assessments, named by fixture id')
    parser.add_argument('--rules-commit', required=True)
    parser.add_argument('--output', type=Path, required=True, help='new private JSON report')
    args = parser.parse_args(argv)
    try:
        rules_hash = frozen_rules(args.rules_commit)
        root, directory = args.data_root.resolve(), args.sessions_root.resolve()
        if not directory.is_relative_to(root / 'validation/private/vbt') or not directory.is_dir():
            raise ValueError('--sessions-root must be a retained personal VBT directory')
        output = args.output.resolve()
        if not output.is_relative_to(workflow.ROOT / 'target'):
            raise ValueError('--output must be under this checkout target/ (private evidence)')
        sessions = [evaluate_session(root, path.parent, args.assessments_dir) for path in sorted(directory.glob('*/session.json'))]
        if not sessions:
            raise ValueError('no retained session states')
        identities = [(s['session_id'], s['page_id']) for s in sessions]
        if len(set(identities)) != len(identities):
            raise ValueError('duplicate session/page identity')
        report = {'format': FORMAT, 'format_version': VERSION, 'rules_commit': args.rules_commit,
                  'rules_sha256': rules_hash, 'evaluator_sha256': workflow.file_sha256(Path(__file__)),
                  'shared_validation_sha256': {name: workflow.file_sha256(Path(module.__file__))
                    for name, module in (('session_contract', session_contract), ('session_ingest', session_ingest),
                                         ('schema_check', workflow.schema_check), ('fixture_probe', workflow.fixture_probe))},
                  'sessions': sessions, 'summary': summarize(sessions),
                  'limits': ['owner-confirmed development inputs; confirmation bias possible',
                             'identity and explicit rejection labels not derivable',
                             'no held-out performance, independent accuracy or automatic acceptance verdict']}
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(serialize(report))
        print(f"wrote {len(sessions)} sessions, {report['summary']['clips_n']} inventory clips")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'evaluation refused: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
