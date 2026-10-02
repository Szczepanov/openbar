#!/usr/bin/env python3
"""Tests for validation/tools/fixture_probe.py."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "validation" / "tools"
PUBLIC_MANIFEST = ROOT / "validation" / "fixtures" / "public" / "manifest.json"
PUBLIC_FIXTURE_ID = "synthetic-clean-side-12"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


schema_check = load("schema_check")
fixture_probe = load("fixture_probe")

PROBED = {
    "repository_path": "validation/private/media/sample.mp4",
    "sha256": "a" * 64,
    "duration_s": 12.345678,
    "encoded_width_px": 1920,
    "encoded_height_px": 1080,
    "rotation_deg": 90,
    "display_width_px": 1080,
    "display_height_px": 1920,
    "nominal_fps": 60,
    "measured_fps": 59.94,
    "frame_count": 740,
}
DRAFT_ARGS = {"fixture_id": "test-clean-side-01", "exercise": "clean"}


def ffprobe_stream(**overrides) -> dict:
    stream = {
        "index": 0,
        "width": 1920,
        "height": 1080,
        "sample_aspect_ratio": "1:1",
        "avg_frame_rate": "60/1",
        "r_frame_rate": "60/1",
        "nb_frames": "900",
        "duration": "15.0",
        "side_data_list": [{"rotation": 90}],
    }
    return {**stream, **overrides}


def probe_with(stream: dict, format_duration: str = "16.0") -> dict:
    output = {"streams": [stream], "format": {"duration": format_duration}}
    with (
        patch.object(fixture_probe, "compute_sha256", return_value="b" * 64),
        patch.object(fixture_probe, "run_command", return_value=MagicMock(returncode=0, stdout=json.dumps(output))),
        patch.object(Path, "is_file", return_value=True),
    ):
        return fixture_probe.probe_video(Path("mock_video.mp4"))


class FixtureProbeUnitTests(unittest.TestCase):
    def test_display_size(self):
        self.assertEqual(fixture_probe.display_size(1920, 1080, 0), (1920, 1080))
        self.assertEqual(fixture_probe.display_size(1920, 1080, 180), (1920, 1080))
        self.assertEqual(fixture_probe.display_size(1920, 1080, 90), (1080, 1920))
        self.assertEqual(fixture_probe.display_size(1920, 1080, 270), (1080, 1920))

    def test_parse_rotation_side_data(self):
        self.assertEqual(fixture_probe.parse_rotation({"side_data_list": [{"rotation": 90}]}), 90)
        self.assertEqual(fixture_probe.parse_rotation({"side_data_list": [{"rotation": -90}]}), 270)
        self.assertEqual(fixture_probe.parse_rotation({}), 0)

    def test_parse_rotation_ignores_legacy_rotate_tag(self):
        # The tag counts clockwise, the display matrix counter-clockwise; media::probe ignores it too.
        self.assertEqual(fixture_probe.parse_rotation({"tags": {"rotate": "90"}}), 0)

    def test_parse_rotation_invalid(self):
        for rotation in (45, 90.5, float("inf"), 450, "invalid"):
            with self.subTest(rotation=rotation), self.assertRaises(fixture_probe.ProbeError):
                fixture_probe.parse_rotation({"side_data_list": [{"rotation": rotation}]})

    def test_parse_fps(self):
        self.assertAlmostEqual(fixture_probe.parse_fps("30/1"), 30.0)
        self.assertAlmostEqual(fixture_probe.parse_fps("60000/1001"), 59.94005994, places=4)
        self.assertIsNone(fixture_probe.parse_fps("0/0"))
        self.assertIsNone(fixture_probe.parse_fps(None))
        self.assertIsNone(fixture_probe.parse_fps("invalid"))

    def test_probe_entries_request_only_stream_and_format_sections(self):
        # A bare `tags`/`frame`/`packet` section makes ffprobe read and decode the whole file.
        sections = {entry.split("=", 1)[0] for entry in fixture_probe.PROBE_ENTRIES.split(":")}
        self.assertEqual(sections, {"stream", "stream_side_data", "format"})

    def test_probe_video_mock(self):
        info = probe_with(ffprobe_stream())

        self.assertEqual(
            {key: value for key, value in info.items() if key != "repository_path"},
            {
                "sha256": "b" * 64,
                "duration_s": 15.0,
                "encoded_width_px": 1920,
                "encoded_height_px": 1080,
                "rotation_deg": 90,
                "display_width_px": 1080,
                "display_height_px": 1920,
                "nominal_fps": 60,
                "measured_fps": 60.0,
                "frame_count": 900,
            },
        )

    def test_probe_video_prefers_stream_duration_over_container(self):
        self.assertEqual(probe_with(ffprobe_stream(duration="15.0"), format_duration="16.0")["duration_s"], 15.0)
        self.assertEqual(probe_with(ffprobe_stream(duration=None), format_duration="16.0")["duration_s"], 16.0)

    def test_probe_video_leaves_measured_fps_unknown_without_average_rate(self):
        info = probe_with(ffprobe_stream(avg_frame_rate="0/0", r_frame_rate="30000/1001"))

        self.assertIsNone(info["measured_fps"])
        self.assertEqual(info["nominal_fps"], 30)

    def test_probe_video_requires_a_frame_rate(self):
        with self.assertRaisesRegex(fixture_probe.ProbeError, "frame rate"):
            probe_with(ffprobe_stream(avg_frame_rate="0/0", r_frame_rate="0/0"))

    def test_probe_video_rejects_non_square_pixels(self):
        for sar in ("1:1", "0:1", None):
            with self.subTest(sar=sar):
                probe_with(ffprobe_stream(sample_aspect_ratio=sar))
        with self.assertRaisesRegex(fixture_probe.ProbeError, "sample aspect ratio"):
            probe_with(ffprobe_stream(sample_aspect_ratio="4:3"))

    def test_repository_relative_path(self):
        self.assertEqual(
            fixture_probe.repository_relative_path(ROOT / "validation" / "private" / "media" / "a.mp4"),
            "validation/private/media/a.mp4",
        )
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(fixture_probe.repository_relative_path(Path(temp) / "a.mp4"))

    def test_contact_sheet_select_expr_picks_first_frame_at_or_after_each_target(self):
        self.assertEqual(
            fixture_probe.contact_sheet_select_expr([0.25, 0.5]),
            "gt(gte(t\\,0.250)*(isnan(prev_selected_t)+lt(prev_selected_t\\,0.250))"
            "+gte(t\\,0.500)*(isnan(prev_selected_t)+lt(prev_selected_t\\,0.500))\\,0)",
        )
        self.assertEqual(fixture_probe.contact_sheet_timestamps(1.0, 4), [0.2, 0.4, 0.6, 0.8])


class DraftTests(unittest.TestCase):
    def draft(self, probed: dict = PROBED, **overrides) -> dict:
        with patch.object(fixture_probe, "probe_video", return_value=probed):
            return fixture_probe.draft_fixture_manifest(Path("sample.mp4"), **{**DRAFT_ARGS, **overrides})

    def test_draft_applies_defaults(self):
        fixture = self.draft()

        self.assertEqual(fixture["purpose"], "validation")
        self.assertEqual(fixture["load"], {"plate_diameter_m": 0.45})
        self.assertEqual(fixture["camera"], {"view": "side", "movement": "fixed"})
        self.assertEqual(
            fixture["conditions"],
            {
                "lighting": "good",
                "plate_visibility": "clear",
                "occlusion": "none",
                "motion_blur": "moderate",
                "challenge_tags": ["60fps", "rotation-metadata-90"],
            },
        )
        self.assertEqual(
            fixture["source"],
            {
                "kind": "self_recorded",
                "reference": "Local recording: sample.mp4",
                "rights_holder": "Project owner",
                "redistribution_status": "private_only",
                "redistribution_evidence": "Contains identifiable people; private development and validation only.",
            },
        )
        self.assertEqual(
            fixture["video"],
            {"nominal_fps": 60, "measured_fps": 59.94, "width_px": 1920, "height_px": 1080,
             "duration_s": 12.345678, "rotation_deg": 90},
        )
        errs = schema_check.validate_document(
            {"schema_version": 1, "fixtures": [fixture]}, schema_check.load_schema(fixture_probe.MANIFEST_SCHEMA_PATH)
        )
        self.assertEqual(errs, [])

    def test_draft_omits_unknown_measured_fps(self):
        self.assertNotIn("measured_fps", self.draft({**PROBED, "measured_fps": None})["video"])

    def test_draft_overrides_defaults(self):
        fixture = self.draft(camera_view="oblique_45", lighting="low", source_kind="third_party", rights_holder=None)

        self.assertEqual(fixture["camera"]["view"], "oblique_45")
        self.assertEqual(fixture["conditions"]["lighting"], "low")
        self.assertEqual(fixture["source"]["kind"], "third_party")
        self.assertNotIn("rights_holder", fixture["source"])

    def test_draft_rejects_media_outside_repository(self):
        with self.assertRaisesRegex(fixture_probe.ProbeError, "outside the repository"):
            self.draft({**PROBED, "repository_path": None})

    def test_draft_rejects_invalid_plate_diameter(self):
        for diameter in (0.0, -0.45, float("nan"), float("inf")):
            with self.subTest(diameter=diameter), self.assertRaisesRegex(fixture_probe.ProbeError, "plate diameter"):
                self.draft(plate_diameter_m=diameter)

    def test_draft_rejects_schema_invalid_values(self):
        for overrides in ({"exercise": "bench_press"}, {"fixture_id": "Not Valid"}, {"lighting": "dim"}):
            with self.subTest(overrides=overrides), self.assertRaises(fixture_probe.ProbeError):
                self.draft(**overrides)


class AppendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manifest_path = Path(self.temp.name) / "manifest.json"
        with patch.object(fixture_probe, "probe_video", return_value=PROBED):
            self.fixture = fixture_probe.draft_fixture_manifest(Path("sample.mp4"), **DRAFT_ARGS)

    def tearDown(self):
        self.temp.cleanup()

    def read_manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def test_creates_manifest(self):
        fixture_probe.append_to_manifest(self.manifest_path, self.fixture)

        self.assertEqual(self.read_manifest(), {"schema_version": 1, "fixtures": [self.fixture]})
        self.assertEqual(list(Path(self.temp.name).iterdir()), [self.manifest_path])

    def test_refuses_to_overwrite_existing_id_without_replace(self):
        fixture_probe.append_to_manifest(self.manifest_path, self.fixture)
        before = self.manifest_path.read_bytes()

        with self.assertRaisesRegex(fixture_probe.ProbeError, "already exists"):
            fixture_probe.append_to_manifest(self.manifest_path, {**self.fixture, "notes": "changed"})
        self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_replace_keeps_other_entries_and_order(self):
        other = {**self.fixture, "id": "other-01"}
        fixture_probe.append_to_manifest(self.manifest_path, self.fixture)
        fixture_probe.append_to_manifest(self.manifest_path, other)
        changed = {**self.fixture, "notes": "changed"}

        fixture_probe.append_to_manifest(self.manifest_path, changed, replace=True)

        self.assertEqual(self.read_manifest()["fixtures"], [changed, other])

    def test_refuses_schema_invalid_manifest_and_leaves_file_untouched(self):
        fixture_probe.append_to_manifest(self.manifest_path, self.fixture)
        before = self.manifest_path.read_bytes()
        broken = {**self.fixture, "id": "broken-01", "camera": {"view": "sideways", "movement": "fixed"}}

        with self.assertRaisesRegex(fixture_probe.ProbeError, "fixture-manifest-v1"):
            fixture_probe.append_to_manifest(self.manifest_path, broken)
        self.assertEqual(self.manifest_path.read_bytes(), before)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [self.manifest_path])

    def test_refuses_non_redistributable_fixture_in_public_manifest(self):
        public_manifest = fixture_probe.PUBLIC_FIXTURES_DIR / "manifest.json"
        before = public_manifest.read_bytes()

        with self.assertRaisesRegex(fixture_probe.ProbeError, "public manifest"):
            fixture_probe.append_to_manifest(public_manifest, self.fixture)
        self.assertEqual(public_manifest.read_bytes(), before)


class MainTests(unittest.TestCase):
    def run_main(self, *argv: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = fixture_probe.main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_inspect_failure_exits_nonzero_and_json_is_always_a_list(self):
        code, stdout, stderr = self.run_main("inspect", "--json", "does-not-exist.mp4")

        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout), [])
        self.assertIn("file not found", stderr)

    def test_draft_cli_defaults_match_function_defaults(self):
        args = fixture_probe.parse_args(["draft", "a.mp4", "--id", "x", "--exercise", "clean"])

        self.assertEqual(
            (args.purpose, args.plate_diameter_m, args.view, args.movement, args.lighting, args.plate_visibility,
             args.occlusion, args.motion_blur, args.source_kind, args.rights_holder),
            ("validation", 0.45, "side", "fixed", "good", "clear", "none", "moderate", "self_recorded",
             "Project owner"),
        )

    def test_draft_error_exits_nonzero_without_traceback(self):
        code, _, stderr = self.run_main(
            "draft", "does-not-exist.mp4", "--id", "x", "--exercise", "clean"
        )

        self.assertEqual(code, 1)
        self.assertTrue(stderr.startswith("error: file not found"))


def png_size(path: Path) -> tuple[int, int]:
    return struct.unpack(">II", path.read_bytes()[16:24])


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe") or os.environ.get("OPENBAR_REQUIRE_FFMPEG") == "1",
    "SKIPPED: ffmpeg/ffprobe not on PATH",
)
class FfmpegIntegrationTests(unittest.TestCase):
    def setUp(self):
        manifest = json.loads(PUBLIC_MANIFEST.read_text(encoding="utf-8"))
        self.entry = next(f for f in manifest["fixtures"] if f["id"] == PUBLIC_FIXTURE_ID)
        self.media = ROOT / self.entry["media"]["repository_path"]

    def test_probe_matches_public_manifest_entry(self):
        info = fixture_probe.probe_video(self.media)
        video = self.entry["video"]

        self.assertEqual(
            (info["repository_path"], info["sha256"], info["encoded_width_px"], info["encoded_height_px"],
             info["rotation_deg"], info["nominal_fps"], info["measured_fps"], info["duration_s"], info["frame_count"]),
            (self.entry["media"]["repository_path"], self.entry["media"]["sha256"], video["width_px"],
             video["height_px"], video["rotation_deg"], video["nominal_fps"], video["measured_fps"],
             video["duration_s"], 12),
        )

    def test_select_expression_keeps_one_frame_per_target(self):
        timestamps = fixture_probe.contact_sheet_timestamps(1.0, 4)
        select = fixture_probe.contact_sheet_select_expr(timestamps)
        completed = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(self.media), "-vf", f"select='{select}',showinfo",
             "-f", "null", "-"],
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

        selected = [round(float(t), 4) for t in re.findall(r"pts_time:([0-9.]+)", completed.stderr)]
        # 12 fps: the first frames at or after 0.2, 0.4, 0.6, 0.8 s are frames 3, 5, 8, 10.
        self.assertEqual(selected, [0.25, 0.4167, 0.6667, 0.8333])

    def test_contact_sheet_grid_size(self):
        with tempfile.TemporaryDirectory() as temp:
            output = fixture_probe.generate_contact_sheet(
                self.media, Path(temp) / "sheet.png", rows=2, cols=2, thumb_width_px=160
            )

            # Two 160x120 tiles per axis plus one 4 px padding gap.
            self.assertEqual(png_size(output), (324, 244))


if __name__ == "__main__":
    unittest.main()
