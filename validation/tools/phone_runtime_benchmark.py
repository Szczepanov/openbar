#!/usr/bin/env python3
"""Benchmark the frozen OpenBar M0 analysis pipeline on the reference Android phone.

The harness is intentionally outside canonical analysis output: wall-clock, memory, battery and
thermal observations are environment evidence, not deterministic measurement data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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
REFERENCE_MODEL = "Xiaomi 14T Pro"
REFERENCE_DEVICE = "rothko"
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


TIMINGS_VERSION = "analyze-stage-timings-v1"
TIMINGS_SUM_TOLERANCE_S = 1e-6
COLD_DEFINITION = (
    "first analyze repeat in this harness invocation, run after the tracker-run "
    "stage-diagnostic passes have already read the same media; OS file cache and CPU state "
    "not controlled, so it is NOT a verified cold start"
)
LATENCY_CONTRACT = "docs/validation/ANDROID_LATENCY_EVIDENCE.md"
LATENCY_MISSING_STAGES = [
    "import request / app path",
    "overlay preparation",
    "per-rep metrics (analyze computes none)",
]
SMALL_N_THRESHOLD = 20
SIDECAR_KEYS = {"schema_version", "timings_version", "command", "stages_s", "total_s"}


class BenchmarkError(RuntimeError):
    pass


def percentile_nearest_rank(values: list[float], p: float) -> float:
    """Deterministic nearest-rank percentile (docs/validation/BENCHMARK.md).

    rank = ceil(p * n), one-indexed over the ascending-sorted values; ``0 < p <= 1``.
    """
    if not isinstance(p, (int, float)) or not math.isfinite(p) or not 0.0 < p <= 1.0:
        raise BenchmarkError("percentile must be in (0, 1]")
    if not values:
        raise BenchmarkError("percentile requires at least one value")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise BenchmarkError("percentile requires finite numeric values")
    ordered = sorted(values)
    rank = max(1, math.ceil(p * len(ordered)))
    return float(ordered[rank - 1])


def tail_statistics(values: list[float]) -> dict[str, float]:
    return {
        "median": float(statistics.median(values)),
        "p95": percentile_nearest_rank(values, 0.95),
        "max": float(max(values)),
    }


def warm_slowdown_ratio(runs: list[dict[str, Any]]) -> float | None:
    """Wall of the last warm repeat over the first warm repeat (warm = repeats 2..n).

    Observation only; it must not be used to infer thermal throttling.
    """
    warm = runs[1:]
    if len(warm) < 2:
        return None
    first, last = warm[0], warm[-1]
    if first["returncode"] != 0 or last["returncode"] != 0 or not float(first["wall_s"]) > 0:
        return None
    return float(last["wall_s"]) / float(first["wall_s"])


def summarize_latency(runs: list[dict[str, Any]], media_span_s: float) -> dict[str, Any]:
    """Non-qualifying latency diagnostics.

    Failed runs exit early (no timeout), so their short walls would make tails look better.
    Statistics are therefore computed over completed runs only and are all null (with
    ``unmeasurable_reason``) when any run failed; failed runs are listed, never dropped.

    "Cold" is the first analyze repeat within this harness invocation only. It is NOT a
    verified OS-page-cache-cold or fresh-process-after-reboot state (see COLD_DEFINITION).
    ``warm_slowdown_ratio`` needs at least 3 repeats and is null otherwise.
    """
    if not runs or not media_span_s > 0:
        raise BenchmarkError("latency summary requires at least one run and a positive media span")
    failed_runs = [run for run in runs if run["returncode"] != 0]
    result: dict[str, Any] = {
        "qualifying": False,
        "cold_definition": COLD_DEFINITION,
        "n_runs": len(runs),
        "small_n": len(runs) < SMALL_N_THRESHOLD,
        "failed_runs": len(failed_runs),
        "failed_run_wall_s": [float(run["wall_s"]) for run in failed_runs],
        "failed_run_returncodes": [run["returncode"] for run in failed_runs],
    }
    if failed_runs:
        result.update(
            {
                "unmeasurable_reason": "one or more runs failed",
                "cold_run_wall_s": None,
                "warm_run_wall_s": None,
                "warm_median_wall_s": None,
                "median_wall_s": None,
                "p95_wall_s": None,
                "max_wall_s": None,
                "median_processing_to_media_ratio": None,
                "p95_processing_to_media_ratio": None,
                "max_processing_to_media_ratio": None,
                "warm_slowdown_ratio": None,
            }
        )
        return result
    walls = [float(run["wall_s"]) for run in runs]
    ratios = [wall / media_span_s for wall in walls]
    wall_stats = tail_statistics(walls)
    ratio_stats = tail_statistics(ratios)
    warm = walls[1:]
    result.update(
        {
            "unmeasurable_reason": None,
            "cold_run_wall_s": walls[0],
            "warm_run_wall_s": warm,
            "warm_median_wall_s": float(statistics.median(warm)) if warm else None,
            "median_wall_s": wall_stats["median"],
            "p95_wall_s": wall_stats["p95"],
            "max_wall_s": wall_stats["max"],
            "median_processing_to_media_ratio": ratio_stats["median"],
            "p95_processing_to_media_ratio": ratio_stats["p95"],
            "max_processing_to_media_ratio": ratio_stats["max"],
            "warm_slowdown_ratio": warm_slowdown_ratio(runs),
        }
    )
    return result


def build_latency_evidence(
    *, production_candidate_frozen: bool, reference_match: bool, stage_timings_requested: bool
) -> dict[str, Any]:
    reasons = [
        "the product latency target has no owner-approved value",
        "production candidate "
        + ("is frozen for this study" if production_candidate_frozen else "is not frozen (#57)"),
        "reference device "
        + ("matched" if reference_match else "did not match (Xiaomi 14T Pro Termux runtime)"),
        "app import and overlay stages are not measured",
    ]
    return {
        "status": "NON-QUALIFYING DIAGNOSTIC",
        "qualifying": False,
        "reason": "Diagnostics only, never a PASS/FAIL for the product target: "
        + "; ".join(reasons)
        + ".",
        "contract": LATENCY_CONTRACT,
        "product_target_declared": False,
        "production_candidate_frozen": production_candidate_frozen,
        "reference_match": reference_match,
        "stage_timings_requested": stage_timings_requested,
        "missing_stages": list(LATENCY_MISSING_STAGES),
    }


def build_artifact(
    *,
    evaluated_commit_id: str,
    env: dict[str, Any],
    cases: list[dict[str, Any]],
    production_candidate_frozen: bool,
    stage_timings_requested: bool,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark_version": BENCHMARK_VERSION,
        "evaluated_commit": evaluated_commit_id,
        "frozen_configuration": {
            "production_candidate_frozen": production_candidate_frozen,
            "tracker": f"{PROBE_TRACKER_ID}@1",
            "filter": "raw-identity@1",
            "kinematics": "backward-difference@1",
            "kinematics_max_gap_s": float(KINEMATICS_MAX_GAP_S),
            "kinematics_min_confidence": float(KINEMATICS_MIN_CONFIDENCE),
            "decode_boundary": "ADR-0006 external ffmpeg/ffprobe process",
        },
        "environment": env,
        "cases": cases,
        "latency_evidence": build_latency_evidence(
            production_candidate_frozen=production_candidate_frozen,
            reference_match=bool(env["reference_match"]),
            stage_timings_requested=stage_timings_requested,
        ),
        "diagnostics_warnings": diagnostics_warnings(cases),
        "gate": assess_gate(
            env, cases, production_candidate_frozen=production_candidate_frozen
        ),
    }


def warning_line(case_id: str) -> str:
    return (
        "phone-runtime-benchmark: warning: analysis bytes differed across repeats "
        f"for case {case_id}"
    )


def _identical(blobs: list[bytes | None]) -> bool | None:
    present = [blob for blob in blobs if blob is not None]
    if len(set(present)) > 1:
        return False
    if not present or len(present) != len(blobs):
        return None
    return True


def determinism_report(
    analysis_blobs: list[bytes | None], support_blobs: list[bytes | None]
) -> dict[str, Any]:
    """Byte comparison of per-run outputs; None entries are runs that produced no file."""
    first = next((blob for blob in analysis_blobs if blob is not None), None)
    return {
        "analysis_bytes_identical": _identical(analysis_blobs),
        "recording_support_bytes_identical": _identical(support_blobs),
        "compared_runs": sum(blob is not None for blob in analysis_blobs),
        "recording_support_compared_runs": sum(blob is not None for blob in support_blobs),
        "analysis_sha256": None if first is None else hashlib.sha256(first).hexdigest(),
    }


def read_optional_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def diagnostics_warnings(cases: list[dict[str, Any]]) -> list[str]:
    return [
        str(case["id"])
        for case in cases
        if False
        in (
            case["latency_diagnostics"]["determinism"]["analysis_bytes_identical"],
            case["latency_diagnostics"]["determinism"]["recording_support_bytes_identical"],
        )
    ]


def _finite_non_negative(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def parse_stage_timings_sidecar(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except ValueError as error:
        raise BenchmarkError("stage-timings sidecar is not valid JSON") from error
    if not isinstance(value, dict):
        raise BenchmarkError("stage-timings sidecar must be a JSON object")
    if set(value) - SIDECAR_KEYS:
        raise BenchmarkError("stage-timings sidecar has unknown top-level keys")
    version = value.get("schema_version")
    if type(version) is not int or version != 1:
        raise BenchmarkError("stage-timings sidecar schema_version must be integer 1")
    if value.get("command") != "analyze":
        raise BenchmarkError("stage-timings sidecar command must be analyze")
    if value.get("timings_version") != TIMINGS_VERSION:
        raise BenchmarkError("stage-timings sidecar timings_version mismatch")
    stages = value.get("stages_s")
    if not isinstance(stages, dict) or not all(
        isinstance(name, str) and _finite_non_negative(seconds) for name, seconds in stages.items()
    ):
        raise BenchmarkError("stage-timings stages_s must map names to finite non-negative seconds")
    total = value.get("total_s")
    if not _finite_non_negative(total):
        raise BenchmarkError("stage-timings total_s must be finite and non-negative")
    if total < sum(stages.values()) - TIMINGS_SUM_TOLERANCE_S:
        raise BenchmarkError("stage-timings stages exceed total_s")
    return {"stages_s": {k: float(v) for k, v in stages.items()}, "total_s": float(total)}


def read_stage_timings(path: Path, run_number: int) -> tuple[dict[str, Any] | None, str | None]:
    """Return (timing, None) or (None, note); notes never contain local paths."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None, f"run {run_number}: stage-timings sidecar missing or unreadable"
    try:
        return parse_stage_timings_sidecar(text), None
    except BenchmarkError as error:
        return None, f"run {run_number}: {error}"


def aggregate_stage_timings(
    timings: list[dict[str, Any] | None] | None, notes: list[str]
) -> dict[str, Any]:
    """Aggregate per-run sidecars; stage names are opaque and only common ones are reported."""
    if timings is None:
        return {"available": False, "reason": "not requested"}
    valid = [timing for timing in timings if timing is not None]
    if not valid:
        return {"available": False, "reason": "no valid stage-timings sidecar", "notes": list(notes)}
    common = set(valid[0]["stages_s"])
    for timing in valid[1:]:
        common &= set(timing["stages_s"])
    stages: dict[str, Any] = {}
    for name in sorted(common):
        series = [timing["stages_s"][name] for timing in valid]
        stats = tail_statistics(series)
        stages[name] = {
            "n": len(series),
            "median_s": stats["median"],
            "p95_s": stats["p95"],
            "max_s": stats["max"],
        }
    unattributed = [timing["total_s"] - sum(timing["stages_s"].values()) for timing in valid]
    result: dict[str, Any] = {
        "available": True,
        "runs_with_timings": len(valid),
        "stages": stages,
        "unattributed_median_s": float(statistics.median(unattributed)),
    }
    all_notes = list(notes)
    dropped = sorted(set().union(*(set(t["stages_s"]) for t in valid)) - common)
    if dropped:
        all_notes.append("stages missing from some run and not aggregated: " + ", ".join(dropped))
    if all_notes:
        result["notes"] = all_notes
    return result


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


PROC_ROOT = Path("/proc")
RSS_SAMPLE_INTERVAL_S = 0.02
SAMPLER_JOIN_TIMEOUT_S = 1.0
RSS_COMPLETE_KEY = "process_tree_rss_complete"
VANISHED = (FileNotFoundError, ProcessLookupError)


def parse_ppid(stat_text: str) -> int | None:
    """Parent PID from a /proc/<pid>/stat line.

    comm is parenthesised and may itself contain spaces and parentheses, so fields are read
    only from the text after the LAST ')'. After it: state, then ppid.
    """
    _, sep, rest = stat_text.rpartition(")")
    fields = rest.split()
    if not sep or len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def scan_proc(proc_root: Path) -> tuple[dict[int, list[int]], set[int], bool] | None:
    """Scan every <proc_root>/<pid>/stat: (parent -> children, parsed pids, scan complete).

    A process vanishing mid-scan (ENOENT/ESRCH) is normal and skipped. Any other read error, or
    an unparseable stat of a live entry, makes the scan incomplete. None means the root could not
    be listed or no stat file was parsed at all (the tree cannot be trusted).
    """
    try:
        entries = [entry for entry in proc_root.iterdir() if entry.name.isdigit()]
    except OSError:
        return None
    children: dict[int, list[int]] = {}
    known: set[int] = set()
    complete = True
    for entry in entries:
        try:
            text = entry.joinpath("stat").read_text(encoding="utf-8", errors="replace")
        except VANISHED:
            continue
        except OSError:
            complete = False
            continue
        ppid = parse_ppid(text)
        if ppid is None:
            complete = False
            continue
        known.add(int(entry.name))
        children.setdefault(ppid, []).append(int(entry.name))
    return (children, known, complete) if known else None


def descendants_status(pid: int, proc_root: Path = PROC_ROOT) -> tuple[list[int], bool, bool]:
    """(descendants of pid, walk complete, pid itself present in the scan).

    The tree always comes from stat parent PIDs: the task/<pid>/children file is absent on some
    kernels (observed on Android 16) and only lists the main thread's children, so it is not used.
    """
    scan = scan_proc(proc_root)
    if scan is None:
        return [], False, False
    children, known, complete = scan
    found: list[int] = []
    stack = [pid]
    seen = {pid}
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                found.append(child)
                stack.append(child)
    return found, complete, pid in known


def descendants(pid: int, proc_root: Path = PROC_ROOT) -> list[int]:
    return descendants_status(pid, proc_root)[0]


def rss_status(pid: int, proc_root: Path = PROC_ROOT) -> tuple[int | None, bool]:
    """(VmRSS in KiB or None, readable). A vanished process is readable-with-no-value."""
    try:
        lines = (proc_root / str(pid) / "status").read_text(encoding="utf-8", errors="replace").splitlines()
    except VANISHED:
        return None, True
    except OSError:
        return None, False
    for line in lines:
        if line.startswith("VmRSS:"):
            try:
                return int(line.split()[1]), True
            except (ValueError, IndexError):
                return None, False
    return None, True


def rss_kib(pid: int, proc_root: Path = PROC_ROOT) -> int | None:
    return rss_status(pid, proc_root)[0]


def process_tree_rss(
    pid: int, proc_root: Path = PROC_ROOT
) -> tuple[int | None, bool, bool]:
    """(summed RSS KiB or None, tree fully enumerated and every member readable, root in scan)."""
    found, complete, root_present = descendants_status(pid, proc_root)
    total = 0
    known = False
    for candidate in [pid, *found]:
        value, readable = rss_status(candidate, proc_root)
        complete = complete and readable
        if value is not None:
            total += value
            known = True
    return (total if known else None), complete, root_present


def sample_is_complete(tree_complete: bool, root_present: bool, root_alive: bool) -> bool:
    """A live root must appear in the scan; otherwise /proc is the wrong namespace or view."""
    return tree_complete and (root_present or not root_alive)


def run_rss_complete(run: dict[str, Any]) -> bool:
    """Completeness of a run's RSS. run_timed always sets the key; inputs from before the key
    existed (old-format dicts) are treated as complete."""
    return bool(run.get(RSS_COMPLETE_KEY, True))


def summarize_rss_sampling(runs: list[dict[str, Any]]) -> dict[str, Any]:
    samples = sum(int(run.get("rss_samples", 0)) for run in runs)
    if samples == 0:
        return {"rss_samples": 0, "rss_mean_scan_ms": None}
    weighted = sum(
        int(run["rss_samples"]) * float(run["rss_mean_scan_ms"])
        for run in runs
        if run.get("rss_mean_scan_ms") is not None
    )
    return {"rss_samples": samples, "rss_mean_scan_ms": weighted / samples}


def run_timed(command: list[str], *, cwd: Path, proc_root: Path = PROC_ROOT) -> dict[str, Any]:
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
    tree_complete = True
    sampler_failed = False
    sample_count = 0
    scan_total_s = 0.0
    stop = threading.Event()

    def take_sample() -> None:
        nonlocal peak_rss_kib, tree_complete, sample_count, scan_total_s
        scan_started = time.perf_counter()
        current, complete, root_present = process_tree_rss(process.pid, proc_root)
        scan_total_s += time.perf_counter() - scan_started
        sample_count += 1
        # Checked after the scan: an unreaped root stays in /proc (even as a zombie).
        root_alive = process.poll() is None
        tree_complete = tree_complete and sample_is_complete(complete, root_present, root_alive)
        if current is not None:
            peak_rss_kib = current if peak_rss_kib is None else max(peak_rss_kib, current)

    def sample() -> None:
        nonlocal sampler_failed
        try:
            # A full /proc scan per sample is a small CPU cost inside the measured wall time.
            while not stop.is_set():
                take_sample()
                if process.poll() is not None:
                    break
                stop.wait(RSS_SAMPLE_INTERVAL_S)
            take_sample()
        except BaseException:  # noqa: BLE001 - any sampler failure must invalidate the RSS
            sampler_failed = True

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    stdout, stderr = process.communicate()
    stop.set()
    sampler.join(timeout=SAMPLER_JOIN_TIMEOUT_S)
    wall_s = time.perf_counter() - started
    # An incompletely enumerated tree (e.g. unscannable /proc), a crashed or still-running
    # sampler would report only part of the tree and look like a plausible low number, so
    # report no RSS at all instead.
    rss_complete = (
        tree_complete
        and not sampler_failed
        and not sampler.is_alive()
        and peak_rss_kib is not None
    )
    return {
        "returncode": process.returncode,
        "wall_s": wall_s,
        "peak_process_tree_rss_mib": peak_rss_kib / 1024.0 if rss_complete else None,
        RSS_COMPLETE_KEY: rss_complete,
        "rss_samples": sample_count,
        "rss_mean_scan_ms": scan_total_s * 1000.0 / sample_count if sample_count else None,
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
    timings_output: Path | None = None,
) -> list[str]:
    command = [
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
    if timings_output is not None:
        command += ["--timings-output", str(timings_output)]
    return command


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


def _peak_rss(runs: list[dict[str, Any]]) -> float | None:
    # Any run with an incompletely enumerated process tree makes the peak unknown, not partial.
    if not all(run_rss_complete(run) for run in runs):
        return None
    return max(
        (run["peak_process_tree_rss_mib"] for run in runs if run["peak_process_tree_rss_mib"] is not None),
        default=None,
    )


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
            "peak_process_tree_rss_mib": _peak_rss(runs),
        }
    walls = [float(run["wall_s"]) for run in runs]
    ratios = [wall / media_span_s for wall in walls]
    return {
        "completed_runs": len(runs),
        "failed_runs": 0,
        "median_wall_s": statistics.median(walls),
        "median_processing_to_media_ratio": statistics.median(ratios),
        "max_processing_to_media_ratio": max(ratios),
        "peak_process_tree_rss_mib": _peak_rss(runs),
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
            "evidence": "Runtime diagnostics were not collected on the designated Xiaomi 14T Pro reference runtime.",
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
    stage_timings_requested: bool = False,
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
    sidecar_timings: list[dict[str, Any] | None] = []
    sidecar_notes: list[str] = []
    support_status: str | None = None
    before_battery = battery_snapshot()
    before_thermal = thermal_snapshot()
    for index in range(repeats):
        analysis_output = case_dir / f"analysis-{index + 1}.json"
        support_output = case_dir / f"recording-support-{index + 1}.json"
        timings_output = case_dir / f"stage-timings-{index + 1}.json" if stage_timings_requested else None
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
                timings_output,
            ),
            cwd=root,
        )
        analyze_runs.append(result)
        if timings_output is not None:
            timing, note = read_stage_timings(timings_output, index + 1)
            sidecar_timings.append(timing)
            if note is not None:
                sidecar_notes.append(note)
        if support_output.exists():
            current = str(read_json(support_output).get("status", "unknown"))
            if support_status is None:
                support_status = current
            elif support_status != current:
                raise BenchmarkError(f"{case_id}: recording-support status changed across repeated runs")
    after_thermal = thermal_snapshot()
    after_battery = battery_snapshot()
    latency = summarize_latency(analyze_runs, media_span_s)
    latency.update(summarize_rss_sampling(analyze_runs))
    latency["determinism"] = determinism_report(
        [read_optional_bytes(case_dir / f"analysis-{i + 1}.json") for i in range(repeats)],
        [read_optional_bytes(case_dir / f"recording-support-{i + 1}.json") for i in range(repeats)],
    )
    latency["stage_timings"] = aggregate_stage_timings(
        sidecar_timings if stage_timings_requested else None, sidecar_notes
    )

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
        RSS_COMPLETE_KEY: all(run_rss_complete(run) for run in analyze_runs),
        "latency_diagnostics": latency,
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
    parser.add_argument(
        "--stage-timings",
        action="store_true",
        help="pass --timings-output to analyze and aggregate stage timings (off by default)",
    )
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
                    stage_timings_requested=args.stage_timings,
                )
                for case in cases_spec
            ]
        artifact = build_artifact(
            evaluated_commit_id=evaluated_commit(root),
            env=env,
            cases=cases,
            production_candidate_frozen=bool(suite.get("production_candidate_frozen", False)),
            stage_timings_requested=args.stage_timings,
        )
        for case_id in artifact["diagnostics_warnings"]:
            print(warning_line(case_id), file=sys.stderr)
        write_json(output_path, artifact)
        print(f"phone runtime evidence: {output_path}")
        print(f"offline-processing gate: {artifact['gate']['status']}")
        return 0
    except (BenchmarkError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"phone-runtime-benchmark: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
