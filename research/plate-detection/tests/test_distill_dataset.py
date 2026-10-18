from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import clips  # noqa: E402
import distill_dataset  # noqa: E402
import truth  # noqa: E402


def _sample_record(
    fixture_id: str,
    frame_index: int,
    time_s: float,
    cx: float = 500.0,
    cy: float = 400.0,
    r: float = 100.0,
    refused: bool = False,
    lost: bool = False,
) -> dict:
    rec: dict = {
        "fixture_id": fixture_id,
        "role": "scored-development-only",
        "method": "track_sam2_sam3seed",
        "frame_index": frame_index,
        "pts": frame_index * 1000,
        "time_s": time_s,
        "width_px": 1080,
        "height_px": 1920,
        "latency_s": 0.1,
        "candidates": []
        if lost
        else [
            {
                "center_x_px": cx,
                "center_y_px": cy,
                "radius_px": r,
                "score": 0.95,
            }
        ],
    }
    if frame_index == 0:
        rec["seed"] = {
            "selected_index": None if refused else 0,
            "refused": refused,
            "tracked": not refused and not lost,
        }
    return rec


class DistillDatasetTests(unittest.TestCase):
    def test_session_group_And_fold_assignment_have_zero_session_overlap(self) -> None:
        sample_clips = [
            "vbt-45854c8cdf352a6f",
            "self-back-squat-side-1015-06",
            "self-snatch-side-1015-01",
            "self-clean-side-0203-17",
            "self-snatch-side-0206-19",
            "self-snatch-side-0212-24",
            "self-back-squat-side-002",
        ]
        folds = distill_dataset.assign_session_folds(sample_clips)
        self.assertEqual(folds["self-back-squat-side-1015-06"], "train")
        self.assertEqual(folds["self-snatch-side-1015-01"], "train")
        self.assertEqual(folds["self-snatch-side-0206-19"], "val")
        self.assertEqual(folds["self-snatch-side-0212-24"], "val")
        self.assertEqual(folds["self-back-squat-side-002"], "val")

    def test_curate_clip_refuses_reserved_and_non_development_clips(self) -> None:
        reserved = {
            "id": "self-snatch-side-1015-01",
            "purpose": "validation",
            "conditions": {"challenge_tags": ["split-reserved-57"]},
        }
        with self.assertRaises(clips.ClipRefused):
            distill_dataset.curate_clip_records(reserved, [], [])

        pilot = {
            "id": "self-snatch-side-0203-16",
            "purpose": "boundary",
            "conditions": {"challenge_tags": []},
        }
        with self.assertRaises(clips.ClipRefused):
            distill_dataset.curate_clip_records(pilot, [], [])

        unexposed_frozen = {
            "id": "self-snatch-side-9999-01",
            "purpose": "validation",
            "conditions": {"challenge_tags": ["split-frozen-eval"]},
        }
        with self.assertRaises(clips.ClipRefused):
            distill_dataset.curate_clip_records(unexposed_frozen, [], [])

    def test_curate_clip_admits_retired_scored_development_only_clip(self) -> None:
        retired = {
            "id": "self-back-squat-side-1015-06",
            "purpose": "validation",
            "conditions": {"challenge_tags": ["split-frozen-eval"]},
        }
        records = [_sample_record("self-back-squat-side-1015-06", 0, 0.0, cx=500.0, cy=400.0, r=100.0)]
        truth_pts = [truth.TruthPoint(0.0, 500.0, 400.0, 100.0, "test")]
        curated = distill_dataset.curate_clip_records(retired, records, truth_pts)
        self.assertEqual(curated["role"], "scored-development-only")
        self.assertEqual(curated["status"], "included")
        self.assertEqual(curated["accepted_frames"], 1)

    def test_curate_clip_excludes_refused_or_off_target_seed(self) -> None:
        fixture = {
            "id": "self-other-side-0215-27",
            "purpose": "development",
            "conditions": {"challenge_tags": []},
        }
        # Seed on wrong plate (cx=200 vs truth cx=700)
        records = [_sample_record("self-other-side-0215-27", 0, 0.0, cx=200.0, cy=400.0, r=100.0)]
        truth_pts = [truth.TruthPoint(0.0, 700.0, 400.0, 100.0, "test")]
        curated = distill_dataset.curate_clip_records(fixture, records, truth_pts)
        self.assertEqual(curated["status"], "excluded")
        self.assertEqual(curated["reason"], "seed_off_target_vs_truth")
        self.assertEqual(curated["accepted_frames"], 0)

        # Nested refusal dict as emitted by sam3-margin-v2 in run_phase_a2.py
        nested_refused = _sample_record("self-other-side-0215-27", 0, 0.0, cx=500.0, cy=400.0, r=100.0)
        nested_refused["seed"] = {
            "selected_index": 0,
            "tracked": True,
            "refusal": {"rule": "sam3-margin-v2", "refused": True, "margin": 0.047},
        }
        curated_nested = distill_dataset.curate_clip_records(fixture, [nested_refused], [])
        self.assertEqual(curated_nested["status"], "excluded")
        self.assertEqual(curated_nested["reason"], "seed_refused")
        self.assertEqual(curated_nested["accepted_frames"], 0)

    def test_curate_clip_applies_guard_and_builds_coco_split(self) -> None:
        fixture = {
            "id": "self-back-squat-side-1015-06",
            "purpose": "development",
            "conditions": {"challenge_tags": []},
        }
        records = [
            _sample_record("self-back-squat-side-1015-06", 0, 0.0, cx=500.0, cy=400.0, r=100.0),
            _sample_record("self-back-squat-side-1015-06", 2, 0.0667, cx=502.0, cy=405.0, r=100.0),
            # Glitch frame: huge jump should be rejected by guard_track
            _sample_record("self-back-squat-side-1015-06", 4, 0.1333, cx=950.0, cy=950.0, r=250.0),
        ]
        truth_pts = [truth.TruthPoint(0.0, 500.0, 400.0, 100.0, "test")]
        curated = distill_dataset.curate_clip_records(fixture, records, truth_pts)
        curated["fold"] = "train"
        self.assertEqual(curated["status"], "included")
        self.assertEqual(curated["accepted_frames"], 2)

        strided = distill_dataset.curate_clip_records(fixture, records, truth_pts, frame_stride=2)
        self.assertEqual(strided["accepted_frames"], 1)

        coco = distill_dataset.build_coco_split([curated], "train")
        self.assertEqual(len(coco["images"]), 2)
        self.assertEqual(len(coco["annotations"]), 2)
        self.assertEqual(coco["annotations"][0]["bbox"], [400.0, 300.0, 200.0, 200.0])

    def test_export_distillation_dataset_writes_manifest_and_coco_files(self) -> None:
        import json
        import shutil
        import tempfile

        private_root = distill_dataset.label_package.PRIVATE_ROOT.resolve()
        private_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private_root, prefix="test-phase-b-") as tmp_str:
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
            teacher_dir = tmp / "teacher-run"
            cand_dir = teacher_dir / "candidates" / "track_sam2_sam3seed_guard"
            cand_dir.mkdir(parents=True)
            (teacher_dir / "run.json").write_text(
                json.dumps(
                    {
                        "harness": "test",
                        "clips": {
                            "self-back-squat-side-1015-06": "scored-development-only",
                            "self-snatch-side-0206-19": "scored-development-only",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            for cid in ("self-back-squat-side-1015-06", "self-snatch-side-0206-19"):
                rec = _sample_record(cid, 0, 0.0, cx=500.0, cy=400.0, r=100.0)
                (cand_dir / f"{cid}.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")

            out_dir = tmp / "distilled-out"
            result = distill_dataset.export_distillation_dataset(
                repo_root=tmp,
                teacher_run_dir=teacher_dir,
                method="track_sam2_sam3seed_guard",
                output_dir=out_dir,
                manifests=[manifest_path],
            )
            self.assertEqual(result["summary"]["included_clips"], 2)
            self.assertEqual(result["summary"]["train_images"], 1)
            self.assertEqual(result["summary"]["val_images"], 1)
            self.assertTrue((out_dir / "dataset_manifest.json").is_file())
            self.assertTrue((out_dir / "annotations" / "train.coco.json").is_file())
            self.assertTrue((out_dir / "annotations" / "val.coco.json").is_file())
            shutil.rmtree(out_dir, ignore_errors=True)

            # Verify run.json role mismatch is refused before export
            (teacher_dir / "run.json").write_text(
                json.dumps({"clips": {"self-back-squat-side-1015-06": "exploration"}}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(clips.ClipRefused):
                distill_dataset.export_distillation_dataset(
                    repo_root=tmp,
                    teacher_run_dir=teacher_dir,
                    method="track_sam2_sam3seed_guard",
                    output_dir=tmp / "distilled-refused",
                    manifests=[manifest_path],
                )


if __name__ == "__main__":
    unittest.main()
