#!/usr/bin/env python3
"""Validate a scene-inventory CSV downloaded from a labelling package page.

The inventory lists the *other* circular objects on each labelled frame (not the target plate),
classed with the wrong-target taxonomy of ``docs/plans/HOUGH_TOP_K_PREPARATION.md``. It is kept
beside the label CSV as its own file with its own SHA-256, so a freeze record can cite it. It is
research tooling: it never enters an annotation document or the canonical ``Analysis``.

``validate`` checks the file against the package it came from (the frame list embedded in the
package's ``index.html``) and prints a deterministic JSON summary including the file hash.

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

COLUMNS = ("timestamp_s", "frame_index", "inventory_state", "object_index", "object_class",
           "x_px", "y_px", "radius_px", "notes")
CLASSES = ("another_plate_target_bar", "concentric_rim", "other_loaded_bar", "stored_plate",
           "rack_equipment", "background_object", "other")
STATES = ("done", "not_done")
OBJECT_FIELDS = ("object_index", "object_class", "x_px", "y_px", "radius_px", "notes")
CONFIG_LINE = re.compile(r"^const CONFIG = (\{.*\});$", re.M)
NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?", re.ASCII)


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
    radius = finite(row["radius_px"], "radius_px", where) if row["radius_px"] else None
    if radius is not None and radius <= 0:
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
    if len(rows) == 1 and not any(rows[0][1][field] for field in OBJECT_FIELDS):
        return {"inventory_state": state, "objects": []}
    if state != "done":
        raise InventoryError(f"line {rows[0][0]}: objects are only allowed on a done frame")
    objects = []
    for position, (line, row) in enumerate(rows):
        if row["object_index"] != str(position):
            raise InventoryError(f"line {line}: object_index must be {position} (contiguous from 0)")
        objects.append(parse_object(row, width, height, f"line {line}"))
    return {"inventory_state": state, "objects": objects}


def parse(csv_text: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(csv_text, newline=""))
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise InventoryError(f"header must be exactly {','.join(COLUMNS)}")
    expected = {f"{frame['timestamp_s']:.6f}": frame["frame_index"] for frame in config["frames"]}
    grouped: dict[str, list[tuple[int, dict[str, str]]]] = {}
    order: list[str] = []
    for line, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise InventoryError(f"line {line}: wrong number of columns")
        key = row["timestamp_s"]
        if key not in expected or row["frame_index"] != str(expected[key]):
            raise InventoryError(f"line {line}: frame {row['frame_index']} at {key} s is not in the package")
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


def summary(csv_bytes: bytes, config: dict[str, Any]) -> dict[str, Any]:
    frames = parse(csv_bytes.decode("utf-8"), config)
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
        "objects_by_class": counts,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = result.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="check an inventory CSV against its package and print a summary")
    validate.add_argument("--package", type=Path, required=True, help="labelling package folder (holds index.html)")
    validate.add_argument("--csv", type=Path, required=True, help="downloaded <fixture>.<annotator>.inventory.csv")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = package_config((args.package / "index.html").read_text(encoding="utf-8"))
        result = summary(args.csv.read_bytes(), config)
    except (ValueError, KeyError, TypeError, csv.Error, OSError) as error:
        print(f"scene-inventory: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
