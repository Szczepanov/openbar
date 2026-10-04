"""Harness for the #95 orchestration tests (not a test module): fake decoder, tracker and analyzer.

FFmpeg, OpenCV, cargo, git and CUDA are replaced: label_package.build writes a one-frame package with
the real page template and metadata, the suggester is a stub, and the fake runner (from the #86
tests) writes a prediction and a schema-valid analysis whose identity matches the video. Everything
else is real: the CSV contract, registration, annotations.build_seed, scale_reference.py, the
report and the session record.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_fakes as fakes  # noqa: E402
import test_analyze_lift  # noqa: E402

import analyze_lift  # noqa: E402
import label_package  # noqa: E402
import session_ingest  # noqa: E402
import session_run  # noqa: E402
import vbt_session  # noqa: E402

ROOT = analyze_lift.ROOT
FRAME_PNG = b"\x89PNG\r\n\x1a\nfake frame"
FPS = 60.0
GOLDEN = ROOT / "crates" / "openbar-core" / "tests" / "fixtures" / "analysis-v1.golden.json"


def fake_probe(path: Path) -> dict[str, Any]:
    return {**test_analyze_lift.fake_probe(path), "encoded_width_px": 1080, "encoded_height_px": 1920,
            "display_width_px": 1080, "display_height_px": 1920, "rotation_deg": 0}


def fake_build(args: argparse.Namespace) -> Path:
    """label_package.build without FFmpeg: same metadata and page, one stand-in frame."""
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    fixture = next(item for item in manifest["fixtures"] if item["id"] == args.fixture)
    index = args.frame_index if args.frame_index is not None else round(args.at_s * FPS)
    output = Path(args.output_dir)
    label_package.require_safe_output(fixture, output)
    (output / "frames").mkdir(parents=True, exist_ok=True)
    (output / "frames" / f"frame_{index:06d}.png").write_bytes(FRAME_PNG)
    size = (1080, 1920)
    label_package.write_text(output / "metadata.json", json.dumps(
        label_package.metadata(fixture, size, args.annotator_id, "fake"), indent=2) + "\n")
    config = label_package.page_config(fixture["id"], args.annotator_id, size, [
        {"file": f"frames/frame_{index:06d}.png", "frame_index": index, "timestamp_s": round(index / FPS, 6)}])
    label_package.write_text(output / "index.html", label_package.render_page(
        label_package.PAGE_TEMPLATE.read_text(encoding="utf-8"), config))
    return output


def fake_extract(media: Path, indices: list[int], expected: list[int], frames_dir: Path,
                 fixture: dict[str, Any]) -> list[Path]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    paths = [frames_dir / f"frame_{index:06d}.png" for index in indices]
    for path in paths:
        path.write_bytes(FRAME_PNG)
    return paths


def fake_cropper(frame: Path, left: int, top: int, right: int, bottom: int) -> bytes:
    return f"crop {frame.name} {left} {top} {right} {bottom}".encode()


class SessionRunner(test_analyze_lift.FakeRunner):
    """The #86 fake, with an analysis whose identity is the tracked video (scale_reference checks it)."""

    def execute(self, argv: list[str]) -> None:
        super().execute(argv)
        if test_analyze_lift.is_track_step(argv):
            return
        output = ROOT / argv[argv.index("--output") + 1]
        manifest = json.loads((ROOT / argv[argv.index("--manifest") + 1]).read_text(encoding="utf-8"))
        fixture_id = argv[argv.index("--fixture") + 1]
        sha = next(f["media"]["sha256"] for f in manifest["fixtures"] if f["id"] == fixture_id)
        analysis = json.loads(GOLDEN.read_text(encoding="utf-8"))
        analysis["identity"] = {**analysis["identity"], "fixture_id": fixture_id, "source_id": fixture_id,
                                "source_sha256": sha}
        output.write_text(json.dumps(analysis, indent=2) + "\n", encoding="utf-8")


class SessionTestCase(unittest.TestCase):
    """Temporary inbox, media, manifest (under target/) and sessions root (under validation/private/vbt/)."""

    def setUp(self) -> None:
        self.dir = test_analyze_lift.repo_temp_dir(self, "vbt-session-test-")
        analyze_lift.PERSONAL_ROOT.mkdir(parents=True, exist_ok=True)
        sessions = tempfile.TemporaryDirectory(prefix="test-sessions-", dir=analyze_lift.PERSONAL_ROOT)
        self.addCleanup(sessions.cleanup)
        self.sessions = Path(sessions.name)
        self.inbox = self.dir / "inbox"
        self.inbox.mkdir()
        self.media = self.dir / "media"
        self.manifest = self.dir / "vbt" / "manifest.json"
        self.downloads = self.dir / "downloads"
        self.downloads.mkdir()
        self.session_id = "2026-10-03"
        for target, replacement in ((analyze_lift.fixture_probe, "probe_video"),):
            patcher = mock.patch.object(target, replacement, side_effect=fake_probe)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("build", fake_build), ("extract", fake_extract),
                            ("probe", lambda media: {"pts": list(range(600))})):
            patcher = mock.patch.object(label_package, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @property
    def session_dir(self) -> Path:
        return self.sessions / self.session_id

    def add_video(self, name: str, content: bytes) -> str:
        (self.inbox / name).write_bytes(content)
        return hashlib.sha256(content).hexdigest()

    def common(self) -> list[str]:
        return ["--session", self.session_id, "--sessions-root", str(self.sessions), "--manifest", str(self.manifest)]

    def ingest(self, *extra: str, suggestions: dict[str, Any] | None = None) -> tuple[int, str, str]:
        argv = ["ingest", *self.common(), "--inbox", str(self.inbox), "--media-dir", str(self.media), *extra]
        return self.main(argv, suggester=lambda frame: json.loads(json.dumps(suggestions or fakes.suggestions())))

    def main(self, argv: list[str], runner: SessionRunner | None = None, **kwargs: Any) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = vbt_session.main(argv, runner=runner or SessionRunner(), cropper=fake_cropper, **kwargs)
        return code, stdout.getvalue(), stderr.getvalue()

    def state(self) -> dict[str, Any]:
        return json.loads((self.session_dir / session_ingest.STATE_NAME).read_text(encoding="utf-8"))

    def write_csv(self, rows: list[dict[str, str]], name: str | None = None) -> Path:
        path = self.downloads / (name or vbt_session.csv_name(self.session_id))
        path.write_bytes(fakes.csv_bytes(rows))
        return path

    def run_args(self, csv: Path, policy: str = "csrt-all-v1", *extra: str) -> list[str]:
        return ["run", *self.common(), "--csv", str(csv), "--plate-diameter-m", "0.45", "--stick-length-m", "1.30",
                "--tracker-policy", policy, "--preset", "vbt-sg-0.15s-v1", *extra]

    def run_session(self, rows: list[dict[str, str]], *extra: str, policy: str = "csrt-all-v1",
                    runner: SessionRunner | None = None) -> tuple[int, str, str]:
        return self.main(self.run_args(self.write_csv(rows), policy, *extra), runner=runner)

    def snapshot(self) -> dict[str, bytes]:
        root = self.session_dir
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in sorted(root.rglob("*")) if path.is_file()} if root.exists() else {}

    def make_gpu_python(self) -> Path:
        return test_analyze_lift.WorkflowTestCase.make_gpu_python(self)  # type: ignore[arg-type]
