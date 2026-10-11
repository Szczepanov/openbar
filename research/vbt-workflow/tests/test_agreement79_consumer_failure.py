"""#79 consumer-failure records: a consumer run that aborted a lift, in the hand-check and the report.

When the pinned consumer aborts a lift it writes no report. Both tools then take the retained consumer message
(`--consumer-failure <exercise>=<file>`): the report renders the lift as statistics_unavailable, and for
back_squat the hand-check records S1-SQ-1 as not checkable. Synthetic temp-dir data only.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agreement79_handcheck as handcheck  # noqa: E402
import agreement79_report_render as render  # noqa: E402
import agreement79_study as study  # noqa: E402
from test_agreement79_handcheck import Fixture  # noqa: E402
from test_agreement79_report import TOOL_COMMIT, Base  # noqa: E402

MESSAGE = b"Error: mixed method fingerprints in zzpriv-wl-S1-SQ-2.csv\n"
NOT_CHECKABLE = "slot_not_checkable:consumer_failed"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class HandcheckConsumerFailureTests(unittest.TestCase):
    """The hand-check records S1-SQ-1 as not checkable when the consumer aborted back_squat; never a substitute."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fx = Fixture(Path(tmp.name).resolve())
        self.fx.write()
        self.runs = self.fx.report_path.parent
        self.fx.report_path.unlink()  # the aborted consumer wrote no report
        self.failure = self.runs / "consumer-failure-back_squat.txt"
        self.failure.write_bytes(MESSAGE)

    def run_tool(self, *extra: str, record: str | None = None, runs: Path | None = None) -> tuple[int, str]:
        argv = ["--inventory", str(self.fx.inventory_path), "--consumer-app", str(self.fx.root / "consumer/app"),
                "--pairing-review", "not_done", "--seed-reference-review", "not_done", "--output", str(self.fx.output),
                "--consumer-failure", record if record is not None else f"back_squat={self.failure}",
                "--runs-dir", str(runs if runs is not None else self.runs), *extra]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            code = handcheck.main(argv, root=self.fx.root, tool_root=self.fx.tool_root, bridge=self.fx.bridge,
                                  git=self.fx.git_state)
        return code, stderr.getvalue()

    def test_consumer_failure_is_recorded_as_not_checkable(self) -> None:
        code, err = self.run_tool()
        self.assertEqual(code, 0, err)
        result = self.fx.result()
        self.assertEqual(result["status"], "failed")
        self.assertIn(NOT_CHECKABLE, result["reasons"])
        self.assertNotIn("slot_not_analyzable", result["reasons"])
        self.assertEqual(result["inputs"]["report"], None)
        self.assertEqual(result["inputs"]["consumer_failure"],  # review L2: the runs folder is bound too
                         {"path": self.failure.relative_to(self.fx.root).as_posix(), "sha256": sha(MESSAGE),
                          "runs_dir": self.runs.relative_to(self.fx.root).as_posix()})
        self.assertEqual((result["reps"], result["velocity_check"], result["parser"]), ([], None, None))
        self.assertEqual(result["public"], {"status": "failed", "reasons": result["reasons"], "values_compared": 0,
                                            "values_matched": 0, "max_abs_discrepancy_mps": None})
        self.assertEqual(self.fx.bridge_calls, 0)  # no values are computed or invented
        self.assertNotIn("mixed method", json.dumps(result))

    def test_not_analyzable_slot_with_a_consumer_failure_stays_not_analyzable(self) -> None:
        self.fx.analyzable = False
        self.fx.write()
        self.fx.report_path.unlink()
        code, err = self.run_tool()
        self.assertEqual(code, 0, err)
        self.assertIn("slot_not_analyzable", self.fx.result()["reasons"])
        self.assertNotIn(NOT_CHECKABLE, self.fx.result()["reasons"])

    def test_invalid_consumer_failure_inputs_exit_1(self) -> None:
        outside = self.fx.root.parent / f"{self.fx.root.name}-outside.txt"
        outside.write_bytes(MESSAGE)
        self.addCleanup(outside.unlink)
        cases: dict[str, Callable[[], tuple[int, str]]] = {
            "missing file": lambda: self.run_tool(record=f"back_squat={self.failure}.missing"),
            "empty file": lambda: (self.failure.write_bytes(b""), self.run_tool())[1],
            "outside root": lambda: self.run_tool(record=f"back_squat={outside}"),
            "run file present": lambda: (self.fx.write(), self.run_tool())[1],
            "markdown run file present": lambda: ((self.runs / "report-back_squat-run2.md").write_bytes(b"x"),
                                                  self.run_tool())[1],
            "wrong lift": lambda: self.run_tool(record=f"snatch={self.failure}"),
            "no lift": lambda: self.run_tool(record=str(self.failure)),
            "also --report": lambda: (self.fx.write(), self.fx.report_path.rename(self.runs / "elsewhere.json"),
                                      self.run_tool("--report", str(self.runs / "elsewhere.json")))[2],
            "runs dir outside root": lambda: self.run_tool(runs=self.fx.root.parent),
            "bound input file": lambda: self.run_tool(record=f"back_squat={self.fx.analysis_path}"),  # review L1
            "no pairs file": lambda: (setattr(self.fx, "pairs_file", False), self.fx.write(),
                                      self.fx.report_path.unlink(), self.run_tool())[3],
        }
        for name, run in cases.items():
            with self.subTest(name):
                self.setUp()
                code, err = run()
                self.assertEqual(code, 1, err)
                self.assertIn("error:", err)
                self.assertNotIn("Traceback", err)
                self.assertFalse(self.fx.output.exists())

    def main(self, *extra: str) -> tuple[int, str]:
        argv = ["--inventory", str(self.fx.inventory_path), "--consumer-app", str(self.fx.root / "consumer/app"),
                "--pairing-review", "not_done", "--seed-reference-review", "not_done", "--output", str(self.fx.output)]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            code = handcheck.main([*argv, *extra], root=self.fx.root, tool_root=self.fx.tool_root,
                                  bridge=self.fx.bridge, git=self.fx.git_state)
        return code, stderr.getvalue()

    def test_consumer_failure_needs_runs_dir(self) -> None:
        code, err = self.main("--consumer-failure", f"back_squat={self.failure}")
        self.assertEqual(code, 1, err)
        self.assertIn("--runs-dir", err)
        self.assertFalse(self.fx.output.exists())

    def test_missing_report_error_names_the_consumer_failure_option(self) -> None:
        code, err = self.main()  # review L7: analyzable S1-SQ-1, neither --report nor --consumer-failure
        self.assertEqual(code, 1, err)
        self.assertIn("--report", err)
        self.assertIn("--consumer-failure back_squat=<file>", err)

    def test_runs_dir_without_a_consumer_failure_is_refused(self) -> None:
        self.fx.write()  # a normal run with its report
        code, err = self.main("--report", str(self.fx.report_path), "--runs-dir", str(self.runs))
        self.assertEqual(code, 1, err)
        self.assertIn("--runs-dir", err)
        self.assertFalse(self.fx.output.exists())


class ReportBindsHandcheckConsumerFailureTests(Base):
    """The report accepts the hand-check's consumer_failed record only when it binds the same retained message."""

    def fail_squat(self) -> Path:
        self.fx.write()
        for suffix in study.CONSUMER_RUN_FILES:
            (self.fx.runs / f"report-back_squat-{suffix}").unlink()
        path = self.fx.runs / "consumer-failure-back_squat.txt"
        path.write_bytes(MESSAGE)
        return path

    def real_handcheck(self, failure: Path) -> Path:
        """The hand-check tool's own output for this locked study (no bridge call is expected)."""
        output = self.fx.root / "validation/private/vbt/study-79/handcheck-consumer-failed.json"
        argv = ["--inventory", str(self.fx.lock_dir / "inventory.json"),
                "--consumer-app", str(self.fx.root.parent / "consumer/app"), "--pairing-review", "not_done",
                "--seed-reference-review", "not_done", "--output", str(output),
                "--consumer-failure", f"back_squat={failure}", "--runs-dir", str(self.fx.runs)]

        def git(path: Path) -> tuple[str, bool]:
            return (TOOL_COMMIT, True) if path == self.fx.tool else (study.CONSUMER_COMMIT, True)
        with contextlib.redirect_stdout(io.StringIO()):
            code = handcheck.main(argv, root=self.fx.root, tool_root=self.fx.tool, git=git,
                                  bridge=lambda *_: self.fail("the bridge must not run"))
        self.assertEqual(code, 0)
        return output

    def report(self, failure: Path, handcheck_path: Path, output: str = "docs/analysis/AGREEMENT_79.md") -> str:
        code, log = self.fx.run(output=output, write=False, extra=("--consumer-failure", f"back_squat={failure}"),
                                **{"--handcheck": str(handcheck_path)})
        self.assertEqual(code, 0, log)
        return self.fx.output(output)

    def patched(self, source: Path, change: Callable[[dict], None]) -> Path:
        document = json.loads(source.read_bytes())
        change(document)
        target = source.with_name("handcheck-patched.json")
        target.write_bytes(study.serialize(document))  # each case replaces the previous patched copy
        return target

    def test_consumer_failed_handcheck_is_bound_and_the_criteria_keep_failing(self) -> None:
        failure = self.fail_squat()
        text = self.report(failure, self.real_handcheck(failure))
        self.assertIn(f"**{render.VERDICT_LABEL}: FAIL**", text)
        self.assertIn("back_squat: statistics_unavailable", text)
        # The reviews were not done (nothing to review), so they are failure reasons too.
        self.assertRegex(text, rf"\| C4 \|[^\n]*\| fail \| handcheck_failed: [^\n|]*{NOT_CHECKABLE}")
        self.assertIn(f"- Reasons: pairing_review_not_done; seed_reference_review_not_done; {NOT_CHECKABLE}", text)
        self.assertNotIn("handcheck_inputs_mismatch", text)
        self.assertIn("- Bound to this inventory and the back_squat run 1 report: yes", text)
        self.assertIn(f"- back_squat: consumer aborted; retained message SHA-256 `{sha(MESSAGE)}`", text)
        for private in ("mixed method", "zzpriv", "consumer-failure"):
            self.assertNotIn(private, text)

    def test_handcheck_consumer_failure_binding_breaches_are_inputs_mismatch(self) -> None:
        failure = self.fail_squat()
        source = self.real_handcheck(failure)
        cases = {"other message": lambda d: d["inputs"]["consumer_failure"].update(sha256="0" * 64),
                 "no record while S1-SQ-1 is analyzable": lambda d: d["inputs"].update(consumer_failure=None),
                 "record without the key": lambda d: d["inputs"].pop("consumer_failure"),
                 "other runs folder": lambda d: d["inputs"]["consumer_failure"].update(  # review L2
                     runs_dir="validation/private/vbt/study-79/other-runs"),
                 "runs folder unrecorded": lambda d: d["inputs"]["consumer_failure"].pop("runs_dir")}
        for index, (name, change) in enumerate(cases.items()):
            with self.subTest(name):
                text = self.report(failure, self.patched(source, change), output=f"target/mismatch-{index}.md")
                self.assertIn("handcheck_inputs_mismatch", text)
                self.assertIn(f"**{render.VERDICT_LABEL}: FAIL**", text)

    def test_handcheck_consumer_failure_without_a_failed_squat_lift_is_inputs_mismatch(self) -> None:
        self.fx.handcheck_patch = lambda d: d["inputs"].update(consumer_failure={"path": "x", "sha256": sha(MESSAGE)})
        code, log = self.fx.run()
        self.assertEqual(code, 0, log)
        self.assertIn("handcheck_inputs_mismatch", self.fx.output())


class ConsumerFailureTests(Base):
    """M1: a retained consumer-failure record renders that lift as statistics_unavailable; others still render."""

    MESSAGE = b"Error: mixed method fingerprints in zzpriv-wl-S1-SN-1.csv\n"
    RUN_FILES = study.CONSUMER_RUN_FILES

    def record(self, exercise: str = "snatch", remove: tuple[str, ...] = RUN_FILES, data: bytes = MESSAGE) -> str:
        self.fx.write()
        for suffix in remove:
            (self.fx.runs / f"report-{exercise}-{suffix}").unlink()
        item = self.fx.put(f"validation/private/vbt/study-79/runs-zzpriv/consumer-failure-{exercise}.txt", data)
        return f"{exercise}={self.fx.root / item['path']}"

    def test_failed_lift_is_unavailable_fails_c3_and_the_rest_still_renders(self) -> None:
        code, log = self.fx.run(write=False, extra=("--consumer-failure", self.record()))
        self.assertEqual(code, 0, log)
        text = self.fx.output()
        self.assertIn(f"**{render.VERDICT_LABEL}: FAIL**", text)
        self.assertIn("snatch: statistics_unavailable", text)
        self.assertIn("| snatch | unavailable (consumer_failed) |", text)
        self.assertIn(f"- snatch: consumer aborted; retained message SHA-256 `{sha(self.MESSAGE)}`", text)
        self.assertIn("| back_squat | 18 | 0.0025 | 0.021811 | -0.04 | 0.045 | 0.0173 | yes |", text)
        self.assertIn("| S1-SN-1 | snatch | enrolled | yes | yes | — | — |", text)
        self.assertNotIn("clean: statistics_unavailable", text)
        for private in ("mixed method", "zzpriv", "consumer-failure"):
            self.assertNotIn(private, text)

    def test_missing_run_files_without_a_record_exit_1(self) -> None:
        self.fx.write()
        for suffix in self.RUN_FILES:
            (self.fx.runs / f"report-snatch-{suffix}").unlink()
        code, log = self.fx.run(write=False)
        self.assertEqual(code, 1, log)
        self.assertIn("--consumer-failure", log)

    def test_inconsistent_or_malformed_records_exit_1(self) -> None:
        def no_pairs() -> tuple[str, ...]:
            for slot in self.fx.slots:
                slot["analyzable"] = slot["analyzable"] and slot["exercise"] != "clean"
            self.fx.write()
            return ("--consumer-failure", f"clean={self.fx.root / self.fx.handcheck_rel}")

        def outside() -> tuple[str, ...]:
            self.record()
            path = self.fx.root.parent / "failure.txt"
            path.write_bytes(self.MESSAGE)
            return ("--consumer-failure", f"snatch={path}")

        def duplicate() -> tuple[str, ...]:
            value = self.record()
            return ("--consumer-failure", value, "--consumer-failure", value)

        cases: dict[str, Callable[[], tuple[str, ...]]] = {
            "run files present": lambda: ("--consumer-failure", self.record(remove=())),
            "one run file present": lambda: ("--consumer-failure", self.record(remove=self.RUN_FILES[1:])),
            "lift without pairs file": no_pairs,
            "empty message": lambda: ("--consumer-failure", self.record(data=b"")),
            "unknown lift": lambda: ("--consumer-failure", self.record().replace("snatch=", "deadlift=", 1)),
            "no lift": lambda: ("--consumer-failure", self.record().split("=", 1)[1]),
            "duplicate": duplicate,
            "missing file": lambda: ("--consumer-failure", self.record() + ".missing"),
            "outside data checkout": outside,
            "bound input file": lambda: (self.record(), "--consumer-failure",  # review L1
                                         f"snatch={self.fx.root / self.fx.slot('S1-SN-1')['files']['wl_csv']['path']}")[1:],
        }
        for name, extra in cases.items():
            with self.subTest(name):
                self.setUp()
                code, log = self.fx.run(write=False, extra=extra())
                self.assertEqual(code, 1, log)
                self.assertFalse((self.fx.tool / "docs/analysis/AGREEMENT_79.md").exists())


if __name__ == "__main__":
    unittest.main()
