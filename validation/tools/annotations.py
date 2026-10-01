#!/usr/bin/env python3
"""Deterministic stdlib-only importer, validator and repeatability check for annotation-v1."""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
FIXTURE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
STATES = {"labelled", "unlabelable", "not_annotated"}
VISIBILITY = {"visible", "partially_occluded", "fully_occluded", "unknown"}
LABEL_QUALITY = {"high", "medium", "low"}
ALL_QUALITY = LABEL_QUALITY | {"unusable", "not_assessed"}
CSV_COLUMNS = {
    "timestamp_s", "requested_timestamp_s", "frame_index", "annotation_state", "visibility",
    "quality", "x_px", "y_px", "radius_px", "diameter_px", "left_px", "top_px",
    "right_px", "bottom_px", "notes",
}


class AnnotationError(ValueError):
    pass


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise AnnotationError(message)


def _keys(value: Any, path: str, required: set[str], allowed: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{path} must be an object")
    missing = sorted(required - set(value))
    extra = sorted(set(value) - allowed)
    _require(not missing, f"{path} is missing required fields: {', '.join(missing)}")
    _require(not extra, f"{path} has unsupported fields: {', '.join(extra)}")
    return value


def _number(value: Any, path: str, *, minimum: float | None = None, positive: bool = False) -> float:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{path} must be numeric")
    value = float(value)
    _require(math.isfinite(value), f"{path} must be finite")
    if minimum is not None:
        _require(value >= minimum, f"{path} must be >= {minimum}")
    if positive:
        _require(value > 0, f"{path} must be > 0")
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnnotationError(f"cannot read JSON {path}: {exc}") from exc
    _require(isinstance(value, dict), f"{path} must contain a JSON object")
    return value


def _fixture(manifest: dict[str, Any], fixture_id: str) -> dict[str, Any]:
    _require(manifest.get("schema_version") == 1, "fixture manifest schema_version must be 1")
    fixtures = manifest.get("fixtures")
    _require(isinstance(fixtures, list), "fixture manifest fixtures must be an array")
    matches = [f for f in fixtures if isinstance(f, dict) and f.get("id") == fixture_id]
    _require(len(matches) == 1, f"fixture_id {fixture_id!r} must match exactly one manifest fixture")
    return matches[0]


def _display_size(fixture: dict[str, Any]) -> tuple[int, int]:
    video = fixture.get("video")
    _require(isinstance(video, dict), "fixture video metadata is required")
    width, height = video.get("width_px"), video.get("height_px")
    _require(isinstance(width, int) and width > 0, "fixture video.width_px must be a positive integer")
    _require(isinstance(height, int) and height > 0, "fixture video.height_px must be a positive integer")
    rotation = video.get("rotation_deg", 0)
    _require(rotation in {0, 90, 180, 270}, "fixture video.rotation_deg must be 0/90/180/270")
    return (height, width) if rotation in {90, 270} else (width, height)


def _target_size(value: Any, width: int, height: int, path: str) -> None:
    value = _keys(value, path, set(), {"radius_px", "diameter_px", "left_px", "top_px", "right_px", "bottom_px"})
    radius, diameter = "radius_px" in value, "diameter_px" in value
    bounds_keys = {"left_px", "top_px", "right_px", "bottom_px"}
    has_bounds = bool(bounds_keys & set(value))
    _require(sum((radius, diameter, has_bounds)) == 1, f"{path} must use exactly one size representation")
    if radius:
        _number(value["radius_px"], f"{path}.radius_px", positive=True)
    elif diameter:
        _number(value["diameter_px"], f"{path}.diameter_px", positive=True)
    else:
        _require(bounds_keys <= set(value), f"{path} bounds require left/top/right/bottom")
        left = _number(value["left_px"], f"{path}.left_px", minimum=0)
        top = _number(value["top_px"], f"{path}.top_px", minimum=0)
        right = _number(value["right_px"], f"{path}.right_px", minimum=0)
        bottom = _number(value["bottom_px"], f"{path}.bottom_px", minimum=0)
        _require(left < right <= width and top < bottom <= height, f"{path} bounds must be ordered and within display dimensions")


def validate_annotation(annotation: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    annotation = _keys(
        annotation, "annotation",
        {"schema_version", "fixture_id", "coordinate_system", "timebase", "provenance", "samples"},
        {"schema_version", "fixture_id", "source_video_sha256", "coordinate_system", "timebase", "provenance", "samples"},
    )
    _require(annotation["schema_version"] == 1, "annotation schema_version must be 1")
    fixture_id = annotation["fixture_id"]
    _require(isinstance(fixture_id, str) and FIXTURE_ID_RE.fullmatch(fixture_id) is not None, "fixture_id must match ^[a-z0-9][a-z0-9._-]*$")
    fixture = _fixture(manifest, fixture_id)

    fixture_hash = fixture.get("media", {}).get("sha256") if isinstance(fixture.get("media"), dict) else None
    source_hash = annotation.get("source_video_sha256")
    if source_hash is not None:
        _require(isinstance(source_hash, str) and SHA256_RE.fullmatch(source_hash) is not None, "source_video_sha256 must be 64 hexadecimal characters")
    if fixture_hash is not None:
        _require(source_hash is not None, "source_video_sha256 is required because the fixture manifest has media.sha256")
        _require(source_hash.lower() == str(fixture_hash).lower(), "source_video_sha256 does not match fixture manifest media.sha256")

    coordinate = _keys(
        annotation["coordinate_system"], "coordinate_system",
        {"space", "origin", "x_direction", "y_direction", "rotation_applied", "width_px", "height_px"},
        {"space", "origin", "x_direction", "y_direction", "rotation_applied", "width_px", "height_px"},
    )
    constants = {"space": "decoded_display_pixels", "origin": "top_left", "x_direction": "right", "y_direction": "down", "rotation_applied": True}
    for key, expected in constants.items():
        _require(coordinate[key] == expected, f"coordinate_system.{key} must be {expected!r}")
    width, height = coordinate["width_px"], coordinate["height_px"]
    _require(isinstance(width, int) and width > 0 and isinstance(height, int) and height > 0, "coordinate dimensions must be positive integers")
    expected = _display_size(fixture)
    _require((width, height) == expected, f"coordinate dimensions {(width, height)} do not match display-oriented fixture dimensions {expected}")

    timebase = _keys(annotation["timebase"], "timebase", {"unit", "origin", "decoder_match_tolerance_s"}, {"unit", "origin", "decoder_match_tolerance_s"})
    _require(timebase["unit"] == "seconds", "timebase.unit must be 'seconds'")
    _require(timebase["origin"] == "media_start", "timebase.origin must be 'media_start'")
    tolerance = _number(timebase["decoder_match_tolerance_s"], "timebase.decoder_match_tolerance_s", minimum=0)

    provenance = _keys(annotation["provenance"], "provenance", {"annotator_id", "annotated_at", "method", "tool"}, {"annotator_id", "annotated_at", "method", "tool", "notes"})
    _require(isinstance(provenance["annotator_id"], str) and provenance["annotator_id"], "provenance.annotator_id must be non-empty")
    _require(provenance["method"] == "manual_plate_centre", "provenance.method must be 'manual_plate_centre'")
    tool = _keys(provenance["tool"], "provenance.tool", {"name", "version"}, {"name", "version"})
    _require(all(isinstance(tool[k], str) and tool[k] for k in ("name", "version")), "provenance.tool name/version must be non-empty")
    try:
        annotated_at = datetime.fromisoformat(str(provenance["annotated_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnnotationError("provenance.annotated_at must be an ISO-8601 date-time") from exc
    _require(annotated_at.tzinfo is not None, "provenance.annotated_at must include a timezone")
    if "notes" in provenance:
        _require(isinstance(provenance["notes"], str), "provenance.notes must be a string")

    samples = annotation["samples"]
    _require(isinstance(samples, list) and samples, "samples must be a non-empty array")
    previous = -math.inf
    counts = {state: 0 for state in STATES}
    allowed = {"timestamp_s", "requested_timestamp_s", "frame_index", "annotation_state", "visibility", "center_px", "target_size_px", "quality", "notes"}
    required = {"timestamp_s", "annotation_state", "visibility", "quality"}
    for i, raw in enumerate(samples):
        path = f"samples[{i}]"
        sample = _keys(raw, path, required, allowed)
        timestamp = _number(sample["timestamp_s"], f"{path}.timestamp_s", minimum=0)
        _require(timestamp > previous, "sample timestamps must be strictly increasing and unique")
        previous = timestamp
        if "requested_timestamp_s" in sample:
            requested = _number(sample["requested_timestamp_s"], f"{path}.requested_timestamp_s", minimum=0)
            _require(abs(requested - timestamp) <= tolerance + 1e-12, f"{path} requested/decoded timestamp difference exceeds decoder_match_tolerance")
        if "frame_index" in sample:
            _require(isinstance(sample["frame_index"], int) and not isinstance(sample["frame_index"], bool) and sample["frame_index"] >= 0, f"{path}.frame_index must be a non-negative integer")

        if "notes" in sample:
            _require(isinstance(sample["notes"], str), f"{path}.notes must be a string")
        state, visibility, quality = sample["annotation_state"], sample["visibility"], sample["quality"]
        _require(state in STATES, f"{path}.annotation_state is invalid")
        _require(visibility in VISIBILITY, f"{path}.visibility is invalid")
        _require(quality in ALL_QUALITY, f"{path}.quality is invalid")
        counts[state] += 1
        has_center, has_size = "center_px" in sample, "target_size_px" in sample
        if state == "labelled":
            _require(has_center, f"{path} labelled sample requires center_px")
            _require(quality in LABEL_QUALITY, f"{path} labelled sample quality must be high/medium/low")
            _require(visibility != "fully_occluded", f"{path} fully occluded target cannot be labelled")
        elif state == "unlabelable":
            _require(not has_center and not has_size, f"{path} unlabelable sample must not contain fabricated centre or target size")
            _require(quality == "unusable", f"{path} unlabelable sample quality must be unusable")
        else:
            _require(not has_center and not has_size, f"{path} not_annotated sample must not contain centre or target size")
            _require(quality == "not_assessed", f"{path} not_annotated sample quality must be not_assessed")

        if has_center:
            center = _keys(sample["center_px"], f"{path}.center_px", {"x_px", "y_px"}, {"x_px", "y_px"})
            x = _number(center["x_px"], f"{path}.center_px.x_px", minimum=0)
            y = _number(center["y_px"], f"{path}.center_px.y_px", minimum=0)
            _require(x < width and y < height, f"{path}.center_px must lie inside display dimensions")
        if has_size:
            _target_size(sample["target_size_px"], width, height, f"{path}.target_size_px")

    return {"schema_version": 1, "fixture_id": fixture_id, "sample_count": len(samples), **counts}


def _optional_float(row: dict[str, str], key: str, row_number: int) -> float | None:
    text = row[key].strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError as exc:
        raise AnnotationError(f"CSV row {row_number} {key} must be numeric") from exc
    _require(math.isfinite(value), f"CSV row {row_number} {key} must be finite")
    return value


def _optional_int(row: dict[str, str], key: str, row_number: int) -> int | None:
    text = row[key].strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise AnnotationError(f"CSV row {row_number} {key} must be an integer") from exc


def import_csv(metadata: dict[str, Any], csv_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    annotation = deepcopy(metadata)
    _require("samples" not in annotation, "import metadata must not contain samples")
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            _require(reader.fieldnames is not None, "CSV must have a header")
            _require(len(reader.fieldnames) == len(CSV_COLUMNS) and set(reader.fieldnames) == CSV_COLUMNS, "CSV header must match the documented annotation import columns exactly")
            samples: list[dict[str, Any]] = []
            for row_number, row in enumerate(reader, start=2):
                _require(None not in row and all(row.get(column) is not None for column in CSV_COLUMNS), f"CSV row {row_number} must contain exactly the documented number of cells")
                timestamp = _optional_float(row, "timestamp_s", row_number)
                _require(timestamp is not None, f"CSV row {row_number} timestamp_s is required")
                sample: dict[str, Any] = {"timestamp_s": timestamp, "annotation_state": row["annotation_state"].strip(), "visibility": row["visibility"].strip(), "quality": row["quality"].strip()}
                requested = _optional_float(row, "requested_timestamp_s", row_number)
                frame_index = _optional_int(row, "frame_index", row_number)
                if requested is not None:
                    sample["requested_timestamp_s"] = requested
                if frame_index is not None:
                    sample["frame_index"] = frame_index
                x, y = _optional_float(row, "x_px", row_number), _optional_float(row, "y_px", row_number)
                _require((x is None) == (y is None), f"CSV row {row_number} x_px and y_px must both be present or absent")
                if x is not None:
                    sample["center_px"] = {"x_px": x, "y_px": y}
                radius, diameter = _optional_float(row, "radius_px", row_number), _optional_float(row, "diameter_px", row_number)
                bounds = [_optional_float(row, key, row_number) for key in ("left_px", "top_px", "right_px", "bottom_px")]
                forms = int(radius is not None) + int(diameter is not None) + int(any(v is not None for v in bounds))
                _require(forms <= 1, f"CSV row {row_number} must use only one target-size representation")
                if radius is not None:
                    sample["target_size_px"] = {"radius_px": radius}
                elif diameter is not None:
                    sample["target_size_px"] = {"diameter_px": diameter}
                elif any(v is not None for v in bounds):
                    _require(all(v is not None for v in bounds), f"CSV row {row_number} bounds require all four values")
                    sample["target_size_px"] = dict(zip(("left_px", "top_px", "right_px", "bottom_px"), bounds, strict=True))
                notes = row["notes"].strip()
                if notes:
                    sample["notes"] = notes
                samples.append(sample)
    except OSError as exc:
        raise AnnotationError(f"cannot read CSV {csv_path}: {exc}") from exc
    annotation["samples"] = samples
    validate_annotation(annotation, manifest)
    return annotation


def repeatability(left: dict[str, Any], right: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    lsum, rsum = validate_annotation(left, manifest), validate_annotation(right, manifest)
    _require(lsum["fixture_id"] == rsum["fixture_id"], "repeatability inputs must reference the same fixture")

    def labelled(doc: dict[str, Any]) -> dict[float, tuple[float, float]]:
        return {
            float(s["timestamp_s"]): (float(s["center_px"]["x_px"]), float(s["center_px"]["y_px"]))
            for s in doc["samples"]
            if s["annotation_state"] == "labelled"
        }

    lp, rp = labelled(left), labelled(right)
    shared = sorted(set(lp) & set(rp))
    _require(bool(shared), "repeatability inputs have no exactly matching labelled timestamps")
    dx: list[float] = []
    dy: list[float] = []
    distances: list[float] = []
    for timestamp in shared:
        lx, ly = lp[timestamp]
        rx, ry = rp[timestamp]
        dx.append(abs(lx - rx))
        dy.append(abs(ly - ry))
        distances.append(math.hypot(lx - rx, ly - ry))

    r6 = lambda value: round(value, 6)
    return {
        "schema_version": 1,
        "fixture_id": lsum["fixture_id"],
        "left_annotator_id": left["provenance"]["annotator_id"],
        "right_annotator_id": right["provenance"]["annotator_id"],
        "matching_labelled_samples": len(shared),
        "left_only_labelled_timestamps": len(set(lp) - set(rp)),
        "right_only_labelled_timestamps": len(set(rp) - set(lp)),
        "mean_abs_dx_px": r6(sum(dx) / len(dx)),
        "mean_abs_dy_px": r6(sum(dy) / len(dy)),
        "mean_euclidean_disagreement_px": r6(sum(distances) / len(distances)),
        "rmse_euclidean_disagreement_px": r6(math.sqrt(sum(value * value for value in distances) / len(distances))),
        "max_euclidean_disagreement_px": r6(max(distances)),
    }


def _write(data: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(rendered)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)

    validate = subs.add_parser("validate")
    validate.add_argument("annotation", type=Path)
    validate.add_argument("--manifest", type=Path, required=True)

    importer = subs.add_parser("import-csv")
    importer.add_argument("--metadata", type=Path, required=True)
    importer.add_argument("--csv", type=Path, required=True)
    importer.add_argument("--manifest", type=Path, required=True)
    importer.add_argument("--output", type=Path, required=True)

    repeat = subs.add_parser("repeatability")
    repeat.add_argument("left", type=Path)
    repeat.add_argument("right", type=Path)
    repeat.add_argument("--manifest", type=Path, required=True)
    repeat.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_json(args.manifest)
        if args.command == "validate":
            _write(validate_annotation(load_json(args.annotation), manifest), None)
        elif args.command == "import-csv":
            _write(import_csv(load_json(args.metadata), args.csv, manifest), args.output)
        elif args.command == "repeatability":
            _write(repeatability(load_json(args.left), load_json(args.right), manifest), args.output)
    except AnnotationError as exc:
        print(f"annotation error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
