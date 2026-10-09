"""Recovery and outgoing-package regressions for #114, with the existing session fakes."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_fakes as fakes
from session_harness import SessionRunner, SessionTestCase

import analyze_lift
import assess_clip
import session_ingest
import session_run
import session_handoff


class HandoffRunner(SessionRunner):
    def execute(self, argv):
        super().execute(argv)
        if "--observations" not in argv:
            return
        output = analyze_lift.ROOT / argv[argv.index("--output") + 1]
        prediction = analyze_lift.ROOT / argv[argv.index("--observations") + 1]
        doc = json.loads(output.read_text())
        doc["provenance"]["tracker"]["id"] = "opencv-csrt"
        doc["provenance"]["tracker"]["implementation"]["parameters"] = {
            "prediction_sha256": analyze_lift.file_sha256(prediction)}
        for sample in doc["raw_observations"]:
            sample["tracker_id"] = "opencv-csrt"
        output.write_text(json.dumps(doc))


class HandoffTests(SessionTestCase):
    def main(self, argv, runner=None, **kwargs):
        return super().main(argv, runner=runner or HandoffRunner(), **kwargs)

    def setUp(self):
        super().setUp()
        self.add_video("lift.mp4", b"one lift")
        self.assertEqual(self.ingest()[0], 0)
        self.session = self.state()
        self.clip = self.session["clips"][0]
        self.csv = self.write_csv([fakes.accepted_row(self.clip, self.session, "clean")])
        self.files = analyze_lift.output_paths(self.session_dir / "analyses", self.clip["fixture_id"], "csrt")
        self.assessments = self.dir / "assessments"
        self.assessments.mkdir()
        self.outgoing = self.dir / "outgoing"

    def complete(self):
        code, _, err = self.main(self.run_args(self.csv))
        self.assertEqual(code, 0, err)

    def assessment(self):
        # Bind an unchanged retained assessment; validator semantics belong to #111's tests.
        doc = json.loads((analyze_lift.ROOT / "validation/examples/vbt-clip-assessment.example.json").read_text())
        doc["fixture_id"] = self.clip["fixture_id"]
        doc["processing"] = assess_clip.check("complete")
        doc["mechanical"] = {"status": "valid", "checks": {name: assess_clip.check("valid")
                                for name in doc["mechanical"]["checks"]}}
        record = json.loads(self.files["run_record"].read_text())
        bound = {**self.files, "video": analyze_lift.ROOT / self.clip["media_path"],
                 "seed": analyze_lift.ROOT / record["inputs"]["seed"]["path"], "manifest": self.manifest}
        validator = self.dir / "validator.exe"
        validator.write_bytes(b"retained validator")
        bound["validator"] = validator
        doc["sources"] = {name: {"path": analyze_lift.display_path(path), "sha256": analyze_lift.file_sha256(path)}
                          for name, path in bound.items()}
        path = self.assessments / (self.clip["fixture_id"] + ".assessment-v1.json")
        session_ingest.write_json(path, doc)
        return path

    def handoff(self, *extra):
        return self.main(["handoff", *self.common(), "--tracker-policy", "csrt-all-v1",
                          "--assessments-dir", str(self.assessments), "--output-dir", str(self.outgoing), *extra])

    def test_incomplete_session_cannot_be_handed_off(self):
        self.files["analysis"].parent.mkdir()
        self.files["analysis"].write_bytes(b"partial")
        code, _, err = self.handoff("--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("incomplete", err)
        self.assertFalse(self.outgoing.exists())

    def test_resume_interrupted_run_then_completed_resume_is_noop(self):
        with mock.patch.object(session_run, "write_report", side_effect=session_ingest.WorkflowError("interrupted")):
            self.assertEqual(self.main(self.run_args(self.csv))[0], 1)
        self.assertFalse((self.session_dir / session_ingest.RECORD_NAME).exists())
        code, _, err = self.main([*self.run_args(self.csv), "--resume"])
        self.assertEqual(code, 0, err)
        before = self.snapshot()
        runner = HandoffRunner()
        code, out, err = self.main([*self.run_args(self.csv), "--resume"], runner=runner)
        self.assertEqual(code, 0, err)
        self.assertIn("complete", out)
        self.assertEqual(runner.executed, [])
        self.assertEqual(self.snapshot(), before)

    def test_completed_resume_refuses_changed_configuration(self):
        self.complete()
        before = self.snapshot()
        args = [*self.run_args(self.csv), "--resume"]
        args[args.index("--stick-length-m") + 1] = "1.4"
        code, _, err = self.main(args)
        self.assertEqual(code, 1)
        self.assertIn("configuration", err)
        self.assertEqual(self.snapshot(), before)

    def test_status_detects_missing_record_and_changed_report(self):
        self.assertEqual(self.main(["status", *self.common()])[0], 1)
        self.complete()
        self.assertEqual(self.main(["status", *self.common()])[0], 0)
        (self.session_dir / "report.html").write_bytes(b"changed")
        self.assertEqual(self.main(["status", *self.common()])[0], 1)

    def test_repeated_dry_run_and_delivery_are_idempotent(self):
        self.complete()
        assessment = self.assessment()
        before = self.snapshot()
        first = self.handoff("--dry-run")
        self.assertEqual(first[0], 0, first[2])
        self.assertEqual(self.handoff("--dry-run"), first)
        self.assertFalse(self.outgoing.exists())
        self.assertEqual(self.snapshot(), before)
        delivered = self.handoff()
        self.assertEqual(delivered[0], 0, delivered[2])
        files = {p.relative_to(self.outgoing): p.read_bytes() for p in self.outgoing.rglob("*") if p.is_file()}
        self.assertEqual(len(list(self.outgoing.glob("*.analysis-v1.json"))), 1)
        self.assertEqual((self.outgoing / self.files["analysis"].name).read_bytes(), self.files["analysis"].read_bytes())
        self.assertEqual((self.outgoing / assessment.name).read_bytes(), assessment.read_bytes())
        self.assertIn("research-only", (self.outgoing / "HANDOFF.txt").read_text())
        self.assertEqual(self.handoff()[0], 0)
        self.assertEqual({p.relative_to(self.outgoing): p.read_bytes() for p in self.outgoing.rglob("*") if p.is_file()}, files)

    def test_other_tracker_analysis_and_stale_assessment_refused(self):
        self.complete()
        assessment = self.assessment()
        extra = self.files["analysis"].with_name(self.clip["fixture_id"] + ".other.analysis-v1.json")
        extra.write_bytes(self.files["analysis"].read_bytes())
        code, _, err = self.handoff("--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("ambiguous", err)
        extra.unlink()
        doc = json.loads(assessment.read_text())
        doc["sources"]["analysis"]["sha256"] = "0" * 64
        session_ingest.write_json(assessment, doc)
        self.assertEqual(self.handoff("--dry-run")[0], 1)

    def test_malformed_final_record_and_changed_analysis_fail_closed(self):
        self.complete()
        record = self.session_dir / session_ingest.RECORD_NAME
        old = record.read_bytes()
        record.write_text('{}')
        self.assertEqual(self.main(["status", *self.common()])[0], 1)
        record.write_bytes(old)
        self.files["analysis"].write_text('{}')
        self.assertEqual(self.main(["status", *self.common()])[0], 1)

    def test_delivery_never_replaces_a_different_package(self):
        self.complete()
        self.assessment()
        self.outgoing.mkdir()
        (self.outgoing / "keep.txt").write_bytes(b"keep")
        self.assertEqual(self.handoff()[0], 1)
        self.assertEqual((self.outgoing / "keep.txt").read_bytes(), b"keep")

    def test_duplicate_arrivals_are_one_clip(self):
        self.add_video("duplicate.mp4", b"one lift")
        self.assertEqual(self.ingest()[0], 0)
        self.assertEqual(len(self.state()["clips"]), 1)

    def test_inbox_zero_bytes_times_out_without_copy(self):
        self.add_video("incomplete.mp4", b"")
        now = iter(range(20))
        code, _, err = self.main(["ingest", *self.common(), "--inbox", str(self.inbox),
                                 "--media-dir", str(self.media), "--inbox-timeout-s", "3"],
                                clock=lambda: next(now), sleep=lambda _: None,
                                suggester=lambda frame: fakes.suggestions())
        self.assertEqual(code, 1)
        self.assertIn("stable", err)
        self.assertFalse((self.media / "incomplete.mp4").exists())

    def test_duplicate_clip_record_is_not_a_second_trial(self):
        self.complete()
        path = self.session_dir / session_ingest.RECORD_NAME
        doc = json.loads(path.read_text())
        doc["clips"].append(doc["clips"][0])
        session_ingest.write_json(path, doc)
        self.assertEqual(self.handoff("--dry-run")[0], 1)

    def test_evidence_or_candidates_changing_during_handoff_refused(self):
        self.complete()
        self.assessment()
        def change(seconds):
            self.files["analysis"].write_bytes(b"changed")
        self.assertEqual(self.main(["handoff", *self.common(), "--tracker-policy", "csrt-all-v1",
                                  "--assessments-dir", str(self.assessments), "--output-dir", str(self.outgoing),
                                  "--dry-run"], sleep=change)[0], 1)
        self.assertFalse(self.outgoing.exists())

    def test_wrong_policy_and_missing_assessment_refused(self):
        self.complete()
        self.assertEqual(self.handoff("--dry-run")[0], 1)
        self.assessment()
        args = ["handoff", *self.common(), "--tracker-policy", "sam2-all-v1", "--assessments-dir", str(self.assessments),
                "--output-dir", str(self.outgoing), "--dry-run"]
        self.assertEqual(self.main(args)[0], 1)

    def test_machine_only_session_stays_research_only(self):
        directory = self.session_dir / "machine-run"
        directory.mkdir()
        (directory / "machine-run-record.json").write_text('{}')
        code, _, err = self.handoff("--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("machine-origin", err)
        self.assertIn("research-only", err)

    def test_unreadable_inbox_candidate_is_not_silently_imported(self):
        self.add_video("partial.mp4", b"partial")
        original = analyze_lift.file_sha256
        def hash_file(path):
            if path.name == "partial.mp4":
                raise PermissionError("still syncing")
            return original(path)
        now = iter(range(20))
        with mock.patch.object(analyze_lift, "file_sha256", side_effect=hash_file):
            code, _, err = self.main(["ingest", *self.common(), "--inbox", str(self.inbox),
                                     "--media-dir", str(self.media), "--inbox-timeout-s", "3"],
                                    clock=lambda: next(now), suggester=lambda frame: fakes.suggestions())
        self.assertEqual(code, 1)
        self.assertIn("stable", err)
        self.assertFalse((self.media / "partial.mp4").exists())

    def test_read_buffer_must_match_bound_analysis_even_if_source_restored(self):
        self.complete()
        self.assessment()
        completed = session_handoff.completed_session(self.session_dir)
        original = Path.read_bytes
        def read_file(path):
            return b"swapped buffer" if path == self.files["analysis"] else original(path)
        with mock.patch.object(Path, "read_bytes", read_file):
            with self.assertRaisesRegex(session_ingest.WorkflowError, "hash|changed"):
                session_handoff.outgoing_files(completed, "csrt-all-v1", self.assessments)

    def test_missing_manifest_null_validator_and_escaping_assessment_path_refused(self):
        self.complete()
        path = self.assessment()
        original = json.loads(path.read_text())
        for failure in ("manifest", "validator", "path"):
            with self.subTest(failure=failure):
                doc = json.loads(json.dumps(original))
                if failure == "manifest":
                    del doc["sources"]["manifest"]
                elif failure == "validator":
                    doc["sources"]["validator"]["sha256"] = None
                else:
                    doc["sources"]["validator"]["path"] = "../outside.exe"
                session_ingest.write_json(path, doc)
                code, _, err = self.handoff("--dry-run")
                self.assertEqual(code, 1)
                self.assertIn("binding", err)
                self.assertFalse(self.outgoing.exists())

    def test_new_analysis_arriving_during_delivery_is_ambiguous(self):
        self.complete()
        self.assessment()
        def arrive(seconds):
            self.files["analysis"].with_name(self.clip["fixture_id"] + ".extra.analysis-v1.json").write_bytes(b"extra")
        code, _, err = self.main(["handoff", *self.common(), "--tracker-policy", "csrt-all-v1",
                                 "--assessments-dir", str(self.assessments), "--output-dir", str(self.outgoing)], sleep=arrive)
        self.assertEqual(code, 1)
        self.assertIn("ambiguous", err)
        self.assertFalse(self.outgoing.exists())

    def test_outgoing_assessment_statuses_are_preserved(self):
        self.complete()
        path = self.assessment()
        doc = json.loads(path.read_text())
        doc["mechanical"]["checks"]["decoded_pts"] = assess_clip.check("unknown", "source_probe_unavailable")
        doc["mechanical"]["status"] = "unknown"
        session_ingest.write_json(path, doc)
        code, _, err = self.handoff()
        self.assertEqual(code, 0, err)
        self.assertEqual((self.outgoing / path.name).read_bytes(), path.read_bytes())
        self.assertIn("assessment=unknown", (self.outgoing / "HANDOFF.txt").read_text())

    def test_manifest_replacement_after_parse_does_not_validate_old_entries(self):
        self.complete()
        original = analyze_lift.fixture_probe.validate_manifest
        def replace(document):
            original(document)
            modified = json.loads(json.dumps(document))
            modified["fixtures"][0]["notes"] = "changed after parse"
            session_ingest.write_json(self.manifest, modified)
        with mock.patch.object(analyze_lift.fixture_probe, "validate_manifest", side_effect=replace):
            with self.assertRaisesRegex(session_ingest.WorkflowError, "changed|mismatch"):
                session_handoff.completed_session(self.session_dir)

    def test_replacement_after_stable_poll_is_not_a_new_import(self):
        self.add_video("new.mp4", b"stable bytes")
        original = session_ingest.stable_inbox
        def replace(*args):
            approved = original(*args)
            (self.inbox / "new.mp4").write_bytes(b"replacement never stable")
            return approved
        with mock.patch.object(session_ingest, "stable_inbox", side_effect=replace):
            code, _, err = self.ingest()
        self.assertEqual(code, 1)
        self.assertIn("changed", err)
        self.assertFalse((self.media / "new.mp4").exists())
