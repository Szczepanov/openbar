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
    def test_validate_export_config_enforces_multiples_of_32_and_finite_tolerances(self) -> None:
        cfg = export_edgetam.validate_export_config(
            input_height_px=1024,
            input_width_px=1024,
            max_abs_diff=1e-4,
        )
        self.assertEqual(cfg["vision_input_shape"], [1, 3, 1024, 1024])
        self.assertEqual(cfg["memory_pix_shape"], [1, 256, 64, 64])
        self.assertEqual(cfg["memory_mask_shape"], [1, 1, 1024, 1024])
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


if __name__ == "__main__":
    unittest.main()
