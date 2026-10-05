"""Synthetic-only tests for the conservative research fusion primitive."""

import copy
import importlib.util
import json
import math
from pathlib import Path
import random
import unittest


SPEC = importlib.util.spec_from_file_location("bar_path_fusion", Path(__file__).parents[1] / "fusion.py")
FUSION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FUSION)


def tracked(timestamp, x, y=10.0, confidence=0.8):
    return {
        "timestamp_s": timestamp, "state": "tracked",
        "center_px": {"x_px": x, "y_px": y}, "confidence": confidence,
    }


def relative(previous, timestamp, dx, dy=0.0, confidence=0.8):
    return {
        "timestamp_s": timestamp, "previous_timestamp_s": previous,
        "delta_px": {"x_px": dx, "y_px": dy}, "confidence": confidence,
    }


def positions(samples):
    return [
        (round(sample["center_px"]["x_px"], 8), round(sample["center_px"]["y_px"], 8))
        if sample["state"] == "tracked" else None
        for sample in samples
    ]


class FusionTests(unittest.TestCase):
    def test_empty_and_absolute_only_identity(self):
        self.assertEqual(FUSION.fuse([], [], {}), [])
        absolute = [tracked(0, 10), tracked(0.13, 11, confidence=0), {"timestamp_s": 0.2, "state": "lost"}]
        self.assertEqual(FUSION.fuse(absolute, [], {}), absolute)
        self.assertIsNot(FUSION.fuse(absolute, [], {})[0], absolute[0])

    def test_two_frame_objective_has_expected_solution(self):
        absolute = [tracked(0, 10), tracked(0.1, 12)]
        fused = FUSION.fuse(absolute, [relative(0, 0.1, 4)], {"huber_delta_px": 100})
        self.assertEqual(positions(fused), [(9.33333333, 10.0), (12.66666667, 10.0)])

    def test_drifting_relative_evidence_is_constrained_by_absolute_anchors(self):
        absolute = [tracked(index / 10, 20 + index) for index in range(9)]
        edges = [relative((index - 1) / 10, index / 10, 1.2) for index in range(1, 9)]
        fused = FUSION.fuse(absolute, edges, {})
        integrated_error = sum(0.2 * index for index in range(9))
        fused_error = sum(abs(sample["center_px"]["x_px"] - (20 + index)) for index, sample in enumerate(fused))
        self.assertLess(fused_error, integrated_error / 4)
        self.assertLess(abs(fused[-1]["center_px"]["x_px"] - 28), 0.2)
        self.assertTrue(all(sample["confidence"] <= 0.8 for sample in fused))

    def test_seeded_input_is_byte_reproducible_and_unchanged(self):
        rng = random.Random(923)
        absolute = [tracked(index / 100, 40 + index + rng.uniform(-0.1, 0.1)) for index in range(30)]
        edges = [relative((index - 1) / 100, index / 100, 1 + rng.uniform(-0.03, 0.03)) for index in range(1, 30)]
        config = dict(FUSION.FUSION_CONFIG)
        original = copy.deepcopy((absolute, edges, config))
        outputs = [json.dumps(FUSION.fuse(absolute, edges, config), sort_keys=True, allow_nan=False) for _ in range(3)]
        self.assertEqual(outputs, [outputs[0]] * 3)
        self.assertEqual((absolute, edges, config), original)

    def test_lost_frame_is_hard_break_despite_relative_only_chain(self):
        absolute = [tracked(0, 10), {"timestamp_s": 0.1, "state": "lost"}, tracked(0.2, 20)]
        edges = [relative(0, 0.1, 2), relative(0.1, 0.2, 2)]
        self.assertEqual(FUSION.fuse(absolute, edges, {}), absolute)
        all_lost = [{"timestamp_s": time, "state": "lost"} for time in (0, 0.1, 0.2)]
        self.assertEqual(FUSION.fuse(all_lost, edges, {}), all_lost)

    def test_missing_relative_measurement_splits_independent_segments(self):
        absolute = [tracked(0, 10), tracked(0.1, 12), tracked(0.2, 30), tracked(0.3, 32)]
        edges = [relative(0, 0.1, 4), {
            "timestamp_s": 0.2, "previous_timestamp_s": 0.1, "delta_px": None, "confidence": None,
        }, relative(0.2, 0.3, 4)]
        fused = FUSION.fuse(absolute, edges, {})
        independent = FUSION.fuse(absolute[:2], edges[:1], {}) + FUSION.fuse(absolute[2:], edges[2:], {})
        self.assertEqual(fused, independent)

    def test_timestamp_gap_blocks_edge_and_threshold_is_inclusive(self):
        absolute = [tracked(0, 10), tracked(0.25, 12)]
        edges = [relative(0, 0.25, 4)]
        self.assertEqual(FUSION.fuse(absolute, edges, {"max_gap_s": 0.2}), absolute)
        self.assertEqual(positions(FUSION.fuse(absolute, edges, {"max_gap_s": 0.25})), [(9.33333333, 10.0), (12.66666667, 10.0)])

    def test_large_registration_outlier_has_bounded_influence_and_confidence_penalty(self):
        absolute = [tracked(0, 100), tracked(0.1, 101)]
        edges = [relative(0, 0.1, 1001)]
        robust = FUSION.fuse(absolute, edges, {"huber_delta_px": 1})
        least_squares = FUSION.fuse(absolute, edges, {"huber_delta_px": 10000})
        self.assertLess(abs(robust[0]["center_px"]["x_px"] - 100), 1.1)
        self.assertEqual(least_squares[0]["state"], "lost")
        self.assertLess(robust[0]["confidence"], 0.2)
        self.assertLess(robust[1]["confidence"], 0.2)

    def test_low_quality_absolute_outlier_is_rejected_by_relative_agreement(self):
        absolute = [tracked(index / 10, 100 + index) for index in range(7)]
        absolute[3] = tracked(0.3, 203, confidence=0.05)
        edges = [relative((index - 1) / 10, index / 10, 1) for index in range(1, 7)]
        fused = FUSION.fuse(absolute, edges, {"relative_weight": 3})
        self.assertLess(abs(fused[3]["center_px"]["x_px"] - 103), 1)
        self.assertLess(fused[3]["confidence"], 0.005)

    def test_constant_displacement_and_acceleration_are_preserved_without_smoothing(self):
        times = [0, 0.02, 0.09, 0.14, 0.3]
        for xs in ([20, 22, 24, 26, 28], [20, 21, 24, 33, 58]):
            absolute = [tracked(time, x, 20 + index * 3) for index, (time, x) in enumerate(zip(times, xs))]
            edges = [relative(times[index - 1], times[index], xs[index] - xs[index - 1], 3) for index in range(1, len(times))]
            fused = FUSION.fuse(absolute, edges, {})
            self.assertEqual(positions(fused), positions(absolute))
            self.assertEqual([sample["confidence"] for sample in fused], [0.8] * len(times))

    def test_zero_confidence_is_not_promoted(self):
        absolute = [tracked(0, 10, confidence=0), tracked(0.1, 11, confidence=0)]
        self.assertEqual(FUSION.fuse(absolute, [relative(0, 0.1, 1)], {}), absolute)

    def test_zero_confidence_relative_outlier_has_no_solve_or_confidence_influence(self):
        absolute = [tracked(0, 10), tracked(0.1, 11)]
        fused = FUSION.fuse(absolute, [relative(0, 0.1, 1000, confidence=0)], {})
        self.assertEqual(fused, absolute)

    def test_optional_initial_missing_relative_sample(self):
        absolute = [tracked(0, 10)]
        edges = [{"timestamp_s": 0, "previous_timestamp_s": None, "delta_px": None}]
        self.assertEqual(FUSION.fuse(absolute, edges, {}), absolute)

    def test_invalid_configurations(self):
        configs = [{"unknown": 1}, {"irls_iterations": 0}, {"irls_iterations": True}, {"irls_iterations": 1.5}, {"confidence_weight_floor": 1.1}, []]
        for key in FUSION.FUSION_CONFIG:
            if key != "irls_iterations":
                configs.extend({key: value} for value in (0, -1, math.nan, math.inf, -math.inf, True, "1"))
        for config in configs:
            with self.subTest(config=config), self.assertRaises(ValueError):
                FUSION.fuse([], [], config)

    def test_invalid_absolute_numeric_and_state_contracts(self):
        cases = [
            [tracked(0, -1)], [tracked(-1, 10)], [tracked(0, 10), tracked(0, 11)],
            [tracked(0.1, 10), tracked(0, 11)], [{"timestamp_s": 0, "state": "unknown"}],
            [{"timestamp_s": 0, "state": "lost", "center_px": None}],
            [{"timestamp_s": 0, "state": "lost", "confidence": None}],
            [{"timestamp_s": 0, "state": "tracked", "center_px": {"x_px": 1, "y_px": 1}}],
            [dict(tracked(0, 10), unexpected=True)],
        ]
        for value in (math.nan, math.inf, -math.inf, True, "1", 10**400):
            cases.extend(([tracked(value, 10)], [tracked(0, value)], [tracked(0, 10, y=value)], [tracked(0, 10, confidence=value)]))
        cases.extend(([tracked(0, 10, confidence=-0.1)], [tracked(0, 10, confidence=1.1)]))
        for samples in cases:
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                FUSION.fuse(samples, [], {})

    def test_invalid_relative_numeric_and_endpoint_contracts(self):
        absolute = [tracked(0, 10), tracked(0.1, 11), tracked(0.2, 12)]
        cases = [
            [relative(0, 0.2, 2)], [relative(0.05, 0.1, 1)], [relative(0, 0.3, 3)],
            [relative(0, 0.1, 1), relative(0, 0.1, 1)],
            [relative(0.1, 0.2, 1), relative(0, 0.1, 1)],
            [relative(None, 0.1, 1)], [relative(None, 0, 1)], [relative(0, 0, 1)],
            [{"timestamp_s": 0.1, "previous_timestamp_s": 0, "delta_px": None, "confidence": 0}],
            [{"timestamp_s": 0.1, "previous_timestamp_s": 0, "delta_px": {"x_px": 1, "y_px": 0}}],
            [{"timestamp_s": 0.1, "previous_timestamp_s": 0}],
        ]
        for value in (math.nan, math.inf, -math.inf, True, "1", 10**400):
            cases.extend(([relative(value, 0.1, 1)], [relative(0, value, 1)], [relative(0, 0.1, value)], [relative(0, 0.1, 1, dy=value)], [relative(0, 0.1, 1, confidence=value)]))
        cases.extend(([relative(0, 0.1, 1, confidence=-0.1)], [relative(0, 0.1, 1, confidence=1.1)]))
        for samples in cases:
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                FUSION.fuse(absolute, samples, {})

    def test_invalid_evidence_is_rejected_even_across_a_loss_or_gap(self):
        absolute = [tracked(0, 10), {"timestamp_s": 1, "state": "lost"}]
        with self.assertRaises(ValueError):
            FUSION.fuse(absolute, [relative(0, 1, math.nan)], {})
        with self.assertRaises(ValueError):
            FUSION.fuse(absolute, [relative(0.5, 1, 2)], {})

    def test_overflowing_normal_system_is_rejected(self):
        with self.assertRaises(ValueError):
            FUSION.fuse([tracked(0, 1e308), tracked(0.1, 1e308)], [relative(0, 0.1, 1)], {"absolute_weight": 1e308})


if __name__ == "__main__":
    unittest.main()
