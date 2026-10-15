#!/usr/bin/env python3
"""Command line for the label-triage research tool (standard library only).

  run-voters  run the three voters on one development fixture (held-out fixtures are refused)
  table       per-frame voter positions, spread and flags      -> <fixture>.disagreement.csv
  triage      frames a human should label + random sample      -> triage_list.csv (+ private reasons)
  calibrate   how often agreement lands within the gate of the human labels (counts, no verdict)

`table`, `triage` and `calibrate` read a job file: {"fixtures": [{"fixture_id", "voters": {name:
prediction path}, "grid_package": dir | "annotation": path}]}, paths relative to the job file.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

import run_voters
import triage_core as core


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def num(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"


def grid_timestamps(package: Path) -> list[float]:
    text = (package / "index.html").read_text(encoding="utf-8")
    match = re.search(r"const CONFIG = (\{.*?\});\n", text, re.S)
    if match is None:
        raise core.TriageError(f"{package}/index.html has no labelling CONFIG")
    return [core._finite(f["timestamp_s"], "frame timestamp_s") for f in json.loads(match.group(1))["frames"]]


def load_job(path: Path) -> list[dict[str, Any]]:
    job = read_json(path)
    entries = job.get("fixtures") if isinstance(job, dict) else None
    if not isinstance(entries, list) or not entries:
        raise core.TriageError("job file needs a non-empty 'fixtures' list")
    resolved = []
    for entry in entries:
        voters = {name: core.load_prediction_centres(read_json(path.parent / p))
                  for name, p in sorted(entry["voters"].items())}
        labels = core.load_label_centres(read_json(path.parent / entry["annotation"])) \
            if "annotation" in entry else None
        if "grid_package" in entry:
            stamps = grid_timestamps(path.parent / entry["grid_package"])
        elif labels is not None:
            stamps = sorted(labels)
        else:
            raise core.TriageError(f"{entry.get('fixture_id')}: needs grid_package or annotation")
        resolved.append({"fixture_id": entry["fixture_id"], "voters": voters, "timestamps": stamps,
                         "labels": labels, "rows": core.compare_voters(voters, stamps)})
    return resolved


def cmd_table(args: argparse.Namespace) -> None:
    for item in load_job(args.job):
        names = sorted(item["voters"])
        header = ["fixture_id", "timestamp_s"] + [f"{n}_{axis}" for n in names for axis in ("x", "y")] + \
                 ["n_valid", "spread_px", "median_x", "median_y", "flags"]
        rows = []
        for row in item["rows"]:
            cells = []
            for name in names:
                p = row["positions"][name]
                cells += [num(p[0]), num(p[1])] if p else ["", ""]
            med = row["median_px"]
            rows.append([item["fixture_id"], f"{row['timestamp_s']:.6f}", *cells, row["n_valid"],
                         num(row["spread_px"]), num(med[0]) if med else "", num(med[1]) if med else "",
                         ";".join(row["flags"])])
        write_csv(args.out_dir / f"{item['fixture_id']}.disagreement.csv", header, rows)


def cmd_triage(args: argparse.Namespace) -> None:
    listing, reasons, summary = [], [], {}
    for item in load_job(args.job):
        chosen = core.select_frames(item["timestamps"], item["rows"], item["fixture_id"],
                                    args.fraction, args.seed)
        total = len(item["timestamps"])
        for pick in chosen:
            listing.append([pick["fixture_id"], pick["index"], f"{pick['timestamp_s']:.6f}"])
            reasons.append([pick["fixture_id"], pick["index"], f"{pick['timestamp_s']:.6f}",
                            ";".join(pick["reasons"])])
        summary[item["fixture_id"]] = {
            "frames_in_grid": total, "frames_to_label": len(chosen),
            "flagged": sum("flagged" in p["reasons"] for p in chosen),
            "random_sample": sum("random" in p["reasons"] for p in chosen),
            "fraction_not_to_label": round(1 - len(chosen) / total, 6) if total else None}
    # The labeller's list carries no reasons, so selection cannot hint at where the plate is.
    write_csv(args.out_dir / "triage_list.csv", ["fixture_id", "index", "timestamp_s"], listing)
    write_csv(args.out_dir / "triage_reasons.csv", ["fixture_id", "index", "timestamp_s", "reasons"], reasons)
    write_json(args.out_dir / "triage_summary.json",
               {"fraction": args.fraction, "seed": args.seed, "spread_limit_px": core.SPREAD_LIMIT_PX,
                "fixtures": summary})


def cmd_calibrate(args: argparse.Namespace) -> None:
    report: dict[str, Any] = {"fixtures": {}}
    pooled = {"agreeing": [0, 0], "not_agreeing": [0, 0]}
    for item in load_job(args.job):
        if item["labels"] is None:
            raise core.TriageError(f"{item['fixture_id']}: calibrate needs an annotation")
        result = core.calibrate(item["rows"], item["labels"])
        report["fixtures"][item["fixture_id"]] = result
        for group in pooled:
            pooled[group][0] += result[group]["n"]
            pooled[group][1] += result[group]["median_within_gate"]
    report["pooled"] = {}
    for group, (n, ok) in pooled.items():
        interval = core.wilson_interval(ok, n)
        report["pooled"][group] = {"n": n, "median_within_gate": ok,
                                   "wilson95": None if interval is None else [round(v, 6) for v in interval]}
    report["note"] = ("Counts and intervals only. Frames of one clip are correlated, so pooled intervals do "
                      "not describe unseen clips. Not a pass/fail verdict.")
    write_json(args.out, report)


def cmd_run_voters(args: argparse.Namespace) -> None:
    written = run_voters.run(args.repo_root, args.manifest, args.fixture, args.seed, args.out_dir,
                             args.cpu_python, args.gpu_python, args.force)
    for path in written:
        print(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("table", cmd_table), ("triage", cmd_triage)):
        p = sub.add_parser(name)
        p.add_argument("--job", type=Path, required=True)
        p.add_argument("--out-dir", type=Path, required=True)
        if name == "triage":
            p.add_argument("--fraction", type=float, default=0.10, help="random sample share of all frames")
            p.add_argument("--seed", type=int, required=True, help="fixed before looking at any data")
        p.set_defaults(handler=handler)
    p = sub.add_parser("calibrate")
    p.add_argument("--job", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(handler=cmd_calibrate)
    p = sub.add_parser("run-voters")
    p.add_argument("--repo-root", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--fixture", required=True)
    p.add_argument("--seed", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--cpu-python", required=True)
    p.add_argument("--gpu-python", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(handler=cmd_run_voters)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.handler(args)
    except (core.TriageError, run_voters.RunError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
