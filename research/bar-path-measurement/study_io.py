"""Strict existing-contract readers for the isolated bar-path measurement study."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "tools"))
import annotations
import schema_check

VERSION = "bar-path-measurement-v1"


def load(path: Path) -> dict:
    value = schema_check.load_strict(path)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected an object")
    return value


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        result = hashlib.file_digest(stream, "sha256")
    return result.hexdigest()


def contract(document: dict, name: str) -> None:
    errors = schema_check.validate_document(
        document, schema_check.load_schema(ROOT / "validation" / "schema" / f"{name}.schema.json")
    )
    if errors:
        raise ValueError(f"{name}: {'; '.join(errors[:5])}")


def number(value: Any, name: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite")
    try:
        value = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be finite") from error
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return float(value)


def fixture(manifest: dict, fixture_id: str, *, held_out: bool = False) -> dict:
    contract(manifest, "fixture-manifest-v1")
    matches = [f for f in manifest["fixtures"] if f["id"] == fixture_id]
    if len(matches) != 1:
        raise ValueError("fixture must occur exactly once in manifest")
    item = matches[0]
    if item["purpose"] == "validation" and not held_out:
        raise ValueError(f"{fixture_id}: held-out fixture refused; use a verified freeze")
    return item


def size(item: dict) -> tuple[int, int]:
    return annotations._display_size(item)


def samples(rows: list[dict], width: int | None = None, height: int | None = None) -> None:
    if not isinstance(rows, list) or not rows:
        raise ValueError("samples must be nonempty")
    previous = -1.0
    for row in rows:
        timestamp = number(row.get("timestamp_s"), "timestamp_s", 0)
        if timestamp <= previous:
            raise ValueError("timestamps must increase strictly")
        previous = timestamp
        if row.get("state") == "lost":
            if "center_px" in row or "confidence" in row:
                raise ValueError("lost samples cannot carry coordinates or confidence")
        elif row.get("state") == "tracked":
            confidence = number(row.get("confidence"), "confidence", 0)
            if confidence > 1:
                raise ValueError("confidence must be <= 1")
            center = row.get("center_px")
            if not isinstance(center, dict) or set(center) != {"x_px", "y_px"}:
                raise ValueError("tracked samples require center_px")
            x = number(center["x_px"], "x_px", 0)
            y = number(center["y_px"], "y_px", 0)
            if (width is not None and x >= width) or (height is not None and y >= height):
                raise ValueError("coordinate outside decoded display")
        else:
            raise ValueError("state must be tracked or lost")


def prediction(path: Path, item: dict) -> dict:
    doc = load(path)
    contract(doc, "tracker-prediction-v1")
    if doc["fixture_id"] != item["id"]:
        raise ValueError("prediction fixture mismatch")
    if doc.get("source_video_sha256", "").lower() != item["media"]["sha256"].lower():
        raise ValueError("prediction source hash mismatch")
    samples(doc["samples"], *size(item))
    return doc


def annotation(path: Path, manifest: dict, item: dict) -> dict:
    doc = load(path)
    annotations.validate_annotation(doc, manifest)
    if doc["fixture_id"] != item["id"]:
        raise ValueError("annotation fixture mismatch")
    return doc


def seed(path: Path, item: dict) -> dict:
    doc = load(path)
    contract(doc, "manual-target-seed-v1")
    if doc["fixture_id"] != item["id"]:
        raise ValueError("seed fixture mismatch")
    value = doc["seed"]
    width, height = size(item)
    center = value["target"]["center"]
    if not 0 <= center["x_px"] < width or not 0 <= center["y_px"] < height:
        raise ValueError("seed outside decoded display")
    if value["source_rotation_deg"] != item["video"]["rotation_deg"]:
        raise ValueError("seed rotation mismatch")
    return doc


def media_path(item: dict, repository_root: Path) -> Path:
    root = repository_root.resolve()
    result = (root / item["media"]["repository_path"]).resolve()
    if not result.is_relative_to(root):
        raise ValueError("media path escapes repository root")
    if digest(result).lower() != item["media"]["sha256"].lower():
        raise ValueError("media hash mismatch")
    return result


def private_output(item: dict, output: Path) -> None:
    if item.get("source", {}).get("redistribution_status") != "allowed":
        if not output.resolve().is_relative_to((ROOT / "validation" / "private").resolve()):
            raise ValueError("private fixture output must stay under this worktree's validation/private")
