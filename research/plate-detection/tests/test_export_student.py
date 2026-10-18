from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import export_student  # noqa: E402


class ExportStudentTests(unittest.TestCase):
    def test_validate_export_config_enforces_stride_and_finite_tolerances(self) -> None:
        cfg = export_student.validate_export_config(
            input_height_px=640,
            input_width_px=640,
            max_logit_abs_diff=1e-4,
            max_box_abs_diff=1e-4,
        )
        self.assertEqual(cfg["input_shape"], [1, 3, 640, 640])

        for bad_dim in (0, -32, 600, True):
            with self.subTest(bad_dim=bad_dim), self.assertRaises(ValueError):
                export_student.validate_export_config(input_height_px=bad_dim)  # type: ignore[arg-type]

        for bad_tol in (0.0, -1e-5, float("nan"), float("inf"), True):
            with self.subTest(bad_tol=bad_tol), self.assertRaises(ValueError):
                export_student.validate_export_config(max_logit_abs_diff=bad_tol)  # type: ignore[arg-type]

    def test_compute_tensor_parity_passes_on_matching_outputs_and_fails_on_drift(self) -> None:
        ref_logits = np.array([[[-2.0], [3.5], [0.1]], [[-1.0], [0.2], [4.2]]], dtype=np.float32)
        ref_boxes = np.array(
            [
                [[0.2, 0.2, 0.1, 0.1], [0.5, 0.5, 0.2, 0.2], [0.8, 0.8, 0.1, 0.1]],
                [[0.3, 0.3, 0.1, 0.1], [0.4, 0.4, 0.1, 0.1], [0.6, 0.6, 0.25, 0.25]],
            ],
            dtype=np.float32,
        )
        exp_logits = ref_logits + 1e-6
        exp_boxes = ref_boxes + 1e-6

        ok = export_student.compute_tensor_parity(ref_logits, ref_boxes, exp_logits, exp_boxes)
        self.assertTrue(ok["passed"])
        self.assertEqual(ok["samples"], 2)
        self.assertEqual(ok["top1_query_match_rate"], 1.0)
        self.assertGreater(ok["top1_box_iou_min"], 0.999)

        # Drift exceeding tolerance must fail
        drifted_boxes = ref_boxes.copy()
        drifted_boxes[0, 1, 0] += 0.01
        bad = export_student.compute_tensor_parity(ref_logits, ref_boxes, exp_logits, drifted_boxes)
        self.assertFalse(bad["passed"])

        # Non-finite values must raise ValueError
        nan_logits = ref_logits.copy()
        nan_logits[0, 0, 0] = float("nan")
        with self.assertRaises(ValueError):
            export_student.compute_tensor_parity(nan_logits, ref_boxes, exp_logits, exp_boxes)

    def test_export_student_detector_refuses_paths_outside_private_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            outside = Path(tmp_str)
            with self.assertRaises(SystemExit):
                export_student.export_student_detector(
                    checkpoint_dir=outside / "ckpt",
                    output_dir=outside / "out",
                )


if __name__ == "__main__":
    unittest.main()
