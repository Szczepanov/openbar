from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "validation" / "tools"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


m0_evidence = load("m0_evidence")
m0_private_evidence = load("m0_private_evidence")
selection = load("tracker_filter_selection")


class CandidateFreezeTests(unittest.TestCase):
    def test_extracts_selected_configuration_per_filter_family(self):
        artifact = {
            "schema_version": 3,
            "development": [
                {
                    "family": "raw",
                    "selected": {
                        "implementation": "raw-identity",
                        "version": "1",
                        "parameters": {},
                    },
                },
                {
                    "family": "moving_average",
                    "selected": {
                        "implementation": "centered-moving-average",
                        "version": "1",
                        "parameters": {"window": 5, "max_gap_s": 0.05},
                    },
                },
                {
                    "family": "savitzky_golay",
                    "selected": {
                        "implementation": "timestamp-aware-savitzky-golay",
                        "version": "1",
                        "parameters": {
                            "window": 7,
                            "polynomial_order": 2,
                            "max_gap_s": 0.05,
                        },
                    },
                },
                {
                    "family": "kalman",
                    "selected": {
                        "implementation": "constant-velocity-kalman",
                        "version": "1",
                        "parameters": {
                            "acceleration_variance_m2_s4": 2.0,
                            "measurement_variance_m2": 0.000004,
                            "initial_velocity_variance_m2_s2": 1.0,
                            "confidence_window_samples": 3,
                            "max_gap_s": 0.05,
                        },
                    },
                },
            ],
        }

        candidates = selection.extract_filter_candidates(artifact)

        self.assertEqual(
            {item["family"] for item in candidates},
            {"raw", "moving_average", "savitzky_golay", "kalman"},
        )
        moving = next(item for item in candidates if item["family"] == "moving_average")
        self.assertEqual(moving["cli_args"][0:2], ["--filter", "moving-average"])
        self.assertIn("--filter-window", moving["cli_args"])
        self.assertIn("--filter-max-gap-s", moving["cli_args"])

    def test_rejects_incomplete_family_freeze(self):
        with self.assertRaises(selection.SelectionError):
            selection.extract_filter_candidates(
                {
                    "schema_version": 3,
                    "development": [
                        {
                            "family": "raw",
                            "selected": {
                                "implementation": "raw-identity",
                                "version": "1",
                                "parameters": {},
                            },
                        }
                    ],
                }
            )


class ScopeTests(unittest.TestCase):
    def test_synthetic_validation_fixture_is_not_held_out_real_evidence(self):
        with self.assertRaises(selection.SelectionError):
            selection.validation_fixtures(
                {
                    "fixtures": [
                        {
                            "id": "synthetic-validation",
                            "purpose": "validation",
                            "source": {"kind": "synthetic"},
                            "camera": {"view": "side", "movement": "fixed"},
                        }
                    ]
                }
            )

    def test_validation_fixture_must_be_side_fixed(self):
        with self.assertRaises(selection.SelectionError):
            selection.validation_fixtures(
                {
                    "fixtures": [
                        {
                            "id": "moving-camera",
                            "purpose": "validation",
                            "source": {"kind": "self_recorded"},
                            "camera": {"view": "side", "movement": "handheld"},
                        }
                    ]
                }
            )


class FalseTrackTests(unittest.TestCase):
    def test_high_confidence_wrong_target_is_counted(self):
        annotation = {
            "timebase": {"decoder_match_tolerance_s": 0.001},
            "samples": [
                {
                    "timestamp_s": 1.0,
                    "annotation_state": "labelled",
                    "quality": "high",
                    "center_px": {"x_px": 10.0, "y_px": 10.0},
                },
                {
                    "timestamp_s": 2.0,
                    "annotation_state": "labelled",
                    "quality": "high",
                    "center_px": {"x_px": 20.0, "y_px": 20.0},
                },
            ],
        }
        prediction = {
            "samples": [
                {
                    "timestamp_s": 1.0,
                    "state": "tracked",
                    "center_px": {"x_px": 10.5, "y_px": 10.0},
                    "confidence": 0.95,
                },
                {
                    "timestamp_s": 2.0,
                    "state": "tracked",
                    "center_px": {"x_px": 30.0, "y_px": 20.0},
                    "confidence": 0.90,
                },
            ]
        }

        result = selection.false_track_diagnostics(annotation, prediction)

        self.assertEqual(result["high_confidence_samples"], 2)
        self.assertEqual(result["high_confidence_false_track_samples"], 1)
        self.assertAlmostEqual(result["high_confidence_false_track_fraction"], 0.5)


class GateTests(unittest.TestCase):
    def test_original_gate_operators_remain_strict(self):
        self.assertEqual(
            selection.gate_status(
                {"plate_center_mae_px": 2.999, "tracking_availability": 0.991}
            ),
            {"plate_center_mae": "PASS", "tracking_availability": "PASS"},
        )
        self.assertEqual(
            selection.gate_status(
                {"plate_center_mae_px": 3.0, "tracking_availability": 0.99}
            ),
            {"plate_center_mae": "FAIL", "tracking_availability": "FAIL"},
        )


class FilterMetricTests(unittest.TestCase):
    def test_filtered_position_error_uses_calibration_reference_coordinates(self):
        annotation = {
            "timebase": {"decoder_match_tolerance_s": 0.001},
            "samples": [
                {
                    "timestamp_s": 1.0,
                    "annotation_state": "labelled",
                    "quality": "high",
                    "center_px": {"x_px": 110.0, "y_px": 80.0},
                },
                {
                    "timestamp_s": 2.0,
                    "annotation_state": "labelled",
                    "quality": "high",
                    "center_px": {"x_px": 120.0, "y_px": 70.0},
                },
            ],
        }
        analysis = {
            "calibration": {
                "scale": {"metres_per_pixel": 0.01},
                "reference": {
                    "geometry": {"center_px": {"x_px": 100.0, "y_px": 100.0}}
                },
            },
            "derived": {
                "filtered": {
                    "filter": {
                        "implementation": "raw-identity",
                        "version": "1",
                        "parameters": {},
                    },
                    "samples": [
                        {
                            "timestamp_s": 1.0,
                            "x_m": 0.10,
                            "y_m": 0.20,
                            "confidence": 1.0,
                        },
                        {
                            "timestamp_s": 2.0,
                            "x_m": 0.20,
                            "y_m": 0.30,
                            "confidence": 1.0,
                        },
                    ],
                }
            },
        }

        metrics = selection.filtered_position_metrics(annotation, analysis)

        self.assertEqual(metrics["comparable_samples"], 2)
        self.assertAlmostEqual(metrics["position_mae_m"], 0.0)
        self.assertAlmostEqual(metrics["position_rmse_m"], 0.0)


class FinalizationTests(unittest.TestCase):
    def evidence(self):
        return {
            "schema_version": 1,
            "study_version": selection.STUDY_VERSION,
            "tracker_results": [
                {
                    "tracker_id": "passing",
                    "gate_status": {
                        "plate_center_mae": "PASS",
                        "tracking_availability": "PASS",
                    },
                },
                {
                    "tracker_id": "failing",
                    "gate_status": {
                        "plate_center_mae": "FAIL",
                        "tracking_availability": "PASS",
                    },
                },
            ],
            "filter_results": [
                {
                    "tracker_id": "passing",
                    "filter_family": "raw",
                    "comparable_samples": 10,
                }
            ],
        }

    def test_cannot_select_tracker_that_fails_original_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            output = Path(tmp) / "final.json"
            evidence.write_text(json.dumps(self.evidence()), encoding="utf-8")
            with self.assertRaises(selection.SelectionError):
                selection.finalize_decision(
                    evidence,
                    output,
                    tracker="failing",
                    filter_family="raw",
                    reject=False,
                    rationale="fails the original gate",
                )

    def test_rejection_is_explicit_and_keeps_selected_fields_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            output = Path(tmp) / "final.json"
            evidence.write_text(json.dumps(self.evidence()), encoding="utf-8")

            result = selection.finalize_decision(
                evidence,
                output,
                tracker=None,
                filter_family=None,
                reject=True,
                rationale="held-out evidence rejects current candidates",
            )

            self.assertEqual(result["decision"]["status"], "rejected")
            self.assertIsNone(result["decision"]["selected_tracker"])
            self.assertIsNone(result["decision"]["selected_filter_family"])


if __name__ == "__main__":
    unittest.main()
