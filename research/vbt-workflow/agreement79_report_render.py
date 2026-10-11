#!/usr/bin/env python3
"""Deterministic, aggregate-only Markdown for the #79 report (agreement79_report.py builds the context).

Never rendered: file paths or names, fixture ids, original names, timestamps, session ids or dates, input
file hashes or per-rep values. Slot ids (S1-SQ-1 ...) are public. A failure renders its stage only when it is
a frozen failure stage and its reason only when `stage:reason` is on the closed allow-list of codes the
inventory derives (``KNOWN_FAILURES``); any other owner reason, code-shaped or not, renders `[owner note]`.
Hand-check reasons render only when they have a code shape ``^[a-z0-9_:.-]+$`` (otherwise
`[unrecognized reason]`), and the WL Analysis version only when it matches ``^[A-Za-z0-9 ._()+-]{1,40}$``.
Every cell is additionally redacted, case-insensitively, against the inventory's private strings (defence in
depth) and Markdown-escaped. Numbers render as the consumer renders its rounded JSON (ECMAScript ``String``).
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import agreement79_study as study
from agreement79_slot_evidence import dig

TITLE = "# #79 owner VBT agreement study — aggregate report"
VERDICT_LABEL = "Mechanical evaluation of the frozen criterion (reviewed decision recorded separately)"
NON_CLAIMS = ("Non-claims: agreement with WL Analysis is not physical accuracy, and this report authorizes no "
              "training-source switch, eligibility promotion or production tracker selection.")
RATIO_CAVEAT = ("The geometric OpenBar/WL ratio is a cross-device measurement ratio, not an independent physical "
                "calibration-scale estimate.")
POOLING_CAVEAT = ("Reps are nested within videos. Pooled figures are descriptive and do not account for "
                  "within-video dependence.")
SCALE_CAVEAT = ("Diagnostic only: the stick/plate `reference_to_plate_ratio` never enters the verdict, primary "
                "velocities are uncorrected, and it is distinct from the geometric OpenBar/WL velocity ratio.")
DASH = "—"
REDACTED = "[redacted]"
OWNER_NOTE = "[owner note]"
UNRECOGNIZED = "[unrecognized reason]"
CODE_RE = re.compile(r"[a-z0-9_:.-]+")
WL_VERSION_RE = re.compile(r"[A-Za-z0-9 ._()+-]{1,40}")
CONDITIONS = (("C1", "Collection and protocol (18 conforming slots; 6 per lift over 3 sessions)"),
              ("C2", "Counts and completeness (3/3/3 per video, no unmatched or excluded reps, 18 pairs per lift)"),
              ("C3", f"Primary agreement (pooled meanVelocityMps LoA within ±{study.TOLERANCE_MPS:.6f} m/s, inclusive)"),
              ("C4", "Reproducibility and hand-check (identical run bytes, inputs unchanged, hand-check passed)"))
STAT_COLUMNS = (("n", "n"), ("bias", "Bias"), ("sampleSd", "Sample SD"), ("lowerLoA", "Lower 95% LoA"),
                ("upperLoA", "Upper 95% LoA"), ("meanAbsoluteDifference", "Mean absolute difference"))
DIAGNOSTIC_COLUMNS = (("slope", "Slope"), ("intercept", "Intercept"), ("pearsonR", "Pearson r"),
                      ("geometricMeanRatio", "Geometric OpenBar/WL measurement ratio"))
SECONDARY = study.METRICS[1:]
COUNT_COLUMNS = (("wlTotal", "WL total"), ("openBarTotal", "OpenBar total"), ("paired", "Paired"),
                 ("wlOnly", "WL only"), ("openBarOnly", "OpenBar only"), ("openBarExcluded", "OpenBar excluded"))

# Closed allow-list: every derived failure the inventory (agreement79_inventory.py, agreement79_slot_evidence.py)
# can record, per stage. A reason outside it never renders, whoever wrote it.
METHOD_FIELDS = ("tracker_policy", "preset", "analyze_options", "plate_diameter_m", "stick_length_m", "clip_tracker",
                 "run_record_sha256", "run_record_format", "run_fixture_id", "run_tracker", "tracker_implementation",
                 "run_preset", "run_analyze_options", "run_plate_diameter_m", "openbar_cli_sha256",
                 "openbar_git_commit", "openbar_tracked_changes", "openbar_untracked_source_files")
DERIVED_REASONS: dict[str, tuple[str, ...]] = {
    "novelty": ("session_date_before_freeze", "duplicate_of_excluded_clip", "creation_time_unavailable",
                "recorded_before_freeze", *(f"duplicate_of_slot:{slot}" for slot in study.SLOTS)),
    "confirmation": ("skipped_on_session_page",),
    "processing": ("not_processed", "session_status_failed", "clip_not_in_session_record", "exercise_mismatch",
                   "openbar_parser_rejected", *(f"method_mismatch:{field}" for field in METHOD_FIELDS),
                   "run_output_mismatch:prediction", "run_output_mismatch:analysis"),
    "assessment": ("assessment_missing", "assessment_invalid", "assessment_unbound", "processing_incomplete",
                   "processing_unknown", "mechanical_invalid", "mechanical_unknown"),
    "reference": ("scale_reference_missing", "scale_reference_unbound"),
    "wl_export": ("wl_csv_missing", "wl_parser_rejected"),
    "protocol": ("attempted_reps_not_three", "seed_not_before_first_rep", "environment_mismatch",
                 "session_has_unenrolled_clip", "slot_order_violation", "session_dates_not_distinct"),
}
KNOWN_FAILURES = frozenset((stage, reason) for stage, reasons in DERIVED_REASONS.items() for reason in reasons)


# --- Public text ---------------------------------------------------------------------------------

def public_code(value: Any, placeholder: str = UNRECOGNIZED) -> str:
    """A tool code shown as-is only when it has a code shape; free text becomes ``placeholder``."""
    return value if isinstance(value, str) and CODE_RE.fullmatch(value) else placeholder


def public_failure(item: Any) -> str:
    """`stage:reason` of an inventory failure; only allow-listed codes render, owner text never does.

    An unlisted reason is `[owner note]` for an owner failure and `[unrecognized reason]` for a derived one.
    """
    stage, reason = dig(item, "stage"), dig(item, "reason")
    shown_stage = stage if isinstance(stage, str) and stage in study.FAILURE_STAGES else OWNER_NOTE
    if isinstance(stage, str) and isinstance(reason, str) and (stage, reason) in KNOWN_FAILURES:
        return f"{shown_stage}:{reason}"
    return f"{shown_stage}:{UNRECOGNIZED if dig(item, 'source') == 'derived' else OWNER_NOTE}"


def public_wl_version(value: Any) -> str:
    return value if isinstance(value, str) and WL_VERSION_RE.fullmatch(value) else OWNER_NOTE


# --- Cells ---------------------------------------------------------------------------------------

def js_number(value: Any) -> str:
    """ECMAScript ``String(number)`` for the finite numbers parsed from the consumer's JSON."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if value == 0:
        return "0"
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    text = repr(value)
    if "e" not in text:
        return text
    if 1e-6 <= abs(value) < 1e21:
        return format(Decimal(text), "f")
    mantissa, exponent = text.split("e")
    return f"{mantissa}e{'-' if exponent.startswith('-') else '+'}{int(exponent.lstrip('+-'))}"


class Cells:
    """Case-insensitive redaction plus Markdown escaping for every input-derived string."""

    def __init__(self, tokens: list[str]) -> None:
        ordered = sorted(set(tokens), key=lambda token: (-len(token), token))  # longest match wins
        self.pattern = re.compile("|".join(map(re.escape, ordered)), re.IGNORECASE) if ordered else None

    def text(self, value: Any) -> str:
        text = str(value)
        if self.pattern is not None:
            text = self.pattern.sub(REDACTED, text)
        text = " ".join(text.splitlines())
        return text.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")

    def stat(self, stats: Any, key: str) -> str:
        if key == "n":
            return js_number(stats["n"])
        value = stats.get(key)
        return js_number(value) if value is not None else f"null ({self.text(dig(stats, 'reasons', key))})"


def yes_no(value: Any) -> str:
    return "yes" if value else "no"


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    return [f"| {' | '.join(header)} |", f"| {' | '.join('---' for _ in header)} |",
            *(f"| {' | '.join(row)} |" for row in rows)]


def lift_names() -> list[str]:
    return [lift["exercise"] for lift in study.LIFTS.values()]


def unavailable(lift: dict[str, Any], cells: Cells) -> str:
    return f"unavailable ({cells.text(lift['reason'])})"


# --- Sections ------------------------------------------------------------------------------------

def header(context: dict[str, Any], cells: Cells) -> list[str]:
    criterion = context["criterion"]
    return [TITLE, "",
            f"Study: `{study.STUDY_ID}`. Preregistration frozen content commit `{study.PREREGISTRATION_COMMIT}` "
            f"(merged to main as `{study.PREREGISTRATION_MAIN_COMMIT}`); freeze instant {study.FREEZE_INSTANT.isoformat()}.",
            "", f"**{VERDICT_LABEL}: {criterion['verdict']}**", "", NON_CLAIMS]


def criterion_section(context: dict[str, Any], cells: Cells) -> list[str]:
    criterion = context["criterion"]
    rows = []
    for name, title in CONDITIONS:
        item = criterion["conditions"][name]
        reasons = "; ".join(cells.text(reason) for reason in item["reasons"]) or DASH
        rows.append([name, title, "pass" if item["passed"] else "fail", reasons])
    return ["## Frozen criterion", "", *table(["Condition", "Requirement", "Result", "Reasons"], rows), "",
            f"Verdict: **{criterion['verdict']}**. Secondary metrics and scale diagnostics never enter the verdict."]


def report_video(context: dict[str, Any], slot: dict[str, Any]) -> dict[str, Any] | None:
    report = context["lifts"][slot["exercise"]]["report"]
    if report is None or not slot["analyzable"]:
        return None
    return next((video for video in report["videos"] if video["label"] == slot["slot"]), None)


def tracking_cells(context: dict[str, Any], slot: dict[str, Any]) -> list[str]:
    if slot["slot"] not in context["tracking"]:
        return [DASH, DASH]
    counts = context["tracking"][slot["slot"]]
    if counts is None:
        return ["unavailable", "unavailable"]
    return [js_number(counts["lost"]), js_number(counts["low_confidence"])]


def slot_section(context: dict[str, Any], cells: Cells) -> list[str]:
    rows = []
    for slot in context["inventory"]["slots"]:
        failures = "; ".join(cells.text(public_failure(item)) for item in slot["failures"])
        video = report_video(context, slot)
        if video is None:
            counts = [DASH] * (len(COUNT_COLUMNS) + 2)
        else:
            paired = video["paired"]
            complete = DASH if not paired else yes_no(all(row["wlComplete"] and row["openBarComplete"] for row in paired))
            counts = [js_number(video["counts"][key]) for key, _ in COUNT_COLUMNS]
            counts += [js_number(video["openBar"]["breakCount"]), complete]
        rows.append([slot["slot"], slot["exercise"], slot["status"], yes_no(slot["analyzable"]),
                     yes_no(slot["protocol_conforming"]), failures or DASH, *counts, *tracking_cells(context, slot)])
    columns = ["Slot", "Lift", "Status", "Analyzable", "Conforming", "Failures (stage:reason)",
               *(title for _, title in COUNT_COLUMNS), "Tracking breaks", "Completeness all true", "Lost",
               "Low confidence"]
    return ["## Slot accounting", "", "Every frozen slot, in frozen order; report columns come from the run 1 "
            "consumer report and are `—` for slots the consumer did not receive. Lost and low confidence count "
            "the slot analysis's raw observations by tracking state (`—` without an analysis, `unavailable` when "
            "it no longer matches its inventory hash or cannot be read).", "", *table(columns, rows)]


def counts_section(context: dict[str, Any], cells: Cells) -> list[str]:
    counts = context["inventory"]["counts"]
    rows = []
    for exercise in lift_names():
        lift = context["lifts"][exercise]
        tally = dig(counts, "per_lift", exercise) or {}
        cells_counts = ([unavailable(lift, cells)] * len(COUNT_COLUMNS) if lift["report"] is None else
                        [js_number(lift["report"]["pooled"]["counts"][key]) for key, _ in COUNT_COLUMNS])
        rows.append([exercise, *(js_number(tally.get(key, 0)) for key in ("enrolled", "analyzable", "protocol_conforming")),
                     *cells_counts])
    return ["## Counts summary", "",
            f"Planned: {counts['planned_videos']} videos, {counts['planned_reps']} attempted reps. "
            f"Enrolled {counts['enrolled']}, not recorded {counts['not_recorded']}, processed {counts['processed']}, "
            f"analyzable {counts['analyzable']}, protocol-conforming {counts['protocol_conforming']}.", "",
            *table(["Lift", "Enrolled", "Analyzable", "Conforming", *(title for _, title in COUNT_COLUMNS)], rows)]


def stats_rows(context: dict[str, Any], cells: Cells, metrics: tuple[str, ...], columns: tuple[tuple[str, str], ...],
               tolerance: bool) -> list[list[str]]:
    rows = []
    for exercise in lift_names():
        lift = context["lifts"][exercise]
        for metric in metrics:
            label = [exercise] if len(metrics) == 1 and tolerance else [exercise, metric]
            if lift["report"] is None:
                rows.append([*label, *([unavailable(lift, cells)] * (len(columns) + tolerance))])
                continue
            stats = lift["report"]["pooled"]["stats"][metric]
            row = [*label, *(cells.stat(stats, key) for key, _ in columns)]
            if tolerance:
                lower, upper = stats.get("lowerLoA"), stats.get("upperLoA")
                row.append(yes_no(lower is not None and upper is not None
                                  and lower >= -study.TOLERANCE_MPS and upper <= study.TOLERANCE_MPS))
            rows.append(row)
    return rows


def statistics_section(context: dict[str, Any], cells: Cells) -> list[str]:
    titles = [title for _, title in STAT_COLUMNS]
    primary = stats_rows(context, cells, ("meanVelocityMps",), STAT_COLUMNS, True)
    secondary = stats_rows(context, cells, SECONDARY, STAT_COLUMNS, False)
    diagnostics = stats_rows(context, cells, study.METRICS, DIAGNOSTIC_COLUMNS, False)
    return ["## Per-lift primary statistics (meanVelocityMps, pooled)", "",
            "Differences are OpenBar minus WL, as serialized (six decimals) by the consumer report.", "",
            *table(["Lift", *titles, f"LoA within ±{study.TOLERANCE_MPS:.6f}"], primary), "",
            "## Per-lift secondary statistics (pooled)", "", *table(["Lift", "Metric", *titles], secondary), "",
            "## Proportional and constant diagnostics (pooled)", "",
            *table(["Lift", "Metric", *(title for _, title in DIAGNOSTIC_COLUMNS)], diagnostics), "",
            RATIO_CAVEAT, "", f"{POOLING_CAVEAT} LoA are descriptive, not confidence intervals."]


def per_video_section(context: dict[str, Any], cells: Cells) -> list[str]:
    rows = []
    for slot in context["inventory"]["slots"]:
        video = report_video(context, slot)
        if video is not None:
            stats = video["stats"]["meanVelocityMps"]
            rows.append([slot["slot"], *(cells.stat(stats, key) for key, _ in STAT_COLUMNS)])
    body = table(["Slot", *(title for _, title in STAT_COLUMNS)], rows) if rows else ["No analyzable slot."]
    return ["## Per-video statistics (meanVelocityMps)", "", *body]


def scale_section(context: dict[str, Any], cells: Cells) -> list[str]:
    rows = []
    for slot in context["inventory"]["slots"]:
        item = context["scale"].get(slot["slot"])
        if item is None:
            continue
        if item["status"] != "available":
            rows.append([slot["slot"], f"unavailable ({cells.text(item['reason'])})", DASH, DASH])
            continue
        rows.append([slot["slot"], js_number(item["value"]), f"[{js_number(item['lower'])}, {js_number(item['upper'])}]",
                     yes_no(item["consistent_with_1"])])
    body = table(["Slot", "Stick/plate ratio", "Interval [lower, upper]", "Consistent with 1"], rows) if rows else [
        "No analyzable slot."]
    return ["## Scale diagnostics", "", SCALE_CAVEAT, "", *body]


def reproducibility_section(context: dict[str, Any], cells: Cells) -> list[str]:
    rows = []
    for exercise in lift_names():
        lift = context["lifts"][exercise]
        if lift["report"] is None:
            rows.append([exercise, *([unavailable(lift, cells)] * 6)])
            continue
        hashes = lift["hashes"]
        rows.append([exercise, f"`{hashes['run1_json']}`", f"`{hashes['run2_json']}`", yes_no(lift["json_identical"]),
                     f"`{hashes['run1_md']}`", f"`{hashes['run2_md']}`", yes_no(lift["md_identical"])])
    verification = context["verification"]
    unchanged = verification["checked"] - verification["mismatch_count"]
    # A lift whose consumer run aborted: only the hash of the retained (private) message is public.
    aborted = [f"- {exercise}: consumer aborted; retained message SHA-256 `{lift['consumer_failure_sha256']}` "
               "(message not rendered)" for exercise in lift_names()
               if (lift := context["lifts"][exercise]).get("consumer_failure_sha256")]
    return ["## Reproducibility", "",
            *table(["Lift", "Run 1 JSON SHA-256", "Run 2 JSON SHA-256", "JSON identical", "Run 1 Markdown SHA-256",
                    "Run 2 Markdown SHA-256", "Markdown identical"], rows), "", *aborted, *([""] if aborted else []),
            f"Input rehash: {unchanged}/{verification['checked']} recorded inputs unchanged "
            f"({verification['mismatch_count']} mismatches); inputs unchanged: {yes_no(verification['inputs_unchanged'])}."]


def handcheck_section(context: dict[str, Any], cells: Cells) -> list[str]:
    handcheck = context["handcheck"]
    discrepancy = handcheck["max_abs_discrepancy_mps"]
    return [f"## Hand-check ({study.HANDCHECK_SLOT})", "",
            f"- Status: {cells.text(handcheck['status'])}",
            f"- Reasons: {'; '.join(cells.text(public_code(reason)) for reason in handcheck['reasons']) or DASH}",
            f"- Values compared/matched: {handcheck['values_compared']}/{handcheck['values_matched']}",
            f"- Max |vy| discrepancy (m/s): {DASH if discrepancy is None else js_number(discrepancy)}",
            f"- Bound to this inventory and the back_squat run 1 report: {yes_no(handcheck['inputs_match'])}"]


def method_config_lines(context: dict[str, Any]) -> list[str]:
    configs = {exercise: lift["report"]["openBarMethodConfigSha256"] for exercise, lift in context["lifts"].items()
               if lift["report"] is not None}
    lines = [f"- OpenBar method config SHA-256, {exercise}: `{configs[exercise]}`" for exercise in lift_names()
             if exercise in configs]
    if configs:
        lines.append(f"- Method config identical across produced lift reports (C3): "
                     f"{yes_no(len(set(configs.values())) == 1)}")
    return lines


def provenance_section(context: dict[str, Any], cells: Cells) -> list[str]:
    inventory, summary, consumer = context["inventory"], context["lock_summary"], context["consumer"]
    tool = context["tool"]
    environment = inventory.get("environment") or {}
    env = ", ".join(f"{key} {cells.text(environment.get(key) or 'unrecorded')}"
                    for key in ("python", "opencv_version", "numpy_version", "ffmpeg", "ffprobe"))
    return ["## Provenance", "",
            f"- Preregistration commit `{study.PREREGISTRATION_COMMIT}`; main commit `{study.PREREGISTRATION_MAIN_COMMIT}`",
            f"- Freeze instant {study.FREEZE_INSTANT.isoformat()}; novelty exclusion list SHA-256 `{study.NOVELTY_EXCLUSION_SHA256}`",
            f"- Inventory SHA-256 `{context['inventory_sha256']}` (equals the lock summary's: "
            f"{yes_no(summary['inventory_sha256'] == context['inventory_sha256'])})",
            f"- Tool commit `{cells.text(summary.get('tool_commit'))}` (tracked files clean: "
            f"{yes_no(summary.get('tool_tree_clean'))}); data checkout commit `{cells.text(inventory.get('data_root_commit'))}`",
            f"- Report tool commit `{cells.text(tool['commit'])}` (tracked files clean: {yes_no(tool['tree_clean'])}); "
            f"hand-check tool commit `{cells.text(tool['handcheck_commit'] or 'unrecorded')}`",
            f"- OpenBar baseline `{study.OPENBAR_BASELINE_COMMIT}`; CLI SHA-256 `{study.OPENBAR_CLI_SHA256}`",
            f"- Consumer commit `{consumer['commit']}`; node {cells.text(consumer['node_version'])}",
            f"- Report schema {study.REPORT_SCHEMA}; segmentation {study.SEGMENTATION}; WL parser {study.WL_PARSER}; "
            f"OpenBar parser {study.OPENBAR_PARSER}; minimum temporal IoU {js_number(study.MIN_OVERLAP)}",
            f"- Tracker {study.TRACKER_IMPLEMENTATION} (policy {study.TRACKER_POLICY}); preset {study.PRESET}",
            *method_config_lines(context),
            f"- Environment: {env}",
            f"- WL Analysis version: {cells.text(public_wl_version(inventory.get('wl_analysis_version')))}"]


SECTIONS = (header, criterion_section, slot_section, counts_section, statistics_section, per_video_section,
            scale_section, reproducibility_section, handcheck_section, provenance_section)


def render(context: dict[str, Any]) -> bytes:
    """UTF-8 Markdown with LF endings; a pure function of ``context``."""
    cells = Cells(context["private_tokens"])
    lines: list[str] = []
    for section in SECTIONS:
        if lines:
            lines.append("")
        lines.extend(section(context, cells))
    return ("\n".join(lines) + "\n").encode("utf-8")
