"""#79 aggregate report: frozen criterion truth table, integrity, privacy, determinism (synthetic roots only)."""
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

import agreement79_inventory as inventory_tool  # noqa: E402
import agreement79_report as report_tool  # noqa: E402
import agreement79_report_render as render  # noqa: E402
import agreement79_study as study  # noqa: E402

# Distinctive private strings: none may ever reach the Markdown.
SESSION_IDS = ("zzpriv-session-alpha", "zzpriv-session-bravo", "zzpriv-session-charlie")
SESSION_DATES = ("2031-02-03", "2031-02-05", "2031-02-07")
ORIGINAL = "ZZPRIV_ORIGINAL_{:02d}.MOV"
REP_WL, REP_OPENBAR = 0.987654, 0.976543  # per-rep values in the private consumer reports
TOOL_COMMIT = "d" * 40
EXERCISES = ("back_squat", "snatch", "clean")
STATS = ("bias", "sampleSd", "lowerLoA", "upperLoA", "meanAbsoluteDifference", "slope", "intercept", "pearsonR",
         "geometricMeanRatio")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture_id(slot: str) -> str:
    return f"vbt-{study.SLOTS.index(slot) + 1:010x}c0ffee"


def stats(lower: float | None = -0.04, upper: float | None = 0.045, n: int = 18) -> dict[str, Any]:
    values = {"bias": 0.0025, "sampleSd": 0.021811, "lowerLoA": lower, "upperLoA": upper,
              "meanAbsoluteDifference": 0.0173, "slope": -0.0123, "intercept": 0.0111, "pearsonR": -0.1234,
              "geometricMeanRatio": 1.004321}
    reasons = {key: None if values[key] is not None else "insufficient_n" for key in STATS}
    return {"n": n, **values, "reasons": reasons}


def metric_stats(**kwargs: Any) -> dict[str, Any]:
    return {metric: stats(**kwargs) for metric in ("meanVelocityMps", "peakVelocityMps", "romCm")}


def video(slot: str, wl_sha: str, analysis_sha: str) -> dict[str, Any]:
    """One consumer report video (buildAgreementReport shape) with three complete paired reps."""
    def row(index: int) -> dict[str, Any]:
        measure = {"wl": REP_WL, "openBar": REP_OPENBAR, "difference": -0.011111, "magnitude": 0.982099}
        return {"label": slot, "loadKg": study.slot_lift(slot)["load_kg"], "wlIndex": index, "openBarIndex": index,
                "wlComplete": True, "openBarComplete": True, "temporalIoU": 0.912345,
                "meanVelocityMps": dict(measure), "peakVelocityMps": dict(measure), "romCm": dict(measure)}

    return {"label": slot, "loadKg": study.slot_lift(slot)["load_kg"], "offsetS": 0,
            "wl": {"fileSha256": wl_sha, "parserVersion": study.WL_PARSER, "segmentationRule": study.SEGMENTATION,
                   "videoId": f"zzpriv-wl-video-{slot}"},
            "openBar": {"fileSha256": analysis_sha, "parserVersion": study.OPENBAR_PARSER,
                        "segmentationRule": study.SEGMENTATION, "sourceVideoSha256": "5" * 64,
                        "methodConfigSha256": "7" * 64, "provenance": {"tracker": "opencv-csrt"}, "breakCount": 1},
            "counts": {"wlTotal": 3, "openBarTotal": 3, "paired": 3, "wlOnly": 0, "openBarOnly": 0, "openBarExcluded": 0},
            "stats": metric_stats(n=3), "paired": [row(index) for index in range(3)],
            "wlOnly": [], "openBarOnly": [], "openBarExcluded": []}


def consumer_report(videos: list[dict[str, Any]]) -> dict[str, Any]:
    videos = sorted(videos, key=lambda item: item["label"])
    total = {key: sum(item["counts"][key] for item in videos) for key in videos[0]["counts"]}
    return {"schemaVersion": study.REPORT_SCHEMA, "segmentationRule": study.SEGMENTATION,
            "wlParserVersion": study.WL_PARSER, "openBarParserVersion": study.OPENBAR_PARSER,
            "openBarMethodConfigSha256": "7" * 64, "differenceConvention": "OpenBar minus WL",
            "primaryMetric": "meanVelocityMps", "secondaryMetrics": ["peakVelocityMps", "romCm"],
            "minOverlap": study.MIN_OVERLAP, "caveats": [render.POOLING_CAVEAT, render.RATIO_CAVEAT],
            "pooled": {"videoCount": len(videos), "counts": total, "stats": metric_stats()}, "videos": videos}


def slot_doc(slot: str, analyzable: bool = True) -> dict[str, Any]:
    lift, session = study.slot_lift(slot), study.slot_session(slot)
    return {"slot": slot, "lift": lift["code"], "exercise": lift["exercise"], "load_kg": lift["load_kg"],
            "session": session, "status": "enrolled", "session_id": SESSION_IDS[session - 1],
            "session_date": SESSION_DATES[session - 1], "fixture_id": fixture_id(slot), "processed": analyzable,
            "analyzable": analyzable, "protocol_conforming": analyzable, "failures": [],
            "assessment_statuses": None, "seed_timestamp_s": 0.5, "attempted_rep_start_s": [2.0, 5.0, 8.0],
            "creation_time": f"{SESSION_DATES[session - 1]}T09:08:07Z", "manifest_entry_sha256": "ab" * 32, "files": {}}


class FakeStudy:
    """A locked study in a temp data checkout, plus a separate tool checkout for the output."""

    def __init__(self, base: Path) -> None:
        self.root, self.tool = base / "data", base / "tool"
        self.study_dir = self.root / study.STUDY_DIR
        self.lock_dir, self.runs = self.study_dir / "lock-zzpriv", self.study_dir / "runs-zzpriv"
        self.slots = [slot_doc(slot) for slot in study.SLOTS]
        self.collection_status = "complete"
        self.report_patch: dict[str, Callable[[dict], None]] = {}
        self.handcheck_patch: Callable[[dict], None] = lambda _: None
        self.scale_rows_patch: Callable[[list], None] = lambda _: None
        self.git_state = (study.CONSUMER_COMMIT, True)
        self.node: Callable[[], str] = lambda: "v24.9.0"
        self.owner_failures: dict[str, list[dict[str, str]]] = {}

    def put(self, relative: str, data: bytes) -> dict[str, str]:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": relative, "sha256": sha(data)}

    def slot(self, name: str) -> dict[str, Any]:
        return self.slots[study.SLOTS.index(name)]

    def write_slots(self) -> None:
        for slot in self.slots:
            sid, fid = slot["session_id"], slot["fixture_id"]
            slot["failures"] = [dict(item, source="owner") for item in self.owner_failures.get(slot["slot"], [])]
            slot["protocol_conforming"] = slot["analyzable"] and not slot["failures"]
            slot["files"] = {"wl_csv": self.put(f"validation/private/vbt/wl/zzpriv-wl-{slot['slot']}.csv",
                                                f"time,v\n0,{slot['slot']}\n".encode()),
                             "analysis": self.put(f"validation/private/vbt/sessions/{sid}/analyses/"
                                                  f"{fid}.opencv-csrt.analysis-v1.json", f'{{"a":"{fid}"}}\n'.encode())}

    def write_sessions(self) -> dict[str, Any]:
        sessions = {}
        for index, sid in enumerate(SESSION_IDS, start=1):
            rows = [{"fixture_id": slot["fixture_id"], "source_video_sha256": "6" * 64,
                     "reference_to_plate_ratio": {"value": 1.0123, "lower": 0.9876, "upper": 1.0371,
                                                  "consistent_with_1": True}}
                    for slot in self.slots if slot["session"] == index]
            self.scale_rows_patch(rows)
            scale = self.put(f"validation/private/vbt/sessions/{sid}/scale-report/scale-reference-v1.json",
                             study.serialize({"schema_version": 1, "rows": rows}))
            record = self.put(f"validation/private/vbt/sessions/{sid}/session-record.json", b'{"r": 1}\n')
            sessions[sid] = {"files": {"scale_report_json": scale, "session_record": record}}
        return sessions

    def write_lifts(self) -> dict[str, Any]:
        lifts = {}
        for lift in study.LIFTS.values():
            exercise = lift["exercise"]
            slots = [slot for slot in self.slots if slot["exercise"] == exercise]
            analyzable = [slot for slot in slots if slot["analyzable"]]
            record: dict[str, Any] = {"slots": [s["slot"] for s in slots], "analyzable_slots": [s["slot"] for s in analyzable]}
            if not analyzable:
                lifts[exercise] = {**record, "pairs_file": None, "reason": "no_analyzable_slots"}
                continue
            pairs = {"pairs": [{"label": s["slot"], "loadKg": s["load_kg"], "wlCsv": "../../wl/x.csv",
                                "openBarAnalysis": "../../sessions/x.json", "offsetS": 0} for s in analyzable]}
            rel = (self.lock_dir / f"pairs-{exercise}.json").relative_to(self.root).as_posix()
            lifts[exercise] = {**record, "pairs_file": self.put(rel, study.serialize(pairs))}
            self.write_report(exercise, analyzable)
        return lifts

    def write_report(self, exercise: str, analyzable: list[dict[str, Any]]) -> None:
        report = consumer_report([video(s["slot"], s["files"]["wl_csv"]["sha256"], s["files"]["analysis"]["sha256"])
                                  for s in analyzable])
        self.report_patch.get(exercise, lambda _: None)(report)
        data = (json.dumps(report, indent=2) + "\n").encode("utf-8")
        markdown = f"# Velocity agreement report\n\n| 0 | 0 | 0.9 | meanVelocityMps | {REP_WL} | {REP_OPENBAR} |\n".encode()
        base = self.runs.relative_to(self.root).as_posix()
        for run in ("run1", "run2"):
            self.put(f"{base}/report-{exercise}-{run}.json", data)
            self.put(f"{base}/report-{exercise}-{run}.md", markdown)

    def write(self) -> None:
        self.write_slots()
        owner = {"slots": [{"slot": s["slot"], "original_name": ORIGINAL.format(i)} for i, s in enumerate(self.slots, 1)]}
        slots_file = self.put("validation/private/vbt/study-79/slots.json", study.serialize(owner))
        exclusion = self.put(study.EXCLUSION_LIST.as_posix(), b"0" * 64 + b"  old.mp4\n")
        sessions = self.write_sessions()
        lifts = self.write_lifts()
        inventory = {
            "format": study.INVENTORY_FORMAT, "format_version": 1, "study_id": study.STUDY_ID, "tool_version": 1,
            "freeze": study.freeze_references(), "collection_status": self.collection_status,
            "plate_diameter_m": 0.45, "stick_length_m": 1.3, "wl_analysis_version": "5.1 (build 77)",
            "assessments_dir": "validation/private/vbt/study-79/assessments", "slots_file": slots_file,
            "exclusion_list": exclusion, "slots": self.slots, "sessions": sessions, "lifts": lifts,
            "environment": {"python": "3.12.7", "opencv_version": "4.12.0", "numpy_version": "2.2.6",
                            "ffmpeg": "ffmpeg version 8.0", "ffprobe": "ffprobe version 8.0"},
            "counts": inventory_tool.counts(self.slots), "lockable": self.collection_status != "in_progress",
            "data_root_commit": study.OPENBAR_BASELINE_COMMIT, "tool_commit": TOOL_COMMIT, "tool_tree_clean": True}
        data = study.serialize(inventory)
        base = self.lock_dir.relative_to(self.root).as_posix()
        self.inventory = self.put(f"{base}/inventory.json", data)
        self.put(f"{base}/lock-summary.json", study.serialize({
            "format": study.LOCK_SUMMARY_FORMAT, "format_version": 1, "study_id": study.STUDY_ID, "tool_version": 1,
            "tool_commit": TOOL_COMMIT, "tool_tree_clean": True, "data_root_commit": study.OPENBAR_BASELINE_COMMIT,
            "inventory_sha256": sha(data), "counts": inventory["counts"], "lockable": inventory["lockable"],
            "freeze": study.freeze_references(), "exclusion_list_sha256": exclusion["sha256"]}))
        self.write_handcheck()

    def write_handcheck(self) -> None:
        squat = self.runs / "report-back_squat-run1.json"
        document = {
            "format": report_tool.HANDCHECK_FORMAT, "format_version": 1, "study_id": study.STUDY_ID, "slot": "S1-SQ-1", "status": "passed",
            "reasons": [], "consumer_commit": study.CONSUMER_COMMIT,
            "inputs": {"analysis": self.slot("S1-SQ-1")["files"]["analysis"],
                       "report": {"path": squat.relative_to(self.root).as_posix(),
                                  "sha256": study.file_sha256(squat) if squat.exists() else None},
                       "inventory": self.inventory},
            "public": {"status": "passed", "reasons": [], "values_compared": 36, "values_matched": 36,
                       "max_abs_discrepancy_mps": 0.0}}
        self.handcheck_patch(document)
        self.put(self.handcheck_rel, study.serialize(document))

    handcheck_rel = "validation/private/vbt/study-79/handcheck-zzpriv.json"

    def run(self, output: str = "docs/analysis/AGREEMENT_79.md", write: bool = True, **argv: str) -> tuple[int, str]:
        if write:
            self.write()
        args = {"--inventory": str(self.lock_dir / "inventory.json"), "--lock-summary": str(self.lock_dir / "lock-summary.json"),
                "--runs-dir": str(self.runs), "--handcheck": str(self.root / self.handcheck_rel),
                "--consumer-app": str(self.root.parent / "consumer/app"), "--output": str(self.tool / output), **argv}
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = report_tool.main([item for pair in args.items() for item in pair], root=self.root,
                                    tool_root=self.tool, git=lambda app: self.git_state, node=self.node)
        return code, stdout.getvalue() + stderr.getvalue()

    def output(self, name: str = "docs/analysis/AGREEMENT_79.md") -> str:
        return (self.tool / name).read_bytes().decode("utf-8")


class Base(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fx = FakeStudy(Path(tmp.name).resolve())

    def run_ok(self, **kwargs: Any) -> str:
        code, log = self.fx.run(**kwargs)
        self.assertEqual(code, 0, log)
        return self.fx.output(kwargs.get("output", "docs/analysis/AGREEMENT_79.md"))


# --- Criterion truth table (pure function) ---------------------------------------------------------

def passing_facts() -> dict[str, Any]:
    slots = [slot_doc(slot) for slot in study.SLOTS]
    reports = {exercise: consumer_report([video(s["slot"], "1" * 64, "2" * 64) for s in slots if s["exercise"] == exercise])
               for exercise in EXERCISES}
    return {"collection_status": "complete", "slots": slots, "reports": reports,
            "reproducibility": {exercise: {"json_identical": True, "md_identical": True} for exercise in EXERCISES},
            "inputs_unchanged": True, "handcheck": {"status": "passed", "reasons": []}}


def report_video(facts: dict[str, Any], slot: str) -> dict[str, Any]:
    report = facts["reports"][study.slot_lift(slot)["exercise"]]
    return next(item for item in report["videos"] if item["label"] == slot)


def primary(facts: dict[str, Any], exercise: str = "snatch") -> dict[str, Any]:
    return facts["reports"][exercise]["pooled"]["stats"]["meanVelocityMps"]


class CriterionTests(unittest.TestCase):
    def evaluate(self, change: Callable[[dict], None] = lambda _: None) -> dict[str, Any]:
        facts = passing_facts()
        change(facts)
        return report_tool.evaluate_criterion(facts)

    def failing(self, result: dict[str, Any]) -> list[str]:
        return [name for name, item in result["conditions"].items() if not item["passed"]]

    def test_all_conditions_pass(self) -> None:
        result = self.evaluate()
        self.assertEqual((result["verdict"], result["provisional"], self.failing(result)), ("PASS", False, []))

    def test_pending_overrides_while_collection_in_progress(self) -> None:
        def in_progress(facts: dict) -> None:
            facts["collection_status"] = "in_progress"
        result = self.evaluate(in_progress)
        self.assertEqual((result["verdict"], result["provisional"], self.failing(result)), ("PENDING", True, []))

        def in_progress_failing(facts: dict) -> None:
            in_progress(facts)
            facts["inputs_unchanged"] = False
        result = self.evaluate(in_progress_failing)
        self.assertEqual((result["verdict"], self.failing(result)), ("PENDING", ["C4"]))

    def test_concluded_is_evaluated_like_complete(self) -> None:
        self.assertEqual(self.evaluate(lambda f: f.update(collection_status="concluded"))["verdict"], "PASS")

    def test_c1_fails_alone_on_an_owner_protocol_failure(self) -> None:
        def owner_failure(facts: dict) -> None:
            slot = facts["slots"][4]
            slot.update(protocol_conforming=False, failures=[{"stage": "protocol", "reason": "late_seed", "source": "owner"}])
        result = self.evaluate(owner_failure)
        self.assertEqual((result["verdict"], self.failing(result)), ("FAIL", ["C1"]))
        self.assertEqual(result["conditions"]["C1"]["reasons"],
                         ["S1-CL-1: protocol:late_seed", "clean: 5/6 conforming slots across 3/3 sessions"])

    def test_c2_fails_alone_on_each_count_and_completeness_breach(self) -> None:
        cases = {
            "wlTotal": lambda f: report_video(f, "S2-SQ-1")["counts"].update(wlTotal=4),
            "openBarTotal": lambda f: report_video(f, "S2-SQ-1")["counts"].update(openBarTotal=2),
            "paired": lambda f: report_video(f, "S2-SQ-1")["counts"].update(paired=2),
            "wlOnly": lambda f: report_video(f, "S2-SQ-1")["counts"].update(wlOnly=1),
            "openBarOnly": lambda f: report_video(f, "S2-SQ-1")["counts"].update(openBarOnly=1),
            "openBarExcluded": lambda f: report_video(f, "S2-SQ-1")["counts"].update(openBarExcluded=1),
            "paired_rep_incomplete": lambda f: report_video(f, "S2-SQ-1")["paired"][1].update(openBarComplete=False),
            "wl_incomplete": lambda f: report_video(f, "S2-SQ-1")["paired"][0].update(wlComplete=False),
            "attempted_reps_not_3": lambda f: f["slots"][6].update(attempted_rep_start_s=[2.0, 5.0]),
            "pooled paired=17": lambda f: f["reports"]["back_squat"]["pooled"]["counts"].update(paired=17),
        }
        for expected, change in cases.items():
            with self.subTest(expected):
                result = self.evaluate(change)
                self.assertEqual((result["verdict"], self.failing(result)), ("FAIL", ["C2"]))
                self.assertEqual(len(result["conditions"]["C2"]["reasons"]), 1)
        incomplete = self.evaluate(cases["paired_rep_incomplete"])["conditions"]["C2"]["reasons"]
        self.assertEqual(incomplete, ["S2-SQ-1: paired_rep_incomplete"])

    def test_a_non_analyzable_slot_fails_c2(self) -> None:
        result = self.evaluate(lambda f: f["slots"][16].update(analyzable=False))
        self.assertEqual(self.failing(result), ["C2"])
        self.assertEqual(result["conditions"]["C2"]["reasons"], ["S3-CL-1: not_analyzable"])

    def test_c3_loa_bounds_are_inclusive_at_the_tolerance(self) -> None:
        for lower, upper in ((-0.05, 0.05), (-0.05, -0.01), (0.01, 0.05), (0.0, 0.0)):
            with self.subTest(lower=lower, upper=upper):
                result = self.evaluate(lambda f: primary(f).update(lowerLoA=lower, upperLoA=upper))
                self.assertEqual((result["verdict"], self.failing(result)), ("PASS", []))

    def test_c3_fails_alone_just_outside_or_null(self) -> None:
        cases = {"lowerLoA=-0.050001": {"lowerLoA": -0.050001}, "upperLoA=0.050001": {"upperLoA": 0.050001},
                 "lowerLoA unavailable (insufficient_n)": {"lowerLoA": None},
                 "upperLoA unavailable (insufficient_n)": {"upperLoA": None}}
        for expected, values in cases.items():
            with self.subTest(expected):
                def change(facts: dict, values: dict = values) -> None:
                    stats_item = primary(facts)
                    stats_item.update(values)
                    for key, value in values.items():
                        stats_item["reasons"][key] = "insufficient_n" if value is None else None
                result = self.evaluate(change)
                self.assertEqual((result["verdict"], self.failing(result)), ("FAIL", ["C3"]))
                self.assertEqual(len(result["conditions"]["C3"]["reasons"]), 1)
                self.assertTrue(result["conditions"]["C3"]["reasons"][0].startswith(f"snatch: {expected}"))

    def test_c3_ignores_secondary_metrics(self) -> None:
        def wide_secondary(facts: dict) -> None:
            stats_item = facts["reports"]["clean"]["pooled"]["stats"]
            stats_item["peakVelocityMps"].update(lowerLoA=-0.4, upperLoA=0.4)
            stats_item["romCm"].update(lowerLoA=None, upperLoA=None)
        self.assertEqual(self.evaluate(wide_secondary)["verdict"], "PASS")

    def test_a_lift_without_a_report_fails_c3(self) -> None:
        def no_snatch(facts: dict) -> None:
            facts["reports"]["snatch"] = None
            del facts["reproducibility"]["snatch"]
        result = self.evaluate(no_snatch)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertEqual(result["conditions"]["C3"]["reasons"], ["snatch: statistics_unavailable"])
        self.assertIn("snatch: pooled paired=unavailable (expected 18)", result["conditions"]["C2"]["reasons"])
        self.assertTrue(result["conditions"]["C4"]["passed"])

    def test_c4_fails_alone_on_each_reproducibility_breach(self) -> None:
        cases = {
            "clean: run1/run2 JSON bytes differ": lambda f: f["reproducibility"]["clean"].update(json_identical=False),
            "clean: run1/run2 Markdown bytes differ": lambda f: f["reproducibility"]["clean"].update(md_identical=False),
            "inputs_changed_since_lock": lambda f: f.update(inputs_unchanged=False),
            "handcheck_failed: value_mismatch": lambda f: f.update(handcheck={"status": "failed",
                                                                              "reasons": ["value_mismatch"]}),
        }
        for expected, change in cases.items():
            with self.subTest(expected):
                result = self.evaluate(change)
                self.assertEqual((result["verdict"], self.failing(result)), ("FAIL", ["C4"]))
                self.assertEqual(result["conditions"]["C4"]["reasons"], [expected])


# --- Command: integrity, exit codes, output ---------------------------------------------------------

class ReportCommandTests(Base):
    def test_complete_study_renders_pass_with_every_section(self) -> None:
        text = self.run_ok()
        self.assertTrue(text.startswith(render.TITLE + "\n"))
        self.assertIn(f"**{render.VERDICT_LABEL}: PASS**", text)
        for section in ("## Frozen criterion", "## Slot accounting", "## Counts summary",
                        "## Per-lift primary statistics", "## Per-lift secondary statistics",
                        "## Proportional and constant diagnostics", "## Per-video statistics", "## Scale diagnostics",
                        "## Reproducibility", "## Hand-check (S1-SQ-1)", "## Provenance"):
            self.assertIn(section, text)
        self.assertIn("| S1-SQ-1 | back_squat | enrolled | yes | yes | — | 3 | 3 | 3 | 0 | 0 | 0 | 1 | yes |", text)
        self.assertIn("| back_squat | 18 | 0.0025 | 0.021811 | -0.04 | 0.045 | 0.0173 | yes |", text)
        self.assertIn("| S2-SN-2 | 1.0123 | [0.9876, 1.0371] | yes |", text)
        self.assertIn(render.RATIO_CAVEAT, text)
        self.assertIn("node v24.9.0", text)
        self.assertNotIn("\r", text)

    def test_two_renders_are_byte_identical(self) -> None:
        self.run_ok()
        code, log = self.fx.run(output="target/second.md", write=False)
        self.assertEqual(code, 0, log)
        self.assertEqual((self.fx.tool / "docs/analysis/AGREEMENT_79.md").read_bytes(),
                         (self.fx.tool / "target/second.md").read_bytes())

    def test_never_overwrites_and_output_must_be_a_new_md_in_docs_analysis_or_target(self) -> None:
        existing = self.fx.tool / "docs/analysis/AGREEMENT_79.md"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"retained\n")
        self.assertEqual(self.fx.run()[0], 1)
        self.assertEqual(existing.read_bytes(), b"retained\n")
        for output in ("docs/plans/AGREEMENT.md", "target/report.txt", "../outside.md"):
            with self.subTest(output):
                self.assertEqual(self.fx.run(output=output, write=False)[0], 1)
                self.assertFalse((self.fx.tool / output).exists())

    def test_integrity_failures_exit_1_and_write_nothing(self) -> None:
        def videos(report: dict) -> list:
            return report["videos"]
        cases: dict[str, Callable[[dict], None]] = {
            "schemaVersion": lambda r: r.update(schemaVersion="velocity-agreement-v2"),
            "segmentationRule": lambda r: r.update(segmentationRule="concentric-segmentation-v1"),
            "wlParserVersion": lambda r: r.update(wlParserVersion="wl-analysis-csv-v1"),
            "openBarParserVersion": lambda r: r.update(openBarParserVersion="openbar-analysis-v1"),
            "minOverlap": lambda r: r.update(minOverlap=0.6),
            "missing label": lambda r: (videos(r).pop(), r["pooled"].update(videoCount=5)),
            "extra label": lambda r: videos(r).append(dict(videos(r)[0], label="S1-SN-1")),
            "duplicate label": lambda r: videos(r).__setitem__(1, dict(videos(r)[1], label=videos(r)[0]["label"])),
            "wl sha": lambda r: videos(r)[2]["wl"].update(fileSha256="e" * 64),
            "analysis sha": lambda r: videos(r)[2]["openBar"].update(fileSha256="e" * 64),
            "offsetS": lambda r: videos(r)[0].update(offsetS=0.1),
            "loadKg": lambda r: videos(r)[0].update(loadKg=50),
            "null without reason": lambda r: r["pooled"]["stats"]["meanVelocityMps"].update(lowerLoA=None),
            "unknown null reason": lambda r: r["pooled"]["stats"]["romCm"]["reasons"].update(slope="made_up"),
            "counts vs rows": lambda r: videos(r)[0]["counts"].update(paired=2),
            "breakCount": lambda r: videos(r)[0]["openBar"].update(breakCount=-1),
        }
        for name, change in cases.items():
            with self.subTest(name):
                self.fx.report_patch = {"clean": change}
                code, log = self.fx.run(output=f"target/{name.replace(' ', '-')}.md")
                self.assertEqual(code, 1, log)
                self.assertFalse((self.fx.tool / f"target/{name.replace(' ', '-')}.md").exists())

    def test_missing_run_file_exits_1(self) -> None:
        self.fx.write()
        (self.fx.runs / "report-snatch-run2.md").unlink()
        self.assertEqual(self.fx.run(write=False)[0], 1)

    def test_inventory_not_matching_the_lock_summary_exits_1(self) -> None:
        self.fx.write()
        path = self.fx.lock_dir / "inventory.json"
        path.write_bytes(path.read_bytes() + b"\n")
        self.assertEqual(self.fx.run(write=False)[0], 1)

    def test_invalid_handcheck_document_exits_1(self) -> None:
        for name, change in {"format": lambda d: d.update(format="other"),
                             "status disagreement": lambda d: d.update(status="failed"),
                             "slot": lambda d: d.update(slot="S1-SQ-2"),
                             "no slot": lambda d: d.pop("slot"),
                             "study": lambda d: d.update(study_id="other-study"),
                             "counts": lambda d: d["public"].update(values_compared=-1)}.items():
            with self.subTest(name):
                self.fx.handcheck_patch = change
                self.assertEqual(self.fx.run(output=f"target/{name.replace(' ', '-')}.md")[0], 1)

    def test_inputs_outside_the_data_checkout_exit_1(self) -> None:
        self.fx.write()
        outside = self.fx.root.parent / "handcheck.json"
        outside.write_bytes((self.fx.root / self.fx.handcheck_rel).read_bytes())
        self.assertEqual(self.fx.run(write=False, **{"--handcheck": str(outside)})[0], 1)

    def test_consumer_checkout_or_node_problems_exit_3(self) -> None:
        def no_node() -> str:
            raise report_tool.InfrastructureError("node missing")
        for name, change in {"commit": lambda: setattr(self.fx, "git_state", ("a" * 40, True)),
                             "dirty": lambda: setattr(self.fx, "git_state", (study.CONSUMER_COMMIT, False)),
                             "node": lambda: setattr(self.fx, "node", no_node)}.items():
            with self.subTest(name):
                self.fx.git_state, self.fx.node = (study.CONSUMER_COMMIT, True), lambda: "v24.9.0"
                change()
                self.assertEqual(self.fx.run(output=f"target/{name}.md")[0], 3)
                self.assertFalse((self.fx.tool / f"target/{name}.md").exists())

    def test_handcheck_input_mismatch_fails_c4_without_aborting(self) -> None:
        self.fx.handcheck_patch = lambda d: d["inputs"]["report"].update(sha256="0" * 64)
        text = self.run_ok()
        self.assertIn(f"**{render.VERDICT_LABEL}: FAIL**", text)
        self.assertIn("handcheck_failed: handcheck_inputs_mismatch", text)
        self.assertIn("- Bound to this inventory and the back_squat run 1 report: no", text)

    def test_failed_handcheck_public_object_is_reported(self) -> None:
        def failed(document: dict) -> None:
            document["status"] = "failed"
            document["public"].update(status="failed", reasons=["value_mismatch"], values_matched=34,
                                      max_abs_discrepancy_mps=2e-9)
        self.fx.handcheck_patch = failed
        text = self.run_ok()
        self.assertIn("- Values compared/matched: 36/34", text)
        self.assertIn("- Max |vy| discrepancy (m/s): 2e-9", text)
        self.assertIn("handcheck_failed: value_mismatch", text)

    def test_changed_input_after_lock_fails_c4(self) -> None:
        self.fx.write()
        (self.fx.root / self.fx.slot("S3-CL-2")["files"]["wl_csv"]["path"]).write_bytes(b"changed\n")
        code, log = self.fx.run(write=False)
        self.assertEqual(code, 0, log)
        text = self.fx.output()
        self.assertIn("inputs_changed_since_lock", text)
        self.assertIn("(1 mismatches); inputs unchanged: no", text)

    def test_differing_run_bytes_fail_c4(self) -> None:
        self.fx.write()
        path = self.fx.runs / "report-clean-run2.json"
        path.write_bytes(path.read_bytes().replace(b"0.0025", b"0.0026"))
        text = self.run_ok(write=False)
        self.assertIn("clean: run1/run2 JSON bytes differ", text)
        self.assertRegex(text, r"\| clean \| `[0-9a-f]{64}` \| `[0-9a-f]{64}` \| no \| `[0-9a-f]{64}` \| `[0-9a-f]{64}` \| yes \|")

    def test_lift_without_analyzable_slots_is_unavailable(self) -> None:
        for slot in self.fx.slots:
            if slot["exercise"] == "snatch":
                slot["analyzable"] = False
        text = self.run_ok()
        self.assertFalse((self.fx.runs / "report-snatch-run1.json").exists())
        self.assertIn("snatch: statistics_unavailable", text)
        self.assertIn("| S1-SN-1 | snatch | enrolled | no | no | — | — | — | — | — | — | — | — | — |", text)
        self.assertIn("| snatch | unavailable (no_analyzable_slots) |", text)
        self.assertIn(f"**{render.VERDICT_LABEL}: FAIL**", text)

    def test_collection_in_progress_is_pending_and_provisional(self) -> None:
        self.fx.collection_status = "in_progress"
        text = self.run_ok()
        self.assertIn(f"**{render.VERDICT_LABEL}: PENDING** (collection in progress", text)
        self.assertIn("| C1 | Collection and protocol (18 conforming slots; 6 per lift over 3 sessions) | pass (provisional) |",
                      text)

    def test_scale_rows_missing_or_changed_are_unavailable_not_fatal(self) -> None:
        self.fx.scale_rows_patch = lambda rows: rows.pop(0)
        text = self.run_ok()
        self.assertIn("| S1-SQ-1 | unavailable (scale_row_missing) | — | — |", text)
        self.assertIn("| S2-SQ-1 | unavailable (scale_row_missing) | — | — |", text)
        scale = self.fx.root / f"validation/private/vbt/sessions/{SESSION_IDS[2]}/scale-report/scale-reference-v1.json"
        scale.write_bytes(scale.read_bytes() + b" ")
        text = self.run_ok(output="target/changed.md", write=False)
        self.assertIn("| S3-SQ-1 | unavailable (scale_report_changed) | — | — |", text)
        self.assertIn(f"**{render.VERDICT_LABEL}: FAIL**", text)


class PrivacyTests(Base):
    def test_markdown_carries_no_private_strings(self) -> None:
        self.fx.owner_failures = {"S2-CL-2": [{"stage": "protocol", "reason":
                                               f"seed late in {ORIGINAL.format(16)} ({SESSION_IDS[1]}, {SESSION_DATES[1]}) | <b>"}]}
        self.fx.handcheck_patch = lambda d: (d.update(status="failed"), d["public"].update(
            status="failed", reasons=[f"window_contains_null at {fixture_id('S1-SQ-1')}"]))
        text = self.run_ok()
        self.assertIn("S2-CL-2: protocol:seed late in [redacted] ([redacted], [redacted]) \\| &lt;b&gt;", text)
        forbidden = ["zzpriv", "ZZPRIV", "validation/private", ".csv", ".json", ".MOV", "c0ffee", "T09:08:07",
                     str(REP_WL), str(REP_OPENBAR), "0.912345", "-0.011111", "5" * 64, "6" * 64,
                     *SESSION_IDS, *SESSION_DATES, *(ORIGINAL.format(i) for i in range(1, 19)),
                     *(fixture_id(slot) for slot in study.SLOTS)]
        for slot in self.fx.slots:
            forbidden += [item["sha256"] for item in slot["files"].values()]
        for token in forbidden:
            self.assertNotIn(token, text)


class HelperTests(unittest.TestCase):
    def test_handcheck_constants_match_the_handcheck_tool(self) -> None:
        import agreement79_handcheck as handcheck
        self.assertEqual((report_tool.HANDCHECK_FORMAT, report_tool.HANDCHECK_SLOT), (handcheck.FORMAT, handcheck.SLOT))
        self.assertEqual(handcheck.FORMAT_VERSION, study.STUDY_DOCUMENT_VERSION)

    def test_js_number_matches_ecmascript_string(self) -> None:
        cases = {0: "0", 18: "18", 0.0: "0", -0.0: "0", 2.0: "2", 0.05: "0.05", -0.050001: "-0.050001",
                 0.000001: "0.000001", 0.00001: "0.00001", 1.5e-7: "1.5e-7", 2e-9: "2e-9", 1e21: "1e+21", True: "true"}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(render.js_number(value), expected)

    def test_redaction_and_escaping(self) -> None:
        cells = render.Cells(["secret-session"])
        self.assertEqual(cells.text("a|b <c>\nsecret-session"), "a\\|b &lt;c&gt; [redacted]")

    def test_render_is_pure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = FakeStudy(Path(tmp).resolve())
            fx.write()
            args = report_tool.resolved(type("Args", (), {
                "inventory": fx.lock_dir / "inventory.json", "lock_summary": fx.lock_dir / "lock-summary.json",
                "runs_dir": fx.runs, "handcheck": fx.root / fx.handcheck_rel, "consumer_app": fx.root / "app",
                "output": fx.tool / "target/x.md"})())
            first = report_tool.build(args, fx.root, fx.tool, lambda app: (study.CONSUMER_COMMIT, True), lambda: "v1")
            second = report_tool.build(copy.copy(args), fx.root, fx.tool, lambda app: (study.CONSUMER_COMMIT, True),
                                       lambda: "v1")
            self.assertEqual(first["markdown"], second["markdown"])
            self.assertFalse((fx.tool / "target/x.md").exists())


if __name__ == "__main__":
    unittest.main()
