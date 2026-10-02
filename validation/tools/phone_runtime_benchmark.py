#!/usr/bin/env python3
"""Benchmark the frozen OpenBar M0 analysis pipeline on the reference Android phone.

The harness is intentionally outside canonical analysis output: wall-clock, memory, battery and
thermal observations are environment evidence, not deterministic measurement data.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
BENCHMARK_VERSION = "phone-runtime-benchmark-v1"
REFERENCE_MODEL = "Google Pixel 8"
REFERENCE_DEVICE = "shiba"
REFERENCE_ABI = "arm64-v8a"
REFERENCE_RUNTIME = "Termux native arm64 release openbar-cli + external ffmpeg/ffprobe"
REPEATS_DEFAULT = 3
PROBE_TRACKER = "template"
PROBE_TRACKER_ID = "template-sad-v1"
PROBE_FILTER = "raw"
KINEMATICS_MAX_GAP_S = "0.2"
KINEMATICS_MIN_CONFIDENCE = "0"
TRACKER_TIMING_RE = re.compile(
    r"(?P<tracker>[a-z0-9._-]+): .*?\n\s*decode (?P<decode>[0-9.]+) s \+ track "
    r"(?P<track>[0-9.]+) s for (?P<media>[0-9.]+) s of selected media "
    r"\((?P<realtime>[0-9.]+)x real time\)",
    re.DOTALL,
)


class BenchmarkError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise BenchmarkError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def first_line(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError:
        return "unavailable"
    if completed.returncode != 0 or not completed.stdout.splitlines():
        return "unavailable"
    return completed.stdout.splitlines()[0]


def getprop(name: str) -> str | None:
    try:
        completed = subprocess.run(
            ["getprop", name],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    value = completed.stdout.strip()
    return value or None


def total_memory_mib() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024.0, 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


def read_number(path: Path, *, scale: float = 1.0) -> float | None:
    try:
        return float(path.read_text(encoding="utf-8").strip()) / scale
    except (OSError, ValueError):
        return None


def battery_snapshot() -> dict[str, float | None]:
    root = Path("/sys/class/power_supply/battery")
    temp = read_number(root / "temp", scale=10.0)
    if temp is not None and not (-20.0 <= temp <= 100.0):
        temp = None
    return {
        "capacity_percent": read_number(root / "capacity"),
        "temperature_c": temp,
    }


def thermal_snapshot() -> dict[str, float | None]:
    values: list[float] = []
    for path in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        value = read_number(path)
        if value is None:
            continue
        if value > 1000:
            value /= 1000.0
        if -20.0 <= value <= 150.0:
            values.append(value)
    return {"max_zone_temperature_c": max(values) if values else None}


def environment() -> dict[str, Any]:
    model = getprop("ro.product.model")
    device = getprop("ro.product.device")
    abi = getprop("ro.product.cpu.abi")
    android_release = getprop("ro.build.version.release")
    sdk = getprop("ro.build.version.sdk")
    machine = platform.machine()
    prefix = os.environ.get("PREFIX", "")
    termux = "/com.termux/" in prefix.replace("\\", "/") or bool(os.environ.get("TERMUX_VERSION"))
    reference_match = (
        device == REFERENCE_DEVICE
        and abi == REFERENCE_ABI
        and machine.lower() in {"aarch64", "arm64"}
        and android_release is not None
        and termux
    )
    return {
        "reference_target": {
            "model": REFERENCE_MODEL,
            "device_codename": REFERENCE_DEVICE,
            "abi": REFERENCE_ABI,
            "runtime": REFERENCE_RUNTIME,
        },
        "observed": {
            "model": model,
            "device_codename": device,
            "abi": abi,
            "machine": machine,
            "android_release": android_release,
            "android_sdk": sdk,
            "termux_runtime": termux,
            "memory_mib": total_memory_mib(),
            "python": sys.version.split()[0],
            "rustc": first_line(["rustc", "--version"]),
            "ffmpeg": first_line(["ffmpeg", "-hide_banner", "-version"]),
        },
        "reference_match": reference_match,
    }


def descendants(pid: int) -> list[int]:
    found: list[int] = []
    stack = [pid]
    seen = {pid}
    while stack:
        current = stack.pop()
        children_file = Path(f"/proc/{current}/task/{current}/children")
        try:
            child_ids = [int(value) for value in children_file.read_text().split()]
        except (OSError, ValueError):
            child_ids = []
        for child in child_ids:
            if child not in seen:
                seen.add(child)
                found.append(child)
                stack.append(child)
    return found


def rss_kib(pid: int) -> int | None:
    try:
        lines = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("VmRSS:"):
            try:
                return int(line.split()[1])
            except (ValueError, IndexError):
                return None
    return None


def process_tree_rss_kib(pid: int) -> int | None:
    values = [rss_kib(candidate) for candidate in [pid, *descendants(pid)]]
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def run_timed(command: list[str], *, cwd: Path) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise BenchmarkError(f"failed to start {command[0]}: {error}") from error

    peak_rss_kib: int | None = None
    stop = threading.Event()

    def sample() -> None:
        nonlocal peak_rss_kib
        while not stop.is_set():
            current = process_tree_rss_kib(process.pid)
            if current is not None:
                peak_rss_kib = current if peak_rss_kib is None else max(peak_rss_kib, current)
            if process.poll() is not None:
                break
            stop.wait(0.02)
        current = process_tree_rss_kib(process.pid)
        if current is not None:
            peak_rss_kib = current if peak_rss_kib is None else max(peak_rss_kib, current)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    stdout, stderr = process.communicate()
    stop.set()
    sampler.join(timeout=1.0)
    wall_s = time.perf_counter() - started
    return {
        "returncode": process.returncode,
        "wall_s": wall_s,
        "peak_process_tree_rss_mib": None if peak_rss_kib is None else peak_rss_kib / 1024.0,
        "stdout": stdout,
        "stderr": stderr,
    }


def tracker_stage_timing(stderr: str, tracker_id: str = PROBE_TRACKER_ID) -> dict[str, float]:
    for match in TRACKER_TIMING_RE.finditer(stderr):
        if match.group("tracker") == tracker_id:
            return {
                "decode_wall_s": float(match.group("decode")),
                "tracker_wall_s": float(match.group("track")),
                "selected_media_span_s": float(match.group("media")),
            }
    raise BenchmarkError(f"tracker-run output did not contain timing for {tracker_id}")


def fixture_by_id(manifest: dict[str, Any], fixture_id: str) -> dict[str, Any]:
    for fixture in manifest.get("fixtures", []):
        if fixture.get("id") == fixture_id:
            return fixture
    raise BenchmarkError(f"fixture '{fixture_id}' was not found in the manifest")


def resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def case_args(case: dict[str, Any], suite_dir: Path) -> tuple[Path, Path, str, float, list[str]]:
    manifest_path = resolve(suite_dir, str(case["manifest"]))
    seed_path = resolve(suite_dir, str(case["seed"]))
    fixture_id = str(case["fixture_id"])
    plate_diameter_m = float(case["plate_diameter_m"])
    selection: list[str] = []
    start = case.get("start_s")
    end = case.get("end_s")
    if (start is None) != (end is None):
        raise BenchmarkError(f"case {case.get('id', fixture_id)} must provide start_s and end_s together")
    if start is not None:
        start_f, end_f = float(start), float(end)
        if start_f < 0 or end_f < start_f:
            raise BenchmarkError(f"case {case.get('id', fixture_id)} has an invalid selected range")
        selection = ["--start-s", str(start_f), "--end-s", str(end_f)]
    return manifest_path, seed_path, fixture_id, plate_diameter_m, selection


def analyze_command(
    binary: Path,
    manifest: Path,
    fixture_id: str,
    seed: Path,
    plate_diameter_m: float,
    selection: list[str],
    output: Path,
    support_output: Path,
) -> list[str]:
    return [
        str(binary),
        "analyze",
        "--manifest",
        str(manifest),
        "--fixture",
        fixture_id,
        "--seed",
        str(seed),
        "--plate-diameter-m",
        str(plate_diameter_m),
        "--tracker",
        PROBE_TRACKER,
        "--filter",
        PROBE_FILTER,
        "--kinematics-max-gap-s",
        KINEMATICS_MAX_GAP_S,
        "--kinematics-min-confidence",
        KINEMATICS_MIN_CONFIDENCE,
        *selection,
        "--diagnostics",
        "quiet",
        "--recording-support-output",
        str(support_output),
        "--output",
        str(output),
    ]


def tracker_command(
    binary: Path,
    manifest: Path,
    fixture_id: str,
    seed: Path,
    selection: list[str],
    output_dir: Path,
) -> list[str]:
    return [
        str(binary),
        "tracker-run",
        "--manifest",
        str(manifest),
        "--fixture",
        fixture_id,
        "--seed",
        str(seed),
        *selection,
        "--output-dir",
        str(output_dir),
    ]


def summarize_repeats(runs: list[dict[str, Any]], media_span_s: float) -> dict[str, Any]:
    if not runs or media_span_s <= 0:
        raise BenchmarkError("runtime summary requires at least one run and a positive media span")
    if any(run["returncode"] != 0 for run in runs):
        return {
            "completed_runs": sum(run["returncode"] == 0 for run in runs),
            "failed_runs": sum(run["returncode"] != 0 for run in runs),
            "median_wall_s": None,
            "median_processing_to_media_ratio": None,
            "max_processing_to_media_ratio": None,
            "peak_process_tree_rss_mib": max(
                (run["peak_process_tree_rss_mib"] for run in runs if run["peak_process_tree_rss_mib"] is not None),
                default=None,
            ),
        }
    walls = [float(run["wall_s"]) for run in runs]
    ratios = [wall / media_span_s for wall in walls]
    rss = [run["peak_process_tree_rss_mib"] for run in runs if run["peak_process_tree_rss_mib"] is not None]
    return {
        "completed_runs": len(runs),
        "failed_runs": 0,
        "median_wall_s": statistics.median(walls),
        "median_processing_to_media_ratio": statistics.median(ratios),
        "max_processing_to_media_ratio": max(ratios),
        "peak_process_tree_rss_mib": max(rss) if rss else None,
    }


def assess_gate(
    env: dict[str, Any],
    cases: list[dict[str, Any]],
    *,
    production_candidate_frozen: bool,
) -> dict[str, str]:
    if not production_candidate_frozen:
        return {
            "status": "NOT MEASURABLE YET",
            "evidence": "#57 has not yet frozen the production tracker/filter candidate for this runtime study.",
            "rationale": "A fast development integration probe cannot satisfy the product performance gate.",
        }
    if not env["reference_match"]:
        return {
            "status": "NOT MEASURABLE YET",
            "evidence": "Runtime diagnostics were not collected on the designated Google Pixel 8 reference runtime.",
            "rationale": "A non-reference device cannot satisfy the phone-class performance gate.",
        }
    gate_cases = [case for case in cases if case["representative_for_gate"]]
    if len(gate_cases) < 2:
        return {
            "status": "NOT MEASURABLE YET",
            "evidence": f"{len(gate_cases)} case(s) are marked representative for the gate.",
            "rationale": "The issue requires representative clips (plural); at least two frozen real supported cases are required.",
        }
    synthetic = [case["id"] for case in gate_cases if case["source_kind"] == "synthetic"]
    if synthetic:
        return {
            "status": "NOT MEASURABLE YET",
            "evidence": "Synthetic cases cannot satisfy the phone runtime gate: " + ", ".join(synthetic) + ".",
            "rationale": "Gate evidence must use representative non-synthetic lifting clips.",
        }
    failed = [case["id"] for case in gate_cases if case["end_to_end"]["failed_runs"] > 0]
    if failed:
        return {
            "status": "FAIL",
            "evidence": "The frozen analysis pipeline failed to complete on: " + ", ".join(failed) + ".",
            "rationale": "A representative reference-phone run that does not complete cannot satisfy the offline-processing gate.",
        }
    unsupported = [
        case["id"] for case in gate_cases if case["recording_support_status"] != "supported"
    ]
    if unsupported:
        return {
            "status": "NOT MEASURABLE YET",
            "evidence": "Gate cases are not yet inside the supported recording envelope: " + ", ".join(unsupported) + ".",
            "rationale": "Gate evidence must use non-synthetic clips inside the evidence-backed supported recording envelope.",
        }
    missing_memory = [
        case["id"] for case in gate_cases if case["end_to_end"]["peak_process_tree_rss_mib"] is None
    ]
    if missing_memory:
        return {
            "status": "NOT MEASURABLE YET",
            "evidence": "Process-tree RSS was unavailable for: " + ", ".join(missing_memory) + ".",
            "rationale": "Issue #59 requires memory/runtime constraints to be recorded on the phone runtime.",
        }
    slow = [
        case["id"]
        for case in gate_cases
        if float(case["end_to_end"]["median_processing_to_media_ratio"]) >= 1.0
    ]
    if slow:
        return {
            "status": "FAIL",
            "evidence": "Median processing/video-duration ratio was >= 1.0 for: " + ", ".join(slow) + ".",
            "rationale": "The provisional M0 target requires offline processing faster than the selected video duration.",
        }
    return {
        "status": "PASS",
        "evidence": "Every representative supported real case completed with median processing/video-duration ratio < 1.0.",
        "rationale": "The frozen pipeline satisfies the provisional offline-processing target on the designated reference phone runtime.",
    }


def evaluate_case(
    *,
    root: Path,
    suite_dir: Path,
    binary: Path,
    case: dict[str, Any],
    repeats: int,
    work_dir: Path,
) -> dict[str, Any]:
    manifest_path, seed_path, fixture_id, plate_diameter_m, selection = case_args(case, suite_dir)
    manifest = read_json(manifest_path)
    fixture = fixture_by_id(manifest, fixture_id)
    case_id = str(case.get("id") or fixture_id)
    source_kind = str(fixture.get("source", {}).get("kind", "unknown"))
    fixture_purpose = str(fixture.get("purpose", "unknown"))
    case_dir = work_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    stage_timings: list[dict[str, float]] = []
    media_span_s: float | None = None
    frame_buffer_mib: float | None = None
    for index in range(repeats):
        tracker_dir = case_dir / f"tracker-{index + 1}"
        result = run_timed(
            tracker_command(binary, manifest_path, fixture_id, seed_path, selection, tracker_dir),
            cwd=root,
        )
        try:
            timing = tracker_stage_timing(result["stderr"])
        except BenchmarkError:
            if result["returncode"] == 0:
                raise
            continue
        prediction = tracker_dir / f"{fixture_id}.{PROBE_TRACKER_ID}.prediction-v1.json"
        if prediction.exists():
            document = read_json(prediction)
            frame_source = document.get("implementation", {}).get("config", {}).get("frame_source", {})
            first_s = frame_source.get("first_selected_timestamp_s")
            last_s = frame_source.get("last_selected_timestamp_s")
            if isinstance(first_s, (int, float)) and isinstance(last_s, (int, float)) and last_s > first_s:
                timing["selected_media_span_s"] = float(last_s) - float(first_s)
            stream = frame_source.get("stream", {})
            selected = frame_source.get("selected_frame_count")
            width = stream.get("display_width_px")
            height = stream.get("display_height_px")
            if frame_buffer_mib is None and all(isinstance(value, int) and value > 0 for value in (selected, width, height)):
                frame_buffer_mib = selected * width * height / (1024.0 * 1024.0)
        stage_timings.append(timing)
        media_span_s = timing["selected_media_span_s"]

    if media_span_s is None or not stage_timings:
        raise BenchmarkError(f"{case_id}: no usable {PROBE_TRACKER_ID} tracker timing was produced")

    analyze_runs: list[dict[str, Any]] = []
    support_status: str | None = None
    before_battery = battery_snapshot()
    before_thermal = thermal_snapshot()
    for index in range(repeats):
        analysis_output = case_dir / f"analysis-{index + 1}.json"
        support_output = case_dir / f"recording-support-{index + 1}.json"
        result = run_timed(
            analyze_command(
                binary,
                manifest_path,
                fixture_id,
                seed_path,
                plate_diameter_m,
                selection,
                analysis_output,
                support_output,
            ),
            cwd=root,
        )
        analyze_runs.append(result)
        if support_output.exists():
            current = str(read_json(support_output).get("status", "unknown"))
            if support_status is None:
                support_status = current
            elif support_status != current:
                raise BenchmarkError(f"{case_id}: recording-support status changed across repeated runs")
    after_thermal = thermal_snapshot()
    after_battery = battery_snapshot()

    stage = {
        "median_decode_wall_s": statistics.median(item["decode_wall_s"] for item in stage_timings),
        "median_tracker_wall_s": statistics.median(item["tracker_wall_s"] for item in stage_timings),
        "median_decode_to_media_ratio": statistics.median(
            item["decode_wall_s"] / item["selected_media_span_s"] for item in stage_timings
        ),
        "median_tracker_to_media_ratio": statistics.median(
            item["tracker_wall_s"] / item["selected_media_span_s"] for item in stage_timings
        ),
        "frame_buffer_mib": frame_buffer_mib,
    }
    return {
        "id": case_id,
        "fixture_id": fixture_id,
        "fixture_purpose": fixture_purpose,
        "source_kind": source_kind,
        "representative_for_gate": bool(case.get("representative_for_gate", False)),
        "recording_support_status": support_status or "unknown",
        "selected_media_span_s": media_span_s,
        "repeats": repeats,
        "end_to_end": summarize_repeats(analyze_runs, media_span_s),
        "stage_diagnostics": stage,
        "battery_before": before_battery,
        "battery_after": after_battery,
        "thermal_before": before_thermal,
        "thermal_after": after_thermal,
    }


def evaluated_commit(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return os.environ.get("OPENBAR_GIT_COMMIT", "unknown")
    return completed.stdout.strip() if completed.returncode == 0 and completed.stdout.strip() else os.environ.get("OPENBAR_GIT_COMMIT", "unknown")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=Path("target/release/openbar-cli"))
    parser.add_argument("--repeats", type=int, default=REPEATS_DEFAULT)
    args = parser.parse_args(argv)

    if args.repeats < 2:
        parser.error("--repeats must be at least 2")

    root = Path(__file__).resolve().parents[2]
    suite_path = args.suite if args.suite.is_absolute() else (root / args.suite).resolve()
    output_path = args.output if args.output.is_absolute() else (root / args.output).resolve()
    binary = args.binary if args.binary.is_absolute() else (root / args.binary).resolve()

    try:
        suite = read_json(suite_path)
        if suite.get("schema_version") != 1:
            raise BenchmarkError("phone runtime suite must use schema_version 1")
        cases_spec = suite.get("cases")
        if not isinstance(cases_spec, list) or not cases_spec:
            raise BenchmarkError("phone runtime suite must contain at least one case")
        if not binary.exists():
            raise BenchmarkError(f"release binary does not exist: {binary}")
        env = environment()
        with tempfile.TemporaryDirectory(prefix="openbar-phone-runtime-") as temp:
            cases = [
                evaluate_case(
                    root=root,
                    suite_dir=suite_path.parent,
                    binary=binary,
                    case=case,
                    repeats=args.repeats,
                    work_dir=Path(temp),
                )
                for case in cases_spec
            ]
        artifact = {
            "schema_version": SCHEMA_VERSION,
            "benchmark_version": BENCHMARK_VERSION,
            "evaluated_commit": evaluated_commit(root),
            "frozen_configuration": {
                "production_candidate_frozen": bool(
                    suite.get("production_candidate_frozen", False)
                ),
                "tracker": f"{PROBE_TRACKER_ID}@1",
                "filter": "raw-identity@1",
                "kinematics": "backward-difference@1",
                "kinematics_max_gap_s": float(KINEMATICS_MAX_GAP_S),
                "kinematics_min_confidence": float(KINEMATICS_MIN_CONFIDENCE),
                "decode_boundary": "ADR-0006 external ffmpeg/ffprobe process",
            },
            "environment": env,
            "cases": cases,
            "gate": assess_gate(
                env,
                cases,
                production_candidate_frozen=bool(suite.get("production_candidate_frozen", False)),
            ),
        }
        write_json(output_path, artifact)
        print(f"phone runtime evidence: {output_path}")
        print(f"offline-processing gate: {artifact['gate']['status']}")
        return 0
    except (BenchmarkError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"phone-runtime-benchmark: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
