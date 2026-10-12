"""#79 inventory: tool-tree lock rule, process boundaries, session/order/date checks, inputs, verify."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agreement79_inventory as inventory  # noqa: E402
import agreement79_report_render as render  # noqa: E402
import agreement79_slot_evidence as evidence  # noqa: E402
import agreement79_study as study  # noqa: E402
from test_agreement79_inventory import (  # noqa: E402
    COMMIT, DERIVED_CASES, SESSION_DATES, SESSION_IDS, InventoryTestCase)


class ToolTreeTests(InventoryTestCase):
    """H5: a lockable inventory needs a tracked-clean tool checkout; drafts record the dirty state."""

    def dirty_tool(self, root: Path) -> tuple[str, bool]:
        state = self.fake.git(root)
        return (COMMIT, False) if state == (COMMIT, True) else state

    def test_lockable_inventory_with_dirty_tool_tree_is_refused(self) -> None:
        code, _, err = self.fake.run(git=self.dirty_tool)
        self.assertEqual(code, 1)
        self.assertIn("tool checkout", err)
        self.assertFalse((self.fake.study_dir / "out").exists())

    def test_in_progress_draft_records_a_dirty_tool_tree(self) -> None:
        self.fake.slots_doc["collection_status"] = "in_progress"
        document = self.inventory_ok(git=self.dirty_tool)
        self.assertFalse(document["tool_tree_clean"] or document["lockable"])
        self.assertFalse(self.fake.load(name="lock-summary.json")["tool_tree_clean"])


def completed(code: int, stdout: bytes = b"", stderr: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout, stderr)


class SessionStatusBoundaryTests(unittest.TestCase):
    """H6: `vbt_session.py status` exit 0/1 is a verdict; a crash or any other exit is infrastructure."""

    def status(self, result: subprocess.CompletedProcess | Exception) -> tuple[bool, str | None]:
        effect = result if isinstance(result, Exception) else None
        with mock.patch.object(evidence.subprocess, "run", side_effect=effect, return_value=result) as run:
            value = evidence.session_status(Path("/repo"), "sess-alpha")
        argv = run.call_args.args[0]
        self.assertEqual(argv[1:], ["research/vbt-workflow/vbt_session.py", "status", "--session", "sess-alpha"])
        self.assertTrue(run.call_args.kwargs["capture_output"])
        return value

    def test_exit_zero_is_complete(self) -> None:
        self.assertEqual(self.status(completed(0, b"session complete\n")), (True, None))

    def test_exit_one_keeps_the_last_message_line(self) -> None:
        result = completed(1, b"checking\n", b"warning: x\nerror: clip 2 output hash differs\n\n")
        self.assertEqual(self.status(result), (False, "error: clip 2 output hash differs"))
        self.assertEqual(self.status(completed(1, b"incomplete: 1 clip pending\n")), (False, "incomplete: 1 clip pending"))
        self.assertEqual(self.status(completed(1)), (False, None))

    def test_traceback_is_infrastructure(self) -> None:
        stderr = b"Traceback (most recent call last):\n  File \"x\", line 1\nKeyError: 'clips'\n"
        for code in (0, 1):
            with self.subTest(code), self.assertRaises(study.StudyInfrastructureError):
                self.status(completed(code, b"", stderr))

    def test_other_exit_codes_are_infrastructure(self) -> None:
        for code in (2, 3, -9):
            with self.subTest(code), self.assertRaises(study.StudyInfrastructureError):
                self.status(completed(code, b"", b"usage: vbt_session.py\n"))

    def test_unrunnable_status_is_infrastructure(self) -> None:
        with self.assertRaises(study.StudyInfrastructureError):
            self.status(OSError("no python"))


class SessionDetailTests(InventoryTestCase):
    def test_failed_status_detail_is_kept_in_the_private_session_entry(self) -> None:
        self.fake.failing_sessions.add("sess-bravo")
        document = self.inventory_ok()
        self.assertEqual(document["sessions"]["sess-bravo"]["status_detail"], "error: session sess-bravo is incomplete")
        self.assertIsNone(document["sessions"]["sess-alpha"]["status_detail"])
        summary = (self.fake.study_dir / "out/lock-summary.json").read_text(encoding="utf-8")
        self.assertNotIn("incomplete", summary)

    def test_status_crash_aborts_with_exit_3(self) -> None:
        def crash(_root: Path, _session: str) -> tuple[bool, str | None]:
            raise study.StudyInfrastructureError("vbt_session.py status crashed")
        code, _, err = self.fake.run(status=crash)
        self.assertEqual(code, 3)
        self.assertIn("crashed", err)
        self.assertFalse((self.fake.study_dir / "out").exists())


class SessionMembershipTests(InventoryTestCase):
    """H7: a session record holding a clip that no slot of that session claims taints the whole session."""

    def session_slots(self, session: str) -> list[str]:
        return [slot for slot in study.SLOTS if self.fake.slot(slot)["session_id"] == session]

    def assert_session_flagged(self, document: dict, session: str) -> None:
        for slot in study.SLOTS:
            flagged = "protocol:session_has_unenrolled_clip" in self.failures(document, slot)
            self.assertEqual(flagged, slot in self.session_slots(session), slot)
            self.assertTrue(self.slot_of(document, slot)["analyzable"], slot)

    def test_unassigned_clip_in_record_flags_every_slot_of_the_session(self) -> None:
        self.fake.records["sess-alpha"]["extra_clips"] = [{"fixture_id": "vbt-ffffffffffffff01", "decision": "confirmed"}]
        self.assert_session_flagged(self.inventory_ok(), "sess-alpha")

    def test_clip_assigned_to_another_session_counts_as_unassigned(self) -> None:
        other = self.fake.slot("S2-SQ-1")["fixture_id"]
        self.fake.records["sess-charlie"]["extra_clips"] = [{"fixture_id": other}]
        self.assert_session_flagged(self.inventory_ok(), "sess-charlie")

    def test_unassigned_skipped_name_flags_every_slot_of_the_session(self) -> None:
        self.fake.records["sess-bravo"]["skipped"] = ["IMG_9999.MOV"]
        self.assert_session_flagged(self.inventory_ok(), "sess-bravo")

    def test_assigned_skipped_name_and_null_fixture_slot_names_are_not_flagged(self) -> None:
        self.fake.slot("S3-CL-2")["fixture_id"] = None
        self.fake.clips["S3-CL-2"]["fixture_id"] = None
        self.fake.slot("S3-CL-2")["protocol_failures"] = [{"stage": "confirmation", "reason": "skipped"}]
        self.fake.records["sess-charlie"]["skipped"] = [self.fake.slot("S3-CL-2")["original_name"]]
        document = self.inventory_ok()
        for slot in study.SLOTS:
            self.assertNotIn("protocol:session_has_unenrolled_clip", self.failures(document, slot), slot)


class SlotOrderTests(InventoryTestCase):
    """H7: within a session, container creation times must strictly increase in frozen slot order."""

    def order_flagged(self, document: dict) -> set[str]:
        return {slot for slot in study.SLOTS if "protocol:slot_order_violation" in self.failures(document, slot)}

    def test_default_increasing_times_are_in_order(self) -> None:
        self.assertEqual(self.order_flagged(self.inventory_ok()), set())

    def test_slot_recorded_before_its_predecessor_is_flagged_non_blocking(self) -> None:
        self.fake.creation["S1-SN-1"] = "2026-10-11T09:59:00Z"
        document = self.inventory_ok()
        self.assertEqual(self.order_flagged(document), {"S1-SN-1"})
        self.assertTrue(self.slot_of(document, "S1-SN-1")["analyzable"])

    def test_equal_times_are_not_strictly_after(self) -> None:
        self.fake.creation["S2-CL-2"] = self.fake.probe(Path("S2-CL-1.mp4"))
        self.assertEqual(self.order_flagged(self.inventory_ok()), {"S2-CL-2"})

    def test_offsets_are_compared_as_instants(self) -> None:
        # 10:01:30Z: after S1-SQ-1 (10:00Z) and before S1-SN-1 (10:02Z), though its text sorts later.
        self.fake.creation["S1-SQ-2"] = "2026-10-11T12:01:30+02:00"
        self.assertEqual(self.order_flagged(self.inventory_ok()), set())

    def test_slots_without_creation_time_are_skipped(self) -> None:
        self.fake.creation["S1-SQ-2"] = None
        self.fake.creation["S1-SN-1"] = "2026-10-11T10:00:30Z"  # after S1-SQ-1, before S1-SQ-2's default
        document = self.inventory_ok()
        self.assertEqual(self.order_flagged(document), set())
        self.assertIn("novelty:creation_time_unavailable", self.failures(document, "S1-SQ-2"))

    def test_sessions_are_ordered_independently(self) -> None:
        self.fake.creation["S2-SQ-1"] = "2026-10-11T09:00:00Z"  # earlier than all of session 1
        self.assertEqual(self.order_flagged(self.inventory_ok()), set())


class SessionDateTests(InventoryTestCase):
    """H1: sessions sharing a day are non-conforming (C1 fails), never an abort; the inventory still locks."""

    FLAG = "protocol:session_dates_not_distinct"

    def test_two_sessions_on_one_day_flag_both_sessions_and_still_lock(self) -> None:
        for slot in study.SLOTS[6:12]:
            self.fake.slot(slot)["session_date"] = SESSION_DATES[0]
        document = self.inventory_ok()
        for slot in study.SLOTS:
            entry = self.slot_of(document, slot)
            self.assertEqual(self.FLAG in self.failures(document, slot), not slot.startswith("S3-"), slot)
            self.assertTrue(entry["analyzable"], slot)
            self.assertEqual(entry["protocol_conforming"], slot.startswith("S3-"), slot)
        self.assertEqual({item["source"] for item in self.slot_of(document, "S2-SQ-1")["failures"]}, {"derived"})
        self.assertTrue(document["lockable"])
        self.assertEqual(document["counts"]["protocol_conforming"], 6)
        out = self.fake.study_dir / "out"
        self.assertEqual(sorted(path.name for path in out.iterdir()),
                         ["inventory.json", "lock-summary.json", "pairs-back_squat.json", "pairs-clean.json",
                          "pairs-snatch.json"])

    def test_three_sessions_on_one_day_flag_every_enrolled_slot(self) -> None:
        for slot in study.SLOTS:
            self.fake.slot(slot)["session_date"] = SESSION_DATES[0]
        self.fake.slot("S3-CL-2").update(fixture_id=None, protocol_failures=[{"stage": "transfer", "reason": "lost"}])
        self.fake.clips["S3-CL-2"]["fixture_id"] = None
        self.fake.slots_doc["slots"][study.SLOTS.index("S2-SN-2")] = {
            "slot": "S2-SN-2", "status": "not_recorded", "protocol_failures": [{"stage": "recording", "reason": "x"}]}
        document = self.inventory_ok()
        for slot in study.SLOTS:
            self.assertEqual(self.FLAG in self.failures(document, slot), slot != "S2-SN-2", slot)
        self.assertEqual(document["counts"]["protocol_conforming"], 0)

    def test_distinct_days_are_not_flagged(self) -> None:
        document = self.inventory_ok()
        self.assertFalse(any(self.FLAG in self.failures(document, slot) for slot in study.SLOTS))


class CreationTimeProbeTests(unittest.TestCase):
    """M3: only ffprobe exit 0 without the tag is a recorded failure; any other outcome is exit 3."""

    def probe(self, result: subprocess.CompletedProcess | Exception) -> str | None:
        effect = result if isinstance(result, Exception) else None
        with mock.patch.object(evidence.subprocess, "run", side_effect=effect, return_value=result) as run:
            value = evidence.ffprobe_creation_time(Path("/v.mp4"))
        self.assertEqual(run.call_args.args[0][0], "ffprobe")
        return value

    def test_tag_is_returned(self) -> None:
        stdout = b'{"format": {"tags": {"creation_time": "2026-10-11T10:00:00.000000Z"}}}\n'
        self.assertEqual(self.probe(completed(0, stdout)), "2026-10-11T10:00:00.000000Z")

    def test_exit_zero_without_the_tag_is_none(self) -> None:
        for stdout in (b'{"format": {}}', b'{"format": {"tags": {}}}', b"{}", b"[]",
                       b'{"format": {"tags": {"creation_time": 5}}}'):
            with self.subTest(stdout):
                self.assertIsNone(self.probe(completed(0, stdout)))

    def test_non_zero_exit_is_infrastructure(self) -> None:
        for code in (1, 2, -9):
            with self.subTest(code), self.assertRaises(study.StudyInfrastructureError):
                self.probe(completed(code, b"", b"v.mp4: Invalid data found when processing input\n"))

    def test_undecodable_output_or_no_ffprobe_is_infrastructure(self) -> None:
        for result in (completed(0, b"{oops"), completed(0, b"\xff"), OSError("no ffprobe")):
            with self.subTest(result), self.assertRaises(study.StudyInfrastructureError):
                self.probe(result)


class EscapingFileTests(InventoryTestCase):
    """SEC2: a bound file resolving outside --root is a clean input error (exit 1), never a traceback."""

    def test_hashed_file_outside_the_root_is_an_input_error(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other).resolve() / "x.json"
            outside.write_bytes(b"{}")
            with self.assertRaises(study.StudyInputError):
                evidence.hashed(self.root, outside)

    def test_symlinked_run_output_escaping_the_root_exits_1(self) -> None:
        slots_path = self.fake.write()
        analysis = next((self.fake.private / "sessions/sess-alpha/analyses").glob("*0001.opencv-csrt.analysis-v1.json"))
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other).resolve() / "analysis.json"
            outside.write_bytes(analysis.read_bytes())
            analysis.unlink()
            try:
                analysis.symlink_to(outside)
            except OSError as error:  # Windows without the symlink privilege (WinError 1314)
                self.skipTest(f"symlinks unavailable: {error}")
            code, _, err = self.fake.main(self.fake.argv(slots_path))
        self.assertEqual(code, 1, err)
        self.assertIn("outside", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse((self.fake.study_dir / "out").exists())


class DerivedCodeAllowListTests(unittest.TestCase):
    """L6: every derived failure code the inventory emits is on the report's closed allow-list."""

    CROSS_SLOT = {"novelty:duplicate_of_slot:S3-CL-2", "processing:session_status_failed",
                  "processing:not_processed", "processing:openbar_parser_rejected", "wl_export:wl_parser_rejected",
                  "protocol:environment_mismatch", "protocol:session_has_unenrolled_clip",
                  "protocol:slot_order_violation", "protocol:session_dates_not_distinct",
                  "novelty:session_date_before_freeze", "processing:method_mismatch:run_record_sha256",
                  "processing:run_output_mismatch:analysis", "processing:run_output_mismatch:prediction",
                  "assessment:processing_unknown", "assessment:mechanical_unknown"}

    def test_derived_codes_render_verbatim(self) -> None:
        codes = {code for _, _, expected, _ in DERIVED_CASES for code in expected} | self.CROSS_SLOT
        for code in sorted(codes):
            stage, reason = code.split(":", 1)
            with self.subTest(code):
                self.assertEqual(render.public_failure({"stage": stage, "reason": reason, "source": "derived"}), code)


class LinkedInputTests(InventoryTestCase):
    """Review M1: a recorded input behind a symlink or junction is invalid input (exit 1), never 'missing'."""

    def make_dir_link(self, link: Path, target: Path) -> None:
        """A directory link (Windows junction, else symlink), removed before the temporary tree is deleted."""
        try:
            if sys.platform == "win32":
                import _winapi
                _winapi.CreateJunction(str(target), str(link))
            else:
                os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError) as error:
            self.skipTest(f"directory links not permitted here: {error}")
        self.addCleanup(lambda: os.path.lexists(link) and (os.rmdir if sys.platform == "win32" else os.unlink)(link))

    def relink_wl(self, relative: str) -> tuple[int, str]:
        self.fake.slot("S1-SQ-1")["wl_csv"] = relative
        slots_path = self.fake.study_dir / "slots.json"
        slots_path.write_bytes(json.dumps(self.fake.slots_doc, indent=2).encode("utf-8"))
        code, _, err = self.fake.main(self.fake.argv(slots_path))
        return code, err

    def test_recorded_path_contract_for_plain_absent_and_non_canonical_spellings(self) -> None:
        self.fake.write()
        for value in ("validation/private/vbt/wl/./S1-SQ-1.csv", "../outside.csv", "validation\\private\\x.csv",
                      "", None):
            with self.subTest(value):
                self.assertIsNone(study.recorded_path(self.root, value))
        for value in ("validation/private/vbt/wl/S1-SQ-1.csv", "validation/private/vbt/wl/absent.csv"):
            with self.subTest(value):  # an absent file keeps its path; callers record it as missing
                self.assertEqual(study.recorded_path(self.root, value), self.root / value)

    def test_input_behind_a_junction_is_refused_even_inside_the_root(self) -> None:
        self.fake.write()
        self.make_dir_link(self.fake.private / "wl-link", self.fake.private / "wl")
        with self.assertRaises(study.StudyInputError):
            study.recorded_path(self.root, "validation/private/vbt/wl-link/S1-SQ-1.csv")
        code, err = self.relink_wl("validation/private/vbt/wl-link/S1-SQ-1.csv")
        self.assertEqual(code, 1, err)
        self.assertIn("link", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse((self.fake.study_dir / "out").exists())

    def test_input_that_is_a_file_symlink_is_refused(self) -> None:
        self.fake.write()
        link = self.fake.private / "wl" / "linked.csv"
        try:
            link.symlink_to(self.fake.private / "wl" / "S1-SQ-1.csv")
        except OSError as error:  # Windows without the symlink privilege (WinError 1314)
            self.skipTest(f"symlinks unavailable: {error}")
        code, err = self.relink_wl("validation/private/vbt/wl/linked.csv")
        self.assertEqual(code, 1, err)
        self.assertIn("link", err)


class MalformedInputTests(InventoryTestCase):
    """M6: unresolvable paths and malformed inventories are clean input errors (exit 1)."""

    def test_unresolvable_path_is_an_input_error(self) -> None:
        self.fake.write()
        error = inventory.workflow.WorkflowError("cannot resolve x: symlink loop")
        with mock.patch.object(inventory.workflow, "safe_resolve", side_effect=error):
            code, _, err = self.fake.run()
        self.assertEqual(code, 1)
        self.assertIn("symlink loop", err)

    def test_malformed_inventories_fail_verify_cleanly(self) -> None:
        self.inventory_ok()
        path = self.fake.study_dir / "out/inventory.json"
        original = path.read_text(encoding="utf-8")
        cases = {
            "file item not object": lambda doc: doc["slots"][0]["files"].update(video="x"),
            "file path not text": lambda doc: doc["slots"][0]["files"]["video"].update(path=7),
            "session files list": lambda doc: doc["sessions"][SESSION_IDS[0]].update(files=[]),
            "slots not list": lambda doc: doc.update(slots={}),
            "slot not object": lambda doc: doc["slots"].__setitem__(0, 3),
            "pairs file text": lambda doc: doc["lifts"]["clean"].update(pairs_file="x"),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                document = json.loads(original)
                mutate(document)
                path.write_text(json.dumps(document), encoding="utf-8")
                code, _, err = self.fake.main(["verify", "--inventory", str(path)])
                self.assertEqual(code, 1, err)
                self.assertIn("error:", err)

    def test_unreadable_inventory_fails_verify(self) -> None:
        code, _, err = self.fake.main(["verify", "--inventory", str(self.root / "missing.json")])
        self.assertEqual(code, 1)
        self.assertIn("cannot read inventory", err)


class FrozenOptionsTests(unittest.TestCase):
    def test_frozen_analyze_options_equal_the_workflow_preset(self) -> None:
        """The literal frozen list must not drift from the preset the workflow runs."""
        self.assertEqual(study.ANALYZE_OPTIONS, list(study.workflow.PRESETS[study.PRESET]))

    def test_preregistration_main_commit_is_a_full_commit(self) -> None:
        """L2: the merge commit of #166 on main, spelled in full like every other recorded commit."""
        self.assertEqual(study.PREREGISTRATION_MAIN_COMMIT, "c6a58b5649ceb089cd4c3d8c180c0996b2f4b33f")
        self.assertIsNotNone(study.COMMIT_RE.fullmatch(study.freeze_references()["preregistration_main_commit"]))


class VerifyTests(InventoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.inventory_ok()
        self.out = self.fake.study_dir / "out"

    def verify(self, *extra: str) -> tuple[int, str, str]:
        return self.fake.main(["verify", "--inventory", str(self.out / "inventory.json"), *extra])

    def test_untouched_inputs_verify(self) -> None:
        result = inventory.verify(self.out / "inventory.json", root=self.root)
        self.assertTrue(result["inputs_unchanged"])
        self.assertTrue(result["inventory_matches_lock_summary"])
        self.assertTrue(result["ok"])
        self.assertEqual((result["mismatches"], result["mismatch_count"]), ([], 0))
        self.assertEqual(result["checked"], 18 * 9 + 3 * 6 + 1 + 3 + 1)
        code, out, _ = self.verify("--lock-summary", str(self.out / "lock-summary.json"))
        self.assertEqual(code, 0)
        self.assertIn("ok", out)

    def test_modified_input_file_is_detected(self) -> None:
        wl = self.root / "validation/private/vbt/wl/S2-CL-1.csv"
        wl.write_bytes(wl.read_bytes() + b"1,2\n")
        result = inventory.verify(self.out / "inventory.json", root=self.root)
        self.assertFalse(result["ok"] or result["inputs_unchanged"])
        self.assertEqual([item["path"] for item in result["mismatches"]], ["validation/private/vbt/wl/S2-CL-1.csv"])
        code, out, _ = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("S2-CL-1.csv", out)

    def test_deleted_input_file_is_detected(self) -> None:
        (self.out / "pairs-clean.json").unlink()
        result = inventory.verify(self.out / "inventory.json", root=self.root)
        self.assertEqual(result["mismatches"][0]["actual"], None)
        self.assertFalse(result["ok"])

    def test_modified_inventory_is_detected(self) -> None:
        path = self.out / "inventory.json"
        path.write_bytes(path.read_bytes().replace(b'"lockable": true', b'"lockable": false'))
        result = inventory.verify(path, root=self.root)
        self.assertTrue(result["inputs_unchanged"])
        self.assertFalse(result["inventory_matches_lock_summary"] or result["ok"])
        self.assertEqual(self.verify()[0], 1)

    def test_missing_lock_summary_fails(self) -> None:
        (self.out / "lock-summary.json").unlink()
        self.assertEqual(self.verify()[0], 1)


class DataRootTests(InventoryTestCase):
    """The evidence checkout must be the clean pinned baseline; the tool checkout is recorded separately."""

    def test_data_root_at_other_commit_is_refused(self) -> None:
        code, _, err = self.fake.run(git=lambda root: ("e" * 40, True))
        self.assertEqual(code, 1)
        self.assertIn("pinned baseline", err)
        self.assertFalse((self.fake.study_dir / "out").exists())

    def test_dirty_data_root_is_refused(self) -> None:
        code, _, err = self.fake.run(git=lambda root: (study.OPENBAR_BASELINE_COMMIT, False))
        self.assertEqual(code, 1)
        self.assertIn("tracked files clean: False", err)

    def test_root_option_selects_the_data_checkout(self) -> None:
        slots_path = self.fake.write()
        argv = ["inventory", "--root", str(self.root), *self.fake.argv(slots_path, "via-option")[1:]]
        code, _, err = self.fake.main(argv, root=Path(self.root.anchor))
        self.assertEqual(code, 0, err)
        self.assertTrue((self.fake.study_dir / "via-option" / "inventory.json").exists())


if __name__ == "__main__":
    unittest.main()
