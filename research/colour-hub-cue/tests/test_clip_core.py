"""Synthetic end-to-end tests of the per-clip arms, the registration guard and the runner refusals."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import clip_core  # noqa: E402
import run_experiment  # noqa: E402
import synthetic  # noqa: E402

STEP_PX = 4.0
TIMES = [round(0.1 * i, 6) for i in range(6)]
START = (150.0, 160.0)


def truth(i):
    return (START[0] + STEP_PX * i, START[1])


def frames(lost_hub_at=()):
    for i, _ in enumerate(TIMES):
        yield synthetic.render(*truth(i), hub=i not in lost_hub_at, noise=2.0 if i in lost_hub_at else 1.5, seed=i)


def coarse(lost=(), offset=1.5):
    result = {}
    for i, t in enumerate(TIMES):
        if i in lost:
            result[t] = {"timestamp_s": t, "state": "lost"}
        else:
            x, y = truth(i)
            result[t] = {"timestamp_s": t, "state": "tracked", "center_px": {"x_px": x + offset, "y_px": y - offset},
                         "confidence": 0.8}
    return result


def seed():
    x, y = truth(0)
    return {"timestamp_s": TIMES[0], "frame_index": 0,
            "target": {"center": {"x_px": x, "y_px": y}, "radius_px": synthetic.RADIUS}}


class ProcessClipTests(unittest.TestCase):
    def run_clip(self, **kwargs):
        return clip_core.process_clip(frames(kwargs.pop("lost_hub_at", ())), TIMES, kwargs.pop("coarse", coarse()),
                                      seed(), **kwargs)

    def test_all_registered_arms_are_produced_with_one_sample_per_frame(self):
        result = self.run_clip()
        self.assertEqual(sorted(result["arms"]), sorted(["baseline", "C", "CH", "H"]))
        for arm, samples in result["arms"].items():
            self.assertEqual([s["timestamp_s"] for s in samples], TIMES)
            self.assertEqual(samples[0]["confidence"], 1.0)
            if arm in ("baseline", "C"):  # fused arms follow experiment.py: seed weight 0, seed frame not scored
                self.assertEqual(samples[0]["center_px"], seed()["target"]["center"])

    def test_gated_rim_recovers_the_true_centre_better_than_the_offset_coarse_centre(self):
        result = self.run_clip()
        for i in range(1, len(TIMES)):
            x, y = truth(i)
            gated = result["arms"]["C"][i]["center_px"]
            self.assertAlmostEqual(gated["x_px"], x, delta=0.6)
            self.assertAlmostEqual(gated["y_px"], y, delta=0.6)
            self.assertAlmostEqual(result["arms"]["baseline"][i]["center_px"]["x_px"], x + 1.5)

    def test_hub_displacement_is_measured_between_consecutive_frames(self):
        result = self.run_clip()
        deltas = [r["delta_px"] for r in result["relative_samples"]]
        self.assertTrue(all(d is not None for d in deltas))
        for delta in deltas:
            self.assertAlmostEqual(delta["x_px"], STEP_PX, delta=0.5)
            self.assertAlmostEqual(delta["y_px"], 0.0, delta=0.5)
        self.assertTrue(result["hub_supported"])

    def test_a_frame_without_an_accepted_hub_gives_no_relative_evidence_and_nothing_is_interpolated(self):
        result = self.run_clip(lost_hub_at={3})
        rel = {r["timestamp_s"]: r for r in result["relative_samples"]}
        self.assertIsNone(rel[TIMES[3]]["delta_px"])
        self.assertIsNone(rel[TIMES[3]]["confidence"])
        self.assertIsNone(rel[TIMES[4]]["delta_px"])
        self.assertIsNotNone(rel[TIMES[2]]["delta_px"])

    def test_coarse_loss_gives_no_coordinate_in_any_arm_that_depends_on_it(self):
        result = self.run_clip(coarse=coarse(lost={2}))
        for arm in ("baseline", "C", "H", "CH"):
            self.assertEqual(result["arms"][arm][2], {"timestamp_s": TIMES[2], "state": "lost"}, arm)

    def test_control_arm_uses_a_different_colour_model_and_loses_coverage(self):
        wrong = clip_core.cues.PlateColour((255.0, 128.0, 128.0), (1.0, 1.0, 1.0))
        result = self.run_clip(control_colour=wrong)
        self.assertEqual(sum(s["state"] == "tracked" for s in result["arms"]["C_control"]), 1)

    def test_rerun_is_identical(self):
        self.assertEqual(json.dumps(self.run_clip(), sort_keys=True), json.dumps(self.run_clip(), sort_keys=True))

    def test_seed_without_a_matching_frame_or_tracked_coarse_seed_is_an_error(self):
        bad_seed = seed() | {"timestamp_s": 5.0}
        with self.assertRaises(ValueError):
            clip_core.process_clip(frames(), TIMES, coarse(), bad_seed)
        with self.assertRaises(ValueError):
            clip_core.process_clip(frames(), TIMES, coarse(lost={0}), seed())


class HubShareTests(unittest.TestCase):
    def test_share_and_registered_support_rule(self):
        diagnostics = [{"timestamp_s": t, "accepted": i % 2 == 0} for i, t in enumerate(TIMES)]
        share = clip_core.hub_labelled_share(diagnostics, TIMES)
        self.assertEqual(share, 0.5)
        self.assertTrue(clip_core.hub_supported_for_scoring(share))
        self.assertFalse(clip_core.hub_supported_for_scoring(0.49))
        self.assertFalse(clip_core.hub_supported_for_scoring(None))
        self.assertIsNone(clip_core.hub_labelled_share(diagnostics, [99.0]))


class RegistrationGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "registration.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, **fields):
        record = {"plan_sha256": run_experiment.io.digest(run_experiment.PLAN), "owner_go_ahead_to_run": False}
        self.path.write_text(json.dumps(record | fields), encoding="utf-8")

    def test_plan_hash_mismatch_is_refused(self):
        self.write(plan_sha256="0" * 64)
        with self.assertRaises(run_experiment.Refused):
            run_experiment.load_registration(self.path)

    def test_no_go_ahead_is_refused_and_a_recorded_go_ahead_is_accepted(self):
        self.write()
        with self.assertRaises(run_experiment.Refused):
            run_experiment.require_go_ahead(self.path)
        self.write(owner_go_ahead_to_run=True)
        self.assertTrue(run_experiment.require_go_ahead(self.path)["owner_go_ahead_to_run"])

    def test_run_without_go_ahead_exits_nonzero_and_writes_nothing(self):
        self.write()
        out = Path(self.tmp.name) / "out"
        code = run_experiment.main(["run", "--registration", str(self.path), "--manifest", "m.json", "--fixture",
                                    "self-snatch-side-002", "--seed", "s.json", "--coarse", "c.json",
                                    "--output-dir", str(out)])
        self.assertEqual(code, 1)
        self.assertFalse(out.exists())

    def test_only_the_three_development_clips_and_never_held_out_ones(self):
        records = {
            "dev": {"id": "self-snatch-side-002", "purpose": "development", "conditions": {"challenge_tags": []}},
            "other": {"id": "self-snatch-side-1015-01", "purpose": "development",
                      "conditions": {"challenge_tags": []}},
            "held": {"id": "self-snatch-side-002", "purpose": "development",
                     "conditions": {"challenge_tags": ["split-reserved-57"]}},
        }
        with mock.patch.object(run_experiment.io, "fixture", side_effect=lambda m, f, **k: records[m]):
            self.assertEqual(run_experiment.require_allowed_fixture("dev", "self-snatch-side-002"), records["dev"])
            with self.assertRaises(run_experiment.Refused):
                run_experiment.require_allowed_fixture("other", "self-snatch-side-1015-01")
            with self.assertRaises(run_experiment.Refused):
                run_experiment.require_allowed_fixture("held", "self-snatch-side-002")

    def test_the_runner_has_no_override_flag(self):
        self.assertNotIn("allow-held-out", run_experiment.build_parser().format_help())
        for sub in ("run", "fit-colour", "grant-go-ahead"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                run_experiment.build_parser().parse_args([sub, "--allow-held-out"])


if __name__ == "__main__":
    unittest.main()
