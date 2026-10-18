#!/usr/bin/env python3
"""Write ``track-guard-v1`` guarded copies of a track run's records (#57).

Reads ``<run-dir>/candidates/<method>/*.jsonl`` for every track method in the run and writes
``<output-dir>/candidates/<method>_guard/*.jsonl`` plus a ``run.json`` that cites the source run
(its SHA-256), the guard configuration and the code commit. The source run is never modified: the
guarded track sits beside the raw one. CPU only; no labels are read.

    python research/plate-detection/apply_guard.py \
        --run-dir validation/private/plate-detection-sam3-seed/run-edgetam-1 \
        --output-dir validation/private/plate-detection-sam3-seed/run-edgetam-1-guard
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import guard  # noqa: E402
import run_phase_a  # noqa: E402

SUFFIX = "_guard"


def guard_run(run_dir: Path, output_dir: Path) -> dict[str, int]:
    source = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if output_dir.exists():
        raise SystemExit(f"{output_dir} exists; use a new --output-dir")
    counts: dict[str, int] = {}
    methods = {}
    for method_dir in sorted(p for p in (run_dir / "candidates").iterdir() if p.is_dir()):
        target = output_dir / "candidates" / f"{method_dir.name}{SUFFIX}"
        target.mkdir(parents=True)
        for path in sorted(method_dir.glob("*.jsonl")):
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            guarded = guard.guard_track(records)
            for record in guarded:
                record["method"] = f"{method_dir.name}{SUFFIX}"
                counts[record["guard"]["status"]] = counts.get(record["guard"]["status"], 0) + 1
            (target / path.name).write_text("".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n"
                                                    for r in guarded), encoding="utf-8")
        methods[f"{method_dir.name}{SUFFIX}"] = {**source["methods"][method_dir.name], "guard": guard.config()}
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", str(HERE)], cwd=HERE, capture_output=True,
                           text=True).stdout.strip()
    record = {"harness": "plate-detection-track-guard-v1", "origin": "machine", "code_commit": commit,
              "dirty": bool(dirty), "source_run": {"path": run_dir.name,
                                                   "run_json_sha256": run_phase_a.sha256_file(run_dir / "run.json")},
              "methods": methods, "clips": source["clips"]}
    (output_dir / "run.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dict(sorted(counts.items()))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args(argv)
    print(json.dumps(guard_run(args.run_dir, args.output_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
