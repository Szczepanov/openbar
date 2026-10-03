"""`report.html` for one VBT session (#95): one self-contained, deterministic page.

Everything shown is read from the run's outputs (seed, prediction, analysis-v1, scale-reference
report); no value is recomputed except the labelled per-rep PREVIEW below. Tracking-check crops come
from frames decoded by label_package.py (the only decode path) and are embedded as PNG data URIs.
No wall-clock time is written, so identical inputs give identical bytes.

Per-rep preview (not authoritative; rep segmentation and VBT metrics belong to the recommender):
a rep is a maximal run of consecutive kinematics samples with vy_mps > 0 (a null velocity, i.e. a gap
or the first sample, ends a run) whose rise from the sample before the run is at least
PREVIEW_MIN_RISE_M. Mean concentric velocity = rise / elapsed time over that span; peak = the
largest vy_mps in the run. Stick-corrected values multiply by the stick/plate scale ratio from the
scale-reference report, because every velocity scales linearly with metres per pixel.
"""
from __future__ import annotations

import base64
import html
import json
import math
from pathlib import Path
from typing import Any, Callable

PREVIEW_MIN_RISE_M = 0.10
CROP_FRAME_OFFSETS = (-2, 0, 2)
CROP_HALF_SIZE_RADII = 1.6
CROP_OUTPUT_PX = 240
Cropper = Callable[[Path, int, int, int, int], bytes]


def default_cropper(frame: Path, left: int, top: int, right: int, bottom: int) -> bytes:
    import cv2  # research venv; imported only when crops are made

    image = cv2.imread(str(frame), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"cannot read {frame.name}")
    crop = image[top:bottom, left:right]
    size = (CROP_OUTPUT_PX, max(1, round(CROP_OUTPUT_PX * crop.shape[0] / crop.shape[1])))
    resized = cv2.resize(crop, size, interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".png", resized)
    if not ok:
        raise ValueError(f"cannot encode a crop of {frame.name}")
    return encoded.tobytes()


# --- Data read from the outputs ------------------------------------------------------------------

def kinematics_samples(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    kinematics = analysis.get("derived", {}).get("kinematics") or {}
    return list(kinematics.get("samples") or [])


def preview_reps(samples: list[dict[str, Any]], min_rise_m: float = PREVIEW_MIN_RISE_M) -> list[dict[str, float]]:
    reps = []
    index = 0
    while index < len(samples):
        vy = samples[index].get("vy_mps")
        if vy is None or vy <= 0 or index == 0:
            index += 1
            continue
        start = index
        while index < len(samples) and samples[index].get("vy_mps") is not None and samples[index]["vy_mps"] > 0:
            index += 1
        before, last = samples[start - 1], samples[index - 1]
        rise = last["y_m"] - before["y_m"]
        elapsed = last["timestamp_s"] - before["timestamp_s"]
        if rise >= min_rise_m and elapsed > 0:
            reps.append({"start_s": before["timestamp_s"], "end_s": last["timestamp_s"], "rise_m": rise,
                         "mean_mps": rise / elapsed,
                         "peak_mps": max(sample["vy_mps"] for sample in samples[start:index])})
    return reps


def peak_sample(samples: list[dict[str, Any]]) -> dict[str, Any] | None:
    moving = [sample for sample in samples if sample.get("vy_mps") is not None]
    return max(moving, key=lambda sample: (sample["vy_mps"], -sample["timestamp_s"])) if moving else None


def observation_at(analysis: dict[str, Any], frame_index: int) -> dict[str, Any] | None:
    return next((obs for obs in analysis.get("raw_observations", []) if obs.get("frame_index") == frame_index), None)


def crop_frames(analysis: dict[str, Any], frame_count: int) -> list[int]:
    """Frame indices around the peak upward velocity (empty without kinematics)."""
    peak = peak_sample(kinematics_samples(analysis))
    if peak is None:
        return []
    observation = next((obs for obs in analysis.get("raw_observations", [])
                        if obs.get("timestamp_s") == peak["timestamp_s"]), None)
    if observation is None or observation.get("frame_index") is None:
        return []
    centre = observation["frame_index"]
    return sorted({min(max(centre + offset, 0), frame_count - 1) for offset in CROP_FRAME_OFFSETS})


def make_crop(frame: Path, frame_index: int, analysis: dict[str, Any], seed: dict[str, Any],
              size: tuple[int, int], cropper: Cropper) -> dict[str, Any]:
    target = seed["seed"]["target"]
    radius = float(target["radius_px"])
    observation = observation_at(analysis, frame_index)
    measurement = (observation or {}).get("measurement")
    centre = (measurement["x_px"], measurement["y_px"]) if measurement else (target["center"]["x_px"],
                                                                             target["center"]["y_px"])
    half = CROP_HALF_SIZE_RADII * radius
    left = max(0, int(math.floor(centre[0] + 0.5 - half)))
    top = max(0, int(math.floor(centre[1] + 0.5 - half)))
    right = min(size[0], int(math.ceil(centre[0] + 0.5 + half)))
    bottom = min(size[1], int(math.ceil(centre[1] + 0.5 + half)))
    png = cropper(frame, left, top, right, bottom)
    return {"frame_index": frame_index, "timestamp_s": (observation or {}).get("timestamp_s"),
            "state": (observation or {}).get("tracking_state", "no observation"),
            "png": base64.b64encode(png).decode("ascii"), "box": (left, top, right, bottom),
            "centre": centre if measurement else None, "radius": radius}


# --- Rendering -----------------------------------------------------------------------------------

def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def num(value: float | None, digits: int) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def velocity_svg(samples: list[dict[str, Any]], reps: list[dict[str, float]]) -> str:
    points = [(s["timestamp_s"], s["vy_mps"]) for s in samples if s.get("vy_mps") is not None]
    if not points:
        return "<p class='muted'>No velocity samples.</p>"
    width, height, pad = 720, 220, 36
    t0, t1 = samples[0]["timestamp_s"], samples[-1]["timestamp_s"]
    low = min(0.0, min(v for _, v in points))
    high = max(0.5, max(v for _, v in points))
    span_t = max(t1 - t0, 1e-9)

    def xy(t: float, v: float) -> str:
        x = pad + (t - t0) / span_t * (width - 2 * pad)
        y = height - pad - (v - low) / (high - low) * (height - 2 * pad)
        return f"{x:.1f},{y:.1f}"

    paths, current = [], []
    for sample in samples:
        if sample.get("vy_mps") is None:
            if len(current) > 1:
                paths.append(current)
            current = []
        else:
            current.append(xy(sample["timestamp_s"], sample["vy_mps"]))
    if len(current) > 1:
        paths.append(current)
    zero = xy(t0, 0.0).split(",")[1]
    bands = "".join(
        f"<rect x='{xy(rep['start_s'], high).split(',')[0]}' y='{pad}' "
        f"width='{float(xy(rep['end_s'], high).split(',')[0]) - float(xy(rep['start_s'], high).split(',')[0]):.1f}' "
        f"height='{height - 2 * pad}' fill='#3ecf8e22'/>" for rep in reps)
    lines = "".join(f"<polyline fill='none' stroke='#4cc2ff' stroke-width='1.5' points='{' '.join(path)}'/>"
                    for path in paths)
    return (f"<svg viewBox='0 0 {width} {height}' width='100%' role='img' aria-label='vertical velocity'>"
            f"{bands}<line x1='{pad}' x2='{width - pad}' y1='{zero}' y2='{zero}' stroke='#666'/>{lines}"
            f"<text x='{pad}' y='14' fill='#9aa4b2' font-size='12'>vy (m/s, plate scale), {num(low, 2)} to "
            f"{num(high, 2)}; t {num(t0, 2)}–{num(t1, 2)} s; green bands: preview reps</text></svg>")


def crops_html(crops: list[dict[str, Any]]) -> str:
    if not crops:
        return "<p class='muted'>No crops (no velocity peak).</p>"
    cells = []
    for crop in crops:
        left, top, right, bottom = crop["box"]
        overlay = ""
        if crop["centre"] is not None:
            cx, cy = crop["centre"][0] + 0.5 - left, crop["centre"][1] + 0.5 - top
            overlay = (f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='{crop['radius']:.1f}' fill='none' stroke='#ffb020' "
                       f"stroke-width='{max(1.0, (right - left) / 160):.1f}'/>"
                       f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='{max(2.0, (right - left) / 80):.1f}' fill='#ffb020'/>")
        cells.append(
            f"<figure><svg viewBox='0 0 {right - left} {bottom - top}' width='{CROP_OUTPUT_PX}'>"
            f"<image href='data:image/png;base64,{crop['png']}' width='{right - left}' height='{bottom - top}'/>"
            f"{overlay}</svg><figcaption>frame {crop['frame_index']} · t={num(crop['timestamp_s'], 3)} s · "
            f"{esc(crop['state'])}</figcaption></figure>")
    return "<div class='crops'>" + "".join(cells) + "</div>"


def rep_table(reps: list[dict[str, float]], ratio: float | None) -> str:
    if not reps:
        return "<p class='muted'>No preview reps (no upward run rising at least " \
               f"{PREVIEW_MIN_RISE_M} m).</p>"
    head = ("<tr><th>Rep</th><th>Span (s)</th><th>Rise (m, plate)</th><th>Mean conc. (m/s, plate)</th>"
            "<th>Mean conc. (m/s, stick-corrected)</th><th>Peak (m/s, plate)</th>"
            "<th>Peak (m/s, stick-corrected)</th></tr>")
    rows = "".join(
        f"<tr><td>{index}</td><td>{num(rep['start_s'], 3)}–{num(rep['end_s'], 3)}</td><td>{num(rep['rise_m'], 3)}</td>"
        f"<td>{num(rep['mean_mps'], 3)}</td><td>{num(None if ratio is None else rep['mean_mps'] * ratio, 3)}</td>"
        f"<td>{num(rep['peak_mps'], 3)}</td><td>{num(None if ratio is None else rep['peak_mps'] * ratio, 3)}</td></tr>"
        for index, rep in enumerate(reps, start=1))
    return f"<table>{head}{rows}</table>"


def clip_section(clip: dict[str, Any]) -> str:
    analysis, prediction, seed = clip["analysis"], clip["prediction"], clip["seed"]["seed"]
    samples = prediction.get("samples", [])
    tracked = sum(sample.get("state") == "tracked" for sample in samples)
    lost = sum(sample.get("state") == "lost" for sample in samples)
    plate_scale = analysis["calibration"]["scale"]["metres_per_pixel"]
    scale_row = clip.get("scale_row")
    ratio = None if scale_row is None else scale_row["reference_to_plate_ratio"]
    stick_scale = None if scale_row is None else scale_row["reference"]["reference_scale_m_per_px"]
    kin = kinematics_samples(analysis)
    reps = preview_reps(kin)
    statuses = ", ".join(f"{item.replace('_', ' ')} {status}" for item, status in clip["statuses"].items())
    stick_text = "—" if stick_scale is None else f"{stick_scale:.6e} m/px"
    ratio_text = "—" if ratio is None else \
        f"{num(ratio['value'], 4)} ({num(ratio['lower'], 4)}–{num(ratio['upper'], 4)})"
    summary = (
        "<table>"
        f"<tr><th>Lift</th><td>{esc(clip['exercise'])}</td><th>Tracker</th><td>{esc(clip['tracker'])}</td></tr>"
        f"<tr><th>Seed</th><td>{num(seed['timestamp_s'], 3)} s, frame {esc(seed.get('frame_index'))}</td>"
        f"<th>Samples</th><td>{tracked} tracked, {lost} lost, {len(samples)} total</td></tr>"
        f"<tr><th>Plate scale</th><td>{plate_scale:.6e} m/px</td><th>Stick scale</th><td>{stick_text}</td></tr>"
        f"<tr><th>Stick / plate</th><td>{ratio_text}</td>"
        f"<th>Page items</th><td>{esc(statuses)}</td></tr></table>")
    return (f"<section><h2>{esc(clip['original_name'])} <span class='muted'>{esc(clip['fixture_id'])}</span></h2>"
            f"{summary}<h3>Vertical velocity</h3>{velocity_svg(kin, reps)}"
            f"<h3>Tracking check around the peak velocity</h3>{crops_html(clip['crops'])}"
            f"<h3>Per-rep PREVIEW (not authoritative)</h3>{rep_table(reps, None if ratio is None else ratio['value'])}"
            "</section>")


def ratio_table(clips: list[dict[str, Any]]) -> str:
    rows = []
    for clip in clips:
        row = clip.get("scale_row")
        if row is None:
            continue
        ratio = row["reference_to_plate_ratio"]
        rows.append(f"<tr><td>{esc(clip['original_name'])}</td><td>{esc(clip['exercise'])}</td>"
                    f"<td>{row['plate_scale_m_per_px']:.6e}</td><td>{row['reference']['reference_scale_m_per_px']:.6e}</td>"
                    f"<td>{num(ratio['value'], 4)} ({num(ratio['lower'], 4)}–{num(ratio['upper'], 4)})</td>"
                    f"<td>{'yes' if ratio['consistent_with_1'] else 'no'}</td></tr>")
    return ("<table><tr><th>Clip</th><th>Lift</th><th>Plate (m/px)</th><th>Stick (m/px)</th><th>Stick / plate</th>"
            "<th>Consistent with 1?</th></tr>" + "".join(rows) + "</table>")


STYLE = """
body { margin:0; background:#101216; color:#e9edf2; font:14px system-ui,sans-serif; }
main { max-width:1000px; margin:0 auto; padding:16px; } section { border-top:1px solid #303743; padding:8px 0 16px; }
table { border-collapse:collapse; margin:6px 0; } th, td { border:1px solid #303743; padding:4px 8px; text-align:left; }
.muted { color:#9aa4b2; font-size:12px; } .warn { background:#3a2a10; border:1px solid #ffb020; padding:8px; border-radius:6px; }
.crops { display:flex; flex-wrap:wrap; gap:8px; } figure { margin:0; } figcaption { font-size:12px; color:#9aa4b2; }
"""


def render_report(session_id: str, configuration: dict[str, Any], clips: list[dict[str, Any]],
                  skipped: list[str]) -> str:
    config_rows = "".join(
        f"<tr><th>{esc(key)}</th><td>{esc(value if isinstance(value, str) else json.dumps(value, sort_keys=True))}</td></tr>"
        for key, value in configuration.items())
    skipped_html = "" if not skipped else f"<p class='muted'>Skipped on the page: {esc(', '.join(skipped))}</p>"
    return (
        "<!doctype html>\n<html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>OpenBar VBT session {esc(session_id)}</title><style>{STYLE}</style></head><body><main>"
        f"<h1>VBT session {esc(session_id)}</h1><table>{config_rows}</table>{skipped_html}"
        "<p class='warn'>Velocities are analysis-v1 values (plate-diameter calibration). Stick-corrected values "
        "multiply them by the filmed-stick/plate scale ratio and are shown for comparison only; the stick is not "
        "an OpenBar calibration method. The per-rep tables are a non-authoritative PREVIEW (simple upward-run "
        "segmentation, see session_report.py); authoritative reps and VBT metrics come from the recommender.</p>"
        f"<h2>Scale ratio</h2>{ratio_table(clips)}"
        + "".join(clip_section(clip) for clip in clips)
        + "</main></body></html>\n"
    )
