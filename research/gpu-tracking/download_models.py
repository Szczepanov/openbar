#!/usr/bin/env python3
"""Download and verify model weights for GPU tracker bake-off (#57 Phase 3).

Models:
  - SAM 2.1 small: sam2.1_hiera_small.pt
  - SAM 2.1 base+: sam2.1_hiera_base_plus.pt
  - Cutie base: cutie-base-mega.pth
  - BootsTAPIR: bootstapir_checkpoint_v2.pt
  - CoTracker3: scaled_offline.pth

Weights are saved to validation/private/models/ and verified by SHA-256.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / "validation" / "private" / "models"

MODEL_SPECS = {
    "sam2.1_small": {
        "filename": "sam2.1_hiera_small.pt",
        "url": "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt",
        "sha256": "6d1aa6f30de5c92224f8172114de081d104bbd23dd9dc5c58996f0cad5dc4d38",
    },
    "sam2.1_bplus": {
        "filename": "sam2.1_hiera_base_plus.pt",
        "url": "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_base_plus.pt",
        "sha256": "a2345aede8715ab1d5d31b4a509fb160c5a4af1970f199d9054ccfb746c004c5",
    },
    "cutie_base": {
        "filename": "cutie-base-mega.pth",
        "url": "https://github.com/hkchengrex/Cutie/releases/download/v1.0/cutie-base-mega.pth",
        "sha256": "9c05402ee36d3a356fb72715d263ba7e1ea06ad3bada48c1306491792da43023",
    },
    "bootstapir": {
        "filename": "bootstapir_checkpoint_v2.pt",
        "url": "https://storage.googleapis.com/dm-tapnet/bootstap/bootstapir_checkpoint_v2.pt",
        "sha256": "8493c7a69e02c85b9382fbb3c7b8b539b36bc08ede744b9e99feb739a0129f4b",
    },
    "cotracker3": {
        "filename": "scaled_offline.pth",
        "url": "https://huggingface.co/facebook/cotracker3/resolve/main/scaled_offline.pth",
        "sha256": "2670d4562ed69326dda775a26e54883925cd11b6fc9b24cb7aa9f8078bce7834",
    },
}


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def download_file(url: str, dest: Path) -> str:
    print(f"Downloading {url} -> {dest} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    temp_path = dest.with_suffix(".download")
    try:
        with urllib.request.urlopen(req) as resp, open(temp_path, "wb") as f:
            while chunk := resp.read(65536):
                f.write(chunk)
        temp_path.replace(dest)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    digest = compute_sha256(dest)
    print(f"  Saved {dest.name} ({dest.stat().st_size} bytes, sha256: {digest})")
    return digest


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    all_ok = True
    for key, spec in MODEL_SPECS.items():
        filename = spec["filename"]
        target = MODELS_DIR / filename
        expected_sha = spec.get("sha256")
        if target.is_file():
            actual_sha = compute_sha256(target)
            if expected_sha and actual_sha != expected_sha.lower():
                print(f"SHA-256 mismatch for {filename}: expected {expected_sha}, got {actual_sha}. Re-downloading...")
                target.unlink()
                actual_sha = download_file(spec["url"], target)
            else:
                print(f"Already exists: {filename} (sha256: {actual_sha})")
        else:
            try:
                actual_sha = download_file(spec["url"], target)
            except Exception as e:
                print(f"Failed to download {filename}: {e}", file=sys.stderr)
                all_ok = False
                continue

        if expected_sha and actual_sha != expected_sha.lower():
            print(f"ERROR: SHA-256 mismatch for {filename}: expected {expected_sha}, got {actual_sha}", file=sys.stderr)
            all_ok = False

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
