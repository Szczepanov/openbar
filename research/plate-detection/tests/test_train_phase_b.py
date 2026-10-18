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


if __name__ == "__main__":
    unittest.main()
