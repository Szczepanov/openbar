#!/usr/bin/env python3
"""Mobile-first local web UI for testing OpenBar on Xiaomi 14T Pro (Termux or LAN).

Research & dogfooding harness under research/phone-ui/. Does not unlock M1 or replace
canonical measurement logic:

1. Upload or pick a local lift video from the phone camera/gallery.
2. Probe stream PTS and extract the seed frame via FFmpeg with ADR-0006 display rotation
   and PTS verification (reusing validation/tools/label_package.py).
3. Tap the plate centre and adjust the pixel radius on the mobile touch canvas (ADR-0007
   pixel-centre coordinates).
4. Write a schema-checked manual-target-seed-v1 JSON and invoke native `openbar-cli analyze`
   (supporting `--tracker csrt-gray`, `template`, or `contrast`) plus `openbar-cli render`.
5. Display the recording-support status, stage timings (vs #179 5.0 s median / 8.0 s max
   budget), non-authoritative upward rep preview (ADR-0012), and deterministic diagnostic SVG.

Standard library only. Requires `ffmpeg`, `ffprobe`, and `openbar-cli` (or `cargo`).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "tools"))
import label_package  # noqa: E402
import schema_check  # noqa: E402

SEED_SCHEMA_PATH = ROOT / "validation" / "schema" / "manual-target-seed-v1.schema.json"
ANALYSIS_SCHEMA_PATH = ROOT / "validation" / "schema" / "analysis-v1.schema.json"

TRACKERS = ("csrt-gray", "template", "contrast")
FILTERS = ("savitzky-golay-0.15s", "savitzky-golay-7", "raw")
EXERCISES = ("back_squat", "snatch", "clean", "deadlift", "bench_press", "other")
PREVIEW_MIN_RISE_M = 0.10
LATENCY_MEDIAN_BUDGET_S = 5.0
LATENCY_MAX_BUDGET_S = 8.0


class PhoneAppError(RuntimeError):
    pass


def compute_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def fixture_id_for_sha256(sha256_hex: str) -> str:
    return f"phone-{sha256_hex[:16]}"


def resolve_openbar_cli(explicit_binary: Path | None = None) -> list[str]:
    """Return the command prefix to run openbar-cli (release binary preferred)."""
    if explicit_binary is not None:
        if not explicit_binary.is_file():
            raise PhoneAppError(f"openbar-cli binary not found at {explicit_binary}")
        return [str(explicit_binary)]

    exe_name = "openbar-cli.exe" if os.name == "nt" else "openbar-cli"
    candidates = [
        p
        for p in (
            ROOT / "target" / "release" / exe_name,
            ROOT / "target" / "debug" / exe_name,
        )
        if p.is_file()
    ]
    if candidates:
        newest = max(candidates, key=lambda p: p.stat().st_mtime)
        return [str(newest)]

    on_path = shutil.which("openbar-cli")
    if on_path:
        return [on_path]

    return ["cargo", "run", "--locked", "-p", "openbar-cli", "--"]


def probe_and_extract_seed_frame(
    video_path: Path,
    work_dir: Path,
    at_s: float = 0.0,
) -> dict[str, Any]:
    """Probe video timeline and extract the display-oriented frame nearest `at_s`."""
    if not video_path.is_file():
        raise PhoneAppError(f"video file not found: {video_path}")
    if not math.isfinite(at_s) or at_s < 0.0:
        raise PhoneAppError(f"seed timestamp must be finite and non-negative, got {at_s}")

    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,sample_aspect_ratio,start_pts,time_base:stream_side_data=rotation:frame=pts",
        "-of",
        "json",
        str(video_path),
    ]
    try:
        probe_proc = subprocess.run(
            probe_cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError as error:
        raise PhoneAppError("ffprobe is not on PATH") from error
    if probe_proc.returncode != 0:
        raise PhoneAppError(f"ffprobe failed: {probe_proc.stderr.strip()[-400:]}")

    try:
        probe = label_package.parse_probe(probe_proc.stdout)
    except label_package.PackageError as error:
        raise PhoneAppError(str(error)) from error

    timestamps_s: list[float] = probe["timestamps_s"]
    frame_index = min(
        range(len(timestamps_s)),
        key=lambda idx: abs(timestamps_s[idx] - at_s),
    )
    timestamp_s = round(timestamps_s[frame_index], 6)
    expected_pts = probe["pts"][frame_index]
    display_w, display_h = label_package.display_size(
        probe["width_px"], probe["height_px"], probe["rotation_deg"]
    )

    work_dir.mkdir(parents=True, exist_ok=True)
    frame_png = work_dir / f"seed_frame_{frame_index:06d}.png"
    extract_cmd = [
        "ffmpeg",
        "-v",
        "info",
        "-nostdin",
        "-y",
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
        "-vf",
        f"select='eq(n\\,{frame_index})',showinfo",
        "-fps_mode",
        "passthrough",
        "-enc_time_base",
        "demux",
        "-frames:v",
        "1",
        str(frame_png),
    ]
    try:
        extract_proc = subprocess.run(
            extract_cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError as error:
        raise PhoneAppError("ffmpeg is not on PATH") from error
    if extract_proc.returncode != 0 or not frame_png.is_file():
        raise PhoneAppError(f"ffmpeg frame extraction failed: {extract_proc.stderr.strip()[-400:]}")

    extracted_pts = label_package.parse_showinfo(extract_proc.stderr)
    try:
        label_package.require_aligned(extracted_pts, [expected_pts])
    except label_package.PackageError as error:
        raise PhoneAppError(str(error)) from error

    sha256_hex = compute_sha256(video_path)
    return {
        "video_path": str(video_path.resolve()),
        "video_name": video_path.name,
        "source_video_sha256": sha256_hex,
        "fixture_id": fixture_id_for_sha256(sha256_hex),
        "frame_index": frame_index,
        "timestamp_s": timestamp_s,
        "duration_s": round(timestamps_s[-1] - timestamps_s[0], 6) if len(timestamps_s) > 1 else 0.0,
        "frame_count": len(timestamps_s),
        "encoded_width_px": probe["width_px"],
        "encoded_height_px": probe["height_px"],
        "rotation_deg": probe["rotation_deg"],
        "display_width_px": display_w,
        "display_height_px": display_h,
        "frame_png_path": str(frame_png.resolve()),
        "frame_png_base64": base64.b64encode(frame_png.read_bytes()).decode("ascii"),
    }


def build_manual_seed_document(
    *,
    fixture_id: str,
    source_video_sha256: str,
    encoded_width_px: int,
    encoded_height_px: int,
    rotation_deg: int,
    display_width_px: int,
    display_height_px: int,
    timestamp_s: float,
    frame_index: int,
    center_x_px: float,
    center_y_px: float,
    radius_px: float,
) -> dict[str, Any]:
    """Construct and schema-validate a manual-target-seed-v1 document."""
    for name, value in (
        ("center_x_px", center_x_px),
        ("center_y_px", center_y_px),
        ("radius_px", radius_px),
        ("timestamp_s", timestamp_s),
    ):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise PhoneAppError(f"{name} must be finite, got {value!r}")

    if radius_px < 1.0:
        raise PhoneAppError(f"radius_px must be >= 1.0, got {radius_px}")
    if not (0.0 <= center_x_px < display_width_px and 0.0 <= center_y_px < display_height_px):
        raise PhoneAppError(
            f"seed centre ({center_x_px:.2f}, {center_y_px:.2f}) is outside display raster "
            f"{display_width_px}x{display_height_px}"
        )
    left_px = center_x_px - radius_px
    top_px = center_y_px - radius_px
    width_box = radius_px * 2.0
    height_box = radius_px * 2.0
    if left_px < 0.0 or top_px < 0.0 or (left_px + width_box) > display_width_px or (top_px + height_box) > display_height_px:
        raise PhoneAppError(
            f"seed target circle (r={radius_px:.1f} px at {center_x_px:.1f},{center_y_px:.1f}) "
            f"extends outside display frame {display_width_px}x{display_height_px}"
        )

    doc: dict[str, Any] = {
        "schema_version": 1,
        "fixture_id": fixture_id,
        "seed": {
            "timestamp_s": round(float(timestamp_s), 6),
            "frame_index": int(frame_index),
            "target": {
                "center": {
                    "x_px": round(float(center_x_px), 4),
                    "y_px": round(float(center_y_px), 4),
                },
                "radius_px": round(float(radius_px), 4),
            },
            "coordinate_space": "display_top_left",
            "source_rotation_deg": int(rotation_deg),
            "selection_confidence": 1.0,
            "notes": "Confirmed by human tap on mobile web UI (research/phone-ui).",
        },
    }
    schema = schema_check.load_schema(SEED_SCHEMA_PATH)
    errors = schema_check.validate_document(doc, schema)
    if errors:
        raise PhoneAppError(f"seed failed schema validation: {'; '.join(errors)}")
    return doc


def upward_rep_preview(analysis: dict[str, Any], min_rise_m: float = PREVIEW_MIN_RISE_M) -> list[dict[str, Any]]:
    """Compute non-authoritative upward rep preview (ADR-0012 metric interval semantics)."""
    kinematics = (analysis.get("derived") or {}).get("kinematics") or {}
    samples = list(kinematics.get("samples") or [])
    reps: list[dict[str, Any]] = []
    index = 0
    while index < len(samples):
        vy = samples[index].get("vy_mps")
        if vy is None or vy <= 0.0 or index == 0:
            index += 1
            continue
        start = index
        while index < len(samples) and samples[index].get("vy_mps") is not None and samples[index]["vy_mps"] > 0.0:
            index += 1
        before = samples[start - 1]
        last = samples[index - 1]
        rise_m = float(last["y_m"]) - float(before["y_m"])
        duration_s = float(last["timestamp_s"]) - float(before["timestamp_s"])
        if rise_m >= min_rise_m and duration_s > 0.0:
            peak_sample = max(samples[start:index], key=lambda s: (float(s["vy_mps"]), -float(s["timestamp_s"])))
            reps.append(
                {
                    "rep_number": len(reps) + 1,
                    "start_s": round(float(before["timestamp_s"]), 4),
                    "end_s": round(float(last["timestamp_s"]), 4),
                    "duration_s": round(duration_s, 4),
                    "upward_rom_m": round(rise_m, 4),
                    "mean_upward_velocity_mps": round(rise_m / duration_s, 4),
                    "peak_upward_velocity_mps": round(float(peak_sample["vy_mps"]), 4),
                    "peak_timestamp_s": round(float(peak_sample["timestamp_s"]), 4),
                }
            )
    return reps


def run_phone_analysis(
    *,
    video_path: Path,
    seed_doc: dict[str, Any],
    plate_diameter_m: float,
    tracker: str,
    filter_preset: str,
    exercise: str,
    work_dir: Path,
    cli_prefix: list[str] | None = None,
) -> dict[str, Any]:
    """Run canonical `openbar-cli analyze` and `openbar-cli render` on a seeded clip."""
    if tracker not in TRACKERS:
        raise PhoneAppError(f"unsupported tracker {tracker!r}; choose from {TRACKERS}")
    if filter_preset not in FILTERS:
        raise PhoneAppError(f"unsupported filter preset {filter_preset!r}; choose from {FILTERS}")
    if exercise not in EXERCISES:
        raise PhoneAppError(f"unsupported exercise {exercise!r}; choose from {EXERCISES}")
    if not math.isfinite(plate_diameter_m) or plate_diameter_m <= 0.0:
        raise PhoneAppError(f"plate_diameter_m must be finite and positive, got {plate_diameter_m}")

    work_dir.mkdir(parents=True, exist_ok=True)
    fixture_id = seed_doc["fixture_id"]
    seed_path = work_dir / f"{fixture_id}.manual-target-seed-v1.json"
    analysis_path = work_dir / f"{fixture_id}.{tracker}.analysis-v1.json"
    support_path = work_dir / f"{fixture_id}.{tracker}.recording-support-v1.json"
    timings_path = work_dir / f"{fixture_id}.{tracker}.timings-v1.json"
    svg_path = work_dir / f"{fixture_id}.{tracker}.diagnostic.svg"

    seed_path.write_text(json.dumps(seed_doc, indent=2) + "\n", encoding="utf-8")

    if filter_preset == "savitzky-golay-0.15s":
        filter_flags = ["--filter", "savitzky-golay", "--filter-window-s", "0.15", "--filter-polynomial-order", "2", "--filter-max-gap-s", "0.2"]
    elif filter_preset == "savitzky-golay-7":
        filter_flags = ["--filter", "savitzky-golay", "--filter-window", "7", "--filter-polynomial-order", "2", "--filter-max-gap-s", "0.2"]
    else:
        filter_flags = ["--filter", "raw"]
    prefix = cli_prefix if cli_prefix is not None else resolve_openbar_cli()

    analyze_cmd = [
        *prefix,
        "analyze",
        "--video",
        str(video_path.resolve()),
        "--camera-view",
        "side",
        "--camera-movement",
        "fixed",
        "--seed",
        str(seed_path.resolve()),
        "--plate-diameter-m",
        str(plate_diameter_m),
        "--tracker",
        tracker,
        *filter_flags,
        "--kinematics-max-gap-s",
        "0.2",
        "--kinematics-min-confidence",
        "0",
        "--output",
        str(analysis_path.resolve()),
        "--recording-support-output",
        str(support_path.resolve()),
        "--timings-output",
        str(timings_path.resolve()),
        "--force",
    ]
    analyze_proc = subprocess.run(
        analyze_cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if analyze_proc.returncode != 0:
        raise PhoneAppError(f"openbar-cli analyze failed: {analyze_proc.stderr.strip()[-500:]}")

    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis_schema = schema_check.load_schema(ANALYSIS_SCHEMA_PATH)
    schema_errors = schema_check.validate_document(analysis, analysis_schema)
    if schema_errors:
        raise PhoneAppError(f"analysis output failed schema check: {'; '.join(schema_errors)}")

    support = json.loads(support_path.read_text(encoding="utf-8"))
    timings = json.loads(timings_path.read_text(encoding="utf-8"))

    render_cmd = [
        *prefix,
        "render",
        "--analysis",
        str(analysis_path.resolve()),
        "--video",
        str(video_path.resolve()),
        "--frame-timestamp-s",
        str(seed_doc["seed"]["timestamp_s"]),
        "--output",
        str(svg_path.resolve()),
        "--force",
    ]
    render_proc = subprocess.run(
        render_cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    svg_text = svg_path.read_text(encoding="utf-8") if render_proc.returncode == 0 and svg_path.is_file() else None

    raw_obs = analysis.get("raw_observations") or []
    tracked_count = sum(1 for o in raw_obs if o.get("tracking_state") == "tracked")
    low_conf_count = sum(1 for o in raw_obs if o.get("tracking_state") == "low_confidence")
    lost_count = sum(1 for o in raw_obs if o.get("tracking_state") == "lost")
    reps = upward_rep_preview(analysis)

    return {
        "fixture_id": fixture_id,
        "exercise": exercise,
        "tracker": analysis["provenance"]["tracker"]["id"],
        "filter": filter_preset,
        "plate_diameter_m": plate_diameter_m,
        "observation_counts": {
            "total": len(raw_obs),
            "tracked": tracked_count,
            "low_confidence": low_conf_count,
            "lost": lost_count,
        },
        "recording_support": support,
        "timings": timings,
        "latency_budget": {
            "reference_phone": "Xiaomi 14T Pro (rothko)",
            "median_budget_s": LATENCY_MEDIAN_BUDGET_S,
            "max_budget_s": LATENCY_MAX_BUDGET_S,
            "within_single_run_median_budget": float(timings.get("total_s", math.inf)) < LATENCY_MEDIAN_BUDGET_S,
            "within_single_run_max_budget": float(timings.get("total_s", math.inf)) < LATENCY_MAX_BUDGET_S,
            "evidence_class": "NON-QUALIFYING DIAGNOSTIC",
        },
        "upward_reps_preview": reps,
        "rep_contract_note": (
            "Upward velocity preview (ADR-0012 concentric-reps@1 metric interval semantics; "
            "unvalidated preview, non-authoritative)."
        ),
        "diagnostic_svg": svg_text,
        "paths": {
            "seed": str(seed_path),
            "analysis": str(analysis_path),
            "recording_support": str(support_path),
            "timings": str(timings_path),
            "diagnostic_svg": str(svg_path) if svg_text else None,
        },
    }


MOBILE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>OpenBar Phone Harness (Xiaomi 14T Pro)</title>
<style>
  :root {
    --bg: #0d1117;
    --panel: #161b22;
    --border: #30363d;
    --text: #e6edf3;
    --muted: #8b949e;
    --accent: #2f81f7;
    --ok: #3fb950;
    --warn: #d29922;
    --err: #f85149;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 12px;
    background: var(--bg); color: var(--text);
    font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  header {
    padding: 10px 12px; border: 1px solid var(--border);
    background: var(--panel); border-radius: 10px; margin-bottom: 12px;
  }
  header h1 { margin: 0 0 4px; font-size: 18px; }
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: 12px; font-weight: 600; background: #3a2a10; color: #ffb020;
    border: 1px solid #ffb020;
  }
  .card {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; padding: 12px; margin-bottom: 12px;
  }
  .card h2 { margin: 0 0 8px; font-size: 16px; }
  label { display: block; font-size: 13px; color: var(--muted); margin-top: 8px; }
  input, select, button {
    width: 100%; padding: 10px 12px; margin-top: 4px;
    border-radius: 8px; border: 1px solid var(--border);
    background: #0d1117; color: var(--text); font-size: 15px;
  }
  button.primary {
    background: var(--accent); border-color: var(--accent);
    color: #fff; font-weight: 600; margin-top: 12px; cursor: pointer;
  }
  button.primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  #canvasWrap {
    position: relative; width: 100%; overflow: hidden;
    border: 1px solid var(--border); border-radius: 8px; background: #000;
    touch-action: none;
  }
  canvas { display: block; width: 100%; height: auto; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
  th, td { border: 1px solid var(--border); padding: 6px 8px; text-align: left; }
  th { background: #0d1117; color: var(--muted); }
  .status-err { color: var(--err); font-weight: 600; }
  .status-ok { color: var(--ok); font-weight: 600; }
  #svgContainer svg { width: 100%; height: auto; border-radius: 8px; }
</style>
</head>
<body>
<header>
  <h1>OpenBar Mobile Test Harness</h1>
  <span class="badge">RESEARCH / DOGFOODING — NOT M1 PRODUCTION</span>
  <div style="font-size:12px;color:var(--muted);margin-top:4px;">
    Target: Xiaomi 14T Pro (rothko) · Native Rust Core + ADR-0006 FFmpeg
  </div>
</header>

<section class="card">
  <h2>1. Select or Record Lift Video</h2>
  <label>Choose video from phone gallery / camera</label>
  <input type="file" id="videoFile" accept="video/*">
  <div class="row">
    <div>
      <label>Seed frame time (s)</label>
      <input type="number" id="atSeconds" value="0.0" min="0" step="0.1">
    </div>
    <div>
      <label>Exercise (metadata)</label>
      <select id="exercise">
        <option value="back_squat">Back squat (Stage 1)</option>
        <option value="snatch">Snatch (candidate)</option>
        <option value="clean">Clean (candidate)</option>
        <option value="deadlift">Deadlift</option>
        <option value="bench_press">Bench press</option>
        <option value="other">Other</option>
      </select>
    </div>
  </div>
  <button class="primary" id="btnLoadFrame">Load Seed Frame</button>
  <div id="probeStatus" style="margin-top:8px;font-size:13px;color:var(--muted);"></div>
</section>

<section class="card" id="seedSection" style="display:none;">
  <h2>2. Tap Plate Centre &amp; Adjust Radius</h2>
  <p style="margin:0 0 8px;font-size:13px;color:var(--muted);">
    Tap the plate centre on the frame below. Adjust the radius slider so the orange circle matches the outer plate rim (keep 4×radius support window inside the frame for <code>csrt-gray</code>).
  </p>
  <div id="canvasWrap">
    <canvas id="seedCanvas"></canvas>
  </div>
  <div class="row">
    <div>
      <label>Plate radius (px): <span id="radiusLabel">24</span></label>
      <input type="range" id="radiusSlider" min="4" max="125" step="0.5" value="24">
    </div>
    <div>
      <label>Plate diameter (m)</label>
      <input type="number" id="plateDiameter" value="0.45" min="0.10" max="1.0" step="0.01">
    </div>
  </div>
  <div class="row">
    <div>
      <label>Rust Tracker (#57)</label>
      <select id="tracker">
        <option value="csrt-gray">csrt-gray (CSRT grayscale)</option>
        <option value="template">template (M0 baseline)</option>
        <option value="contrast">contrast (M0 baseline)</option>
      </select>
    </div>
    <div>
      <label>Filter</label>
      <select id="filterPreset">
        <option value="savitzky-golay-0.15s">Savitzky-Golay (0.15 s, order 2 — 30/60 fps)</option>
        <option value="savitzky-golay-7">Savitzky-Golay (7 samples, order 2)</option>
        <option value="raw">Raw identity</option>
      </select>
    </div>
  </div>
  <button class="primary" id="btnAnalyze">Run OpenBar Analysis</button>
  <div id="analyzeStatus" style="margin-top:8px;font-size:13px;"></div>
</section>

<section class="card" id="resultSection" style="display:none;">
  <h2>3. Canonical Analysis &amp; Upward Velocity Preview</h2>
  <div id="summaryBox"></div>
  <h3 style="font-size:14px;margin:12px 0 4px;">Upward Rep Preview (ADR-0012 metric interval, unvalidated)</h3>
  <div id="repBox"></div>
  <h3 style="font-size:14px;margin:12px 0 4px;">Stage Timings (#179 diagnostic vs 5.0 s median / 8.0 s max budget)</h3>
  <div id="timingBox"></div>
  <h3 style="font-size:14px;margin:12px 0 4px;">Deterministic Diagnostic SVG (ADR-0013 raw observations)</h3>
  <div id="svgContainer"></div>
</section>

<script>
let currentProbe = null;
let frameImg = new Image();
let centerPx = { x: 0, y: 0 };

const canvas = document.getElementById('seedCanvas');
const ctx = canvas.getContext('2d');
const radiusSlider = document.getElementById('radiusSlider');
const radiusLabel = document.getElementById('radiusLabel');

function drawSeedOverlay() {
  if (!currentProbe || !frameImg.complete) return;
  const w = currentProbe.display_width_px;
  const h = currentProbe.display_height_px;
  canvas.width = w;
  canvas.height = h;
  ctx.drawImage(frameImg, 0, 0, w, h);

  const r = parseFloat(radiusSlider.value);
  const cx = centerPx.x + 0.5;
  const cy = centerPx.y + 0.5;

  ctx.save();
  ctx.strokeStyle = '#ffb020';
  ctx.lineWidth = Math.max(2, w / 240);
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.stroke();

  ctx.fillStyle = '#ffb020';
  ctx.beginPath();
  ctx.arc(cx, cy, Math.max(3, w / 160), 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

canvas.addEventListener('pointerdown', (ev) => {
  if (!currentProbe) return;
  const rect = canvas.getBoundingClientRect();
  const scaleX = currentProbe.display_width_px / rect.width;
  const scaleY = currentProbe.display_height_px / rect.height;
  centerPx.x = Math.max(0, Math.min(currentProbe.display_width_px - 1, (ev.clientX - rect.left) * scaleX - 0.5));
  centerPx.y = Math.max(0, Math.min(currentProbe.display_height_px - 1, (ev.clientY - rect.top) * scaleY - 0.5));
  drawSeedOverlay();
});

radiusSlider.addEventListener('input', () => {
  radiusLabel.textContent = radiusSlider.value;
  drawSeedOverlay();
});

document.getElementById('btnLoadFrame').addEventListener('click', async () => {
  const fileInput = document.getElementById('videoFile');
  const status = document.getElementById('probeStatus');
  if (!fileInput.files.length) {
    status.textContent = 'Please select a video file first.';
    return;
  }
  status.textContent = 'Probing PTS timeline & extracting seed frame...';
  const file = fileInput.files[0];
  const atS = document.getElementById('atSeconds').value || '0';
  const qs = new URLSearchParams({ name: file.name, at_s: atS });

  try {
    const resp = await fetch('/api/probe-frame?' + qs.toString(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'Probe failed');
    currentProbe = data;
    centerPx = {
      x: Math.floor(data.display_width_px / 2),
      y: Math.floor(data.display_height_px / 2),
    };
    const maxR = Math.min(125, Math.floor(Math.min(data.display_width_px, data.display_height_px) / 4));
    radiusSlider.max = Math.max(8, maxR);
    radiusSlider.value = Math.min(24, Math.max(6, Math.floor(maxR * 0.6)));
    radiusLabel.textContent = radiusSlider.value;

    frameImg.onload = () => {
      document.getElementById('seedSection').style.display = 'block';
      drawSeedOverlay();
    };
    frameImg.src = 'data:image/png;base64,' + data.frame_png_base64;
    status.textContent = `${data.video_name} · ${data.display_width_px}×${data.display_height_px} (rot ${data.rotation_deg}°) · ${data.frame_count} frames (${data.duration_s}s) · seed frame #${data.frame_index} at ${data.timestamp_s}s`;
  } catch (err) {
    status.innerHTML = `<span class="status-err">${err.message}</span>`;
  }
});

document.getElementById('btnAnalyze').addEventListener('click', async () => {
  if (!currentProbe) return;
  const status = document.getElementById('analyzeStatus');
  const btn = document.getElementById('btnAnalyze');
  btn.disabled = true;
  status.textContent = 'Running native openbar-cli analyze + render...';

  const payload = {
    video_path: currentProbe.video_path,
    fixture_id: currentProbe.fixture_id,
    source_video_sha256: currentProbe.source_video_sha256,
    encoded_width_px: currentProbe.encoded_width_px,
    encoded_height_px: currentProbe.encoded_height_px,
    rotation_deg: currentProbe.rotation_deg,
    display_width_px: currentProbe.display_width_px,
    display_height_px: currentProbe.display_height_px,
    timestamp_s: currentProbe.timestamp_s,
    frame_index: currentProbe.frame_index,
    center_x_px: centerPx.x,
    center_y_px: centerPx.y,
    radius_px: parseFloat(radiusSlider.value),
    plate_diameter_m: parseFloat(document.getElementById('plateDiameter').value),
    tracker: document.getElementById('tracker').value,
    filter_preset: document.getElementById('filterPreset').value,
    exercise: document.getElementById('exercise').value,
  };

  try {
    const resp = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const res = await resp.json();
    if (!resp.ok) throw new Error(res.error || 'Analysis failed');

    status.innerHTML = '<span class="status-ok">Analysis complete.</span>';
    document.getElementById('resultSection').style.display = 'block';

    const counts = res.observation_counts;
    const sup = res.recording_support.status;
    const totalS = res.timings.total_s.toFixed(3);
    const budgetOk = res.latency_budget.within_single_run_median_budget;

    document.getElementById('summaryBox').innerHTML = `
      <table>
        <tr><th>Fixture ID</th><td><code>${res.fixture_id}</code></td><th>Tracker</th><td><code>${res.tracker}</code></td></tr>
        <tr><th>Samples</th><td>${counts.tracked} tracked · ${counts.low_confidence} low-conf · ${counts.lost} lost (${counts.total} total)</td>
            <th>Recording support</th><td><code>${sup}</code></td></tr>
        <tr><th>Total analyze time</th><td><strong>${totalS} s</strong> (${budgetOk ? '< 5.0 s target' : '>= 5.0 s target'})</td>
            <th>Evidence class</th><td><code>${res.latency_budget.evidence_class}</code></td></tr>
      </table>
    `;

    const reps = res.upward_reps_preview;
    if (!reps.length) {
      document.getElementById('repBox').innerHTML = '<p style="color:var(--muted);font-size:13px;">No upward reps >= 0.10 m detected.</p>';
    } else {
      const rows = reps.map(r => `
        <tr>
          <td>${r.rep_number}</td>
          <td>${r.start_s.toFixed(2)}–${r.end_s.toFixed(2)} (${r.duration_s.toFixed(2)}s)</td>
          <td>${r.upward_rom_m.toFixed(3)} m</td>
          <td><strong>${r.mean_upward_velocity_mps.toFixed(3)} m/s</strong></td>
          <td>${r.peak_upward_velocity_mps.toFixed(3)} m/s</td>
        </tr>
      `).join('');
      document.getElementById('repBox').innerHTML = `
        <table>
          <tr><th>Rep</th><th>Metric Interval</th><th>Upward ROM</th><th>Mean Upward Vel</th><th>Peak Upward Vel</th></tr>
          ${rows}
        </table>
      `;
    }

    const stageRows = Object.entries(res.timings.stages_s || {})
      .map(([k, v]) => `<tr><td><code>${k}</code></td><td>${Number(v).toFixed(4)} s</td></tr>`)
      .join('');
    document.getElementById('timingBox').innerHTML = `
      <table>
        <tr><th>Stage</th><th>Duration</th></tr>
        ${stageRows}
        <tr><th>Total</th><th>${totalS} s</th></tr>
      </table>
    `;

    document.getElementById('svgContainer').innerHTML = res.diagnostic_svg || '<p>No SVG rendered.</p>';
  } catch (err) {
    status.innerHTML = `<span class="status-err">${err.message}</span>`;
  } finally {
    btn.disabled = false;
  }
});
</script>
</body>
</html>
"""


def make_handler(work_dir: Path, cli_prefix: list[str]) -> type[http.server.BaseHTTPRequestHandler]:
    class PhoneRequestHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            pass

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path in ("/", "/index.html"):
                body = MOBILE_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            try:
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path == "/api/probe-frame":
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0:
                        self._send_json(400, {"error": "empty video upload body"})
                        return
                    params = urllib.parse.parse_qs(parsed.query)
                    raw_name = (params.get("name") or ["upload.mp4"])[0]
                    at_s = float((params.get("at_s") or ["0.0"])[0])
                    filename = Path(raw_name).name
                    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "upload.mp4"
                    uploads_dir = work_dir / "uploads"
                    uploads_dir.mkdir(parents=True, exist_ok=True)
                    dest_path = uploads_dir / safe_name
                    dest_path.write_bytes(self.rfile.read(length))

                    result = probe_and_extract_seed_frame(dest_path, work_dir / "frames", at_s=at_s)
                    self._send_json(200, result)
                    return

                if parsed.path == "/api/analyze":
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    seed_doc = build_manual_seed_document(
                        fixture_id=payload["fixture_id"],
                        source_video_sha256=payload["source_video_sha256"],
                        encoded_width_px=int(payload["encoded_width_px"]),
                        encoded_height_px=int(payload["encoded_height_px"]),
                        rotation_deg=int(payload["rotation_deg"]),
                        display_width_px=int(payload["display_width_px"]),
                        display_height_px=int(payload["display_height_px"]),
                        timestamp_s=float(payload["timestamp_s"]),
                        frame_index=int(payload["frame_index"]),
                        center_x_px=float(payload["center_x_px"]),
                        center_y_px=float(payload["center_y_px"]),
                        radius_px=float(payload["radius_px"]),
                    )
                    result = run_phone_analysis(
                        video_path=Path(payload["video_path"]),
                        seed_doc=seed_doc,
                        plate_diameter_m=float(payload.get("plate_diameter_m", 0.45)),
                        tracker=str(payload.get("tracker", "csrt-gray")),
                        filter_preset=str(payload.get("filter_preset", "savitzky-golay-0.15s")),
                        exercise=str(payload.get("exercise", "back_squat")),
                        work_dir=work_dir / "runs",
                        cli_prefix=cli_prefix,
                    )
                    self._send_json(200, result)
                    return

                self._send_json(404, {"error": "unknown endpoint"})
            except Exception as error:
                self._send_json(400, {"error": str(error)})

    return PhoneRequestHandler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (use 0.0.0.0 for LAN access)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=ROOT / "target" / "phone-ui",
        help="Directory for uploaded clips, seeds, and analysis artifacts",
    )
    parser.add_argument(
        "--binary",
        type=Path,
        default=None,
        help="Optional path to prebuilt openbar-cli release binary",
    )
    args = parser.parse_args(argv)

    cli_prefix = resolve_openbar_cli(args.binary)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    handler = make_handler(args.work_dir.resolve(), cli_prefix)
    server = http.server.ThreadingHTTPServer((args.host, args.port), handler)
    print(f"OpenBar Phone UI listening on http://{args.host}:{args.port} (work_dir={args.work_dir})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
