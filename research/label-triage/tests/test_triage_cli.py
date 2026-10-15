"""Tests for the held-out refusal, voter command lines and the table/triage/calibrate commands."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_voters  # noqa: E402
import triage_cli  # noqa: E402


def fixture(identifier, purpose="development", tags=()):
    return {"id": identifier, "purpose": purpose, "conditions": {"challenge_tags": list(tags)}}


def sample(stamp, x, y=100.0):
    return {"timestamp_s": stamp, "state": "tracked", "center_px": {"x_px": x, "y_px": y}, "confidence": 0.9}


class GuardTests(unittest.TestCase):
    def manifest(self, directory, *fixtures):
        path = Path(directory) / "manifest.json"
        path.write_text(json.dumps({"schema_version": 1, "fixtures": list(fixtures)}), encoding="utf-8")
        return path

    def test_validation_and_reserved_fixtures_are_refused_development_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.manifest(tmp, fixture("dev"), fixture("val", "validation"),
                                 fixture("res", "development", ["split-reserved-57"]))
            run_voters.refuse_held_out(path, "dev")
            for held_out in ("val", "res"):
                with self.assertRaises(run_voters.RunError):
                    run_voters.refuse_held_out(path, held_out)

    def test_unknown_fixture_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(run_voters.RunError):
                run_voters.refuse_held_out(self.manifest(tmp, fixture("dev")), "nope")

    def test_run_refuses_held_out_before_starting_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.manifest(tmp, fixture("val", "validation"))
            with self.assertRaises(run_voters.RunError):
                run_voters.run(Path(tmp), path, "val", Path(tmp) / "seed.json", Path(tmp) / "out", "py", "gpu")
            self.assertFalse((Path(tmp) / "out").exists())


class CommandTests(unittest.TestCase):
    def test_three_voters_with_the_expected_interpreters_and_flags(self):
        commands = run_voters.build_commands(Path("/repo"), Path("/m.json"), "dev", Path("/s.json"),
                                             Path("/out"), "cpu-py", "gpu-py")
        by_name = {voter.name: argv for voter, argv, _ in commands}
        self.assertEqual(sorted(by_name), ["bootstapir-affine", "lk-affine", "sam2.1-bplus-circle"])
        self.assertEqual(by_name["lk-affine"][0], "cpu-py")
        self.assertEqual(by_name["sam2.1-bplus-circle"][0], "gpu-py")
        for argv in by_name.values():
            self.assertIn("--omit-runtime", argv)
        self.assertIn("--geometry-output", by_name["sam2.1-bplus-circle"])
        self.assertNotIn("--geometry-output", by_name["bootstapir-affine"])
        self.assertNotIn("csrt", " ".join(" ".join(a) for a in by_name.values()).replace("opencv-tracking", ""))

    def test_voters_are_never_run_with_an_allow_held_out_flag(self):
        for _, argv, _ in run_voters.build_commands(Path("/r"), Path("/m"), "d", Path("/s"), Path("/o"), "c", "g"):
            self.assertNotIn("--allow-held-out", argv)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        stamps = [float(i) for i in range(10)]
        offsets = {"a": 0.0, "b": 0.5, "c": 1.0}
        for name, offset in offsets.items():
            samples = []
            for i, stamp in enumerate(stamps):
                if name == "b" and i == 4:
                    samples.append({"timestamp_s": stamp, "state": "lost"})
                else:
                    samples.append(sample(stamp, 100.0 + 10 * i + offset + (20.0 if name == "c" and i == 7 else 0.0)))
            (self.dir / f"{name}.json").write_text(json.dumps(
                {"schema_version": 1, "samples": samples}), encoding="utf-8")
        labels = [{"annotation_state": "labelled", "timestamp_s": s, "center_px": {"x_px": 100.5 + 10 * i, "y_px": 100.0}}
                  for i, s in enumerate(stamps)]
        (self.dir / "ann.json").write_text(json.dumps({"schema_version": 1, "samples": labels}), encoding="utf-8")
        job = {"fixtures": [{"fixture_id": "dev", "annotation": "ann.json",
                             "voters": {n: f"{n}.json" for n in offsets}}]}
        self.job = self.dir / "job.json"
        self.job.write_text(json.dumps(job), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        self.assertEqual(triage_cli.main([str(a) for a in args]), 0)

    def test_table_has_one_row_per_timestamp_and_flags_the_lost_and_spread_frames(self):
        self.run_cli("table", "--job", self.job, "--out-dir", self.dir / "t")
        lines = (self.dir / "t" / "dev.disagreement.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 11)
        self.assertIn("lost:b", lines[5])
        self.assertIn("spread", lines[8])

    def test_triage_list_hides_reasons_and_keeps_them_in_a_separate_file(self):
        self.run_cli("triage", "--job", self.job, "--out-dir", self.dir / "x", "--fraction", "0.2", "--seed", 5)
        listing = (self.dir / "x" / "triage_list.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(listing[0], "fixture_id,index,timestamp_s")
        reasons = (self.dir / "x" / "triage_reasons.csv").read_text(encoding="utf-8")
        self.assertIn("flagged", reasons)
        self.assertNotIn("flagged", "\n".join(listing))
        indices = {int(line.split(",")[1]) for line in listing[1:]}
        self.assertTrue({1, 5, 8} <= indices)

    def test_every_command_is_byte_identical_on_rerun(self):
        for run in ("1", "2"):
            out = self.dir / run
            self.run_cli("table", "--job", self.job, "--out-dir", out)
            self.run_cli("triage", "--job", self.job, "--out-dir", out, "--fraction", "0.3", "--seed", 9)
            self.run_cli("calibrate", "--job", self.job, "--out", out / "calibration.json")
        for name in ("dev.disagreement.csv", "triage_list.csv", "triage_reasons.csv",
                     "triage_summary.json", "calibration.json"):
            self.assertEqual((self.dir / "1" / name).read_bytes(), (self.dir / "2" / name).read_bytes(), name)

    def test_calibration_pools_counts_and_is_not_a_verdict(self):
        self.run_cli("calibrate", "--job", self.job, "--out", self.dir / "c.json")
        report = json.loads((self.dir / "c.json").read_text(encoding="utf-8"))
        self.assertEqual(report["fixtures"]["dev"]["n_labelled_rows"], 10)
        self.assertEqual(report["pooled"]["agreeing"]["n"] + report["pooled"]["not_agreeing"]["n"], 10)
        self.assertEqual(sorted(report), ["fixtures", "note", "pooled"])
        self.assertEqual(sorted(report["pooled"]), ["agreeing", "not_agreeing"])

    def test_bad_input_returns_an_error_and_writes_nothing(self):
        (self.dir / "a.json").write_text("{}", encoding="utf-8")
        self.assertEqual(triage_cli.main(["table", "--job", str(self.job), "--out-dir", str(self.dir / "bad")]), 1)
        self.assertFalse((self.dir / "bad").exists())


if __name__ == "__main__":
    unittest.main()
