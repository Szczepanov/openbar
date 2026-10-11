"""#79 inventory: pinned-consumer parser preflight (H2), its node bridge boundary and a real-consumer run."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agreement79_slot_evidence as evidence  # noqa: E402
import agreement79_study as study  # noqa: E402
from test_agreement79_inventory import COMMIT, InventoryTestCase  # noqa: E402

ACCEPTED = {"accepted": True, "error": None}


class InventoryPreflightTests(InventoryTestCase):
    def test_every_analyzable_slot_is_preflighted_with_its_bound_files(self) -> None:
        document = self.inventory_ok()
        self.assertEqual(len(self.fake.preflight_calls), 18)
        by_slot = {wl.stem: (analysis, wl) for _, analysis, wl in self.fake.preflight_calls}
        for slot in study.SLOTS:
            entry = self.slot_of(document, slot)
            analysis, wl = by_slot[slot]
            self.assertEqual((self.fake.rel(analysis), self.fake.rel(wl)),
                             (entry["files"]["analysis"]["path"], entry["files"]["wl_csv"]["path"]))
            self.assertEqual(entry["parser_preflight"], {"openbar": ACCEPTED, "wl": ACCEPTED})

    def test_slots_already_blocked_or_without_both_files_are_not_preflighted(self) -> None:
        self.fake.slot("S1-SQ-2")["wl_csv"] = None
        self.fake.clips["S2-SN-1"]["assessment"] = False
        self.fake.failing_sessions.add("sess-charlie")
        document = self.inventory_ok()
        called = {wl.stem for _, _, wl in self.fake.preflight_calls}
        self.assertEqual(called, set(study.SLOTS[:12]) - {"S1-SQ-2", "S2-SN-1"})
        for slot in ("S1-SQ-2", "S2-SN-1", "S3-CL-1"):
            self.assertIsNone(self.slot_of(document, slot)["parser_preflight"], slot)

    def test_non_blocking_failures_still_get_preflighted(self) -> None:
        self.fake.clips["S1-SQ-1"]["scale_row"] = False
        self.inventory_ok()
        self.assertIn("S1-SQ-1", {wl.stem for _, _, wl in self.fake.preflight_calls})

    def test_openbar_rejection_blocks_the_slot_and_stays_private(self) -> None:
        message = "OpenBar analysis has no kinematics samples."
        self.fake.rejected["S2-CL-1"] = {"openbar": message}
        document = self.inventory_ok()
        slot = self.slot_of(document, "S2-CL-1")
        self.assertEqual(self.failures(document, "S2-CL-1"), {"processing:openbar_parser_rejected"})
        self.assertFalse(slot["analyzable"])
        self.assertEqual(slot["parser_preflight"], {"openbar": {"accepted": False, "error": message}, "wl": ACCEPTED})
        pairs = json.loads((self.fake.study_dir / "out/pairs-clean.json").read_bytes())["pairs"]
        self.assertNotIn("S2-CL-1", [pair["label"] for pair in pairs])
        summary = (self.fake.study_dir / "out/lock-summary.json").read_text(encoding="utf-8")
        self.assertNotIn(message, summary)
        self.assertNotIn("parser_preflight", summary)
        self.assertEqual(json.loads(summary)["counts"]["analyzable"], 17)

    def test_wl_rejection_blocks_the_slot(self) -> None:
        self.fake.rejected["S3-SN-2"] = {"wl": "Row 12 of the per-frame table is incomplete.", "openbar": "bad"}
        document = self.inventory_ok()
        self.assertEqual(self.failures(document, "S3-SN-2"),
                         {"wl_export:wl_parser_rejected", "processing:openbar_parser_rejected"})
        self.assertFalse(self.slot_of(document, "S3-SN-2")["analyzable"])

    def test_preflight_runs_for_in_progress_drafts(self) -> None:
        self.fake.slots_doc["collection_status"] = "in_progress"
        self.fake.rejected["S1-SQ-1"] = {"wl": "Frame rate must be a positive number."}
        document = self.inventory_ok()
        self.assertEqual(self.failures(document, "S1-SQ-1"), {"wl_export:wl_parser_rejected"})

    def test_preflight_infrastructure_failure_aborts_without_writing(self) -> None:
        def broken(_app: Path, _analysis: Path, _wl: Path) -> dict[str, Any]:
            raise study.StudyInfrastructureError("node could not be run")
        code, _, err = self.fake.run(preflight=broken)
        self.assertEqual(code, 3)
        self.assertIn("node", err)
        self.assertFalse((self.fake.study_dir / "out").exists())


class ConsumerCheckoutTests(InventoryTestCase):
    def consumer_state(self, state: tuple[str, bool]):
        def git(root: Path) -> tuple[str, bool]:
            return state if root == self.fake.app.parent else self.fake.git(root)
        return git

    def test_consumer_at_other_commit_or_dirty_aborts_with_exit_3(self) -> None:
        for state in (("d" * 40, True), (study.CONSUMER_COMMIT, False)):
            with self.subTest(state):
                code, _, err = self.fake.run(git=self.consumer_state(state))
                self.assertEqual(code, 3, err)
                self.assertIn(study.CONSUMER_COMMIT, err)
                self.assertFalse((self.fake.study_dir / "out").exists())
                self.assertEqual(self.fake.preflight_calls, [])

    def test_consumer_app_is_required(self) -> None:
        slots_path = self.fake.write()
        argv = self.fake.argv(slots_path)[:-2]
        with self.assertRaises(SystemExit) as raised:
            self.fake.main(argv)
        self.assertEqual(raised.exception.code, 2)

    def test_consumer_commit_is_not_the_tool_commit(self) -> None:
        self.assertNotEqual(COMMIT, study.CONSUMER_COMMIT)


def completed(code: int, stdout: bytes = b"", stderr: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout, stderr)


class PreflightBridgeBoundaryTests(unittest.TestCase):
    """The Python side of the bridge: argv, strict output shape, and every failure is infrastructure."""

    def call(self, result: subprocess.CompletedProcess | Exception) -> dict[str, Any]:
        effect = result if isinstance(result, Exception) else None
        with mock.patch.object(evidence.subprocess, "run", side_effect=effect, return_value=result) as run:
            value = evidence.consumer_preflight(Path("/app"), Path("/a.json"), Path("/w.csv"))
        self.assertEqual(run.call_args.args[0], ["node", "--experimental-strip-types", str(evidence.BRIDGE),
                                                 "preflight", str(Path("/app")), str(Path("/a.json")),
                                                 str(Path("/w.csv"))])
        return value

    def line(self, value: Any) -> bytes:
        return (json.dumps(value) + "\n").encode()

    def test_well_formed_output_is_returned(self) -> None:
        output = {"openbar": ACCEPTED, "wl": {"accepted": False, "error": "Row 3 is incomplete."}}
        self.assertEqual(self.call(completed(0, b"warning\n" + self.line(output))), output)

    def test_failures_are_infrastructure(self) -> None:
        cases = {
            "no node": OSError("node not found"),
            "bridge exit": completed(1, b"", b"Cannot find module openBarAnalysis.ts\n"),
            "no output": completed(0, b""),
            "not json": completed(0, b"{oops\n"),
            "reps leaked": completed(0, self.line({"openbar": ACCEPTED, "wl": ACCEPTED, "reps": []})),
            "extra source key": completed(0, self.line({"openbar": {**ACCEPTED, "reps": []}, "wl": ACCEPTED})),
            "accepted text": completed(0, self.line({"openbar": {"accepted": "yes", "error": None}, "wl": ACCEPTED})),
            "accepted with error": completed(0, self.line({"openbar": {"accepted": True, "error": "x"}, "wl": ACCEPTED})),
            "rejected without error": completed(0, self.line({"openbar": ACCEPTED, "wl": {"accepted": False,
                                                                                         "error": None}})),
            "list": completed(0, self.line([ACCEPTED, ACCEPTED])),
        }
        for name, result in cases.items():
            with self.subTest(name), self.assertRaises(study.StudyInfrastructureError):
                self.call(result)

    def test_bridge_source_keeps_reps_mode_and_mirrors_the_consumer_calls(self) -> None:
        source = evidence.BRIDGE.read_text(encoding="utf-8")
        self.assertNotIn("\r", source)
        for text in ("parseOpenBarAnalysis(text, segmentation.CONCENTRIC_SEGMENTATION_V2)",
                     "frameCount: parsed.frames.length", "reps: parsed.reps",
                     "parseWlAnalysisCsv(", "WL_ANALYSIS_CSV_PARSER_V2", "new TextDecoder('utf-8', { fatal: true })"):
            self.assertIn(text, source)


def analysis_document() -> dict[str, Any]:
    """Synthetic analysis-v1 subset the consumer parser accepts: two squat-like cycles at 30 fps."""
    dt, profile = 1 / 30, ([-0.6] * 20 + [0.0] * 3 + [0.6] * 25 + [0.0] * 6) * 2
    samples, t, y = [], 0.0, 0.0
    for index, velocity in enumerate(profile):
        if index:
            t, y = t + dt, y + velocity * dt
        samples.append({"timestamp_s": t, "x_m": 0.0, "y_m": y, "confidence": 1.0})
    kinematics = [{**sample, "vx_mps": None if index == 0 else 0.0, "vy_mps": None if index == 0 else velocity}
                  for index, (sample, velocity) in enumerate(zip(samples, profile))]
    return {
        "schema_version": 1, "identity": {"source_id": "synthetic-video", "source_sha256": "a" * 64},
        "calibration": {"method": "plate_diameter", "method_version": 1,
                        "scale": {"diameter_m": 0.45, "diameter_px": 200, "metres_per_pixel": 0.00225},
                        "coordinate_convention": "reference_centre_x_right_y_up",
                        "quality": {"status": "unassessed", "warnings": ["geometry_unassessed"]}},
        "derived": {"filtered": {"filter": {"implementation": "savitzky-golay", "version": "1",
                                            "parameters": {"window": 9, "order": 2}}, "samples": samples},
                    "kinematics": {"input": "filtered", "samples": kinematics, "method": {
                        "implementation": "backward-difference", "version": "1",
                        "parameters": {"max_gap_s": 0.2, "min_confidence": 0.5}}}},
        "provenance": {"pipeline": {"openbar_version": "0.1.0", "git_commit": "703c097"},
                       "tracker": {"id": "opencv-csrt", "implementation": {
                           "implementation": "opencv-csrt", "version": "1",
                           "parameters": {"tracker": "opencv-csrt", "threads": 1}}}},
    }


def wl_csv() -> bytes:
    """Synthetic WL Analysis per-frame export (header block, then ordinal, time, velocity, displacement)."""
    profile = ([-0.6] * 20 + [0.0] * 3 + [0.6] * 25 + [0.0] * 6) * 2
    lines = ["Video id,date,Video resolution,Frame rate,weight,tags", "1,03/10/2026,1920x1080,30,40,squat",
             "Video id: 1", 'Frame ordinal,Time (s),"velocity (vertical, m/s)","displacement (vertical, cm)"']
    displacement = 0.0
    for ordinal, velocity in enumerate(profile, start=2):
        displacement += velocity * 100 / 30
        lines.append(f"{ordinal},{(ordinal - 1) / 30!r},{velocity!r},{displacement!r}")
    return ("\n".join(lines) + "\n").encode("utf-8")


@unittest.skipUnless(os.environ.get("OPENBAR_CONSUMER_APP"), "set OPENBAR_CONSUMER_APP to the pinned consumer app dir")
class ConsumerPreflightIntegrationTests(unittest.TestCase):
    """Runs the real pinned consumer parsers through the bridge's preflight mode (node --experimental-strip-types)."""

    def setUp(self) -> None:
        self.app = Path(os.environ["OPENBAR_CONSUMER_APP"]).resolve()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name).resolve()
        self.analysis = self.dir / "analysis.json"
        self.wl = self.dir / "wl.csv"
        self.analysis.write_bytes(json.dumps(analysis_document()).encode("utf-8"))
        self.wl.write_bytes(wl_csv())

    def preflight(self) -> dict[str, Any]:
        return evidence.consumer_preflight(self.app, self.analysis, self.wl)

    def test_synthetic_pair_is_accepted(self) -> None:
        self.assertEqual(self.preflight(), {"openbar": ACCEPTED, "wl": ACCEPTED})

    def test_truncated_wl_csv_is_rejected_by_the_wl_parser_only(self) -> None:
        self.wl.write_bytes(wl_csv().splitlines(keepends=True)[0])
        result = self.preflight()
        self.assertEqual(result["openbar"], ACCEPTED)
        self.assertFalse(result["wl"]["accepted"])
        self.assertIsInstance(result["wl"]["error"], str)

    def test_invalid_utf8_and_non_analysis_are_rejected(self) -> None:
        self.analysis.write_bytes(b'{"schema_version": 1, "x": "\xff"}')
        result = self.preflight()
        self.assertFalse(result["openbar"]["accepted"])
        self.assertEqual(result["wl"], ACCEPTED)
        self.analysis.write_bytes(b"{}")
        self.assertFalse(self.preflight()["openbar"]["accepted"])


if __name__ == "__main__":
    unittest.main()
