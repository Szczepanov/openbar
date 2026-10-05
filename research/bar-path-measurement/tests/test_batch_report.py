from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import batch_report as batch
import study_io as io

PUBLIC = io.ROOT / 'validation/fixtures/public'
FIXTURE = 'synthetic-clean-side-12'


class BatchReportTests(unittest.TestCase):
    def inputs(self, root):
        manifest = PUBLIC / 'manifest.json'
        paths = {'annotation': PUBLIC / f'annotations/{FIXTURE}.annotation-v1.json',
                 'seed': PUBLIC / f'seeds/{FIXTURE}.manual-target-seed-v1.json',
                 'sam': PUBLIC / 'predictions/synthetic-perfect.prediction-v1.json',
                 'csrt': PUBLIC / 'predictions/synthetic-perfect.prediction-v1.json'}
        snapshot = {'schema_version': 1, 'study_version': io.VERSION, 'kind': 'development_baseline',
                    'manifest_sha256': io.digest(manifest),
                    'inputs': {n: {'path': str(p), 'sha256': io.digest(p)} for n, p in paths.items()}}
        path = root / 'baseline.json'
        io.write(path, snapshot)
        args = argparse.Namespace(manifest=manifest, snapshot=[f'{FIXTURE}={path}'], output_dir=root / 'out',
                                  max_gap_s=.2, canonical=False, repository_root=io.ROOT)
        return snapshot, args

    def test_real_delegation_repeatability_seed_exclusion_and_input_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, args = self.inputs(root)
            snapshot_path = root / 'baseline.json'
            before = snapshot_path.read_bytes()
            first = batch.run(args)
            files = {p.relative_to(args.output_dir): p.read_bytes() for p in args.output_dir.rglob('*') if p.is_file()}
            args.output_dir = root / 'repeat'
            self.assertEqual(first, batch.run(args))
            self.assertEqual(files, {p.relative_to(args.output_dir): p.read_bytes() for p in args.output_dir.rglob('*') if p.is_file()})
            self.assertEqual(snapshot_path.read_bytes(), before)
            self.assertIsNone(first['production_candidate'])
            annotation = io.load(PUBLIC / f'annotations/{FIXTURE}.annotation-v1.json')
            count = sum(r['annotation_state'] == 'labelled' for r in annotation['samples']) - 1
            self.assertEqual(first['fixtures'][FIXTURE]['sam']['matched_tracked_samples'], count)

    def test_bad_snapshot_and_hashes_refused_without_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            original, args = self.inputs(Path(tmp))
            for change in ({'schema_version': True}, {'schema_version': 2}, {'kind': 'held_out'},
                           {'fixture_id': 'other'},
                           {'manifest_sha256': '0' * 64},
                           {'inputs': {**original['inputs'], 'sam': {**original['inputs']['sam'], 'sha256': '0' * 64}}}):
                io.write(Path(tmp) / 'baseline.json', {**copy.deepcopy(original), **change})
                with self.assertRaises(ValueError):
                    batch.run(args)
                self.assertFalse(args.output_dir.exists())

    def test_duplicate_and_existing_output_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, args = self.inputs(Path(tmp))
            args.snapshot *= 2
            with self.assertRaisesRegex(ValueError, 'unique'):
                batch.run(args)
            self.assertFalse(args.output_dir.exists())
            args.snapshot = args.snapshot[:1]
            args.output_dir.mkdir()
            marker = args.output_dir / 'owner-progress.txt'
            marker.write_text('keep')
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                batch.run(args)
            self.assertEqual(marker.read_text(), 'keep')

    def test_held_out_refused_before_reading_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot, args = self.inputs(Path(tmp))
            manifest = io.load(args.manifest)
            manifest['fixtures'][0]['purpose'] = 'validation'
            args.manifest = Path(tmp) / 'manifest.json'
            io.write(args.manifest, manifest)
            snapshot['manifest_sha256'] = io.digest(args.manifest)
            snapshot['inputs'] = {}
            io.write(Path(tmp) / 'baseline.json', snapshot)
            with self.assertRaisesRegex(ValueError, 'held-out'):
                batch.run(args)
            self.assertFalse(args.output_dir.exists())

    def test_canonical_rendering_delegated_and_invalid_gap_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, args = self.inputs(Path(tmp))
            for gap in (0, float('nan'), float('inf'), -1):
                args.max_gap_s = gap
                with self.assertRaises(ValueError):
                    batch.run(args)
            args.max_gap_s = .2
            args.canonical = True
            with patch('study_io.media_path'), patch('experiment.canonical_outputs') as canonical, \
                    patch('batch_report.subprocess.run') as render:
                batch.run(args)
                canonical.assert_called_once()
                self.assertEqual(render.call_count, 2)
                self.assertEqual([Path(c.args[0][-1]).name for c in render.call_args_list], ['csrt.svg', 'sam.svg'])
