from __future__ import annotations

import copy
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import motion_observation as motion


def frame(t, y=None):
    return {"time_s": t, "candidates": [] if y is None else [
        {"center_x_px": 10.0, "center_y_px": y, "radius_px": 10.0, "score": 0.9}]}


class MotionTests(unittest.TestCase):
    def test_motion_and_stationary_warning_leave_input_untouched(self):
        records = [frame(i / 10, i * 2.0) for i in range(20)]
        original = copy.deepcopy(records)
        report = motion.observe(records)
        self.assertEqual(report["observation"], "motion_present")
        self.assertAlmostEqual(report["supported_duration_s"], 1.9)
        self.assertEqual(report["supported_time_fraction"], 1.0)
        self.assertEqual(records, original)
        self.assertEqual(motion.observe([frame(0, 5), frame(.1, 5)])["observation"],
                         "insufficient_motion_evidence")

    def test_loss_and_long_gap_never_create_motion(self):
        for middle in ([frame(.2)], []):
            records = [frame(0, 0), frame(.1, 0), *middle, frame(2, 100), frame(2.1, 100)]
            report = motion.observe(records)
            self.assertEqual(report["observation"], "insufficient_motion_evidence")
            self.assertEqual(report["segment_count"], 2)
            self.assertEqual(report["max_segment_p02_p98_travel_radii"], 0)
            self.assertAlmostEqual(report["supported_duration_s"], .2)
            self.assertLess(report["supported_time_fraction"], .1)

    def test_dense_short_burst_does_not_dominate_time_percentiles(self):
        records = [frame(i / 10, 0) for i in range(101)]
        records.extend(frame(10 + i / 100000, 100) for i in range(1, 1001))
        records.extend(frame(10.01 + i / 10, 0) for i in range(1, 101))
        report = motion.observe(records)
        self.assertEqual(report["max_segment_p02_p98_travel_radii"], 0)
        self.assertEqual(report["max_segment_range_radii"], 10)

    def test_empty_lost_and_single_point_are_inconclusive(self):
        for records in ([], [frame(0)], [frame(0, 1)]):
            report = motion.observe(records)
            self.assertEqual(report["observation"], "no_supported_intervals")
            self.assertIsNone(report["max_segment_p02_p98_travel_radii"])

    def test_threshold_is_inclusive_and_radius_reference_is_fixed(self):
        records = [frame(0, 0), frame(.1, 15), frame(.2, 0)]
        records[1]["candidates"][0]["radius_px"] = 100
        report = motion.observe(records)
        self.assertEqual(report["reference_radius_px"], 10)
        self.assertEqual(report["max_segment_p02_p98_travel_radii"], 1.5)
        self.assertEqual(report["observation"], "motion_present")

    def test_invalid_input_fails_closed(self):
        bad = [
            [frame(0, 0), frame(0, 1)], [frame(1, 0), frame(0, 1)],
            [frame(float("nan"), 0)], [frame(0, float("inf"))],
            [frame(-1, 0)],
        ]
        for field, value in (("radius_px", 0), ("score", 1.1), ("center_x_px", float("nan"))):
            record = frame(0, 0)
            record["candidates"][0][field] = value
            bad.append([record])
        record = frame(0, 0)
        record["candidates"] *= 2
        bad.append([record])
        for records in bad:
            with self.subTest(records=records), self.assertRaises(ValueError):
                motion.observe(records)

    def test_run_report_is_deterministic_gated_and_hashes_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            fixture = {"id": "example", "purpose": "exploration",
                       "conditions": {"challenge_tags": ["split-exploration"]}}
            manifest.write_text(json.dumps({"fixtures": [fixture]}))
            run = root / "run"
            folder = run / "candidates" / "track"
            folder.mkdir(parents=True)
            source = {"clips": {"example": "exploration"}, "methods": {"track": {}}}
            (run / "run.json").write_text(json.dumps(source))
            track = folder / "example.jsonl"
            track.write_text("\n".join(json.dumps({**r, "fixture_id": "example", "method": "track"})
                                       for r in [frame(0, 0), frame(.1, 0)]))
            first = motion.report_run(run, [manifest])
            self.assertEqual(first, motion.report_run(run, [manifest]))
            for input_path, digest in ((track, first["tracks"]["track"]["example"]["source_sha256"]),
                                       (run / "run.json", first["source_run_sha256"])):
                original = input_path.read_bytes()
                read_bytes, read_text = Path.read_bytes, Path.read_text

                def replace_after_bytes(path, *args, **kwargs):
                    data = read_bytes(path, *args, **kwargs)
                    if path == input_path:
                        path.write_bytes(original + b"\n")
                    return data

                def replace_after_text(path, *args, **kwargs):
                    data = read_text(path, *args, **kwargs)
                    if path == input_path:
                        path.write_bytes(original + b"\n")
                    return data

                try:
                    with patch.object(Path, "read_bytes", replace_after_bytes), \
                            patch.object(Path, "read_text", replace_after_text):
                        changed = motion.report_run(run, [manifest])
                    reported = (changed["source_run_sha256"] if input_path.name == "run.json" else
                                changed["tracks"]["track"]["example"]["source_sha256"])
                    self.assertEqual(reported, digest)
                    self.assertNotEqual(reported, hashlib.sha256(input_path.read_bytes()).hexdigest())
                finally:
                    input_path.write_bytes(original)
            original = manifest.read_bytes()
            load_fixtures = motion.clips.load_fixtures

            def replace_manifest(paths):
                fixtures = load_fixtures(paths)
                manifest.write_bytes(original + b"\n")
                return fixtures

            try:
                with patch.object(motion.clips, "load_fixtures", replace_manifest), self.assertRaises(ValueError):
                    motion.report_run(run, [manifest])
            finally:
                manifest.write_bytes(original)
            track.write_text(track.read_text() + "\n")
            self.assertNotEqual(first["tracks"], motion.report_run(run, [manifest])["tracks"])
            fixture["conditions"]["challenge_tags"].append("split-reserved-57")
            manifest.write_text(json.dumps({"fixtures": [fixture]}))
            track.write_text("not json: must refuse before reading")
            with self.assertRaises(motion.clips.ClipRefused):
                motion.report_run(run, [manifest])
            fixture["conditions"]["challenge_tags"].remove("split-reserved-57")
            manifest.write_text(json.dumps({"fixtures": [fixture]}))
            track.unlink()
            with self.assertRaises(FileNotFoundError):
                motion.report_run(run, [manifest])

    def test_cli_preserves_sources_and_refuses_output_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"fixtures": [{"id": "self-other-side-0215-27"}]}))
            run = root / "run"
            folder = run / "candidates" / "track"
            folder.mkdir(parents=True)
            (run / "run.json").write_text(json.dumps({"clips": {
                "self-other-side-0215-27": "scored-development-only"}, "methods": {"track": {}}}))
            track = folder / "self-other-side-0215-27.jsonl"
            track.write_text(json.dumps({**frame(0, 1), "fixture_id": "self-other-side-0215-27",
                                         "method": "track"}) + "\n")
            original = track.read_bytes()
            output = root / "report.json"
            args = ["--run-dir", str(run), "--manifest", str(manifest), "--output", str(output)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(motion.main(args), 0)
            saved = output.read_bytes()
            with self.assertRaises(FileExistsError):
                motion.main(args)
            self.assertEqual(output.read_bytes(), saved)
            self.assertEqual(track.read_bytes(), original)
            other = root / "other.json"
            with contextlib.redirect_stdout(io.StringIO()):
                motion.main(args[:-1] + [str(other)])
            self.assertEqual(saved, other.read_bytes())
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                motion.main(args[:-1] + [str(run / "overwrite.json")])

    def test_unsupported_restart_run_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            manifest.write_text('{"fixtures": []}')
            (root / "run.json").write_text(json.dumps({"clips": {}, "methods": {
                "track": {"redetect": {"rule": "redetect-v1"}}}}))
            with self.assertRaises(ValueError):
                motion.report_run(root, [manifest])


if __name__ == "__main__":
    unittest.main()
