#!/usr/bin/env python3
"""Draw the label and each method's top-3 candidates on a clip's decision frame (#233).

Failure review only: writes half-size JPEGs under ``<run-dir>/overlays/`` (inside validation/private),
green = human label, one colour per method, thick = top-1. Guarded like the runner.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "validation" / "tools"))

import clips  # noqa: E402
import label_package  # noqa: E402
import truth  # noqa: E402

COLOURS = {"hough": (0, 165, 255), "gdino_box": (255, 0, 0), "gdino_sam2": (255, 0, 255), "sam3": (0, 200, 200)}


def draw(clip_id: str, fixture: dict, repo_root: Path, run_dir: Path) -> Path:
    import cv2
    clips.development_role(fixture)
    points = truth.load_truth(clip_id, repo_root / "validation" / "private")
    if not points:
        raise SystemExit(f"{clip_id} has no labels; nothing to overlay")
    first = points[0]
    methods = [m for m in COLOURS if (run_dir / "candidates" / m / f"{clip_id}.jsonl").is_file()]
    records = {}
    for m in methods:
        rows = [json.loads(line) for line in (run_dir / "candidates" / m / f"{clip_id}.jsonl").read_text().splitlines()]
        records[m] = min(rows, key=lambda r: abs(r["time_s"] - first.timestamp_s))
    index = records[methods[0]]["frame_index"]
    media = repo_root / fixture["media"]["repository_path"]
    label_package.require_media_hash(fixture, media)
    probed = label_package.probe(media)
    [frame] = label_package.extract(media, [index], [probed["pts"][index]], run_dir / "overlays" / "tmp", fixture)
    image = cv2.imread(str(frame))
    frame.unlink()
    centre = (round(first.center_x_px), round(first.center_y_px))
    cv2.circle(image, centre, round(first.radius_px), (0, 255, 0), 4)
    for row_offset, m in enumerate(methods):
        top = sorted(records[m]["candidates"], key=lambda r: -r["score"])[:3]
        for rank, c in enumerate(top, 1):
            x, y, r = round(c["center_x_px"]), round(c["center_y_px"]), round(c["radius_px"])
            cv2.circle(image, (x, y), r, COLOURS[m], 3 if rank == 1 else 1)
            cv2.putText(image, f"{m}#{rank} {c['score']:.2f}", (x - r, y - r - 5 - 18 * row_offset),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOURS[m], 2)
    out = run_dir / "overlays" / f"{clip_id}.jpg"
    cv2.imwrite(str(out), cv2.resize(image, None, fx=0.5, fy=0.5))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", type=Path, required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("clip", nargs="+")
    args = p.parse_args(argv)
    run_dir = args.run_dir.resolve()
    if not run_dir.is_relative_to(label_package.PRIVATE_ROOT.resolve()):
        raise SystemExit(f"run dir must be under {label_package.PRIVATE_ROOT}")
    fixtures = clips.load_fixtures([args.repo_root / "validation/private/manifest.json",
                                    args.repo_root / "validation/private/vbt/manifest.json"])
    for clip_id in args.clip:
        if clip_id not in fixtures:
            raise SystemExit(f"unknown clip id {clip_id!r}")
        print(draw(clip_id, fixtures[clip_id], args.repo_root, run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
