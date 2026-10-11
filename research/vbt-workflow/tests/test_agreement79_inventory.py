"""#79 private inventory, 18-slot accounting, pairs files, lock summary and rehash (synthetic roots only)."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agreement79_inventory as inventory  # noqa: E402
import agreement79_study as study  # noqa: E402

SESSION_IDS = ("sess-alpha", "sess-bravo", "sess-charlie")
SESSION_DATES = ("2026-10-11", "2026-10-13", "2026-10-15")
CREATED = "2026-10-11T10:00:00.000000Z"
COMMIT = "c" * 40


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def valid_assessment(fixture_id: str) -> dict[str, Any]:
    ok = {"status": "valid", "reasons": []}
    return {
        "schema_version": 1, "implementation": "openbar-vbt-clip-assessment", "implementation_version": 1,
        "fixture_id": fixture_id,
        "sources": {"run_record": {"path": "x", "sha256": None},
                    "validator": {"path": "target/release/openbar-cli.exe", "sha256": study.OPENBAR_CLI_SHA256}},
        "processing": {"status": "complete", "reasons": []},
        "mechanical": {"status": "valid", "checks": {name: dict(ok) for name in
                                                      ("run_binding", "source_binding", "canonical_analysis", "decoded_pts")}},
        "experiment_suitability": {"status": "unknown", "reasons": ["experiment_criteria_not_predeclared"]},
        "accuracy": {"status": "not_established", "reasons": ["independent_accuracy_evidence_missing"]},
    }


class FakeStudy:
    """An in-memory owner study that tests mutate before `write()` materialises it under a temp repo root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.private = root / "validation/private/vbt"
        self.study_dir = root / study.STUDY_DIR
        self.assessments = self.study_dir / "assessments"
        self.manifest = self.private / "manifest.json"
        self.excluded: list[str] = ["0" * 64]
        self.creation: dict[str, str | None] = {}
        self.failing_sessions: set[str] = set()
        self.app = root / "consumer" / "app"
        self.rejected: dict[str, dict[str, str]] = {}
        self.preflight_calls: list[tuple[Path, Path, Path]] = []
        self.hashes: dict[str, dict[str, str]] = {}
        self.scale_patch: dict[str, Callable[[dict], None]] = {}
        self.run_patch: dict[str, Callable[[dict], None]] = {}
        self.assessment_patch: dict[str, Callable[[dict], None]] = {}
        self.slots_doc: dict[str, Any] = {
            "format": "owner-vbt-agreement-79-slots", "format_version": 1, "study_id": study.STUDY_ID,
            "collection_status": "complete", "plate_diameter_m": 0.45, "stick_length_m": 1.30,
            "wl_analysis_version": "not_visible",
            "assessments_dir": "validation/private/vbt/study-79/assessments", "slots": []}
        self.clips: dict[str, dict[str, Any]] = {}
        self.records: dict[str, dict[str, Any]] = {}
        for number, slot in enumerate(study.SLOTS, start=1):
            session = study.slot_session(slot) - 1
            fixture_id = f"vbt-{number:016x}"
            self.slots_doc["slots"].append({
                "slot": slot, "status": "enrolled", "protocol_failures": [],
                "session_id": SESSION_IDS[session], "session_date": SESSION_DATES[session],
                "original_name": f"IMG_{number:04d}.MOV", "fixture_id": fixture_id,
                "wl_csv": f"validation/private/vbt/wl/{slot}.csv", "attempted_rep_start_s": [2.0, 5.0, 8.0]})
            self.clips[slot] = {"fixture_id": fixture_id, "video": f"video bytes {slot}".encode(),
                                "exercise": study.slot_lift(slot)["exercise"], "tracker": "csrt",
                                "seed_timestamp_s": 0.5, "assessment": True, "scale_row": True, "in_record": True,
                                "wl": f"time,velocity\n0,{number}\n".encode()}
        for session_id in SESSION_IDS:
            self.records[session_id] = {"configuration": {
                "plate_diameter_m": 0.45, "stick_length_m": 1.30, "stick_markers": "lowest and highest marker",
                "tracker_policy": study.TRACKER_POLICY, "preset": study.PRESET,
                "analyze_options": list(study.ANALYZE_OPTIONS)}, "skipped": []}

    def slot(self, slot: str) -> dict[str, Any]:
        return self.slots_doc["slots"][study.SLOTS.index(slot)]

    def put(self, path: Path, data: bytes) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return sha_bytes(data)

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def write_clip(self, slot: str, session_dir: Path) -> dict[str, Any] | None:
        clip, entry = self.clips[slot], self.slot(slot)
        if entry["status"] != "enrolled":
            return None
        fixture_id = clip["fixture_id"]
        if entry.get("wl_csv") and clip["wl"] is not None:
            self.put(self.root / entry["wl_csv"], clip["wl"])
        if fixture_id is None:
            return None
        video = self.private / "media" / f"{slot}.mp4"
        video_sha = self.put(video, clip["video"])
        seed = session_dir / "seeds" / f"{fixture_id}.manual-target-seed-v1.json"
        seed_sha = self.put(seed, dump({"fixture_id": fixture_id, "seed": {"timestamp_s": clip["seed_timestamp_s"],
                                                                            "frame_index": 30}}))
        label = session_dir / "seeds" / f"{fixture_id}.session-label.csv"
        click = session_dir / "scale" / f"{fixture_id}.scale-reference.csv"
        label_sha, click_sha = self.put(label, b"label\n"), self.put(click, b"click\n")
        analyses = session_dir / "analyses"
        prediction = analyses / f"{fixture_id}.opencv-csrt.prediction-v1.json"
        analysis = analyses / f"{fixture_id}.opencv-csrt.analysis-v1.json"
        prediction_sha = self.put(prediction, dump({"prediction": slot}))
        analysis_sha = self.put(analysis, dump({"analysis": slot}))
        self.hashes[slot] = {"source_video_sha256": video_sha, "analysis_sha256": analysis_sha,
                             "click_csv_sha256": click_sha}
        run = {
            "format": "openbar-research-vbt-run-record", "format_version": 2, "workflow_version": "vbt-workflow-3",
            "fixture_id": fixture_id,
            "inputs": {"video": {"path": self.rel(video), "sha256": video_sha},
                       "seed": {"path": self.rel(seed), "sha256": seed_sha, "fixture_id": fixture_id,
                                "timestamp_s": clip["seed_timestamp_s"], "frame_index": 30},
                       "manifest": {"path": self.rel(self.manifest)}, "manifest_entry": {"sha256": "ab" * 32}},
            "configuration": {"exercise": clip["exercise"], "plate_diameter_m": 0.45, "tracker": "csrt",
                              "tracker_implementation": "opencv-csrt", "preset": study.PRESET,
                              "analyze_options": list(study.ANALYZE_OPTIONS)},
            "outputs": {"prediction": {"file": prediction.name, "sha256": prediction_sha},
                        "analysis": {"file": analysis.name, "sha256": analysis_sha}},
            "openbar": {"git_commit": study.OPENBAR_BASELINE_COMMIT, "tracked_changes": False},
            "environment": {"python": "3.12.7", "ffmpeg": "ffmpeg version 8.0", "ffprobe": "ffprobe version 8.0",
                            "openbar_cli": {"path": "target/release/openbar-cli.exe", "sha256": study.OPENBAR_CLI_SHA256},
                            "opencv_version": "4.12.0", "numpy_version": "2.2.6"}}
        self.run_patch.get(slot, lambda _: None)(run)
        run_path = analyses / f"{fixture_id}.opencv-csrt.run-record.json"
        run_sha = self.put(run_path, dump(run))
        if clip["assessment"]:
            assessment = valid_assessment(fixture_id)
            assessment["sources"]["run_record"] = {"path": self.rel(run_path), "sha256": run_sha}
            self.assessment_patch.get(slot, lambda _: None)(assessment)
            self.put(self.assessments / f"{fixture_id}.assessment-v1.json", dump(assessment))
        if not clip["in_record"]:
            return None
        return {"fixture_id": fixture_id, "original_name": entry["original_name"], "decision": "confirmed",
                "exercise": clip["exercise"], "tracker": clip["tracker"],
                "video": {"path": self.rel(video), "sha256": video_sha},
                "seed": {"path": self.rel(seed), "sha256": seed_sha,
                         "label_csv": {"path": self.rel(label), "sha256": label_sha}},
                "scale_click_csv": {"path": self.rel(click), "sha256": click_sha},
                "analyze_lift": {"argv": [], "run_record": {"path": self.rel(run_path), "sha256": run_sha}}}

    def write(self) -> Path:
        self.put(self.manifest, dump({"fixtures": []}))
        for session_id in SESSION_IDS:
            directory = self.private / "sessions" / session_id
            slots = [slot for slot in study.SLOTS if self.slot(slot).get("session_id") == session_id]
            clips = [record for record in (self.write_clip(slot, directory) for slot in slots) if record]
            csv_sha = self.put(directory / "session-input.csv", f"csv {session_id}\n".encode())
            state_sha = self.put(directory / "session.json", dump({"session_id": session_id}))
            report_sha = self.put(directory / "report.html", b"<html></html>\n")
            rows = [self.scale_row(slot) for slot in slots
                    if self.clips[slot]["scale_row"] and self.clips[slot]["fixture_id"]]
            self.put(directory / "scale-report/scale-reference-v1.json", dump({"schema_version": 1, "rows": rows}))
            record = {"format": "openbar-research-vbt-session-record", "format_version": 1,
                      "session_id": session_id, "page_id": "p" * 12,
                      "inputs": {"session_csv": {"path": self.rel(directory / "session-input.csv"), "sha256": csv_sha},
                                 "session_state": {"path": self.rel(directory / "session.json"), "sha256": state_sha},
                                 "manifest": {"path": self.rel(self.manifest)}},
                      "configuration": self.records[session_id]["configuration"],
                      "clips": clips + self.records[session_id].get("extra_clips", []),
                      "skipped": self.records[session_id]["skipped"],
                      "report": {"path": self.rel(directory / "report.html"), "sha256": report_sha}}
            self.put(directory / "session-record.json", dump(record))
        self.put(self.root / study.EXCLUSION_LIST, "".join(f"{digest}  old.mp4\n" for digest in self.excluded).encode())
        slots_path = self.study_dir / "slots.json"
        self.put(slots_path, dump(self.slots_doc))
        return slots_path

    def scale_row(self, slot: str) -> dict[str, Any]:
        row = {"fixture_id": self.clips[slot]["fixture_id"], "reference_to_plate_ratio": {"value": 1.0},
               **self.hashes.get(slot, {})}
        self.scale_patch.get(slot, lambda _: None)(row)
        return row

    # Injected boundaries -----------------------------------------------------------------------
    def status(self, root: Path, session_id: str) -> tuple[bool, str | None]:
        assert root == self.root
        if session_id in self.failing_sessions:
            return False, f"error: session {session_id} is incomplete"
        return True, None

    def preflight(self, app: Path, analysis: Path, wl_csv: Path) -> dict[str, Any]:
        """Accepts every pair unless `rejected[<slot>]` maps a source to its error message."""
        assert app == self.app
        self.preflight_calls.append((app, analysis, wl_csv))
        rejected = self.rejected.get(wl_csv.stem, {})
        return {source: {"accepted": source not in rejected, "error": rejected.get(source)}
                for source in ("openbar", "wl")}

    def probe(self, video: Path) -> str | None:
        """Container creation times one minute apart in frozen slot order (S1-SQ-1 is CREATED)."""
        default = CREATED.replace("10:00:", f"10:{study.SLOTS.index(video.stem):02d}:")
        return self.creation.get(video.stem, default)

    def git(self, root: Path) -> tuple[str, bool]:
        """The fake root is the pinned data checkout, consumer/ the pinned consumer, else the tool checkout."""
        if root == self.root:
            return study.OPENBAR_BASELINE_COMMIT, True
        return (study.CONSUMER_COMMIT, True) if root == self.app.parent else (COMMIT, True)

    def exclusion_sha(self) -> str:
        return study.file_sha256(self.root / study.EXCLUSION_LIST)

    def run(self, output: str = "out", **overrides: Any) -> tuple[int, str, str]:
        slots_path = self.write() if not (self.study_dir / "slots.json").exists() else self.study_dir / "slots.json"
        return self.main(self.argv(slots_path, output), **overrides)

    def argv(self, slots_path: Path, output: str = "out") -> list[str]:
        return ["inventory", "--slots", str(slots_path), "--output-dir", str(self.study_dir / output),
                "--consumer-app", str(self.app)]

    def main(self, argv: list[str], **overrides: Any) -> tuple[int, str, str]:
        options = {"root": self.root, "status": self.status, "probe": self.probe, "git": self.git,
                   "preflight": self.preflight,
                   "exclusion_sha256": self.exclusion_sha() if (self.root / study.EXCLUSION_LIST).exists() else None,
                   **overrides}
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = inventory.main(argv, **options)
        return code, out.getvalue(), err.getvalue()

    def load(self, output: str = "out", name: str = "inventory.json") -> dict[str, Any]:
        return json.loads((self.study_dir / output / name).read_text(encoding="utf-8"))


class InventoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.fake = FakeStudy(self.root)

    def inventory_ok(self, **overrides: Any) -> dict[str, Any]:
        code, _, err = self.fake.run(**overrides)
        self.assertEqual(code, 0, err)
        return self.fake.load()

    def slot_of(self, document: dict[str, Any], slot: str) -> dict[str, Any]:
        return document["slots"][study.SLOTS.index(slot)]

    def failures(self, document: dict[str, Any], slot: str) -> set[str]:
        return {f"{item['stage']}:{item['reason']}" for item in self.slot_of(document, slot)["failures"]}


class HappyPathTests(InventoryTestCase):
    def test_all_enrolled_slots_are_analyzable_and_conforming(self) -> None:
        code, out, err = self.fake.run()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out.strip().splitlines()), 1)
        document = self.fake.load()
        self.assertEqual(document["format"], "owner-vbt-agreement-79-inventory")
        self.assertEqual(document["counts"], {
            "planned_videos": 18, "planned_reps": 54, "enrolled": 18, "not_recorded": 0, "processed": 18,
            "analyzable": 18, "protocol_conforming": 18,
            "per_lift": {exercise: {"enrolled": 6, "analyzable": 6, "protocol_conforming": 6}
                         for exercise in ("back_squat", "snatch", "clean")}})
        self.assertTrue(document["lockable"])
        self.assertEqual([entry["slot"] for entry in document["slots"]], list(study.SLOTS))
        first = document["slots"][0]
        self.assertEqual(first["failures"], [])
        self.assertEqual(set(first["files"]), {"video", "seed", "label_csv", "scale_click_csv", "run_record",
                                               "prediction", "analysis", "assessment", "wl_csv"})
        self.assertEqual(first["files"]["wl_csv"]["path"], "validation/private/vbt/wl/S1-SQ-1.csv")
        self.assertEqual(first["files"]["wl_csv"]["sha256"], sha_bytes(self.fake.clips["S1-SQ-1"]["wl"]))
        self.assertEqual(first["assessment_statuses"], {"processing": "complete", "mechanical": "valid",
                                                        "experiment_suitability": "unknown",
                                                        "accuracy": "not_established"})
        self.assertEqual(first["seed_timestamp_s"], 0.5)
        self.assertEqual(first["creation_time"], CREATED)
        self.assertEqual(first["manifest_entry_sha256"], "ab" * 32)
        self.assertEqual(set(document["sessions"]), set(SESSION_IDS))
        self.assertEqual(set(document["sessions"]["sess-alpha"]["files"]),
                         {"session_record", "session_csv", "session_state", "scale_report_json", "report_html",
                          "manifest"})
        self.assertEqual(document["environment"]["opencv_version"], "4.12.0")
        self.assertEqual(document["slots_file"]["path"], "validation/private/vbt/study-79/slots.json")
        self.assertEqual(document["freeze"], study.freeze_references())

    def test_pairs_files_list_analyzable_slots_with_relative_paths(self) -> None:
        document = self.inventory_ok()
        out = self.fake.study_dir / "out"
        for code, lift in study.LIFTS.items():
            pairs_path = out / f"pairs-{lift['exercise']}.json"
            data = pairs_path.read_bytes()
            self.assertNotIn(b"\r\n", data)
            record = document["lifts"][lift["exercise"]]
            self.assertEqual(record["pairs_file"], {"path": self.fake.rel(pairs_path), "sha256": sha_bytes(data)})
            slots = [slot for slot in study.SLOTS if f"-{code}-" in slot]
            self.assertEqual(record["slots"], slots)
            self.assertEqual(record["analyzable_slots"], slots)
            pairs = json.loads(data)["pairs"]
            self.assertEqual([pair["label"] for pair in pairs], slots)
            for pair in pairs:
                self.assertEqual(pair["loadKg"], lift["load_kg"])
                self.assertEqual(pair["offsetS"], 0)
                self.assertEqual(pair["wlCsv"], f"../../wl/{pair['label']}.csv")
                self.assertTrue(pair["openBarAnalysis"].startswith("../../sessions/"))
                self.assertNotIn("\\", pair["openBarAnalysis"])
                self.assertTrue((out / pair["openBarAnalysis"]).resolve().is_file())

    def test_outputs_are_byte_deterministic(self) -> None:
        self.inventory_ok()
        self.assertEqual(self.fake.run("again")[0], 0)
        out, again = self.fake.study_dir / "out", self.fake.study_dir / "again"
        for name in ("pairs-back_squat.json", "pairs-snatch.json", "pairs-clean.json"):
            self.assertEqual((out / name).read_bytes(), (again / name).read_bytes())
        first, second = self.fake.load("out"), self.fake.load("again")
        for document in (first, second):
            for lift in document["lifts"].values():
                lift["pairs_file"]["path"] = lift["pairs_file"]["path"].rsplit("/", 1)[1]
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory() as other:
            twin = FakeStudy(Path(other).resolve())
            self.assertEqual(twin.run()[0], 0)
            for name in ("inventory.json", "lock-summary.json", "pairs-snatch.json"):
                self.assertEqual((out / name).read_bytes(), (twin.study_dir / "out" / name).read_bytes(), name)

    def test_lock_summary_is_public_safe(self) -> None:
        document = self.inventory_ok()
        out = self.fake.study_dir / "out"
        data = (out / "lock-summary.json").read_bytes()
        summary = json.loads(data)
        self.assertEqual(set(summary), {"format", "format_version", "study_id", "tool_version", "tool_commit",
                                        "tool_tree_clean", "data_root_commit", "inventory_sha256", "counts", "lockable", "freeze",
                                        "exclusion_list_sha256"})
        self.assertEqual(summary["inventory_sha256"], sha_bytes((out / "inventory.json").read_bytes()))
        self.assertEqual(summary["counts"], document["counts"])
        self.assertEqual((summary["tool_commit"], summary["tool_tree_clean"]), (COMMIT, True))
        self.assertEqual(summary["data_root_commit"], study.OPENBAR_BASELINE_COMMIT)
        self.assertEqual((document["data_root_commit"], document["tool_commit"]),
                         (study.OPENBAR_BASELINE_COMMIT, COMMIT))
        text = data.decode("utf-8")
        for slot in study.SLOTS:
            entry = self.fake.slot(slot)
            for secret in (entry["fixture_id"], entry["original_name"], entry["session_id"], entry["session_date"]):
                self.assertNotIn(secret, text)
        for marker in ("validation/", ".json", ".csv", ".mp4", "private", CREATED):
            self.assertNotIn(marker, text)
        file_hashes = {item["sha256"] for entry in document["slots"] for item in entry["files"].values()}
        file_hashes |= {item["sha256"] for entry in document["sessions"].values() for item in entry["files"].values()}
        self.assertFalse(any(digest in text for digest in file_hashes))


class AccountingTests(InventoryTestCase):
    def test_not_recorded_slot_is_counted_and_left_out_of_pairs(self) -> None:
        self.fake.slots_doc["slots"][study.SLOTS.index("S2-SN-2")] = {
            "slot": "S2-SN-2", "status": "not_recorded",
            "protocol_failures": [{"stage": "recording", "reason": "phone storage full"}]}
        document = self.inventory_ok()
        slot = self.slot_of(document, "S2-SN-2")
        self.assertEqual(slot["status"], "not_recorded")
        self.assertEqual(slot["failures"], [{"stage": "recording", "reason": "phone storage full", "source": "owner"}])
        self.assertFalse(slot["analyzable"] or slot["protocol_conforming"])
        self.assertEqual((slot["files"], slot["fixture_id"], slot["session_id"]), ({}, None, None))
        self.assertEqual((document["counts"]["enrolled"], document["counts"]["not_recorded"]), (17, 1))
        self.assertEqual(document["counts"]["per_lift"]["snatch"], {"enrolled": 5, "analyzable": 5,
                                                                    "protocol_conforming": 5})
        pairs = json.loads((self.fake.study_dir / "out/pairs-snatch.json").read_bytes())["pairs"]
        self.assertNotIn("S2-SN-2", [pair["label"] for pair in pairs])

    def test_owner_failures_do_not_change_analyzability(self) -> None:
        self.fake.slot("S1-CL-2")["protocol_failures"] = [{"stage": "processing", "reason": "owner note"},
                                                          {"stage": "reference", "reason": "stick moved"}]
        document = self.inventory_ok()
        slot = self.slot_of(document, "S1-CL-2")
        self.assertTrue(slot["analyzable"])
        self.assertFalse(slot["protocol_conforming"])
        self.assertEqual(self.failures(document, "S1-CL-2"), {"processing:owner note", "reference:stick moved"})
        self.assertEqual({item["source"] for item in slot["failures"]}, {"owner"})

    def test_lift_without_analyzable_slots_gets_no_pairs_file(self) -> None:
        for slot in study.SLOTS:
            if "-SN-" in slot:
                self.fake.slot(slot)["wl_csv"] = None
        document = self.inventory_ok()
        self.assertEqual(document["lifts"]["snatch"]["pairs_file"], None)
        self.assertEqual(document["lifts"]["snatch"]["reason"], "no_analyzable_slots")
        self.assertEqual(document["lifts"]["snatch"]["analyzable_slots"], [])
        self.assertFalse((self.fake.study_dir / "out/pairs-snatch.json").exists())
        self.assertTrue((self.fake.study_dir / "out/pairs-clean.json").exists())
        self.assertNotIn("reason", document["lifts"]["clean"])

    def test_in_progress_collection_is_not_lockable_and_writes_no_pairs_files(self) -> None:
        self.fake.slots_doc["collection_status"] = "in_progress"
        document = self.inventory_ok()
        self.assertFalse(document["lockable"])
        self.assertFalse(self.fake.load(name="lock-summary.json")["lockable"])
        out = self.fake.study_dir / "out"
        self.assertEqual(sorted(path.name for path in out.iterdir()), ["inventory.json", "lock-summary.json"])
        for exercise, record in document["lifts"].items():
            self.assertEqual((record["pairs_file"], record["reason"]), (None, "collection_in_progress"), exercise)
            self.assertEqual(len(record["analyzable_slots"]), 6)
        self.assertEqual(document["counts"]["analyzable"], 18)

    def test_duplicate_video_is_a_novelty_failure_of_the_later_slot(self) -> None:
        self.fake.clips["S2-CL-1"]["video"] = self.fake.clips["S1-SQ-2"]["video"]
        document = self.inventory_ok()
        self.assertEqual(self.failures(document, "S1-SQ-2"), set())
        self.assertEqual(self.failures(document, "S2-CL-1"), {"novelty:duplicate_of_slot:S1-SQ-2"})
        self.assertFalse(self.slot_of(document, "S2-CL-1")["analyzable"])

    def test_excluded_video_is_a_novelty_failure(self) -> None:
        self.fake.excluded.append(sha_bytes(self.fake.clips["S3-SQ-1"]["video"]))
        document = self.inventory_ok()
        self.assertEqual(self.failures(document, "S3-SQ-1"), {"novelty:duplicate_of_excluded_clip"})
        self.assertFalse(self.slot_of(document, "S3-SQ-1")["analyzable"])

    def test_environment_difference_marks_the_differing_slot_non_blocking(self) -> None:
        self.fake.run_patch["S3-CL-2"] = lambda run: run["environment"].update(numpy_version="2.3.0")
        document = self.inventory_ok()
        self.assertEqual(self.failures(document, "S3-CL-2"), {"protocol:environment_mismatch"})
        self.assertTrue(self.slot_of(document, "S3-CL-2")["analyzable"])
        self.assertEqual(self.failures(document, "S1-SQ-1"), set())
        self.assertEqual(document["environment"]["numpy_version"], "2.2.6")

    def test_environment_reference_is_the_first_processed_slot_not_the_majority(self) -> None:
        self.fake.run_patch["S1-SQ-1"] = lambda run: run["environment"].update(numpy_version="2.3.0")
        document = self.inventory_ok()
        self.assertEqual(document["environment"]["numpy_version"], "2.3.0")
        self.assertEqual(self.failures(document, "S1-SQ-1"), set())
        for slot in study.SLOTS[1:]:
            self.assertEqual(self.failures(document, slot), {"protocol:environment_mismatch"}, slot)
        self.assertEqual(document["counts"]["analyzable"], 18)

    def test_unhashable_environment_values_are_compared_canonically(self) -> None:
        self.fake.slot("S1-SQ-1")["fixture_id"] = None
        self.fake.clips["S1-SQ-1"]["fixture_id"] = None
        self.fake.slot("S1-SQ-1")["protocol_failures"] = [{"stage": "transfer", "reason": "file lost"}]
        for slot in ("S1-SQ-2", "S1-SN-1"):
            self.fake.run_patch[slot] = lambda run: run["environment"].update(python={"version": [3, 12]})
        document = self.inventory_ok()
        self.assertEqual(document["environment"]["python"], {"version": [3, 12]})
        self.assertEqual(self.failures(document, "S1-SN-1"), set())
        self.assertEqual(self.failures(document, "S1-SN-2"), {"protocol:environment_mismatch"})

    def test_null_fixture_is_not_processed_unless_owner_reported_a_failure(self) -> None:
        self.fake.slot("S1-SN-1")["fixture_id"] = None
        self.fake.clips["S1-SN-1"]["fixture_id"] = None
        self.fake.slot("S1-SN-2")["fixture_id"] = None
        self.fake.clips["S1-SN-2"]["fixture_id"] = None
        self.fake.slot("S1-SN-2")["protocol_failures"] = [{"stage": "transfer", "reason": "file lost"}]
        document = self.inventory_ok()
        self.assertEqual(self.failures(document, "S1-SN-1"), {"processing:not_processed"})
        self.assertEqual(self.failures(document, "S1-SN-2"), {"transfer:file lost"})
        for slot in ("S1-SN-1", "S1-SN-2"):
            self.assertFalse(self.slot_of(document, slot)["analyzable"])
        self.assertEqual(document["counts"]["processed"], 16)


def mutate_record(key: str, value: Any) -> Callable[[FakeStudy], None]:
    def apply(fake: FakeStudy) -> None:
        fake.records["sess-alpha"]["configuration"][key] = value
    return apply


def patch_run(update: Callable[[dict], None]) -> Callable[[FakeStudy], None]:
    def apply(fake: FakeStudy) -> None:
        fake.run_patch["S1-SQ-1"] = update
    return apply


def patch_assessment(update: Callable[[dict], None]) -> Callable[[FakeStudy], None]:
    def apply(fake: FakeStudy) -> None:
        fake.assessment_patch["S1-SQ-1"] = update
    return apply


def clip_value(key: str, value: Any) -> Callable[[FakeStudy], None]:
    def apply(fake: FakeStudy) -> None:
        fake.clips["S1-SQ-1"][key] = value
    return apply


def slot_value(key: str, value: Any) -> Callable[[FakeStudy], None]:
    def apply(fake: FakeStudy) -> None:
        fake.slot("S1-SQ-1")[key] = value
    return apply


def scale_value(key: str, value: Any) -> Callable[[FakeStudy], None]:
    def apply(fake: FakeStudy) -> None:
        fake.scale_patch["S1-SQ-1"] = lambda row: row.update({key: value})
    return apply


def unhashed_scale_row(fake: FakeStudy) -> None:
    fake.scale_patch["S1-SQ-1"] = lambda row: [row.pop(key) for key in fake.hashes["S1-SQ-1"]]


def skip_on_page(fake: FakeStudy) -> None:
    fake.clips["S1-SQ-1"]["in_record"] = False
    fake.records["sess-alpha"]["skipped"] = [fake.slot("S1-SQ-1")["original_name"]]


def creation(value: str | None) -> Callable[[FakeStudy], None]:
    def apply(fake: FakeStudy) -> None:
        fake.creation["S1-SQ-1"] = value
    return apply


def processing_incomplete(assessment: dict) -> None:
    assessment["processing"] = {"status": "incomplete", "reasons": ["run_output_missing"]}


def mechanical_invalid(assessment: dict) -> None:
    assessment["mechanical"]["status"] = "invalid"
    assessment["mechanical"]["checks"]["decoded_pts"] = {"status": "invalid", "reasons": ["source_pts_invalid"]}


def method(field: str) -> str:
    return f"processing:method_mismatch:{field}"


# (name, mutation, expected failures of S1-SQ-1, analyzable)
DERIVED_CASES: list[tuple[str, Callable[[FakeStudy], None], set[str], bool]] = [
    ("skipped", skip_on_page, {"confirmation:skipped_on_session_page"}, False),
    ("not in record", clip_value("in_record", False), {"processing:clip_not_in_session_record"}, False),
    ("exercise", clip_value("exercise", "snatch"), {"processing:exercise_mismatch"}, False),
    ("policy", mutate_record("tracker_policy", "sam2-all-v1"), {method("tracker_policy")}, False),
    ("preset", mutate_record("preset", None), {method("preset")}, False),
    ("options", mutate_record("analyze_options", ["--filter", "raw"]), {method("analyze_options")}, False),
    ("plate", mutate_record("plate_diameter_m", 0.44), {method("plate_diameter_m")}, False),
    ("stick", mutate_record("stick_length_m", 1.31), {method("stick_length_m")}, False),
    ("clip tracker", clip_value("tracker", "sam2.1-bplus-circle"), {method("clip_tracker")}, False),
    ("run version", patch_run(lambda run: run.update(format_version=1)), {method("run_record_format")}, False),
    ("run fixture", patch_run(lambda run: run.update(fixture_id="vbt-ffffffffffffffff")),
     {method("run_fixture_id")}, False),
    ("run tracker", patch_run(lambda run: run["configuration"].update(tracker="sam2.1-bplus-circle")),
     {method("run_tracker")}, False),
    ("implementation", patch_run(lambda run: run["configuration"].update(tracker_implementation="x")),
     {method("tracker_implementation")}, False),
    ("run preset", patch_run(lambda run: run["configuration"].update(preset=None)), {method("run_preset")}, False),
    ("run options", patch_run(lambda run: run["configuration"].update(analyze_options=[])),
     {method("run_analyze_options")}, False),
    ("run plate", patch_run(lambda run: run["configuration"].update(plate_diameter_m=0.5)),
     {method("run_plate_diameter_m")}, False),
    ("cli", patch_run(lambda run: run["environment"].update(openbar_cli={"path": "x", "sha256": "1" * 64})),
     {method("openbar_cli_sha256")}, False),
    ("cargo cli", patch_run(lambda run: run["environment"].pop("openbar_cli")), {method("openbar_cli_sha256")}, False),
    ("commit", patch_run(lambda run: run["openbar"].update(git_commit="d" * 40)), {method("openbar_git_commit")}, False),
    ("dirty", patch_run(lambda run: run["openbar"].update(tracked_changes=True)),
     {method("openbar_tracked_changes")}, False),
    ("assessment missing", clip_value("assessment", False), {"assessment:assessment_missing"}, False),
    ("assessment invalid", patch_assessment(lambda a: a.update(accuracy={"status": "established", "reasons": []})),
     {"assessment:assessment_invalid"}, False),
    ("assessment run hash", patch_assessment(lambda a: a["sources"]["run_record"].update(sha256="2" * 64)),
     {"assessment:assessment_unbound"}, False),
    ("assessment validator", patch_assessment(lambda a: a["sources"]["validator"].update(sha256="3" * 64)),
     {"assessment:assessment_unbound"}, False),
    ("assessment fixture", patch_assessment(lambda a: a.update(fixture_id="vbt-eeeeeeeeeeeeeeee")),
     {"assessment:assessment_unbound"}, False),
    ("processing", patch_assessment(processing_incomplete), {"assessment:processing_incomplete"}, False),
    ("mechanical", patch_assessment(mechanical_invalid), {"assessment:mechanical_invalid"}, False),
    ("before freeze", creation("2026-10-01T10:00:00Z"), {"novelty:recorded_before_freeze"}, False),
    ("at freeze", creation("2026-10-09T17:20:54+02:00"), {"novelty:recorded_before_freeze"}, False),
    ("no creation", creation(None), {"novelty:creation_time_unavailable"}, False),
    ("garbage creation", creation("yesterday"), {"novelty:creation_time_unavailable"}, False),
    ("naive creation", creation("2026-10-11T10:00:00"), {"novelty:creation_time_unavailable"}, False),
    ("two reps", slot_value("attempted_rep_start_s", [2.0, 5.0]), {"protocol:attempted_reps_not_three"}, True),
    ("repeated rep", slot_value("attempted_rep_start_s", [2.0, 2.0, 5.0]), {"protocol:attempted_reps_not_three"}, True),
    ("negative rep", slot_value("attempted_rep_start_s", [-1.0, 2.0, 5.0]), {"protocol:attempted_reps_not_three"}, True),
    ("seed at rep", clip_value("seed_timestamp_s", 2.0), {"protocol:seed_not_before_first_rep"}, True),
    ("scale", clip_value("scale_row", False), {"reference:scale_reference_missing"}, True),
    ("scale video", scale_value("source_video_sha256", "4" * 64), {"reference:scale_reference_unbound"}, True),
    ("scale analysis", scale_value("analysis_sha256", None), {"reference:scale_reference_unbound"}, True),
    ("scale click", scale_value("click_csv_sha256", "5" * 64), {"reference:scale_reference_unbound"}, True),
    ("scale no hashes", unhashed_scale_row, {"reference:scale_reference_unbound"}, True),
    ("wl null", slot_value("wl_csv", None), {"wl_export:wl_csv_missing"}, False),
    ("wl absent", clip_value("wl", None), {"wl_export:wl_csv_missing"}, False),
    ("wl outside", slot_value("wl_csv", "../outside.csv"), {"wl_export:wl_csv_missing"}, False),
    ("wl not canonical", slot_value("wl_csv", "validation/private/vbt/wl/./S1-SQ-1.csv"),
     {"wl_export:wl_csv_missing"}, False),
]


class DerivedFailureTests(InventoryTestCase):
    def test_each_derived_failure_reason(self) -> None:
        for name, mutation, expected, analyzable in DERIVED_CASES:
            with self.subTest(name):
                with tempfile.TemporaryDirectory() as other:
                    fake = FakeStudy(Path(other).resolve())
                    mutation(fake)
                    code, _, err = fake.run()
                    self.assertEqual(code, 0, err)
                    document = fake.load()
                    self.assertEqual(self.failures(document, "S1-SQ-1"), expected)
                    slot = self.slot_of(document, "S1-SQ-1")
                    self.assertEqual(slot["analyzable"], analyzable)
                    self.assertFalse(slot["protocol_conforming"])
                    self.assertEqual({item["source"] for item in slot["failures"]}, {"derived"})
                    self.assertEqual(self.failures(document, "S1-SQ-2"),
                                     expected if name in {"policy", "preset", "options", "plate", "stick"} else set())

    def test_session_status_failure_marks_every_slot_of_that_session(self) -> None:
        self.fake.failing_sessions.add("sess-bravo")
        document = self.inventory_ok()
        for slot in study.SLOTS:
            expected = {"processing:session_status_failed"} if slot.startswith("S2-") else set()
            self.assertEqual(self.failures(document, slot), expected, slot)
        self.assertEqual(document["counts"]["analyzable"], 12)
        self.assertEqual(document["counts"]["processed"], 12)

    def test_session_date_before_freeze_day(self) -> None:
        for slot in study.SLOTS[:6]:
            self.fake.slot(slot)["session_date"] = "2026-10-08"
        for slot in study.SLOTS[6:12]:
            self.fake.slot(slot)["session_date"] = "2026-10-09"
        document = self.inventory_ok()
        # A pre-freeze session is not prospective: retained and accounted for, never paired.
        self.assertEqual(self.failures(document, "S1-SQ-1"), {"novelty:session_date_before_freeze"})
        self.assertFalse(self.slot_of(document, "S1-SQ-1")["analyzable"])
        self.assertEqual(self.failures(document, "S2-SQ-1"), set())

    def test_modified_run_record_breaks_its_binding(self) -> None:
        slots_path = self.fake.write()
        run = next((self.fake.private / "sessions/sess-alpha/analyses").glob("*0001.opencv-csrt.run-record.json"))
        run.write_bytes(run.read_bytes() + b"\n")
        code, _, err = self.fake.main(self.fake.argv(slots_path))
        self.assertEqual(code, 0, err)
        self.assertIn(method("run_record_sha256"), self.failures(self.fake.load(), "S1-SQ-1"))

    def test_modified_analysis_breaks_the_run_output_binding(self) -> None:
        slots_path = self.fake.write()
        analysis = next((self.fake.private / "sessions/sess-alpha/analyses").glob("*0001.opencv-csrt.analysis-v1.json"))
        analysis.write_bytes(b"{}\n")
        code, _, err = self.fake.main(self.fake.argv(slots_path))
        self.assertEqual(code, 0, err)
        # The scale row still binds the original analysis bytes (M5), so it is unbound too.
        self.assertEqual(self.failures(self.fake.load(), "S1-SQ-1"),
                         {"processing:run_output_mismatch:analysis", "reference:scale_reference_unbound"})


class AbortAndInputTests(InventoryTestCase):
    def assert_nothing_written(self) -> None:
        self.assertFalse((self.fake.study_dir / "out").exists())

    def test_exclusion_digest_mismatch_is_an_input_error(self) -> None:
        code, _, err = self.fake.run(exclusion_sha256="f" * 64)
        self.assertEqual(code, 1)
        self.assertIn("exclusion", err)
        self.assert_nothing_written()

    def test_missing_exclusion_list_is_an_input_error(self) -> None:
        slots_path = self.fake.write()
        (self.root / study.EXCLUSION_LIST).unlink()
        code, _, err = self.fake.main(self.fake.argv(slots_path), exclusion_sha256="f" * 64)
        self.assertEqual(code, 1, err)
        self.assertIn("exclusion", err)
        self.assert_nothing_written()

    def test_malformed_exclusion_list_is_an_input_error(self) -> None:
        slots_path = self.fake.write()
        path = self.root / study.EXCLUSION_LIST
        path.write_bytes(b"not-a-digest  old.mp4\n")
        code, _, err = self.fake.main(self.fake.argv(slots_path), exclusion_sha256=study.file_sha256(path))
        self.assertEqual(code, 1, err)
        self.assert_nothing_written()

    def test_ffprobe_unavailable_aborts(self) -> None:
        def unavailable(_video: Path) -> str | None:
            raise study.StudyInfrastructureError("ffprobe could not be run")
        code, _, err = self.fake.run(probe=unavailable)
        self.assertEqual(code, 3)
        self.assertIn("ffprobe", err)
        self.assert_nothing_written()

    def test_status_unavailable_aborts(self) -> None:
        def unavailable(_root: Path, _session: str) -> tuple[bool, str | None]:
            raise study.StudyInfrastructureError("status could not be run")
        self.assertEqual(self.fake.run(status=unavailable)[0], 3)
        self.assert_nothing_written()

    def test_existing_output_directory_is_refused(self) -> None:
        existing = self.fake.study_dir / "out"
        existing.mkdir(parents=True)
        (existing / "keep.txt").write_bytes(b"keep")
        code, _, err = self.fake.run()
        self.assertEqual(code, 1)
        self.assertIn("exists", err)
        self.assertEqual(sorted(path.name for path in existing.iterdir()), ["keep.txt"])

    def test_output_directory_must_be_inside_the_study_directory(self) -> None:
        for output in ("../elsewhere", "."):
            with self.subTest(output):
                code, _, _ = self.fake.run(output)
                self.assertEqual(code, 1)

    def test_invalid_slots_documents_are_rejected(self) -> None:
        def swap(doc: dict) -> None:
            doc["slots"][0], doc["slots"][1] = doc["slots"][1], doc["slots"][0]

        def same_id_across(doc: dict) -> None:
            for entry in doc["slots"][6:12]:
                entry["session_id"] = SESSION_IDS[0]

        def same_date_across(doc: dict) -> None:
            for entry in doc["slots"][6:12]:
                entry["session_date"] = SESSION_DATES[0]

        cases: dict[str, Callable[[dict], None]] = {
            "order": swap,
            "count": lambda doc: doc["slots"].pop(),
            "unknown top key": lambda doc: doc.update(extra=1),
            "missing top key": lambda doc: doc.pop("wl_analysis_version"),
            "unknown slot key": lambda doc: doc["slots"][3].update(note="x"),
            "missing slot key": lambda doc: doc["slots"][3].pop("attempted_rep_start_s"),
            "format": lambda doc: doc.update(format="other"),
            "version bool": lambda doc: doc.update(format_version=True),
            "study id": lambda doc: doc.update(study_id="other"),
            "collection status": lambda doc: doc.update(collection_status="done"),
            "plate zero": lambda doc: doc.update(plate_diameter_m=0),
            "plate text": lambda doc: doc.update(plate_diameter_m="0.45"),
            "stick": lambda doc: doc.update(stick_length_m=1.31),
            "wl version multiline": lambda doc: doc.update(wl_analysis_version="1.0\n2"),
            "assessments outside": lambda doc: doc.update(assessments_dir="../assessments"),
            "status": lambda doc: doc["slots"][0].update(status="lost"),
            "stage": lambda doc: doc["slots"][0].update(protocol_failures=[{"stage": "luck", "reason": "x"}]),
            "empty reason": lambda doc: doc["slots"][0].update(protocol_failures=[{"stage": "protocol", "reason": ""}]),
            "failure key": lambda doc: doc["slots"][0].update(
                protocol_failures=[{"stage": "protocol", "reason": "x", "source": "owner"}]),
            "not recorded without failure": lambda doc: doc["slots"].__setitem__(
                0, {"slot": "S1-SQ-1", "status": "not_recorded", "protocol_failures": []}),
            "not recorded with extras": lambda doc: doc["slots"].__setitem__(
                0, {"slot": "S1-SQ-1", "status": "not_recorded", "session_id": "sess-alpha",
                    "protocol_failures": [{"stage": "recording", "reason": "x"}]}),
            "session id within": lambda doc: doc["slots"][2].update(session_id="sess-other"),
            "session date within": lambda doc: doc["slots"][2].update(session_date="2026-10-12"),
            "session id across": same_id_across,
            "session date across": same_date_across,
            "bad date": lambda doc: doc["slots"][0].update(session_date="2026-02-30"),
            "unsafe session id": lambda doc: [entry.update(session_id="../x") for entry in doc["slots"][:6]],
            "fixture id": lambda doc: doc["slots"][0].update(fixture_id="VBT-1"),
            "rep text": lambda doc: doc["slots"][0].update(attempted_rep_start_s=["2.0", 3, 4]),
            "reps not list": lambda doc: doc["slots"][0].update(attempted_rep_start_s=2.0),
            "wl number": lambda doc: doc["slots"][0].update(wl_csv=3),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                with tempfile.TemporaryDirectory() as other:
                    fake = FakeStudy(Path(other).resolve())
                    slots_path = fake.write()
                    document = copy.deepcopy(fake.slots_doc)
                    mutate(document)
                    slots_path.write_bytes(dump(document))
                    code, _, err = fake.run()
                    self.assertEqual(code, 1, f"{name}: {err}")
                    self.assertIn("error:", err)
                    self.assertFalse((fake.study_dir / "out").exists())

    def test_duplicate_json_keys_are_rejected(self) -> None:
        slots_path = self.fake.write()
        slots_path.write_bytes(slots_path.read_bytes().replace(b'"format": ', b'"format": "x", "format": ', 1))
        code, _, _ = self.fake.main(self.fake.argv(slots_path))
        self.assertEqual(code, 1)


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
