"""Replay existing development evidence in one batch; no new annotation or model run."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

import experiment
import study_io as io

VERSION = 'development-batch-report-v1'


def prepare(fixture_id: str, snapshot_path: Path, args) -> argparse.Namespace:
    snapshot = io.load(snapshot_path)
    if (type(snapshot.get('schema_version')) is not int or snapshot['schema_version'] != 1 or
            snapshot.get('study_version') != io.VERSION or snapshot.get('kind') != 'development_baseline'):
        raise ValueError('expected a versioned development baseline snapshot')
    if snapshot['manifest_sha256'] != io.digest(args.manifest):
        raise ValueError('snapshot manifest hash mismatch')
    if snapshot.get('fixture_id', fixture_id) != fixture_id:
        raise ValueError('snapshot fixture identity mismatch')
    manifest = io.load(args.manifest)
    item = io.fixture(manifest, fixture_id)
    io.private_output(item, args.output_dir)
    paths = {}
    for name in ('annotation', 'seed', 'csrt', 'sam'):
        recorded = snapshot['inputs'][name]
        path = Path(recorded['path'])
        if io.digest(path) != recorded['sha256']:
            raise ValueError(f'{name}: snapshot input hash mismatch')
        paths[name] = path
    annotation = io.annotation(paths['annotation'], manifest, item)
    seed = io.seed(paths['seed'], item)
    predictions = {name: io.prediction(paths[name], item) for name in ('csrt', 'sam')}
    experiment.seed_reference_timestamp(seed, predictions['sam'], annotation)
    if args.canonical:
        io.media_path(item, args.repository_root)
    return argparse.Namespace(manifest=args.manifest, fixture=fixture_id, seed=paths['seed'],
                              annotation=paths['annotation'], candidate=[f'{n}={paths[n]}' for n in ('csrt', 'sam')],
                              baseline='sam', relative=[], stationary_window=[], max_gap_s=args.max_gap_s,
                              canonical=args.canonical, repository_root=args.repository_root,
                              output_dir=args.output_dir / fixture_id)


def summarize(report: dict) -> dict:
    return {name: {'labelled_samples': m['labelled_samples'],
                  'matched_tracked_samples': m['matched_tracked_samples'],
                  'center_mae_px': m['center_error_px']['mae'],
                  'delta_mae_px': m['delta_position_error_px']['mae'],
                  'available_intervals': m['available_intervals'],
                  'high_confidence_error_gt_3_px': m['confidence_vs_error'][2]['error_gt_3_px']}
            for name, candidate in report['candidates'].items()
            for m in (candidate['seed_excluded'],)}


def run(args) -> dict:
    if io.number(args.max_gap_s, 'max_gap_s', 0) == 0:
        raise ValueError('max_gap_s must be positive')
    if args.output_dir.exists():
        raise ValueError('output directory exists; refusing to overwrite results')
    paths = experiment.named_paths(args.snapshot)
    prepared = [prepare(name, path, args) for name, path in paths.items()]
    if not prepared or len({a.fixture for a in prepared}) != len(prepared):
        raise ValueError('supply at least one snapshot with unique fixture IDs')
    prepared.sort(key=lambda a: a.fixture)

    # Claim the destination only after every snapshot/input/media preflight succeeds. If any later
    # diagnostic or delegated CLI step fails, remove only the directory this invocation created so a
    # corrected rerun is not blocked by incomplete evidence.
    args.output_dir.mkdir(parents=True, exist_ok=False)
    complete = False
    try:
        reports = {}
        for options in prepared:
            report = experiment.diagnose(options)
            reports[options.fixture] = summarize(report)
            if args.canonical:
                for index, name in enumerate(('csrt', 'sam')):
                    subprocess.run(['cargo', 'run', '--locked', '-q', '-p', 'openbar-cli', '--', 'render',
                                    '--analysis', str(options.output_dir / f'canonical-{index}.analysis-v1.json'),
                                    '--output', str(options.output_dir / f'{name}.svg')], check=True, cwd=io.ROOT)
        result = {'schema_version': 1, 'method_version': VERSION, 'kind': 'development_batch_report',
                  'evidence_class': 'existing_sparse_development_manual_labels', 'fixtures': reports,
                  'config': {'max_gap_s': args.max_gap_s, 'canonical': args.canonical, 'filter': 'raw'},
                  'manifest_sha256': io.digest(args.manifest),
                  'snapshots': {name: {'path': str(p.resolve()), 'sha256': io.digest(p)}
                                for name, p in sorted(paths.items())},
                  'source_sha256': {name: io.digest(Path(__file__).with_name(name)) for name in
                                    ('batch_report.py', 'experiment.py', 'study_io.py', 'motion_metrics.py')},
                  'production_candidate': None,
                  'limitations': ['no new manual work or machine-generated annotations',
                                  'sparse labels do not establish dense accuracy or stationary jitter',
                                  'held-out selection and independent physical velocity remain unevaluated']}
        io.write(args.output_dir / 'summary.json', result)
        lines = ['# Automatic development report', '',
                 'Existing sparse manual labels; initialization seed excluded. No new annotation required.', '',
                 '| Clip | Tracker | Matched labels | Centre MAE px | Delta MAE px | Usable intervals | High-confidence errors >3 px |',
                 '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
        def display(value):
            return 'unsupported' if value is None else f'{value:.3f}'
        for fixture, candidates in reports.items():
            for name, m in candidates.items():
                lines.append(f"| {fixture} | {name} | {m['matched_tracked_samples']}/{m['labelled_samples']} | "
                             f"{display(m['center_mae_px'])} | {display(m['delta_mae_px'])} | "
                             f"{m['available_intervals']} | {m['high_confidence_error_gt_3_px']} |")
        lines += ['', 'Per-clip motion-diagnostics.json contains paired common-support, loss and confidence diagnostics.',
                  'Canonical mode adds separate raw-filter Analysis files, authoritative benchmark results and csrt.svg/sam.svg.',
                  'Canonical benchmark results include initialization; the table above excludes the seed.',
                  'Rendered velocity is an experimental derivative, not validated physical accuracy.',
                  'No production candidate is selected. Held-out footage remains untouched.', '']
        (args.output_dir / 'README.md').write_bytes('\n'.join(lines).encode())
        complete = True
        return result
    finally:
        if not complete:
            shutil.rmtree(args.output_dir, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--snapshot', action='append', required=True, metavar='FIXTURE=PATH')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--repository-root', type=Path, default=io.ROOT)
    parser.add_argument('--max-gap-s', type=float, default=.2)
    parser.add_argument('--canonical', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = run(args)
        print(f"Report ready: {len(result['fixtures'])} development clips in {args.output_dir}")
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'batch report: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
