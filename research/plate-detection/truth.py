"""Human plate labels for development clips, from the three formats already in validation/private.

Labels are read only by the scorer, never by a detector or selection rule (#233). Formats:

- frozen-eval label CSVs (``<id>.<pass>.labels.csv``); pass ``a`` with the rows of an
  adjudicated ``.adj`` file (override rows only) replacing the same timestamps, or removing them
  when the adjudicated row is not labelled. The freeze record's ``unresolved`` flag is not read:
  development scoring uses the adjudicated geometry as it stands;
- ``annotation-v1`` JSON (``validation/private/annotations/<id>.annotation-v1.json``);
- ``manual-target-seed-v1`` JSON (one labelled frame).
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TruthPoint:
    timestamp_s: float
    center_x_px: float
    center_y_px: float
    radius_px: float
    source: str


def _finite(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


def _point(t: float, x: float, y: float, r: float, source: str) -> TruthPoint:
    if not _finite(t, x, y, r) or r <= 0:
        raise ValueError(f"invalid truth point {(t, x, y, r)!r} in {source}")
    return TruthPoint(t, x, y, r, source)


def label_rows(path: Path) -> list[tuple[float, TruthPoint | None]]:
    """Every row as (timestamp, point); the point is None unless the row is labelled with a radius."""
    rows: list[tuple[float, TruthPoint | None]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            t = float(row["timestamp_s"])
            if row["annotation_state"] != "labelled" or not row["radius_px"]:
                rows.append((t, None))
                continue
            rows.append((t, _point(t, float(row["x_px"]), float(row["y_px"]), float(row["radius_px"]), path.name)))
    return rows


def from_label_csv(path: Path) -> list[TruthPoint]:
    """Labelled rows with a radius; rows without a radius cannot score size and are skipped."""
    return [point for _, point in label_rows(path) if point is not None]


def from_annotation(path: Path) -> list[TruthPoint]:
    document = json.loads(path.read_text(encoding="utf-8"))
    points = []
    for sample in document["samples"]:
        size = sample.get("target_size_px") or {}
        if sample["annotation_state"] != "labelled" or "radius_px" not in size:
            continue
        centre = sample["center_px"]
        points.append(_point(sample["timestamp_s"], centre["x_px"], centre["y_px"], size["radius_px"], path.name))
    return points


def from_seed(path: Path) -> list[TruthPoint]:
    seed = json.loads(path.read_text(encoding="utf-8"))["seed"]
    target = seed["target"]
    return [_point(seed["timestamp_s"], target["center"]["x_px"], target["center"]["y_px"],
                   target["radius_px"], path.name)]


def load_truth(fixture_id: str, private_root: Path) -> list[TruthPoint]:
    """First available source in order: pass-a labels with adjudicated rows applied, annotation,
    seed; else []."""
    labels = private_root / "frozen-eval" / "labels"
    pass_a = labels / f"{fixture_id}.a.labels.csv"
    if pass_a.is_file():
        merged = {round(p.timestamp_s, 6): p for p in from_label_csv(pass_a)}
        adjudicated = labels / f"{fixture_id}.adj.labels.csv"
        if adjudicated.is_file():
            for t, point in label_rows(adjudicated):  # a non-labelled adjudication removes the point
                if point is None:
                    merged.pop(round(t, 6), None)
                else:
                    merged[round(t, 6)] = point
        return [merged[key] for key in sorted(merged)]
    annotation = private_root / "annotations" / f"{fixture_id}.annotation-v1.json"
    if annotation.is_file():
        return sorted(from_annotation(annotation), key=lambda p: p.timestamp_s)
    for seeds in (private_root / "seeds", private_root / "vbt" / "seeds"):
        seed = seeds / f"{fixture_id}.manual-target-seed-v1.json"
        if seed.is_file():
            return from_seed(seed)
    return []
