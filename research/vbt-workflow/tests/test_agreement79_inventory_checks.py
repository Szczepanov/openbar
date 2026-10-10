"""#79 inventory: tool-tree lock rule, session status boundary, session/order checks, malformed inputs."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agreement79_inventory as inventory  # noqa: E402
import agreement79_slot_evidence as evidence  # noqa: E402
import agreement79_study as study  # noqa: E402
from test_agreement79_inventory import COMMIT, SESSION_IDS, InventoryTestCase  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
