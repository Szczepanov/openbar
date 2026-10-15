"""Run the three voters through their existing research scripts (no measurement logic here).

Held-out fixtures (`validation/tools/held_out_guard.py`) are always refused. There is deliberately
no override: lifting that is the owner's decision and would be a separate, reviewed change.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "validation" / "tools"))
import held_out_guard  # noqa: E402


class RunError(RuntimeError):
    """A voter could not be started or a guard refused the fixture."""


@dataclass(frozen=True)
class Voter:
    name: str
    interpreter: str  # "cpu" (research OpenCV venv) or "gpu" (GPU venv)
    script: str  # repository-relative
    extra: tuple[str, ...]
    geometry_sidecar: bool = False


# Different principles on purpose: learned segmentation + circle fit, learned point tracking and
# classical optical flow. CSRT is excluded because the clean-room candidate under #57 is CSRT-class.
VOTERS: tuple[Voter, ...] = (
    Voter("bootstapir-affine", "gpu", "research/gpu-tracking/track_gpu.py",
          ("--candidate", "bootstapir-affine")),
    Voter("lk-affine", "cpu", "research/opencv-tracking/track.py", ("--tracker", "lk-affine")),
    Voter("sam2.1-bplus-circle", "gpu", "research/gpu-tracking/track_gpu.py",
          ("--candidate", "sam2.1-bplus-circle"), geometry_sidecar=True),
)


def refuse_held_out(manifest: Path, fixture_id: str) -> None:
    document = json.loads(manifest.read_text(encoding="utf-8"))
    matches = [f for f in document.get("fixtures", []) if f.get("id") == fixture_id]
    if len(matches) != 1:
        raise RunError(f"fixture {fixture_id!r} is not exactly once in {manifest}")
    if held_out_guard.is_held_out(matches[0]):
        raise RunError(
            f"{fixture_id} is held-out (purpose validation or split-reserved-57): refused. "
            "Voters never run on held-out clips without a separate owner decision."
        )


def output_path(out_dir: Path, fixture_id: str, voter: Voter) -> Path:
    return out_dir / f"{fixture_id}.{voter.name}.prediction.json"


def build_commands(repo_root: Path, manifest: Path, fixture_id: str, seed: Path, out_dir: Path,
                   cpu_python: str, gpu_python: str) -> list[tuple[Voter, list[str], Path]]:
    commands = []
    for voter in VOTERS:
        target = output_path(out_dir, fixture_id, voter)
        argv = [cpu_python if voter.interpreter == "cpu" else gpu_python,
                str(repo_root / voter.script), "--manifest", str(manifest), "--fixture", fixture_id,
                "--seed", str(seed), *voter.extra, "--omit-runtime", "--output", str(target)]
        if voter.geometry_sidecar:
            argv += ["--geometry-output", str(out_dir / f"{fixture_id}.{voter.name}.geometry.json")]
        commands.append((voter, argv, target))
    return commands


def run(repo_root: Path, manifest: Path, fixture_id: str, seed: Path, out_dir: Path,
        cpu_python: str, gpu_python: str, force: bool = False) -> list[Path]:
    refuse_held_out(manifest, fixture_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for voter, argv, target in build_commands(repo_root, manifest, fixture_id, seed, out_dir,
                                              cpu_python, gpu_python):
        if target.exists() and not force:
            raise RunError(f"{target} exists; pass --force to replace it")
        result = subprocess.run(argv, cwd=repo_root, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RunError(f"{voter.name} failed ({result.returncode}): {result.stderr[-400:].strip()}")
        written.append(target)
    return written
