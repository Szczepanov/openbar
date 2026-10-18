#!/usr/bin/env python3
"""Validate a scene-inventory CSV downloaded from a labelling package page.

The inventory lists the *other* circular objects on each labelled frame (not the target plate),
classed with the wrong-target taxonomy of ``docs/plans/HOUGH_TOP_K_PREPARATION.md`` plus
``unknown`` for a circle the annotator cannot classify. Every object has a centre and a radius,
because a later scorer matches candidates on both. The inventory is kept beside the label CSV as
its own file with its own SHA-256, so a freeze record can cite it. It is research tooling: it never
enters an annotation document or the canonical ``Analysis``.

``validate`` checks the file against the package it came from (the ``package_id`` and frame list
embedded in the package's ``index.html``), optionally against that package's label CSV, and prints
a deterministic JSON summary including the file hash.

Standard library only.
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

COLUMNS = ("package_id", "timestamp_s", "frame_index", "inventory_state", "object_index", "object_class",
           "x_px", "y_px", "radius_px", "notes")
CLASSES = ("another_plate_target_bar", "concentric_rim", "other_loaded_bar", "stored_plate",
           "rack_equipment", "background_object", "other", "unknown")
STATES = ("done", "not_done")
OBJECT_FIELDS = ("object_index", "object_class", "x_px", "y_px", "radius_px", "notes")
# The label CSV the same page writes (label_page.html csvRows), read only for the --labels guard.
LABEL_COLUMNS = ("timestamp_s", "requested_timestamp_s", "frame_index", "annotation_state", "visibility", "quality",
                 "x_px", "y_px", "radius_px", "diameter_px", "left_px", "top_px", "right_px", "bottom_px", "notes")
LABEL_STATES = ("labelled", "unlabelable", "not_annotated")
# Guard against re-marking the labelled plate as an inventory object; not a matching tolerance. The page
# compares unrounded values and this tool the 2-decimal CSV values, so they can differ right at 2 px.
SAME_CIRCLE_PX = 2.0
CONFIG_LINE = re.compile(r"^const CONFIG = (\{.*\});$", re.M)
NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?", re.ASCII)
BOM = b"\xef\xbb\xbf"


class InventoryError(ValueError):
    pass


def package_config(page_text: str) -> dict[str, Any]:
    """The configuration ``label_package.render_page`` embedded in a package's ``index.html``."""
    matches = CONFIG_LINE.findall(page_text)
    if len(matches) != 1:
        raise InventoryError("package index.html must embed exactly one CONFIG object")
    config = json.loads(matches[0])
    if not isinstance(config, dict) or not isinstance(config.get("frames"), list):
        raise InventoryError("package CONFIG has no frame list")
    return config


def finite(text: str, field: str, where: str) -> float:
    """A plain decimal as the page writes it; ``float`` alone would also take ``nan``, ``1_0`` or spaces."""
    if not NUMBER.fullmatch(text):
        raise InventoryError(f"{where}: {field} {text!r} is not a plain decimal number")
    value = float(text)
    if not math.isfinite(value):
        raise InventoryError(f"{where}: {field} must be finite")
    return value


def parse_object(row: dict[str, str], width: int, height: int, where: str) -> dict[str, Any]:
    cls = row["object_class"]
    if cls not in CLASSES:
        raise InventoryError(f"{where}: object_class {cls!r} is not one of {', '.join(CLASSES)}")
    x, y = finite(row["x_px"], "x_px", where), finite(row["y_px"], "y_px", where)
    if not (0 <= x < width and 0 <= y < height):
        raise InventoryError(f"{where}: centre ({x}, {y}) is outside 0 <= x < {width}, 0 <= y < {height}")
    radius = finite(row["radius_px"], "radius_px", where)
    if radius <= 0:
        raise InventoryError(f"{where}: radius_px must be positive")
    if cls == "other" and not row["notes"].strip():
        raise InventoryError(f"{where}: an 'other' object needs a description in notes")
    return {"object_class": cls, "x_px": x, "y_px": y, "radius_px": radius, "notes": row["notes"]}


def frame_entry(rows: list[tuple[int, dict[str, str]]], width: int, height: int) -> dict[str, Any]:
    """One frame's rows: a single empty row (not done, or done with no objects) or indexed objects."""
    states = {row["inventory_state"] for _, row in rows}
    if len(states) != 1 or next(iter(states)) not in STATES:
        raise InventoryError(f"line {rows[0][0]}: inventory_state must be one of {STATES}, the same on every row of a frame")
    state = next(iter(states))
    if len(rows) == 1 and not rows[0][1]["object_index"]:
        if any(rows[0][1][field] for field in OBJECT_FIELDS):
            raise InventoryError(f"line {rows[0][0]}: a frame row without object_index must have empty object fields")
        return {"inventory_state": state, "objects": []}
    if state != "done":
        raise InventoryError(f"line {rows[0][0]}: objects are only allowed on a done frame")
    objects = []
    for position, (line, row) in enumerate(rows):
        if row["object_index"] != str(position):
            raise InventoryError(f"line {line}: object_index must be {position} (contiguous from 0)")
        objects.append(parse_object(row, width, height, f"line {line}"))
    return {"inventory_state": state, "objects": objects}


def decode(data: bytes) -> str:
    if data.startswith(BOM):
        raise InventoryError("the file starts with a UTF-8 BOM, so it was re-saved by another program; "
                             "download it again from the page")
    text = data.decode("utf-8")
    # Split on newlines only: str.splitlines would also split on form feeds or U+2028 inside a field.
    lines = text.replace("\r\n", "\n").split("\n")
    if lines[-1] == "":
        lines = lines[:-1]
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            raise InventoryError(f"line {number}: blank line; the page never writes one")
    return text


def parse(csv_text: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(csv_text, newline=""))
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise InventoryError(f"header must be exactly {','.join(COLUMNS)}")
    expected = {f"{frame['timestamp_s']:.6f}": frame["frame_index"] for frame in config["frames"]}
    grouped: dict[str, list[tuple[int, dict[str, str]]]] = {}
    order: list[str] = []
    for row in reader:
        line = reader.line_num
        if None in row or any(value is None for value in row.values()):
            raise InventoryError(f"line {line}: wrong number of columns")
        if row["package_id"] != config.get("package_id"):
            raise InventoryError(f"line {line}: package_id {row['package_id']!r} is not this package's "
                                 f"{config.get('package_id')!r}")
        key = row["timestamp_s"]
        if key not in expected or row["frame_index"] != str(expected[key]):
            raise InventoryError(f"line {line}: frame {row['frame_index']} at {key} s is not in the package "
                                 "(a spreadsheet re-save can rewrite 0.000000 as 0)")
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        elif order[-1] != key:
            raise InventoryError(f"line {line}: rows of one frame must be consecutive")
        grouped[key].append((line, row))
    if order != list(expected):
        raise InventoryError("the inventory must list every package frame once, in package order")
    width, height = int(config["width_px"]), int(config["height_px"])
    return [{"timestamp_s": float(key), "frame_index": expected[key], **frame_entry(grouped[key], width, height)}
            for key in order]


def labelled_plates(label_csv: bytes, config: dict[str, Any]) -> dict[str, tuple[float, float, float]]:
    """Labelled plate centre and radius per timestamp from the same package's label CSV (frames with a
    radius). Read as strictly as the inventory, so a wrong or broken file fails instead of checking nothing."""
    reader = csv.DictReader(io.StringIO(decode(label_csv), newline=""))
    if tuple(reader.fieldnames or ()) != LABEL_COLUMNS:
        raise InventoryError(f"--labels: header must be exactly {','.join(LABEL_COLUMNS)}")
    expected = [(f"{frame['timestamp_s']:.6f}", str(frame["frame_index"])) for frame in config["frames"]]
    seen, plates = [], {}
    for row in reader:
        where = f"--labels line {reader.line_num}"
        if None in row or any(value is None for value in row.values()):
            raise InventoryError(f"{where}: wrong number of columns")
        seen.append((row["timestamp_s"], row["frame_index"]))
        if row["annotation_state"] not in LABEL_STATES:
            raise InventoryError(f"{where}: annotation_state {row['annotation_state']!r} is not one of {LABEL_STATES}")
        if row["annotation_state"] == "labelled" and row["radius_px"]:
            plates[row["timestamp_s"]] = (finite(row["x_px"], "x_px", where), finite(row["y_px"], "y_px", where),
                                          finite(row["radius_px"], "radius_px", where))
    if seen != expected:
        raise InventoryError("--labels: the label CSV must list this package's frames once, in package order")
    return plates


def require_not_the_plate(frames: list[dict[str, Any]], plates: dict[str, tuple[float, float, float]]) -> None:
    for frame in frames:
        plate = plates.get(f"{frame['timestamp_s']:.6f}")
        for index, item in enumerate(frame["objects"]):
            if plate and math.hypot(item["x_px"] - plate[0], item["y_px"] - plate[1]) <= SAME_CIRCLE_PX \
                    and abs(item["radius_px"] - plate[2]) <= SAME_CIRCLE_PX:
                raise InventoryError(f"frame {frame['frame_index']}, object {index}: this is the labelled plate, "
                                     "not another object")


def summary(csv_bytes: bytes, config: dict[str, Any], label_csv: bytes | None = None) -> dict[str, Any]:
    frames = parse(decode(csv_bytes), config)
    if label_csv is not None:
        require_not_the_plate(frames, labelled_plates(label_csv, config))
    done = [frame for frame in frames if frame["inventory_state"] == "done"]
    if not done:
        raise InventoryError("no frame has a complete inventory")
    counts = {cls: 0 for cls in CLASSES}
    for frame in done:
        for item in frame["objects"]:
            counts[item["object_class"]] += 1
    return {
        "fixture_id": config.get("fixture_id"),
        "annotator_id": config.get("annotator_id"),
        "package_id": config.get("package_id"),
        "inventory_sha256": hashlib.sha256(csv_bytes).hexdigest(),
        "frames": len(frames),
        "frames_done": len(done),
        "done_timestamps_s": [frame["timestamp_s"] for frame in done],
        "objects_by_class": counts,
        "checked_against_labels": label_csv is not None,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = result.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="check an inventory CSV against its package and print a summary")
    validate.add_argument("--package", type=Path, required=True, help="labelling package folder (holds index.html)")
    validate.add_argument("--csv", type=Path, required=True, help="downloaded <fixture>.<annotator>.inventory.csv")
    validate.add_argument("--labels", type=Path,
                          help="the same package's label CSV; fails if an object re-marks the labelled plate")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = package_config((args.package / "index.html").read_text(encoding="utf-8"))
        labels = args.labels.read_bytes() if args.labels else None
        result = summary(args.csv.read_bytes(), config, labels)
    except (ValueError, KeyError, TypeError, csv.Error, OSError) as error:
        print(f"scene-inventory: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
