from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiment
import study_io as io

PUBLIC = io.ROOT / "validation" / "fixtures" / "public"
FIXTURE = "synthetic-clean-side-12"
SEED = PUBLIC / "seeds" / f"{FIXTURE}.manual-target-seed-v1.json"
ANNOTATION = PUBLIC / "annotations" / f"{FIXTURE}.annotation-v1.json"
PREDICTION = PUBLIC / "predictions" / "synthetic-perfect.prediction-v1.json"


class ExperimentTests(unittest.TestCase):
    def test_diagnostics_full_command_repeatability_and_seed_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report"
            args = ["diagnose", "--manifest", str(PUBLIC / "manifest.json"), "--fixture", FIXTURE,
                    "--seed", str(SEED), "--annotation", str(ANNOTATION), "--candidate", f"perfect={PREDICTION}",
                    "--baseline", "perfect", "--output-dir", str(output)]
            self.assertEqual(experiment.main(args), 0)
            first = (output / "motion-diagnostics.json").read_bytes()
            self.assertEqual(experiment.main(args), 0)
            self.assertEqual(first, (output / "motion-diagnostics.json").read_bytes())
            doc = json.loads(first)
            self.assertEqual(doc["candidates"]["perfect"]["all"]["center_error_px"]["mae"], 0)
            self.assertEqual(doc["candidates"]["perfect"]["seed_excluded"]["matched_tracked_samples"],
                             doc["candidates"]["perfect"]["all"]["matched_tracked_samples"] - 1)
            self.assertEqual(doc["evidence_class"], "development_manual_labels")

    def test_held_out_refused_before_missing_inputs_are_read(self):
        manifest = io.load(PUBLIC / "manifest.json")
        manifest["fixtures"][0]["purpose"] = "validation"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            io.write(path, manifest)
            args = ["diagnose", "--manifest", str(path), "--fixture", FIXTURE, "--seed", "missing-seed",
                    "--annotation", "missing-annotation", "--candidate", "x=missing-prediction", "--baseline", "x",
                    "--output-dir", str(Path(tmp) / "out")]
            with patch("study_io.annotation", side_effect=AssertionError("must not read held-out labels")):
                self.assertEqual(experiment.main(args), 2)

    def test_prediction_identity_hash_bounds_and_lost_contract(self):
        item = io.fixture(io.load(PUBLIC / "manifest.json"), FIXTURE)
        original = io.load(PREDICTION)
        for mutate in (
            lambda d: d.update(fixture_id="other"),
            lambda d: d.update(source_video_sha256="0" * 64),
            lambda d: d["samples"][0]["center_px"].update(x_px=320),
            lambda d: d["samples"][0].update(state="lost"),
            lambda d: d["samples"][1].update(timestamp_s=0),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                doc = copy.deepcopy(original)
                mutate(doc)
                path = Path(tmp) / "prediction.json"
                io.write(path, doc)
                with self.assertRaises(ValueError):
                    io.prediction(path, item)

    def test_private_output_enforced(self):
        item = io.fixture(io.load(PUBLIC / "manifest.json"), FIXTURE)
        item["source"]["redistribution_status"] = "private_only"
        with self.assertRaisesRegex(ValueError, "validation/private"):
            io.private_output(item, io.ROOT / "target" / "report")
        io.private_output(item, io.ROOT / "validation" / "private" / "report")

    def test_negative_seed_and_frame_rotation_mismatch(self):
        item = io.fixture(io.load(PUBLIC / "manifest.json"), FIXTURE)
        original = io.load(SEED)
        for mutate in (lambda d: d["seed"]["target"]["center"].update(x_px=-1),
                       lambda d: d["seed"].update(source_rotation_deg=90)):
            with tempfile.TemporaryDirectory() as tmp:
                doc = copy.deepcopy(original)
                mutate(doc)
                path = Path(tmp) / "seed.json"
                io.write(path, doc)
                with self.assertRaises(ValueError):
                    io.seed(path, item)

    def test_no_duplicate_candidates(self):
        for values in (["x=a", "x=b"], ["x"], ["=a"], ["x="]):
            with self.assertRaises(ValueError):
                experiment.named_paths(values)

    def test_decoder_failures_are_reframed_as_fail_closed_value_errors(self):
        class FakeDecoder:
            class SpikeError(RuntimeError):
                pass

            @staticmethod
            def decode_frames(*_args):
                raise FakeDecoder.SpikeError("frame count mismatch")

        with self.assertRaisesRegex(ValueError, "decoder: frame count mismatch"):
            list(experiment.validated_decode_frames(FakeDecoder, Path("fake.mp4"), 1, 1, 1))

    def test_runner_maps_crop_translation_and_does_not_fill_loss(self):
        import numpy as np
        import vision
        sys.path.insert(0, str(io.ROOT / "research" / "opencv-tracking"))
        import track
        manifest = io.load(PUBLIC / "manifest.json")
        seed = io.load(SEED)
        coarse = io.load(PREDICTION)
        coarse["samples"] = coarse["samples"][:4]
        centers = [100., 102., 104., 106.]
        for row, x in zip(coarse["samples"], centers):
            row["center_px"] = {"x_px": x, "y_px": 120.}
        seed["seed"]["target"]["center"] = {"x_px": 100., "y_px": 120.}
        seed["seed"]["target"]["radius_px"] = 10
        seed["seed"]["selection_confidence"] = 0.0
        radial_results = [dict(center_px={"x_px": x, "y_px": 120.}, confidence=.7, diagnostics={}) for x in centers]
        radial_results[2] = dict(center_px=None, confidence=None, diagnostics={"loss_reason": "synthetic_occlusion"})
        frames = [np.zeros((240, 320, 3), dtype=np.uint8) for _ in centers]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            seed_path, coarse_path = folder / "seed.json", folder / "coarse.json"
            io.write(seed_path, seed)
            io.write(coarse_path, coarse)
            args = argparse.Namespace(manifest=PUBLIC / "manifest.json", fixture=FIXTURE, seed=seed_path,
                                      coarse=coarse_path, method="radial", config=None, repository_root=io.ROOT,
                                      output_dir=folder / "out")
            with patch("study_io.media_path", return_value=Path("fake.mp4")), \
                 patch("experiment.decoder_environment", return_value={"ffmpeg": "synthetic-test", "ffprobe": "synthetic-test"}), \
                 patch("experiment.repository_state", return_value={"commit": "test", "working_tree_dirty": False}), \
                 patch("label_package.probe", return_value={"timestamps_s": [r["timestamp_s"] for r in coarse["samples"]]}), \
                 patch("label_package.require_fixture_probe_match"), \
                 patch.object(track, "decode_frames", return_value=iter(frames)), \
                 patch.object(vision, "radial_center", side_effect=radial_results), \
                 patch.object(vision, "relative_shift", return_value=dict(delta_px={"x_px": 0., "y_px": 0.}, confidence=.8, diagnostics={})):
                result = experiment.run(args)
            self.assertEqual(result["absolute_tracked"], 3)
            absolute = io.prediction(folder / "out/radial.prediction-v1.json", manifest["fixtures"][0])
            fused = io.prediction(folder / "out/radial-fused.prediction-v1.json", manifest["fixtures"][0])
            self.assertEqual(absolute["samples"][0]["confidence"], 1.0)
            self.assertEqual(fused["samples"][0]["confidence"], 1.0)
            self.assertEqual(absolute["samples"][2], {"timestamp_s": coarse["samples"][2]["timestamp_s"], "state": "lost"})
            self.assertEqual(fused["samples"][2], absolute["samples"][2])
            sidecar = io.load(folder / "out/radial.sidecar.json")
            self.assertEqual(sidecar["relative_samples"][0]["delta_px"], {"x_px": 2., "y_px": 0.})
            self.assertEqual(sidecar["provenance"]["seed_selection_confidence"], 0.0)
            self.assertEqual(sidecar["provenance"]["fusion_seed_anchor_weight"], 0.0)
            self.assertEqual(sidecar["absolute_diagnostics"][0]["manual_seed_selection_confidence"], 0.0)
            self.assertNotIn("runtime", fused)

    def test_canonical_metrics_delegate_to_cli(self):
        args = argparse.Namespace(manifest=PUBLIC / "manifest.json", annotation=ANNOTATION, seed=SEED,
                                  fixture=FIXTURE, max_gap_s=.2, repository_root=io.ROOT)
        item = io.fixture(io.load(args.manifest), FIXTURE)
        with tempfile.TemporaryDirectory() as tmp:
            args.output_dir = Path(tmp)
            with patch("experiment.subprocess.run") as run:
                experiment.canonical_outputs(args, item, {"perfect": PREDICTION}, io.load(ANNOTATION), 0.)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(len(commands), 2)
            self.assertIn("benchmark", commands[0])
            self.assertIn("--observations", commands[1])
            self.assertIn("raw", commands[1])
            io.contract(io.load(args.output_dir / "canonical.benchmark-v1.json"), "benchmark-suite-v1")

    def test_non_exact_seed_timestamp_excludes_actual_seed_frame(self):
        seed = io.load(SEED)
        seed["seed"]["timestamp_s"] = .0001
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seed.json"
            io.write(path, seed)
            args = ["diagnose", "--manifest", str(PUBLIC / "manifest.json"), "--fixture", FIXTURE,
                    "--seed", str(path), "--annotation", str(ANNOTATION), "--candidate", f"perfect={PREDICTION}",
                    "--baseline", "perfect", "--output-dir", str(Path(tmp) / "out")]
            self.assertEqual(experiment.main(args), 0)
            doc = io.load(Path(tmp) / "out/motion-diagnostics.json")
            self.assertEqual(doc["config"]["seed_reference_timestamp_s"], 0.)
            self.assertNotIn(0., [r["timestamp_s"] for r in doc["candidates"]["perfect"]["seed_excluded"]["matched_errors"]])
        del seed["seed"]["frame_index"]
        self.assertEqual(experiment.seed_reference_timestamp(seed, io.load(PREDICTION), io.load(ANNOTATION)), 0.)

    def test_canonical_decode_span_preserves_unlabelled_prediction_tail(self):
        args = argparse.Namespace(manifest=PUBLIC / "manifest.json", annotation=ANNOTATION, seed=SEED,
                                  fixture=FIXTURE, max_gap_s=.2, repository_root=io.ROOT)
        annotation = io.load(ANNOTATION)
        annotation["samples"] = annotation["samples"][:-3]
        expected_end = io.load(PREDICTION)["samples"][-1]["timestamp_s"] + .000001
        with tempfile.TemporaryDirectory() as tmp:
            args.output_dir = Path(tmp)
            with patch("experiment.subprocess.run") as run:
                experiment.canonical_outputs(args, io.fixture(io.load(args.manifest), FIXTURE),
                                             {"perfect": PREDICTION}, annotation, 0.)
            analyze = run.call_args_list[-1].args[0]
            self.assertEqual(float(analyze[analyze.index("--end-s") + 1]), expected_end)


if __name__ == "__main__":
    unittest.main()
