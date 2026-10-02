#!/usr/bin/env python3
"""Download and verify ONNX models for OpenCV neural trackers (Phase 1)."""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / "models"

MODEL_SOURCES = {
    "vittrack": [
        {
            "filename": "object_tracking_vittrack_2023sep.onnx",
            "url": "https://huggingface.co/opencv/object_tracking_vittrack/resolve/main/object_tracking_vittrack_2023sep.onnx",
        }
    ],
    "nanotrack": [
        {
            "filename": "nanotrack_backbone_sim.onnx",
            "url": "https://github.com/HonglinChu/SiamTrackers/raw/master/NanoTrack/models/nanotrackv2/nanotrack_backbone_sim.onnx",
        },
        {
            "filename": "nanotrack_head_sim.onnx",
            "url": "https://github.com/HonglinChu/SiamTrackers/raw/master/NanoTrack/models/nanotrackv2/nanotrack_head_sim.onnx",
        },
    ],
    "dasiamrpn": [
        {
            "filename": "dasiamrpn_model.onnx",
            "url": "https://files.kde.org/kdenlive/motion-tracker/DaSiamRPN/dasiamrpn_model.onnx",
        },
        {
            "filename": "dasiamrpn_kernel_r1.onnx",
            "url": "https://files.kde.org/kdenlive/motion-tracker/DaSiamRPN/dasiamrpn_kernel_r1.onnx",
        },
        {
            "filename": "dasiamrpn_kernel_cls1.onnx",
            "url": "https://files.kde.org/kdenlive/motion-tracker/DaSiamRPN/dasiamrpn_kernel_cls1.onnx",
        },
    ],
}


def download_file(url: str, dest: Path) -> str:
    print(f"Downloading {url} -> {dest} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as f:
        while chunk := resp.read(65536):
            f.write(chunk)
    h = hashlib.sha256()
    with open(dest, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    digest = h.hexdigest()
    print(f"  Saved {dest.name} ({dest.stat().st_size} bytes, sha256: {digest})")
    return digest


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    for tracker, files in MODEL_SOURCES.items():
        results[tracker] = {}
        for entry in files:
            path = MODELS_DIR / entry["filename"]
            if not path.is_file():
                digest = download_file(entry["url"], path)
            else:
                h = hashlib.sha256()
                with open(path, "rb") as f:
                    while chunk := f.read(65536):
                        h.update(chunk)
                digest = h.hexdigest()
                print(f"Already exists: {path.name} (sha256: {digest})")
            results[tracker][entry["filename"]] = digest
    print("\nSHA-256 summary:")
    import json
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
