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
import motion_metrics as metrics
import study_io as io

PUBLIC = io.ROOT / "validation" / "fixtures" / "public"
FIXTURE = "synthetic-clean-side-12"
SEED = PUBLIC / "seeds" / f"{FIXTURE}.manual-target-seed-v1.json"
ANNOTATION = PUBLIC / "annotations" / f"{FIXTURE}.annotation-v1.json"
PREDICTION = PUBLIC / "predictions" / "synthetic-perfect.prediction-v1.json"


def labels(count: int = 5) -> list[dict]:
    return [
        {
            "timestamp_s": i / 10,
            "frame_index": i,
            "annotation_state": "labelled",
            "center_px": {"x_px": 50 + i, "y_px": 100 - i},
        }
        for i in range(count)
    ]


def observations(reference: list[dict]) -> list[dict]:
    return [
        {
            "timestamp_s": row["timestamp_s"],
            "state": "tracked",
            "confidence": 0.8,
            "center_px": dict(row["center_px"]),
        }
        for row in reference
    ]


class ReviewRegressionTests(unittest.TestCase):
    def test_seed_exclusion_does_not_create_cross_seed_delta_interval(self):
        reference = labels()
        result = metrics.evaluate(reference, observations(reference), seed_timestamp_s=0.2)

        self.assertEqual(
            [(row["start_s"], row["end_s"]) for row in result["intervals"]],
            [(0.0, 0.1), (0.3, 0.4)],
        )
        self.assertEqual(result["available_intervals"], 2)
        self.assertNotIn((0.1, 0.3), [(row["start_s"], row["end_s"]) for row in result["intervals"]])

    def test_relative_seed_exclusion_does_not_bridge_or_anchor_at_seed(self):
        reference = labels()
        predicted = observations(reference)
        edges = [
            {
                "previous_timestamp_s": reference[index - 1]["timestamp_s"],
                "timestamp_s": reference[index]["timestamp_s"],
                "delta_px": {"x_px": 1.0, "y_px": -1.0},
                "confidence": 0.9,
            }
            for index in range(1, len(reference))
        ]

        result = metrics.relative_diagnostics(
            reference,
            predicted,
            edges,
            seed_timestamp_s=0.2,
        )

        self.assertEqual(
            [(row["start_s"], row["end_s"]) for row in result["intervals"]],
            [(0.0, 0.1), (0.3, 0.4)],
        )
        self.assertTrue(all(row["anchor_timestamp_s"] != 0.2 for row in result["drift_samples"]))

    def test_canonical_outputs_use_pre_resolved_seed_timestamp(self):
        args = argparse.Namespace(
            manifest=PUBLIC / "manifest.json",
            annotation=ANNOTATION,
            seed=SEED,
            fixture=FIXTURE,
            max_gap_s=0.2,
            repository_root=io.ROOT,
        )
        item = io.fixture(io.load(args.manifest), FIXTURE)
        annotation = io.load(ANNOTATION)

        with tempfile.TemporaryDirectory() as tmp:
            args.output_dir = Path(tmp)
            with patch("experiment.subprocess.run") as run:
                experiment.canonical_outputs(
                    args,
                    item,
                    {"perfect": PREDICTION},
                    annotation,
                    0.123,
                )

            suite = json.loads((args.output_dir / "canonical.benchmark-v1.json").read_text())
            self.assertEqual(suite["cases"][0]["selected_range_s"]["start_s"], 0.123)
            analyze = run.call_args_list[-1].args[0]
            self.assertAlmostEqual(float(analyze[analyze.index("--start-s") + 1]), 0.122999)

    def test_snapshot_private_fixture_output_cannot_escape_private_tree(self):
        manifest = io.load(PUBLIC / "manifest.json")
        private_manifest = copy.deepcopy(manifest)
        fixture = next(row for row in private_manifest["fixtures"] if row["id"] == FIXTURE)
        fixture["source"]["redistribution_status"] = "private_only"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            io.write(manifest_path, private_manifest)
            args = [
                "snapshot",
                "--manifest",
                str(manifest_path),
                "--input",
                f"perfect={PREDICTION}",
                "--output",
                str(root / "escaped-baseline.json"),
            ]
            self.assertEqual(experiment.main(args), 2)
            self.assertFalse((root / "escaped-baseline.json").exists())


if __name__ == "__main__":
    unittest.main()
