#!/usr/bin/env python3
"""Independent filmed-length scale check for OpenBar validation evidence.

Evidence tooling only: never writes to analysis-v1 and never changes calibration semantics.
The package command delegates single-frame decoding/extraction to label_package.py, then adds a
two-endpoint reference page. The report command hashes and validates the video, analysis and exact
click-CSV bytes before producing deterministic JSON and Markdown.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import label_package

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_PAGE_TEMPLATE = Path(__file__).with_name("scale_reference_page.html")
REFERENCE_CONFIG_NAME = "reference-config.json"
REFERENCE_PAGE_NAME = "reference.html"
TOOL = {"name": "openbar-scale-reference", "version": "1"}
SCHEMA_VERSION = 1
ENDPOINT_PRECISION_PX = 1.0
LENGTH_UNCERTAINTY_PX = 2.0 * ENDPOINT_PRECISION_PX
EXPECTED_ANALYSIS_SCHEMA_VERSION = 1
EXPECTED_CALIBRATION_METHOD = "plate_diameter"
EXPECTED_CALIBRATION_METHOD_VERSION = 1
DEFAULT_REPORT_DIR = ROOT / "validation" / "private" / "scale-reference" / "reports"
CSV_COLUMNS = [
    "fixture_id", "source_video_sha256", "package_id", "frame_index", "timestamp_s",
    "width_px", "height_px", "known_length_m", "point_a_x_px", "point_a_y_px",
    "point_b_x_px", "point_b_y_px",
]
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PACKAGE_ID_RE = re.compile(r"^[0-9a-f]{16}$")
LABEL_CONFIG_RE = re.compile(r"const CONFIG = (\{.*?\});\n", re.S)


class ScaleReferenceError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ScaleReferenceError(message)


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ScaleReferenceError(f"{name} must be a finite number") from error
    if not math.isfinite(number):
        raise ScaleReferenceError(f"{name} must be a finite number")
    return number


def _positive(value: Any, name: str) -> float:
    number = _finite(value, name)
    _require(number > 0, f"{name} must be positive")
    return number


def _positive_int(value: Any, name: str) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value > 0,
        f"{name} must be a positive integer",
    )
    return value


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ScaleReferenceError(f"cannot read required file {path}") from error
    return digest.hexdigest()


def _reject_constant(name: str) -> Any:
    raise ScaleReferenceError(f"non-finite JSON number {name!r} is invalid")


def _finite_json_float(literal: str) -> float:
    value = float(literal)
    if not math.isfinite(value):
        raise ScaleReferenceError(f"JSON number {literal!r} is out of finite range")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScaleReferenceError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def loads_strict(data: bytes, source: str) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ScaleReferenceError(f"{source} is not valid UTF-8") from error
    try:
        return json.loads(
            text,
            parse_constant=_reject_constant,
            parse_float=_finite_json_float,
            object_pairs_hook=_unique_object,
        )
    except ScaleReferenceError:
        raise
    except ValueError as error:
        raise ScaleReferenceError(f"{source} is malformed JSON: {error}") from error


def read_json(path: Path) -> tuple[Any, bytes]:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ScaleReferenceError(f"cannot read {path}") from error
    return loads_strict(data, path.name), data


def measure_segment(
    *,
    known_length_m: float,
    point_a: dict[str, float],
    point_b: dict[str, float],
    width_px: int,
    height_px: int,
) -> dict[str, Any]:
    """Measure a clicked segment using ADR-0007's v1 point window.

    Each endpoint has bounded radial click error +/-1 px. The segment length therefore has a
    conservative +/-2 px bound. Because scale = known_length / length_px is nonlinear, propagate
    that bound through the reciprocal exactly instead of using a first-order approximation.
    """
    length_m = _positive(known_length_m, "known length")
    width = _positive_int(width_px, "width_px")
    height = _positive_int(height_px, "height_px")
    ax = _finite(point_a.get("x_px"), "point_a.x_px")
    ay = _finite(point_a.get("y_px"), "point_a.y_px")
    bx = _finite(point_b.get("x_px"), "point_b.x_px")
    by = _finite(point_b.get("y_px"), "point_b.y_px")

    for name, x_px, y_px in (("point_a", ax, ay), ("point_b", bx, by)):
        _require(
            0 <= x_px < width and 0 <= y_px < height,
            f"{name} ({x_px}, {y_px}) is outside ADR-0007 v1 point window [0,{width}) x [0,{height})",
        )

    length_px = math.hypot(bx - ax, by - ay)
    _require(math.isfinite(length_px), "segment length is non-finite")
    _require(length_px > 0, "zero-length reference segment is invalid")
    _require(
        length_px > LENGTH_UNCERTAINTY_PX,
        "reference segment is too short for the bounded click-uncertainty model",
    )
    reference_scale = length_m / length_px
    reference_scale_lower = length_m / (length_px + LENGTH_UNCERTAINTY_PX)
    reference_scale_upper = length_m / (length_px - LENGTH_UNCERTAINTY_PX)
    minus_uncertainty = reference_scale - reference_scale_lower
    plus_uncertainty = reference_scale_upper - reference_scale
    _require(
        all(
            math.isfinite(value) and value > 0
            for value in (
                reference_scale,
                reference_scale_lower,
                reference_scale_upper,
                minus_uncertainty,
                plus_uncertainty,
            )
        ),
        "reference scale interval is non-finite or non-positive",
    )
    return {
        "known_length_m": length_m,
        "point_a": {"x_px": ax, "y_px": ay},
        "point_b": {"x_px": bx, "y_px": by},
        "length_px": length_px,
        "endpoint_precision_px": ENDPOINT_PRECISION_PX,
        "length_uncertainty_px": LENGTH_UNCERTAINTY_PX,
        "reference_scale_m_per_px": reference_scale,
        "reference_scale_lower_m_per_px": reference_scale_lower,
        "reference_scale_upper_m_per_px": reference_scale_upper,
        "reference_scale_minus_uncertainty_m_per_px": minus_uncertainty,
        "reference_scale_plus_uncertainty_m_per_px": plus_uncertainty,
    }


def plate_scale_from_analysis(analysis: Any, fixture_id: str, source_sha256: str) -> float:
    _require(isinstance(analysis, dict), "analysis must be a JSON object")
    _require(
        analysis.get("schema_version") == EXPECTED_ANALYSIS_SCHEMA_VERSION,
        "analysis must be analysis-v1 (schema_version 1)",
    )
    identity = analysis.get("identity")
    _require(isinstance(identity, dict), "analysis.identity must be an object")
    _require(
        identity.get("fixture_id") == fixture_id,
        "analysis fixture does not match the package/video fixture",
    )
    _require(
        identity.get("source_sha256") == source_sha256,
        "analysis source_sha256 does not match the package/video bytes",
    )
    calibration = analysis.get("calibration")
    _require(isinstance(calibration, dict), "analysis.calibration must be an object")
    _require(
        calibration.get("method") == EXPECTED_CALIBRATION_METHOD,
        "analysis calibration method must be plate_diameter",
    )
    _require(
        calibration.get("method_version") == EXPECTED_CALIBRATION_METHOD_VERSION,
        "analysis calibration method_version must be 1",
    )
    try:
        value = calibration["scale"]["metres_per_pixel"]
    except (KeyError, TypeError) as error:
        raise ScaleReferenceError("analysis is missing calibration.scale.metres_per_pixel") from error
    return _positive(value, "analysis calibration.scale.metres_per_pixel")


def compare_to_plate(
    *,
    reference_scale_m_per_px: float,
    reference_scale_lower_m_per_px: float,
    reference_scale_upper_m_per_px: float,
    plate_scale_m_per_px: float,
) -> dict[str, Any]:
    reference = _positive(reference_scale_m_per_px, "reference_scale_m_per_px")
    lower = _positive(reference_scale_lower_m_per_px, "reference_scale_lower_m_per_px")
    upper = _positive(reference_scale_upper_m_per_px, "reference_scale_upper_m_per_px")
    _require(lower <= reference <= upper, "reference scale interval does not contain its nominal value")
    plate = _positive(plate_scale_m_per_px, "plate_scale_m_per_px")
    ratio = reference / plate
    ratio_lower = lower / plate
    ratio_upper = upper / plate
    minus_uncertainty = ratio - ratio_lower
    plus_uncertainty = ratio_upper - ratio
    _require(
        all(
            math.isfinite(value)
            for value in (ratio, ratio_lower, ratio_upper, minus_uncertainty, plus_uncertainty)
        ),
        "ratio interval is non-finite",
    )
    return {
        "value": ratio,
        "lower": ratio_lower,
        "upper": ratio_upper,
        "minus_uncertainty": minus_uncertainty,
        "plus_uncertainty": plus_uncertainty,
        "consistent_with_1": ratio_lower <= 1.0 <= ratio_upper,
    }


def parse_click_csv(data: bytes) -> tuple[dict[str, Any], str]:
    """Hash and parse exactly the same CSV bytes; no second file read is involved."""
    digest = _sha256_bytes(data)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ScaleReferenceError("click CSV is not valid UTF-8") from error
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ScaleReferenceError(f"malformed click CSV: {error}") from error
    _require(len(rows) == 2, "malformed click CSV: expected exactly one header and one data row")
    _require(
        rows[0] == CSV_COLUMNS,
        "malformed click CSV: columns do not match the scale-reference contract",
    )
    _require(
        len(rows[1]) == len(CSV_COLUMNS),
        "malformed click CSV: data row has the wrong number of columns",
    )
    raw = dict(zip(CSV_COLUMNS, rows[1]))
    _require(all(value != "" for value in raw.values()), "malformed click CSV: blank values are not allowed")
    try:
        frame_index = int(raw["frame_index"])
        width_px = int(raw["width_px"])
        height_px = int(raw["height_px"])
    except ValueError as error:
        raise ScaleReferenceError(
            "malformed click CSV: frame_index/width_px/height_px must be integers"
        ) from error
    _require(frame_index >= 0, "malformed click CSV: frame_index must be non-negative")
    _require(width_px > 0 and height_px > 0, "malformed click CSV: dimensions must be positive")
    _require(
        SHA256_RE.fullmatch(raw["source_video_sha256"]) is not None,
        "malformed click CSV: invalid video SHA-256",
    )
    _require(
        PACKAGE_ID_RE.fullmatch(raw["package_id"]) is not None,
        "malformed click CSV: invalid package_id",
    )
    parsed: dict[str, Any] = {
        "fixture_id": raw["fixture_id"],
        "source_video_sha256": raw["source_video_sha256"],
        "package_id": raw["package_id"],
        "frame_index": frame_index,
        "timestamp_s": _finite(raw["timestamp_s"], "click timestamp_s"),
        "width_px": width_px,
        "height_px": height_px,
        "known_length_m": _finite(raw["known_length_m"], "click known_length_m"),
        "point_a_x_px": _finite(raw["point_a_x_px"], "click point_a_x_px"),
        "point_a_y_px": _finite(raw["point_a_y_px"], "click point_a_y_px"),
        "point_b_x_px": _finite(raw["point_b_x_px"], "click point_b_x_px"),
        "point_b_y_px": _finite(raw["point_b_y_px"], "click point_b_y_px"),
    }
    _require(parsed["fixture_id"] != "", "malformed click CSV: fixture_id is blank")
    _require(parsed["timestamp_s"] >= 0, "malformed click CSV: timestamp_s must be non-negative")
    return parsed, digest


def validate_package_binding(
    metadata: Any,
    config: Any,
    click: dict[str, Any],
    fixture_id: str,
    source_video_sha256: str,
) -> None:
    _require(isinstance(metadata, dict), "package metadata must be an object")
    _require(isinstance(config, dict), "reference config must be an object")
    _require(
        metadata.get("fixture_id") == fixture_id,
        "package metadata fixture does not match the video fixture",
    )
    _require(
        config.get("fixture_id") == fixture_id,
        "reference config fixture does not match the video fixture",
    )
    _require(
        click.get("fixture_id") == fixture_id,
        "click CSV fixture does not match the video fixture",
    )
    for label, value in [
        ("package metadata", metadata.get("source_video_sha256")),
        ("reference config", config.get("source_video_sha256")),
        ("click CSV", click.get("source_video_sha256")),
    ]:
        _require(
            value == source_video_sha256,
            f"{label} video SHA-256 does not match the actual video bytes",
        )

    coordinate = metadata.get("coordinate_system")
    _require(isinstance(coordinate, dict), "package metadata coordinate_system must be an object")
    width_px = _positive_int(coordinate.get("width_px"), "package metadata width_px")
    height_px = _positive_int(coordinate.get("height_px"), "package metadata height_px")
    _require(
        config.get("width_px") == width_px and config.get("height_px") == height_px,
        "reference config dimensions do not match package metadata",
    )
    _require(
        click.get("width_px") == width_px and click.get("height_px") == height_px,
        "click CSV dimensions do not match package metadata",
    )

    package_id = config.get("package_id")
    _require(
        isinstance(package_id, str) and PACKAGE_ID_RE.fullmatch(package_id) is not None,
        "reference config package_id is invalid",
    )
    _require(
        click.get("package_id") == package_id,
        "click CSV package_id does not match the reference package",
    )

    frames = config.get("frames")
    _require(
        isinstance(frames, list) and len(frames) == 1 and isinstance(frames[0], dict),
        "reference config must contain exactly one frame",
    )
    frame = frames[0]
    _require(
        click.get("frame_index") == frame.get("frame_index"),
        "click CSV frame does not match the reference package",
    )
    frame_timestamp = _finite(frame.get("timestamp_s"), "reference frame timestamp_s")
    _require(
        click.get("timestamp_s") == frame_timestamp,
        "click CSV timestamp does not match the reference package",
    )
    known_length = _positive(config.get("known_length_m"), "reference config known length")
    _require(
        click.get("known_length_m") == known_length,
        "click CSV known length does not match the reference package",
    )


def _fixture_from_manifest(manifest: Any, fixture_id: str) -> dict[str, Any]:
    _require(
        isinstance(manifest, dict) and isinstance(manifest.get("fixtures"), list),
        "manifest must contain fixtures",
    )
    matches = [
        item for item in manifest["fixtures"]
        if isinstance(item, dict) and item.get("id") == fixture_id
    ]
    _require(
        len(matches) == 1,
        f"fixture {fixture_id!r} must appear exactly once in the manifest",
    )
    return matches[0]


def _reference_config_from_label_package(
    package_dir: Path,
    known_length_m: float,
) -> dict[str, Any]:
    try:
        page = (package_dir / "index.html").read_text(encoding="utf-8")
    except OSError as error:
        raise ScaleReferenceError("cannot read delegated label package page") from error
    match = LABEL_CONFIG_RE.search(page)
    _require(match is not None, "delegated label package does not contain its injected CONFIG")
    try:
        label_config = json.loads(match.group(1))
    except ValueError as error:
        raise ScaleReferenceError("delegated label package CONFIG is malformed") from error
    metadata, _ = read_json(package_dir / "metadata.json")
    _require(isinstance(metadata, dict), "delegated package metadata must be an object")
    frames = label_config.get("frames")
    _require(
        isinstance(frames, list) and len(frames) == 1,
        "scale reference requires exactly one delegated frame",
    )
    return {
        "fixture_id": label_config.get("fixture_id"),
        "source_video_sha256": metadata.get("source_video_sha256"),
        "package_id": label_config.get("package_id"),
        "width_px": label_config.get("width_px"),
        "height_px": label_config.get("height_px"),
        "known_length_m": known_length_m,
        "frames": frames,
    }


def build_reference_package(args: argparse.Namespace) -> Path:
    known_length_m = _positive(args.known_length_m, "known length")
    delegated = argparse.Namespace(
        manifest=args.manifest,
        fixture=args.fixture,
        step_s=None,
        frame_index=args.frame_index,
        at_s=args.at_s,
        start_s=None,
        end_s=None,
        include_frame=[],
        annotator_id=args.annotator_id,
        output_dir=args.output_dir,
    )
    try:
        package_dir = label_package.build(delegated)
    except label_package.PackageError as error:
        raise ScaleReferenceError(str(error)) from error

    config = _reference_config_from_label_package(package_dir, known_length_m)
    label_package.write_text(
        package_dir / REFERENCE_CONFIG_NAME,
        json.dumps(config, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )
    try:
        template = REFERENCE_PAGE_TEMPLATE.read_text(encoding="utf-8")
    except OSError as error:
        raise ScaleReferenceError(f"cannot read {REFERENCE_PAGE_TEMPLATE}") from error
    label_package.write_text(
        package_dir / REFERENCE_PAGE_NAME,
        label_package.render_page(template, config),
    )
    return package_dir


def evaluate_case(
    *,
    manifest: Any,
    analysis_path: Path,
    package_dir: Path,
    click_csv_path: Path,
) -> dict[str, Any]:
    metadata, _ = read_json(package_dir / "metadata.json")
    config, _ = read_json(package_dir / REFERENCE_CONFIG_NAME)
    _require(isinstance(config, dict), "reference config must be a JSON object")
    fixture_id = config.get("fixture_id")
    _require(
        isinstance(fixture_id, str) and fixture_id != "",
        "reference config fixture_id is invalid",
    )
    fixture = _fixture_from_manifest(manifest, fixture_id)
    try:
        media_rel = fixture["media"]["repository_path"]
        manifest_sha = fixture["media"]["sha256"]
    except (KeyError, TypeError) as error:
        raise ScaleReferenceError(f"fixture {fixture_id!r} is missing media identity") from error
    _require(
        isinstance(media_rel, str) and media_rel != "",
        "fixture media.repository_path is invalid",
    )
    _require(
        isinstance(manifest_sha, str) and SHA256_RE.fullmatch(manifest_sha) is not None,
        "fixture media.sha256 is invalid",
    )
    actual_video_sha = hash_file(ROOT / media_rel)
    _require(
        actual_video_sha == manifest_sha,
        "actual video bytes do not match the manifest SHA-256",
    )

    try:
        csv_bytes = click_csv_path.read_bytes()
    except OSError as error:
        raise ScaleReferenceError(f"cannot read click CSV {click_csv_path}") from error
    click, click_sha = parse_click_csv(csv_bytes)
    validate_package_binding(metadata, config, click, fixture_id, actual_video_sha)

    analysis, analysis_bytes = read_json(analysis_path)
    analysis_sha = _sha256_bytes(analysis_bytes)
    plate_scale = plate_scale_from_analysis(analysis, fixture_id, actual_video_sha)
    reference = measure_segment(
        known_length_m=click["known_length_m"],
        point_a={"x_px": click["point_a_x_px"], "y_px": click["point_a_y_px"]},
        point_b={"x_px": click["point_b_x_px"], "y_px": click["point_b_y_px"]},
        width_px=click["width_px"],
        height_px=click["height_px"],
    )
    reference["frame_index"] = click["frame_index"]
    reference["timestamp_s"] = click["timestamp_s"]
    comparison = compare_to_plate(
        reference_scale_m_per_px=reference["reference_scale_m_per_px"],
        reference_scale_lower_m_per_px=reference["reference_scale_lower_m_per_px"],
        reference_scale_upper_m_per_px=reference["reference_scale_upper_m_per_px"],
        plate_scale_m_per_px=plate_scale,
    )
    return {
        "fixture_id": fixture_id,
        "source_video_sha256": actual_video_sha,
        "analysis_sha256": analysis_sha,
        "click_csv_sha256": click_sha,
        "reference": reference,
        "plate_scale_m_per_px": plate_scale,
        "reference_to_plate_ratio": comparison,
    }


def _sorted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: row["fixture_id"])
    fixture_ids = [row["fixture_id"] for row in ordered]
    video_hashes = [row["source_video_sha256"] for row in ordered]
    _require(
        len(fixture_ids) == len(set(fixture_ids)),
        "report accepts at most one row per fixture",
    )
    _require(
        len(video_hashes) == len(set(video_hashes)),
        "report accepts at most one row per video",
    )
    return ordered


def render_json_report(rows: list[dict[str, Any]]) -> bytes:
    document = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "uncertainty_model": {
            "endpoint_precision_px": ENDPOINT_PRECISION_PX,
            "length_uncertainty_px": LENGTH_UNCERTAINTY_PX,
            "propagation": "bounded_endpoint_error_exact_reciprocal_interval",
        },
        "rows": _sorted_rows(rows),
    }
    try:
        return (
            json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ScaleReferenceError(
            f"report contains a non-serializable/non-finite value: {error}"
        ) from error


def _num(value: float) -> str:
    return format(value, ".12g")


def render_markdown_report(rows: list[dict[str, Any]]) -> bytes:
    ordered = _sorted_rows(rows)
    lines = [
        "# Scale-reference report",
        "",
        "Evidence only: this report does not alter analysis-v1 or plate calibration.",
        "",
        "Uncertainty model: each clicked endpoint has bounded radial precision of ±1 px; "
        "segment length is therefore bounded by ±2 px. That distance bound is propagated "
        "exactly through scale = known_length_m / length_px, producing an asymmetric scale "
        "interval. The plate scale is read unchanged from calibration.scale.metres_per_pixel; "
        "only reference click uncertainty is propagated into the ratio.",
        "",
        "| Fixture | Reference scale | Plate scale | Reference / plate ratio | Consistent with 1? |",
        "| --- | ---: | ---: | ---: | :---: |",
    ]
    for row in ordered:
        ref = row["reference"]
        ratio = row["reference_to_plate_ratio"]
        lines.append(
            f"| {row['fixture_id']} | "
            f"{_num(ref['reference_scale_m_per_px'])} "
            f"-{_num(ref['reference_scale_minus_uncertainty_m_per_px'])}/"
            f"+{_num(ref['reference_scale_plus_uncertainty_m_per_px'])} m/px | "
            f"{_num(row['plate_scale_m_per_px'])} m/px | "
            f"{_num(ratio['value'])} -{_num(ratio['minus_uncertainty'])}/"
            f"+{_num(ratio['plus_uncertainty'])} | "
            f"{'yes' if ratio['consistent_with_1'] else 'no'} |"
        )
    lines += ["", "## Provenance hashes", ""]
    for row in ordered:
        lines += [
            f"### {row['fixture_id']}",
            "",
            f"- video SHA-256: {row['source_video_sha256']}",
            f"- analysis SHA-256: {row['analysis_sha256']}",
            f"- click CSV SHA-256: {row['click_csv_sha256']}",
            "",
        ]
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def write_report(args: argparse.Namespace) -> Path:
    _require(
        len(args.analysis) == len(args.package_dir) == len(args.csv),
        "repeat --analysis, --package-dir and --csv the same number of times",
    )
    _require(len(args.analysis) > 0, "at least one report case is required")
    manifest, _ = read_json(args.manifest)
    rows = [
        evaluate_case(
            manifest=manifest,
            analysis_path=analysis,
            package_dir=package_dir,
            click_csv_path=csv_path,
        )
        for analysis, package_dir, csv_path in zip(
            args.analysis,
            args.package_dir,
            args.csv,
        )
    ]
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "scale-reference-v1.json").write_bytes(render_json_report(rows))
    (output_dir / "SCALE_REFERENCE_REPORT.md").write_bytes(
        render_markdown_report(rows)
    )
    return output_dir


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = result.add_subparsers(dest="command", required=True)

    package = sub.add_parser(
        "package",
        help="build one decoded frame plus the two-endpoint click page",
    )
    package.add_argument("--manifest", type=Path, required=True)
    package.add_argument("--fixture", required=True)
    selection = package.add_mutually_exclusive_group(required=True)
    selection.add_argument("--frame-index", type=int)
    selection.add_argument("--at-s", type=float)
    package.add_argument("--known-length-m", type=float, required=True)
    package.add_argument("--annotator-id", default="scale-reference")
    package.add_argument("--output-dir", type=Path, required=True)

    report = sub.add_parser(
        "report",
        help="compare one or more clicked references with analysis-v1 plate scales",
    )
    report.add_argument("--manifest", type=Path, required=True)
    report.add_argument("--analysis", action="append", type=Path, required=True)
    report.add_argument("--package-dir", action="append", type=Path, required=True)
    report.add_argument("--csv", action="append", type=Path, required=True)
    report.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "package":
            output = build_reference_package(args)
            print(
                f"open {output / REFERENCE_PAGE_NAME} in a browser; "
                "click both ends and download the CSV"
            )
        else:
            output = write_report(args)
            print(
                f"wrote {output / 'scale-reference-v1.json'} and "
                f"{output / 'SCALE_REFERENCE_REPORT.md'}"
            )
    except (ScaleReferenceError, OSError, KeyError, ValueError) as error:
        print(f"scale-reference: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
