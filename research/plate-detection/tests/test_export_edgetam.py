from __future__ import annotations

import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import export_edgetam  # noqa: E402
import train_phase_b  # noqa: E402


class ExportEdgeTamTests(unittest.TestCase):
    def test_validate_export_config_enforces_multiples_of_32_and_finite_tolerances(self) -> None:
        cfg = export_edgetam.validate_export_config(
            input_height_px=1024,
            input_width_px=1024,
            max_abs_diff=1e-4,
        )
        self.assertEqual(cfg["vision_input_shape"], [1, 3, 1024, 1024])
        self.assertEqual(cfg["memory_pix_shape"], [1, 256, 64, 64])
        self.assertEqual(cfg["memory_mask_shape"], [1, 1, 1024, 1024])
        self.assertEqual(cfg["decoder_emb_shape"], [1, 256, 64, 64])
        self.assertEqual(cfg["decoder_sparse_shape"], [1, 1, 2, 256])
        self.assertEqual(cfg["decoder_high_res_0_shape"], [1, 256, 256, 256])
        self.assertEqual(cfg["decoder_high_res_1_shape"], [1, 256, 128, 128])
        self.assertEqual(cfg["max_abs_diff"], 1e-4)

        for bad_dim in (0, -32, 1000, True):
            with self.subTest(bad_dim=bad_dim), self.assertRaises(ValueError):
                export_edgetam.validate_export_config(input_height_px=bad_dim)  # type: ignore[arg-type]

        for bad_tol in (0.0, -1e-5, float("nan"), float("inf"), True):
            with self.subTest(bad_tol=bad_tol), self.assertRaises(ValueError):
                export_edgetam.validate_export_config(max_abs_diff=bad_tol)  # type: ignore[arg-type]

    def test_compute_feature_parity_passes_on_matching_tensors_and_detects_drift(self) -> None:
        t1 = np.ones((1, 256, 64, 64), dtype=np.float32)
        t2 = np.zeros((1, 256, 32, 32), dtype=np.float32)
        ref = [t1, t2]

        exp_match = [t1 + 1e-6, t2 - 1e-6]
        res = export_edgetam.compute_feature_parity(ref, exp_match, max_abs_diff=1e-4)
        self.assertTrue(res["passed"])
        self.assertAlmostEqual(res["overall_max_abs_diff"], 1e-6, places=5)
        self.assertEqual(len(res["per_tensor"]), 2)

        exp_drift = [t1 + 1e-3, t2]
        res_drift = export_edgetam.compute_feature_parity(ref, exp_drift, max_abs_diff=1e-4)
        self.assertFalse(res_drift["passed"])
        self.assertGreater(res_drift["overall_max_abs_diff"], 1e-4)

    def test_compute_feature_parity_rejects_shape_mismatches_and_non_finite_values(self) -> None:
        t1 = np.ones((1, 10), dtype=np.float32)
        t2 = np.ones((1, 12), dtype=np.float32)

        with self.assertRaises(ValueError):
            export_edgetam.compute_feature_parity([t1], [t2])

        with self.assertRaises(ValueError):
            export_edgetam.compute_feature_parity([t1], [])

        with self.assertRaises(ValueError):
            export_edgetam.compute_feature_parity([], [])

        t_nan = np.array([float("nan")], dtype=np.float32)
        with self.assertRaises(ValueError):
            export_edgetam.compute_feature_parity([t_nan], [np.array([1.0], dtype=np.float32)])

    def test_export_edgetam_tracker_refuses_paths_outside_private_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            outside = Path(tmp_str)
            with self.assertRaises(SystemExit):
                export_edgetam.export_edgetam_tracker(
                    output_dir=outside / "out",
                )

    def test_dataset_admission_runs_before_creating_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            private = Path(tmp_str)
            with (
                patch.object(export_edgetam.label_package, "PRIVATE_ROOT", private),
                patch.object(train_phase_b, "validate_dataset_dir", side_effect=RuntimeError("held-out fixture")) as admission,
                patch.dict(sys.modules, {"transformers": SimpleNamespace()}),
                self.assertRaisesRegex(RuntimeError, "held-out fixture"),
            ):
                export_edgetam.export_edgetam_tracker(output_dir=private / "out", dataset_dir=private / "dataset")
            admission.assert_called_once_with(private / "dataset", export_edgetam.ROOT)
            self.assertFalse((private / "out").exists())

    def test_export_connects_raw_fpn_to_decoder_and_reuses_loaded_modules(self) -> None:
        try:
            import torch
            from transformers import EdgeTamVideoModel
        except ImportError:
            self.skipTest("run in the plate-detection research environment with torch and transformers")

        class Vision(torch.nn.Module):
            def forward(self, x):
                return SimpleNamespace(fpn_hidden_states=(
                    x.mean(1, keepdim=True)[:, :, ::4, ::4].expand(-1, 256, -1, -1),
                    x.mean(1, keepdim=True)[:, :, ::8, ::8].expand(-1, 256, -1, -1),
                    x.mean(1, keepdim=True)[:, :, ::16, ::16].expand(-1, 256, -1, -1),
                ))

        class Memory(torch.nn.Module):
            def forward(self, pix, mask):
                return pix, mask

        class Decoder(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.conv_s0 = torch.nn.Conv2d(256, 32, 1)
                self.conv_s1 = torch.nn.Conv2d(256, 64, 1)

            def forward(self, image_embeddings, image_positional_embeddings, sparse_prompt_embeddings,
                        dense_prompt_embeddings, multimask_output, high_resolution_features):
                hi0, hi1 = high_resolution_features
                assert hi0.shape[1] == 32 and hi1.shape[1] == 64
                value = hi0.mean() + hi1.mean() + image_embeddings.mean()
                return value.reshape(1, 1, 1, 1, 1), value.reshape(1, 1, 1), value, value.reshape(1, 1, 1)

        model = SimpleNamespace(vision_encoder=Vision(), memory_encoder=Memory(), mask_decoder=Decoder())
        model.eval = lambda: model
        loaded_programs = []
        real_load = torch.export.load

        def load(path):
            program = real_load(path)
            program.module = Mock(wraps=program.module)
            loaded_programs.append(program)
            return program

        with tempfile.TemporaryDirectory() as tmp_str:
            private = Path(tmp_str)
            with (
                patch.object(export_edgetam.label_package, "PRIVATE_ROOT", private),
                patch.object(export_edgetam, "ROOT", private),
                patch.object(export_edgetam, "_system_metadata", return_value={}),
                patch.object(EdgeTamVideoModel, "from_pretrained", return_value=model),
                patch.object(torch.export, "load", side_effect=load),
            ):
                result = export_edgetam.export_edgetam_tracker(
                    output_dir=private / "out", input_height_px=32, input_width_px=32,
                )
            self.assertTrue(result["passed"])
            self.assertEqual(len(loaded_programs), 3)
            for program in loaded_programs:
                self.assertEqual(program.module.call_count, 1, "module reconstruction must stay outside latency loops")
            fpn = loaded_programs[0].module()(torch.randn(1, 3, 32, 32))
            args = (fpn[2], torch.zeros_like(fpn[2]), torch.zeros(1, 1, 2, 256),
                    torch.zeros_like(fpn[2]), fpn[0], fpn[1])
            with torch.no_grad():
                actual = loaded_programs[2].module()(*args)
                native = model.mask_decoder(*args[:4], False, [model.mask_decoder.conv_s0(fpn[0]),
                                                               model.mask_decoder.conv_s1(fpn[1])])
            for expected, observed in zip((native[0], native[1], native[3]), actual):
                torch.testing.assert_close(observed, expected, rtol=0, atol=0)
            self.assertEqual(result["exported_subgraphs"]["mask_decoder"]["outputs"], [
                "masks [1, 1, 1, 1, 1]", "iou_pred [1, 1, 1]", "object_score_logits [1, 1, 1]",
            ])


if __name__ == "__main__":
    unittest.main()
