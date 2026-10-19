from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import clips  # noqa: E402
import detectors  # noqa: E402
import geometry  # noqa: E402
import train_phase_b  # noqa: E402

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - image augmentation tests skip without numpy/OpenCV
    cv2 = None
    np = None


class TrainPhaseBTests(unittest.TestCase):
    def test_coco_to_hf_annotations_converts_and_validates_boxes(self) -> None:
        img = {"id": 7, "file_name": "frames/c/frame_000000.png", "width": 1080, "height": 1920}
        anns = [{"id": 1, "image_id": 7, "category_id": 1, "bbox": [400.0, 300.0, 200.0, 200.0], "area": 40000.0}]
        hf = train_phase_b.coco_to_hf_annotations(img, anns)
        self.assertEqual(hf["image_id"], 7)
        self.assertEqual(len(hf["annotations"]), 1)
        self.assertEqual(hf["annotations"][0]["category_id"], 0)
        self.assertEqual(hf["annotations"][0]["bbox"], [400.0, 300.0, 200.0, 200.0])

        with self.assertRaises(ValueError):
            train_phase_b.coco_to_hf_annotations(img, [{"bbox": [0.0, 0.0, 0.0, 10.0]}])
        with self.assertRaises(ValueError):
            train_phase_b.coco_to_hf_annotations(img, [{"bbox": [0.0, 0.0, float("nan"), 10.0]}])

    def test_evaluate_box_predictions_scores_recall_top1_refusal_and_geometry(self) -> None:
        val_coco = {
            "images": [
                {"id": 1, "width": 1080, "height": 1920, "fixture_id": "self-snatch-side-0206-19"},
                {"id": 2, "width": 1080, "height": 1920, "fixture_id": "self-snatch-side-0212-24"},
            ],
            "annotations": [
                {
                    "id": 1,
                    "image_id": 1,
                    "bbox": [400.0, 500.0, 200.0, 200.0],
                    "center_x_px": 500.0,
                    "center_y_px": 600.0,
                    "radius_px": 100.0,
                },
                {
                    "id": 2,
                    "image_id": 2,
                    "bbox": [400.0, 500.0, 200.0, 200.0],
                    "center_x_px": 500.0,
                    "center_y_px": 600.0,
                    "radius_px": 100.0,
                },
            ],
        }
        preds = {
            # Image 1: exact hit with clear score margin
            1: [
                {
                    "center_x_px": 502.0,
                    "center_y_px": 600.0,
                    "radius_px": 101.0,
                    "score": 0.92,
                    "box_xyxy": [401.0, 499.0, 603.0, 701.0],
                }
            ],
            # Image 2: two close candidates -> refused by sam3-margin-v2
            2: [
                {
                    "center_x_px": 500.0,
                    "center_y_px": 600.0,
                    "radius_px": 100.0,
                    "score": 0.85,
                    "box_xyxy": [400.0, 500.0, 600.0, 700.0],
                },
                {
                    "center_x_px": 200.0,
                    "center_y_px": 600.0,
                    "radius_px": 100.0,
                    "score": 0.83,
                    "box_xyxy": [100.0, 500.0, 300.0, 700.0],
                },
            ],
        }
        metrics = train_phase_b.evaluate_box_predictions(preds, val_coco)
        self.assertEqual(metrics["evaluated_frames"], 2)
        self.assertEqual(metrics["frame_recall"], 2)
        self.assertEqual(metrics["frame_plausible_top1"], 2)
        self.assertEqual(metrics["refusal"]["refused"], 1)
        self.assertEqual(metrics["refusal"]["accepted"], 1)
        self.assertEqual(metrics["refusal"]["accepted_correct"], 1)
        self.assertGreater(metrics["box_iou_median"], 0.95)
        self.assertLess(metrics["centre_error_rel_median"], 0.02)

    def test_validate_dataset_dir_checks_roles_and_session_overlap(self) -> None:
        private_root = train_phase_b.label_package.PRIVATE_ROOT.resolve()
        private_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private_root, prefix="test-train-phase-b-") as tmp_str:
            tmp = Path(tmp_str)
            manifest_path = tmp / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "fixtures": [
                            {
                                "id": "self-back-squat-side-1015-06",
                                "purpose": "development",
                                "conditions": {"challenge_tags": []},
                            },
                            {
                                "id": "self-snatch-side-0206-19",
                                "purpose": "development",
                                "conditions": {"challenge_tags": []},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            ds_dir = tmp / "dataset"
            (ds_dir / "annotations").mkdir(parents=True)
            (ds_dir / "dataset_manifest.json").write_text(
                json.dumps(
                    {
                        "clips": [
                            {"fixture_id": "self-back-squat-side-1015-06", "role": "scored-development-only"},
                            {"fixture_id": "self-snatch-side-0206-19", "role": "scored-development-only"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (ds_dir / "annotations" / "train.coco.json").write_text(
                json.dumps(
                    {
                        "images": [
                            {
                                "id": 1,
                                "fixture_id": "self-back-squat-side-1015-06",
                                "session_group": "session-self-2026-10-15",
                            }
                        ],
                        "annotations": [],
                    }
                ),
                encoding="utf-8",
            )
            (ds_dir / "annotations" / "val.coco.json").write_text(
                json.dumps(
                    {
                        "images": [
                            {
                                "id": 1,
                                "fixture_id": "self-snatch-side-0206-19",
                                "session_group": "session-self-2023-02-06",
                            }
                        ],
                        "annotations": [],
                    }
                ),
                encoding="utf-8",
            )

            validated = train_phase_b.validate_dataset_dir(ds_dir, tmp, manifests=[manifest_path])
            self.assertIn("dataset_manifest_sha256", validated["hashes"])

            # Leak session into val -> must raise RuntimeError
            (ds_dir / "annotations" / "val.coco.json").write_text(
                json.dumps(
                    {
                        "images": [
                            {
                                "id": 1,
                                "fixture_id": "self-snatch-side-0206-19",
                                "session_group": "session-self-2026-10-15",
                            }
                        ],
                        "annotations": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError):
                train_phase_b.validate_dataset_dir(ds_dir, tmp, manifests=[manifest_path])

            # Mismatch role in dataset_manifest.json -> must raise ClipRefused
            (ds_dir / "dataset_manifest.json").write_text(
                json.dumps(
                    {"clips": [{"fixture_id": "self-back-squat-side-1015-06", "role": "exploration"}]}
                ),
                encoding="utf-8",
            )
            with self.assertRaises(clips.ClipRefused):
                train_phase_b.validate_dataset_dir(ds_dir, tmp, manifests=[manifest_path])

    def test_synthetic_disc_mask_matches_circle(self) -> None:
        circle = geometry.Circle(50.0, 40.0, 12.0, "box")
        mask = detectors.synthetic_disc_mask(100, 80, circle)
        self.assertEqual(mask.shape, (80, 100))
        self.assertTrue(bool(mask[40, 50]))
        self.assertFalse(bool(mask[0, 0]))
        fitted = geometry.circle_from_mask(mask)
        self.assertIsNotNone(fitted)
        self.assertAlmostEqual(fitted.center_x_px, 50.0, delta=0.5)
        self.assertAlmostEqual(fitted.center_y_px, 40.0, delta=0.5)
        self.assertAlmostEqual(fitted.radius_px, 12.0, delta=1.0)

    def test_linear_sum_assignment_solves_rectangular_and_square_matrices(self) -> None:
        # 4 queries x 1 target (typical single-plate DETR matching)
        r, c = train_phase_b._linear_sum_assignment([[5.0], [1.2], [0.3], [4.0]])
        self.assertEqual(r.tolist(), [2])
        self.assertEqual(c.tolist(), [0])

        # 3 x 3 assignment where greedy row-by-row would be suboptimal
        cost = [
            [4.0, 1.0, 3.0],
            [2.0, 0.0, 5.0],
            [3.0, 2.0, 2.0],
        ]
        r3, c3 = train_phase_b._linear_sum_assignment(cost)
        self.assertEqual(r3.tolist(), [0, 1, 2])
        self.assertEqual(c3.tolist(), [1, 0, 2])


class AugmentationTests(unittest.TestCase):
    def test_hflip_bbox_mirrors_in_pixel_edge_coordinates(self) -> None:
        self.assertEqual(train_phase_b.hflip_bbox([10.0, 5.0, 20.0, 8.0], 100), [70.0, 5.0, 20.0, 8.0])
        # mirroring twice is the identity
        box = [12.25, 3.5, 30.5, 30.5]
        self.assertEqual(train_phase_b.hflip_bbox(train_phase_b.hflip_bbox(box, 64), 64), box)
        with self.assertRaises(ValueError):
            train_phase_b.hflip_bbox([0.0, 0.0, float("inf"), 1.0], 10)

    def test_crop_window_always_contains_the_whole_box(self) -> None:
        box = [300.25, 900.5, 200.0, 200.0]
        for scale in (0.1, 0.6, 0.8, 0.99):
            for u, v in ((0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (0.3, 0.9)):
                window = train_phase_b.crop_window_containing(box, 1080, 1920, scale, u, v)
                self.assertIsNotNone(window)
                x0, y0, cw, ch = window
                self.assertTrue(0 <= x0 and 0 <= y0 and x0 + cw <= 1080 and y0 + ch <= 1920)
                self.assertTrue(x0 <= box[0] and box[0] + box[2] <= x0 + cw)
                self.assertTrue(y0 <= box[1] and box[1] + box[3] <= y0 + ch)
                self.assertLess(cw, 1080)

    def test_crop_window_refuses_boxes_it_cannot_hold(self) -> None:
        # a box sticking out of the frame (unclipped teacher circle) is never cropped
        self.assertIsNone(train_phase_b.crop_window_containing([-5.0, 10.0, 50.0, 50.0], 100, 100, 0.6, 0.5, 0.5))
        # a box spanning the whole frame leaves no smaller crop
        self.assertIsNone(train_phase_b.crop_window_containing([0.0, 0.0, 100.0, 100.0], 100, 100, 0.6, 0.5, 0.5))
        with self.assertRaises(ValueError):
            train_phase_b.crop_window_containing([1.0, 1.0, 5.0, 5.0], 100, 100, 0.0, 0.5, 0.5)
        with self.assertRaises(ValueError):
            train_phase_b.crop_window_containing([1.0, 1.0, 5.0, 5.0], 100, 100, 0.5, 1.5, 0.5)

    def test_cosine_lr_factor_endpoints(self) -> None:
        self.assertEqual(train_phase_b.cosine_lr_factor(0, 100), 1.0)
        self.assertAlmostEqual(train_phase_b.cosine_lr_factor(100, 100), 0.05)
        self.assertAlmostEqual(train_phase_b.cosine_lr_factor(50, 100), 0.525)
        with self.assertRaises(ValueError):
            train_phase_b.cosine_lr_factor(0, 0)


@unittest.skipIf(np is None or cv2 is None, "numpy and opencv are required for image augmentation tests")
class AugmentSampleTests(unittest.TestCase):
    @staticmethod
    def _image_with_square(width: int, height: int, box: list[float]) -> "np.ndarray":
        img = np.full((height, width, 3), 40, dtype=np.uint8)
        x, y, w, h = (int(v) for v in box)
        img[y : y + h, x : x + w] = (250, 30, 30)
        return img

    def test_geometry_only_ops_keep_the_plate_inside_the_box(self) -> None:
        config = {
            **train_phase_b.AUGMENT_V1_CONFIG,
            "crop": {**train_phase_b.AUGMENT_V1_CONFIG["crop"], "p": 1.0},
            "hflip": {"p": 1.0},
            "colour": {**train_phase_b.AUGMENT_V1_CONFIG["colour"], "p": 0.0},
            "motion_blur": {**train_phase_b.AUGMENT_V1_CONFIG["motion_blur"], "p": 0.0},
            "jpeg": {**train_phase_b.AUGMENT_V1_CONFIG["jpeg"], "p": 0.0},
        }
        box = [60.0, 150.0, 40.0, 40.0]
        img = self._image_with_square(200, 320, box)
        original = img.copy()
        for seed in range(10):
            out, new_box, applied = train_phase_b.augment_sample(img, box, np.random.default_rng(seed), config)
            self.assertEqual(applied, ["crop", "hflip"])
            x, y, w, h = (int(v) for v in new_box)
            self.assertEqual((w, h), (40, 40))
            self.assertTrue(0 <= x and 0 <= y and x + w <= out.shape[1] and y + h <= out.shape[0])
            # every plate pixel is inside the transformed box, and the box is fully plate
            red = (out[..., 0] == 250)
            self.assertEqual(int(red.sum()), 40 * 40)
            self.assertTrue(red[y : y + h, x : x + w].all())
        np.testing.assert_array_equal(img, original)  # input never mutated

    def test_full_pipeline_is_seeded_and_returns_valid_output(self) -> None:
        box = [60.0, 150.0, 40.0, 40.0]
        img = self._image_with_square(200, 320, box)
        a = train_phase_b.augment_sample(img, box, np.random.default_rng([0, 3, 17]))
        b = train_phase_b.augment_sample(img, box, np.random.default_rng([0, 3, 17]))
        np.testing.assert_array_equal(a[0], b[0])
        self.assertEqual(a[1:], b[1:])
        self.assertEqual(a[0].dtype, np.uint8)
        self.assertEqual(a[0].ndim, 3)
        self.assertTrue(all(name in train_phase_b.AUGMENT_V1_CONFIG["order"] for name in a[2]))

    def test_rejects_invalid_inputs(self) -> None:
        with self.assertRaises(ValueError):
            train_phase_b.augment_sample(np.zeros((10, 10), dtype=np.uint8), [1, 1, 2, 2], np.random.default_rng(0))
        with self.assertRaises(ValueError):
            train_phase_b.augment_sample(
                np.zeros((10, 10, 3), dtype=np.uint8), [1, 1, 0, 2], np.random.default_rng(0)
            )


if __name__ == "__main__":
    unittest.main()
