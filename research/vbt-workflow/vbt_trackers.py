"""The --tracker choices of the personal VBT workflow (#86). Standard library only.

Each tracker is an existing research script that writes tracker-prediction-v1; this module only
describes how to call it and what it records. No measurement logic lives here.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vbt_process import Runner, WorkflowError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "tools"))
import schema_check  # noqa: E402
TRACK_SCRIPT = ROOT / "research" / "opencv-tracking" / "track.py"
GPU_TRACK_SCRIPT = ROOT / "research" / "gpu-tracking" / "track_gpu.py"
GEOMETRY_SIDECAR_FORMAT = "openbar-research-geometry-sidecar"

# Runs in the --gpu-python interpreter before tracking: SAM 2 needs torch with a visible CUDA device.
CUDA_PROBE = (
    "import json, torch; "
    "from sam2.build_sam import build_sam2_video_predictor; "
    "available = torch.cuda.is_available(); "
    "print(json.dumps({'torch': torch.__version__, 'cuda_available': available, "
    "'device': torch.cuda.get_device_name(0) if available else None, 'sam2_importable': True}))"
)


@dataclass(frozen=True)
class TrackerSpec:
    """One --tracker choice: which research script produces its prediction-v1, and what it writes."""

    name: str  # the --tracker value
    implementation: str  # the prediction's implementation.name; also part of every output file name
    script: Path
    needs_gpu_python: bool  # False: the interpreter running the workflow (research venv); True: --gpu-python
    geometry_sidecar: bool  # the tracker also writes a circle-fit geometry sidecar
    determinism: dict[str, str]  # copied verbatim into the run record


TRACKERS: dict[str, TrackerSpec] = {
    "csrt": TrackerSpec(
        name="csrt",
        implementation="opencv-csrt",
        script=TRACK_SCRIPT,
        needs_gpu_python=False,
        geometry_sidecar=False,
        determinism={
            "prediction": "byte_identical_rerun",
            "basis": "track.py --omit-runtime on CPU; checked by the OPENBAR_VBT_E2E=1 test",
        },
    ),
    "sam2.1-bplus-circle": TrackerSpec(
        name="sam2.1-bplus-circle",
        implementation="sam2.1-bplus-circle",
        script=GPU_TRACK_SCRIPT,
        needs_gpu_python=True,
        geometry_sidecar=True,
        determinism={
            "prediction": "byte_identical_rerun_observed_same_gpu_stack",
            "basis": "track_gpu.py --omit-runtime; two runs on one RTX 3060 Ti gave identical samples (max centre "
                     "difference 0 px) on the 12-frame public synthetic fixture and an 832-frame private clip. "
                     "Observed, not guaranteed: another GPU model, driver, torch or CUDA build may differ; "
                     "implementation.config in the prediction records them.",
        },
    ),
}


def tracker_arguments(spec: TrackerSpec, prediction: str, geometry: str | None) -> list[str]:
    """Tracker-specific flags after the common --manifest/--fixture/--seed. Always --omit-runtime."""
    if spec.script == GPU_TRACK_SCRIPT:
        if geometry is None:
            raise ValueError(f"{spec.name} writes a geometry sidecar and needs its path")
        return ["--candidate", spec.implementation, "--omit-runtime",
                "--output", prediction, "--geometry-output", geometry]
    return ["--tracker", spec.name, "--omit-runtime", "--output", prediction]


def tracker_environment(spec: TrackerSpec, config: dict[str, Any], gpu_python: str | None) -> dict[str, Any]:
    """Tracker-side versions, copied from the prediction's own implementation.config."""
    if not spec.needs_gpu_python:
        return {"opencv_version": config.get("opencv_version"), "numpy_version": config.get("numpy_version")}
    return {
        "gpu_python": {"path": gpu_python},
        "gpu_model": config.get("gpu_model"),
        "driver_version": config.get("driver_version"),
        "tracker_dependencies": config.get("dependencies"),
    }


def commands_note(spec: TrackerSpec) -> str:
    if spec.needs_gpu_python:
        return ("Run from the repository root; the track step uses the GPU venv interpreter at its recorded path, "
                "and the analyze step needs no particular Python.")
    return "Run from the repository root; 'python' is the research venv interpreter."


def require_cuda(runner: Runner, gpu_python: str) -> None:
    """Fail before registration or tracking unless the GPU interpreter has torch and a CUDA device."""
    try:
        text = runner.capture([gpu_python, "-c", CUDA_PROBE])
    except WorkflowError as error:
        raise WorkflowError(
            f"--gpu-python {gpu_python} cannot load the torch/SAM 2 environment required by "
            f"--tracker sam2.1-bplus-circle: {error}"
        ) from error
    try:
        probe = json.loads(text.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as error:
        raise WorkflowError(
            f"--gpu-python {gpu_python} gave an unreadable CUDA check: {text.strip()[-200:]!r}"
        ) from error
    if not isinstance(probe, dict) or probe.get("cuda_available") is not True:
        torch_version = probe.get("torch") if isinstance(probe, dict) else None
        raise WorkflowError(
            f"no CUDA device is available to {gpu_python} (torch {torch_version}); "
            "--tracker sam2.1-bplus-circle needs an NVIDIA GPU and a CUDA build of torch. Use --tracker csrt instead"
        )


def require_tracker_outputs(spec: TrackerSpec, fixture_id: str, prediction: dict[str, Any],
                            staged: dict[str, Path]) -> None:
    """The staged prediction (and sidecar) must come from the requested tracker for this fixture."""
    name = prediction.get("implementation", {}).get("name")
    if name != spec.implementation or prediction.get("fixture_id") != fixture_id:
        raise WorkflowError(
            f"tracker prediction is from {name!r} for {prediction.get('fixture_id')!r}; "
            f"expected {spec.implementation!r} for {fixture_id!r}"
        )
    if not spec.geometry_sidecar:
        return
    try:
        sidecar = schema_check.load_strict(staged["geometry"])
    except (schema_check.DocumentError, OSError) as error:
        raise WorkflowError(f"cannot read the geometry sidecar: {error}") from error
    if (not isinstance(sidecar, dict) or sidecar.get("format") != GEOMETRY_SIDECAR_FORMAT
            or sidecar.get("format_version") != 0
            or sidecar.get("fixture_id") != fixture_id
            or sidecar.get("implementation", {}).get("name") != spec.implementation):
        raise WorkflowError(f"the geometry sidecar is not a {GEOMETRY_SIDECAR_FORMAT} format 0 for "
                            f"{spec.implementation} and {fixture_id}")
    if sidecar.get("implementation") != prediction.get("implementation"):
        raise WorkflowError("the geometry sidecar implementation/provenance does not match the tracker prediction")

    prediction_samples = prediction.get("samples")
    geometry_samples = sidecar.get("samples")
    if not isinstance(prediction_samples, list) or not isinstance(geometry_samples, list):
        raise WorkflowError("the geometry sidecar is not aligned with the tracker prediction: samples must be lists")
    if len(geometry_samples) != len(prediction_samples):
        raise WorkflowError(
            "the geometry sidecar is not aligned with the tracker prediction: "
            f"{len(geometry_samples)} geometry samples for {len(prediction_samples)} prediction samples"
        )
    for index, (prediction_sample, geometry_sample) in enumerate(zip(prediction_samples, geometry_samples)):
        if (not isinstance(prediction_sample, dict) or not isinstance(geometry_sample, dict)
                or geometry_sample.get("timestamp_s") != prediction_sample.get("timestamp_s")):
            raise WorkflowError(
                "the geometry sidecar is not aligned with the tracker prediction: "
                f"timestamp mismatch at sample {index}"
            )
