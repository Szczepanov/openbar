from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import export_edgetam  # noqa: E402


class ExportEdgeTamTests(unittest.TestCase):
    def test_validate_export_config_enforces_multiples_of_32_and_positive_tolerances(self) -> None:
        cfg = export_edgetam.validate_edgetam_export_config(
            input_height_px=1024,
            input_width_px=1024,
            max_abs_diff=1e-4,
        )
        self.assertEqual(cfg["image_shape"], [1, 3, 1024, 1024])
        self.assertEqual(cfg["high_res_0_shape"], [1, 32, 256, 256])
        self.assertEqual(cfg["high_res_1_shape"], [1, 64, 128, 128])
        self.assertEqual(cfg["embedding_shape"], [1, 256, 64, 64])
        self.assertEqual(cfg["sparse_prompt_shape"], [1, 1, 2, 256])
        self.assertEqual(cfg["max_abs_diff"], 1e-4)

        for bad_dim in (0, -32, 1000, 1023, True):
            with self.subTest(bad_dim=bad_dim), self.assertRaises(ValueError):
                export_edgetam.validate_edgetam_export_config(input_height_px=bad_dim)  # type: ignore[arg-type]

        for bad_tol in (0.0, -1e-5, float("nan"), float("inf"), True):
            with self.subTest(bad_tol=bad_tol), self.assertRaises(ValueError):
                export_edgetam.validate_edgetam_export_config(max_abs_diff=bad_tol)  # type: ignore[arg-type]

    def test_compute_encoder_parity_passes_and_detects_drift(self) -> None:
        h0 = np.zeros((1, 32, 256, 256), dtype=np.float32)
        h1 = np.zeros((1, 64, 128, 128), dtype=np.float32)
        emb = np.zeros((1, 256, 64, 64), dtype=np.float32)
        pos = np.zeros((1, 256, 64, 64), dtype=np.float32)

        ref = (h0, h1, emb, pos)
        exp = (h0 + 1e-6, h1 + 1e-6, emb + 1e-6, pos + 1e-6)

        ok = export_edgetam.compute_encoder_parity(ref, exp, max_abs_diff=1e-4)
        self.assertTrue(ok["passed"])
        self.assertLessEqual(ok["max_abs_diff"], 1e-5)
        for name in ("high_res_0", "high_res_1", "image_embeddings", "image_positional_embeddings"):
            self.assertTrue(ok["tensors"][name]["passed"])

        # Injected drift beyond tolerance fails
        drifted = (h0 + 0.05, h1, emb, pos)
        bad = export_edgetam.compute_encoder_parity(ref, drifted, max_abs_diff=1e-4)
        self.assertFalse(bad["passed"])
        self.assertFalse(bad["tensors"]["high_res_0"]["passed"])

        # Non-finite values fail closed
        nan_h0 = h0.copy()
        nan_h0[0, 0, 0, 0] = float("nan")
        with self.assertRaises(ValueError):
            export_edgetam.compute_encoder_parity((nan_h0, h1, emb, pos), exp)

    def test_compute_decoder_parity_passes_and_detects_drift(self) -> None:
        masks = np.zeros((1, 1, 1, 256, 256), dtype=np.float32)
        iou = np.array([[[0.95]]], dtype=np.float32)
        obj_score = np.array([[[2.5]]], dtype=np.float32)

        ref = (masks, iou, obj_score)
        exp = (masks + 1e-6, iou + 1e-6, obj_score + 1e-6)

        ok = export_edgetam.compute_decoder_parity(ref, exp, max_abs_diff=1e-4)
        self.assertTrue(ok["passed"])
        self.assertLessEqual(ok["max_abs_diff"], 1e-5)
        for name in ("masks", "iou_pred", "object_score_logits"):
            self.assertTrue(ok["tensors"][name]["passed"])

        # Injected drift beyond tolerance fails
        drifted = (masks, iou, obj_score + 0.1)
        bad = export_edgetam.compute_decoder_parity(ref, drifted, max_abs_diff=1e-4)
        self.assertFalse(bad["passed"])
        self.assertFalse(bad["tensors"]["object_score_logits"]["passed"])

        # Non-finite values fail closed
        inf_iou = iou.copy()
        inf_iou[0, 0, 0] = float("inf")
        with self.assertRaises(ValueError):
            export_edgetam.compute_decoder_parity((masks, inf_iou, obj_score), exp)

    def test_export_edgetam_models_refuses_paths_outside_private_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            outside = Path(tmp_str)
            with self.assertRaises(SystemExit):
                export_edgetam.export_edgetam_models(
                    output_dir=outside / "out",
                )


if __name__ == "__main__":
    unittest.main()
