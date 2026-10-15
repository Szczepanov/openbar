#!/usr/bin/env python3
"""Runner for the registered colour and hub cue experiment (research only).

It refuses to run unless (1) the registration record matches the committed plan file, (2) the owner has
recorded a go-ahead in that record (`grant-go-ahead`, run by the owner, never by an agent), (3) the
fixture is one of the three development clips, and (4) the held-out guard accepts it. There is no
override flag. The decode path reuses research/bar-path-measurement/experiment.py unchanged.

  check            report the registration status (never fails the shell)
  grant-go-ahead   owner only: record the go-ahead for exactly this plan hash
  fit-colour       write the colour model of one development clip's seed frame (for the control arm)
  run              produce a tracker-prediction-v1 stream per arm for one development clip
  run --wiring-check   plumbing test only: accepts a SYNTHETIC fixture, needs no go-ahead, marks every
                   output `smoke_test`, and refuses any non-synthetic clip. It is not the experiment.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for extra in (HERE, ROOT / "research" / "bar-path-measurement", ROOT / "research" / "opencv-tracking",
              ROOT / "validation" / "tools"):
    sys.path.insert(0, str(extra))

import clip_core  # noqa: E402
import cues  # noqa: E402
import experiment  # noqa: E402
import held_out_guard  # noqa: E402
import study_io as io  # noqa: E402

ALLOWED_FIXTURES = frozenset({"self-back-squat-side-002", "self-clean-jerk-side-002", "self-snatch-side-002"})
PLAN = ROOT / "docs" / "plans" / "COLOUR_HUB_CUE_EXPERIMENT_PLAN.md"
ARM_NAMES = {"baseline": "coarse", "C": "colour-gate", "C_control": "colour-gate-control",
             "H": "hub-fused", "CH": "colour-gate-hub-fused"}


class Refused(RuntimeError):
    """The run is not allowed; nothing was decoded or written."""


def load_registration(path: Path) -> dict[str, Any]:
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("plan_sha256") != io.digest(PLAN):
        raise Refused("the plan file no longer matches the registered SHA-256; register a new plan instead")
    return record


def require_go_ahead(path: Path) -> dict[str, Any]:
    record = load_registration(path)
    if record.get("owner_go_ahead_to_run") is not True:
        raise Refused("no owner go-ahead is recorded for this plan (owner runs `grant-go-ahead`)")
    return record


def require_allowed_fixture(manifest: dict[str, Any], fixture_id: str) -> dict[str, Any]:
    item = io.fixture(manifest, fixture_id)
    if fixture_id not in ALLOWED_FIXTURES:
        raise Refused(f"{fixture_id} is not one of the three registered development clips")
    if held_out_guard.is_held_out(item):
        raise Refused(f"{fixture_id} is held-out; refused with no override")
    return item


def require_synthetic(manifest: dict[str, Any], fixture_id: str) -> dict[str, Any]:
    item = io.fixture(manifest, fixture_id)
    if item.get("source", {}).get("kind") != "synthetic":
        raise Refused(f"--wiring-check accepts only synthetic fixtures, not {fixture_id}")
    return item


def decoded_inputs(args: argparse.Namespace, item: dict[str, Any]):
    import track as decoder
    import label_package

    media = io.media_path(item, args.repository_root)
    probed = label_package.probe(media)
    label_package.require_fixture_probe_match(item, probed)
    width, height = io.size(item)
    rounded = [round(t, 6) for t in probed["timestamps_s"]]
    if len(set(rounded)) != len(rounded):
        raise ValueError("six-decimal timestamp precision would merge decoded frames")
    frames = experiment.validated_decode_frames(decoder, media, width, height, len(rounded))
    return frames, rounded, width, height


def cmd_check(args: argparse.Namespace) -> None:
    try:
        record = load_registration(args.registration)
        print(f"plan hash matches; go-ahead recorded: {record.get('owner_go_ahead_to_run') is True}")
    except (Refused, OSError, json.JSONDecodeError) as error:
        print(f"not runnable: {error}")


def cmd_grant(args: argparse.Namespace) -> None:
    record = load_registration(args.registration)
    record.update(owner_go_ahead_to_run=True, go_ahead_note=args.note, status="go_ahead_recorded")
    io.write(args.registration, record)
    print("go-ahead recorded for plan", record["plan_sha256"])


def cmd_fit_colour(args: argparse.Namespace) -> None:
    require_go_ahead(args.registration)
    manifest = io.load(args.manifest)
    item = require_allowed_fixture(manifest, args.fixture)
    seed = io.seed(args.seed, item)["seed"]
    frames, rounded, _, _ = decoded_inputs(args, item)
    index = min(range(len(rounded)), key=lambda i: abs(rounded[i] - seed["timestamp_s"]))
    model = None
    for i, frame in enumerate(frames):  # drain fully so decoder errors surface
        if i == index:
            model = cues.fit_plate_colour(frame, tuple(seed["target"]["center"][a] for a in clip_core.AXES),
                                          seed["target"]["radius_px"])
    io.write(args.output, {"source_fixture": args.fixture, "median": list(model.median), "scale": list(model.scale)})


def cmd_run(args: argparse.Namespace) -> None:
    manifest = io.load(args.manifest)
    if args.wiring_check:
        item = require_synthetic(manifest, args.fixture)
        plan_sha256 = io.digest(PLAN)
    else:
        if args.registration is None:
            raise Refused("--registration is required")
        plan_sha256 = require_go_ahead(args.registration)["plan_sha256"]
        item = require_allowed_fixture(manifest, args.fixture)
    io.private_output(item, args.output_dir)
    seed_doc = io.seed(args.seed, item)
    coarse = io.prediction(args.coarse, item)
    control = None
    if args.control_colour:
        document = json.loads(args.control_colour.read_text(encoding="utf-8"))
        if document["source_fixture"] == args.fixture:
            raise Refused("the control colour model must come from a different clip")
        control = cues.PlateColour(tuple(document["median"]), tuple(document["scale"]))
    frames, rounded, width, height = decoded_inputs(args, item)
    coarse_by_time = {r["timestamp_s"]: r for r in coarse["samples"]}
    if set(coarse_by_time) - set(rounded):
        raise ValueError("coarse timestamps must exactly match decoded timestamps at six decimals")
    result = clip_core.process_clip(frames, rounded, coarse_by_time, seed_doc["seed"], control_colour=control)
    provenance = {"plan_sha256": plan_sha256, "smoke_test": args.wiring_check, **experiment.repository_state(),
                  "manifest_sha256": io.digest(args.manifest), "seed_sha256": io.digest(args.seed),
                  "coarse_prediction_sha256": io.digest(args.coarse),
                  "source_sha256": {p.name: io.digest(p) for p in sorted(HERE.glob("*.py"))},
                  "environment": experiment.environment() | experiment.decoder_environment(),
                  "hub_supported": result["hub_supported"], "confidence": "algorithm_specific_not_probability"}
    common = {"schema_version": 1, "fixture_id": args.fixture, "coordinate_space": "decoded_display_pixels",
              "source_video_sha256": item["media"]["sha256"]}
    for arm, samples in result["arms"].items():
        io.samples(samples, width, height)
        document = {**common, "implementation": {"name": f"research-colour-hub-{ARM_NAMES[arm]}" + ("-smoke" if args.wiring_check else ""), "version": "1",
                                                 "config": provenance}, "samples": samples}
        io.contract(document, "tracker-prediction-v1")
        io.write(args.output_dir / f"{ARM_NAMES[arm]}.prediction-v1.json", document)
    io.write(args.output_dir / "sidecar.json", {
        "schema_version": 1, "fixture_id": args.fixture, "provenance": provenance,
        "relative_samples": result["relative_samples"], "diagnostics": result["diagnostics"]})
    print(json.dumps({"fixture": args.fixture, "arms": sorted(result["arms"]), "hub_supported": result["hub_supported"]}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("check", cmd_check), ("grant-go-ahead", cmd_grant),
                          ("fit-colour", cmd_fit_colour), ("run", cmd_run)):
        p = sub.add_parser(name)
        p.add_argument("--registration", type=Path, required=name != "run")
        p.set_defaults(handler=handler)
        if name == "grant-go-ahead":
            p.add_argument("--note", required=True)
        if name in ("fit-colour", "run"):
            p.add_argument("--manifest", type=Path, required=True)
            p.add_argument("--fixture", required=True)
            p.add_argument("--seed", type=Path, required=True)
            p.add_argument("--repository-root", type=Path, default=ROOT)
        if name == "fit-colour":
            p.add_argument("--output", type=Path, required=True)
        if name == "run":
            p.add_argument("--coarse", type=Path, required=True, help="SAM 2.1 small-circle tracker-prediction-v1")
            p.add_argument("--output-dir", type=Path, required=True)
            p.add_argument("--control-colour", type=Path)
            p.add_argument("--wiring-check", action="store_true",
                           help="plumbing test on a synthetic fixture only; not the experiment")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.handler(args)
    except (Refused, ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
