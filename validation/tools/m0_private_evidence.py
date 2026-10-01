"""Local real-video evidence for issue #14 (aggregates only).

Evaluates fixtures from a private, git-ignored manifest (``validation/private/manifest.json``) with
the same frozen configuration as the public subset, using a release build so timings are not
dominated by debug code. Only aggregate facts are emitted: no frames, coordinates or manifest notes.

Counts and timings are parsed from the Rust CLI's own ``tracker-run`` summary rather than recomputed
here, so tracker semantics (for example what counts as low confidence) stay in Rust.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import m0_evidence as evidence_tool

SUMMARY = re.compile(
    r"^(?P<id>[\w.-]+): (?P<observations>\d+) observations \((?P<range>[^)]*)\) of "
    r"(?P<frames>\d+) selected frames; tracked (?P<tracked>\d+), low-confidence (?P<low>\d+), "
    r"lost (?P<lost>\d+).*\n\s+decode (?P<decode>[\d.]+) s \+ track (?P<track>[\d.]+) s for "
    r"(?P<media>[\d.]+) s of selected media \((?P<ratio>[\d.]+)x real time\)",
    re.MULTILINE,
)
FAILURE = re.compile(r"^tracker (?P<id>[\w.-]+) failed: (?P<reason>.+)$", re.MULTILINE)
CLI_ERROR_CATEGORY = re.compile(r"status=failure error\[(?P<category>[a-z-]+)\]")
# Published failure text is limited to plain words and numbers so a local path cannot leak.
SAFE_REASON = re.compile(r"^[A-Za-z0-9 .,()=+/_-]+$")
PATH_LIKE = re.compile(r"(^|[\s(])/|\.\.")
WITHHELD_REASON = "reason withheld: CLI message was not plain text"


def publishable_reason(text: str) -> str:
    text = text.strip()
    return text if SAFE_REASON.match(text) and not PATH_LIKE.search(text) else WITHHELD_REASON


def cli_error_category(stderr: str) -> str:
    match = CLI_ERROR_CATEGORY.search(stderr)
    return match["category"] if match else "unknown"


def parse_tracker_run(stderr: str, tracker_ids: list[str]) -> list[dict[str, Any]]:
    """Per-tracker outcome from the CLI summary; fails closed if a tracker is unaccounted for."""
    outcomes: dict[str, dict[str, Any]] = {}
    for match in SUMMARY.finditer(stderr):
        outcomes[match["id"]] = {
            "implementation": match["id"],
            "status": "ok",
            "observations": int(match["observations"]),
            "observed_range": match["range"],
            "selected_frames": int(match["frames"]),
            "tracked": int(match["tracked"]),
            "low_confidence": int(match["low"]),
            "lost": int(match["lost"]),
            "decode_s": float(match["decode"]),
            "track_s": float(match["track"]),
            "selected_media_s": float(match["media"]),
            "realtime_ratio": float(match["ratio"]),
        }
    for match in FAILURE.finditer(stderr):
        outcomes[match["id"]] = {
            "implementation": match["id"],
            "status": "failed",
            "failure_reason": publishable_reason(match["reason"]),
        }
    missing = [tracker_id for tracker_id in tracker_ids if tracker_id not in outcomes]
    if missing:
        raise evidence_tool.EvidenceError(f"tracker-run reported no outcome for {missing}")
    return [outcomes[tracker_id] for tracker_id in tracker_ids]


def display_size(video: dict[str, Any]) -> str:
    width, height = video["width_px"], video["height_px"]
    if video.get("rotation_deg", 0) % 180 == 90:
        width, height = height, width
    return f"{width}x{height}"


def fixture_facts(fixture: dict[str, Any]) -> dict[str, Any]:
    video, conditions = fixture["video"], fixture.get("conditions", {})
    return {
        "id": fixture["id"],
        "exercise": fixture["exercise"],
        "purpose": fixture["purpose"],
        "nominal_fps": video.get("nominal_fps"),
        "measured_fps": video.get("measured_fps"),
        "display_size_px": display_size(video),
        "duration_s": video.get("duration_s"),
        "camera": fixture.get("camera", {}),
        "motion_blur": conditions.get("motion_blur"),
        "occlusion": conditions.get("occlusion"),
        "plate_visibility": conditions.get("plate_visibility"),
        "lighting": conditions.get("lighting"),
        "challenge_tags": conditions.get("challenge_tags", []),
        "media_sha256": fixture["media"]["sha256"],
        "redistribution_status": fixture["source"]["redistribution_status"],
    }


def benchmark_trackers(
    *, manifest: Path, fixture_id: str, annotation: Path, seed: Path, run_dir: Path, out_dir: Path,
    commit: str, outcomes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool, list[str | None]]:
    """Benchmark every tracker that succeeded in this run; its prediction file must exist."""
    results, identical, commits = [], True, []
    for outcome in outcomes:
        if outcome["status"] != "ok":
            continue
        tracker_id = outcome["implementation"]
        prediction = run_dir / f"{fixture_id}.{tracker_id}.prediction-v1.json"
        if not prediction.exists():
            raise evidence_tool.EvidenceError(f"{fixture_id}: {tracker_id} succeeded but wrote no prediction")
        samples = evidence_tool.load_json(prediction).get("samples") or []
        if not samples:
            raise evidence_tool.EvidenceError(f"{fixture_id}: {tracker_id} prediction has no samples")
        suite = out_dir / f"{tracker_id}.benchmark-v1.json"
        evidence_tool.build_suite(
            output=suite, manifest=manifest, fixture_id=fixture_id, annotations=annotation, seed=seed,
            prediction=prediction, tracker_id=tracker_id,
            selected_range_s=(samples[0]["timestamp_s"], samples[-1]["timestamp_s"]), commit=commit,
        )
        result, same = evidence_tool.benchmark_case(
            suite, out_dir / f"{tracker_id}.benchmark-a.json", out_dir / f"{tracker_id}.benchmark-b.json",
            release=True,
        )
        commits.append(result.pop("git_commit", None))
        results.append(result)
        identical &= same
    return results, identical, commits


def analyse_twice(*, manifest: Path, fixture: dict[str, Any], seed: Path, out_dir: Path) -> dict[str, Any]:
    outputs = [out_dir / "analysis-a.json", out_dir / "analysis-b.json"]
    for output in outputs:
        output.unlink(missing_ok=True)
        completed = evidence_tool.cli(
            evidence_tool.analyze_command(
                manifest=manifest, fixture_id=fixture["id"], seed=seed,
                plate_diameter_m=fixture["load"]["plate_diameter_m"], output=output,
            ),
            release=True,
            check=False,
        )
        if completed.returncode != 0:
            # Only the CLI's error category is kept; its message can contain local paths.
            return {"runs": 2, "identical": None, "error": f"analyze-failed:{cli_error_category(completed.stderr)}"}
    return {"runs": 2, "identical": outputs[0].read_bytes() == outputs[1].read_bytes(), "error": None}


def private_determinism_pass(fixtures: list[dict[str, Any]]) -> bool:
    """Every seeded fixture must have analysed identically; a failed analysis is not a pass."""
    analyses = [item["analysis"] for item in fixtures if "analysis" in item]
    repeats = [item["benchmark_outputs_identical"] for item in fixtures
               if item.get("benchmark_outputs_identical") is not None]
    return all(analysis["identical"] is True for analysis in analyses) and all(repeats)


def evaluate_fixture(manifest: Path, fixture: dict[str, Any], out_root: Path, commit: str) -> dict[str, Any]:
    private_root = manifest.parent
    fixture_id = fixture["id"]
    facts = fixture_facts(fixture)
    media = evidence_tool.ROOT / fixture["media"]["repository_path"]
    if evidence_tool.sha256(media) != fixture["media"]["sha256"]:
        raise evidence_tool.EvidenceError(f"{fixture_id}: media does not match its manifest sha256")
    seed = private_root / "seeds" / f"{fixture_id}.manual-target-seed-v1.json"
    annotation = private_root / "annotations" / f"{fixture_id}.annotation-v1.json"
    labelled = (
        evidence_tool.comparable_labelled_samples(evidence_tool.load_json(annotation)) if annotation.exists() else 0
    )
    facts["annotation"] = {"present": annotation.exists(), "comparable_labelled_samples": labelled}
    if not seed.exists():
        facts["seed"] = {"present": False}
        facts["status"] = "skipped: no manual target seed"
        return facts
    facts["seed"] = {
        "present": True,
        "selection_confidence": evidence_tool.load_json(seed)["seed"].get("selection_confidence"),
    }

    out_dir = out_root / fixture_id
    run_dir = out_dir / "tracker-run"
    shutil.rmtree(run_dir, ignore_errors=True)  # never benchmark predictions from an earlier run
    completed = evidence_tool.cli(
        ["tracker-run", "--manifest", str(manifest), "--fixture", fixture_id, "--seed", str(seed),
         "--output-dir", str(run_dir)],
        release=True,
        check=False,
    )
    facts["trackers"] = parse_tracker_run(completed.stderr, list(evidence_tool.TRACKERS))
    facts["benchmarks"], facts["benchmark_outputs_identical"], commits = (
        benchmark_trackers(manifest=manifest, fixture_id=fixture_id, annotation=annotation, seed=seed,
                           run_dir=run_dir, out_dir=out_dir, commit=commit, outcomes=facts["trackers"])
        if labelled > 0
        else ([], None, [])
    )
    facts["analysis"] = analyse_twice(manifest=manifest, fixture=fixture, seed=seed, out_dir=out_dir)
    if facts["analysis"]["identical"] is not None:
        commits.append(evidence_tool.analysis_commit(evidence_tool.load_json(out_dir / "analysis-a.json")))
    evidence_tool.require_commit_agreement(commit, commits)
    facts["status"] = "evaluated"
    return facts


def generate(manifest: Path, out_root: Path, commit: str) -> dict[str, Any]:
    document = evidence_tool.load_json(manifest)
    fixtures = [evaluate_fixture(manifest, fixture, out_root, commit) for fixture in document["fixtures"]]
    labelled = {item["id"]: item["annotation"]["comparable_labelled_samples"] for item in fixtures}
    analysed = [item for item in fixtures if item.get("analysis", {}).get("identical") is not None]
    cpu = evidence_tool.cpu_description()
    return {
        "scope": "private_local_subset_aggregates_only",
        "fixture_manifest_sha256": evidence_tool.sha256(manifest),
        "build_profile": "release",
        "coverage": evidence_tool.dataset_coverage(document, labelled),
        "fixtures": fixtures,
        "analysed_fixture_count": len(analysed),
        "determinism_pass": private_determinism_pass(fixtures),
        "runtime_diagnostics": [
            f"{item['id']} {tracker['implementation']} {tracker['realtime_ratio']:.2f}x real time "
            f"({tracker['selected_media_s']:.1f} s media, release build, {cpu})"
            for item in fixtures
            for tracker in item.get("trackers", [])
            if tracker["status"] == "ok"
        ],
    }


def fixture_rows(fixtures: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Fixture | Exercise | Role | FPS nominal/measured | Display | Duration s | Blur | Occlusion | Plate visibility | Seed | Labelled samples | Status |",
        "| --- | --- | --- | ---: | --- | ---: | --- | --- | --- | --- | ---: | --- |",
    ]
    for item in fixtures:
        seed = item["seed"]
        seed_text = f"yes (conf {seed['selection_confidence']})" if seed["present"] else "missing"
        lines.append(
            f"| {item['id']} | {item['exercise']} | {item['purpose']} | {item['nominal_fps']}/{item['measured_fps']} | "
            f"{item['display_size_px']} | {item['duration_s']} | {item['motion_blur']} | {item['occlusion']} | "
            f"{item['plate_visibility']} | {seed_text} | {item['annotation']['comparable_labelled_samples']} | "
            f"{item['status']} |"
        )
    return lines


def tracker_rows(fixtures: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Fixture | Tracker | Outcome | Observations / selected frames | Tracked | Low-confidence | Lost | Observed range | Decode + track s | Real-time ratio |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    for item in fixtures:
        for tracker in item.get("trackers", []):
            if tracker["status"] != "ok":
                lines.append(f"| {item['id']} | {tracker['implementation']} | failed: {tracker['failure_reason']} "
                             "| | | | | | | |")
                continue
            lines.append(
                f"| {item['id']} | {tracker['implementation']} | ok | {tracker['observations']}/{tracker['selected_frames']} | "
                f"{tracker['tracked']} | {tracker['low_confidence']} | {tracker['lost']} | {tracker['observed_range']} | "
                f"{tracker['decode_s']:.2f} + {tracker['track_s']:.2f} | {tracker['realtime_ratio']:.2f}x |"
            )
    return lines


def build_report(evidence: dict[str, Any]) -> str:
    private = evidence["private_subset"]
    fixtures = private["fixtures"]
    benchmarked = [
        {**result, "label": f"{item['id']} {result['implementation']}"}
        for item in fixtures
        for result in item.get("benchmarks", [])
    ]
    determinism = [
        f"- {item['id']}: canonical analysis byte-identical across {item['analysis']['runs']} runs: "
        f"{item['analysis']['identical']}" + (f" (error: {item['analysis']['error']})" if item["analysis"]["error"] else "")
        for item in fixtures
        if "analysis" in item
    ]
    lines = [
        "# M0 Evidence Report — Local Real-Video Subset (aggregates only)",
        "",
        "Issue: #14",
        "",
        "Generated locally from private, non-redistributable clips with",
        "`python3 validation/tools/m0_evidence.py --private-manifest validation/private/manifest.json`.",
        "It is a snapshot, not regenerated by CI. The media, frames, coordinates and manifest notes are not",
        "published; the project owner approved publishing these aggregates.",
        "",
        f"- Evaluated commit: `{evidence['evaluated_commit']}`.",
        f"- Private manifest sha256: `{private['fixture_manifest_sha256']}`.",
        f"- Build profile: {private['build_profile']}; environment: {evidence['environment']['os']}, "
        f"{evidence['environment']['cpu']}.",
        "- Configuration: identical to the public subset (template + raw-identity@1 probe, backward-difference@1,",
        "  max gap 0.2 s, kinematic min confidence 0).",
        "",
        "## Fixtures",
        "",
        *fixture_rows(fixtures),
        "",
        *evidence_tool.coverage_lines(private["coverage"]),
        "",
        "## Tracker behaviour (tracker-declared states, unverified)",
        "",
        "Counts come from each tracker's own state output. Without ground-truth annotation they do not",
        "show whether a tracked position is correct, so they are not availability or accuracy evidence.",
        "",
        *tracker_rows(fixtures),
        "",
        "## Accuracy against annotations",
        "",
        *(evidence_tool.tracker_table(benchmarked, label="Fixture / tracker") if benchmarked
          else ["No private fixture has annotations yet, so no accuracy is reported."]),
        "",
        "## Determinism",
        "",
        *(determinism or ["- No private fixture was analysed."]),
        "",
        "## Provisional gate status (public + local evidence)",
        "",
        *evidence_tool.gate_table(evidence["gates"]),
        "",
        "## Limits",
        "",
        "- All local fixtures are development material from one recording setup; none is held out.",
        "- Real-time ratios are desktop diagnostics. The offline-speed gate targets phone-class hardware.",
        "- Manual seeds that the owner has not confirmed lower the weight of any tracking result started from them.",
        "",
    ]
    return "\n".join(lines)
