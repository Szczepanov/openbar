from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "validation" / "tools"
MANIFEST_PATH = ROOT / "validation" / "fixtures" / "public" / "manifest.json"
FIXTURE_ID = "synthetic-clean-side-12"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"openbar_{name}", TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


label_package = load("label_package")
annotations = load("annotations")


def private_fixture() -> dict:
    fixture = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["fixtures"][0]
    return {**fixture, "source": {**fixture["source"], "redistribution_status": "private_only"}}


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", header[16:24])


class FrameSelectionTests(unittest.TestCase):
    def test_grid_picks_nearest_decoded_frames_and_keeps_explicit_frames(self):
        timestamps = [0.0, 0.031, 0.067, 0.1, 0.133, 0.17, 0.2]

        self.assertEqual(label_package.select_frames(timestamps, 0.0, 0.2, 0.1, [5]), [0, 3, 5, 6])

    def test_grid_times_between_frames_do_not_duplicate_indices(self):
        self.assertEqual(label_package.select_frames([0.0, 1.0], 0.0, 1.0, 0.25, []), [0, 1])

    def test_invalid_grid_or_frame_is_rejected(self):
        with self.assertRaises(label_package.PackageError):
            label_package.select_frames([0.0, 1.0], 0.0, 1.0, 0.0, [])
        with self.assertRaises(label_package.PackageError):
            label_package.select_frames([0.0, 1.0], 1.0, 0.0, 0.5, [])
        with self.assertRaises(label_package.PackageError):
            label_package.select_frames([0.0, 1.0], 0.0, 1.0, 0.5, [2])

    def test_grid_rejects_non_finite_and_out_of_media_windows(self):
        for start, end, step in [
            (-0.01, 1.0, 0.5),
            (0.0, 1.01, 0.5),
            (float("nan"), 1.0, 0.5),
            (0.0, 1.0, float("inf")),
            (0.0, 1.0, 1e-300),
        ]:
            with self.assertRaises(label_package.PackageError):
                label_package.select_frames([0.0, 0.5, 1.0], start, end, step, [])

    def test_display_size_swaps_for_quarter_turn_rotation(self):
        self.assertEqual(label_package.display_size(1280, 720, 90), (720, 1280))
        self.assertEqual(label_package.display_size(1280, 720, 270), (720, 1280))
        self.assertEqual(label_package.display_size(1280, 720, 180), (1280, 720))


def probe_json(stream_extra: dict, frames: list) -> str:
    stream = {"width": 1280, "height": 720, "time_base": "1/90000", "start_pts": 0, **stream_extra}
    return json.dumps({"streams": [stream], "frames": frames})


class ProbeTests(unittest.TestCase):
    def test_parses_timestamps_from_start_pts_and_rotation(self):
        probed = label_package.parse_probe(probe_json(
            {"start_pts": 900, "side_data_list": [{"rotation": -90}], "sample_aspect_ratio": "1:1"},
            [{"pts": 900}, {"pts": 3885}, {"pts": 6870}],
        ))

        self.assertEqual(probed["timestamps_s"], [0.0, 2985 / 90000, 5970 / 90000])
        self.assertEqual(probed["rotation_deg"], 270)
        self.assertEqual(probed["pts"], [900, 3885, 6870])

    def test_rejects_media_the_rust_frame_source_rejects(self):
        missing_start = json.loads(probe_json({}, [{"pts": 0}]))
        del missing_start["streams"][0]["start_pts"]
        cases = {
            "sar": probe_json({"sample_aspect_ratio": "4:3"}, [{"pts": 0}]),
            "rotation": probe_json({"side_data_list": [{"rotation": 45}]}, [{"pts": 0}]),
            "zero width": probe_json({"width": 0}, [{"pts": 0}]),
            "zero height": probe_json({"height": 0}, [{"pts": 0}]),
            "zero timebase numerator": probe_json({"time_base": "0/1"}, [{"pts": 0}]),
            "zero timebase denominator": probe_json({"time_base": "1/0"}, [{"pts": 0}]),
            "invalid timebase": probe_json({"time_base": "x/90000"}, [{"pts": 0}]),
            "missing start": json.dumps(missing_start),
            "before start": probe_json({"start_pts": 10}, [{"pts": 5}]),
            "missing pts": probe_json({}, [{"pts": 0}, {}]),
            "non-increasing": probe_json({}, [{"pts": 5}, {"pts": 5}]),
            "no frames": probe_json({}, []),
            "no stream": json.dumps({"streams": [], "frames": [{"pts": 0}]}),
        }
        for name, text in cases.items():
            with self.assertRaises(label_package.PackageError, msg=name):
                label_package.parse_probe(text)


class FixtureProbeContractTests(unittest.TestCase):
    def test_fixture_raster_and_rotation_must_match_probe(self):
        fixture = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["fixtures"][0]
        video = fixture["video"]
        probed = {
            "width_px": video["width_px"],
            "height_px": video["height_px"],
            "rotation_deg": video.get("rotation_deg", 0),
        }

        label_package.require_fixture_probe_match(fixture, probed)
        for key, value in [
            ("width_px", probed["width_px"] + 1),
            ("height_px", probed["height_px"] + 1),
            ("rotation_deg", (probed["rotation_deg"] + 90) % 360),
        ]:
            changed = {**probed, key: value}
            with self.assertRaises(label_package.PackageError, msg=key):
                label_package.require_fixture_probe_match(fixture, changed)


class AlignmentTests(unittest.TestCase):
    STDERR = (
        "[Parsed_showinfo_1 @ 0000] n:   0 pts:   3072 pts_time:0.25    duration: 1024\n"
        "[Parsed_showinfo_1 @ 0000] n:   1 pts:   6144 pts_time:0.5     duration: 1024\n"
    )

    def test_showinfo_pts_must_equal_probed_pts(self):
        extracted = label_package.parse_showinfo(self.STDERR)

        self.assertEqual(extracted, [3072, 6144])
        label_package.require_aligned(extracted, [3072, 6144])
        with self.assertRaises(label_package.PackageError):
            label_package.require_aligned(extracted, [3072, 7168])
        with self.assertRaises(label_package.PackageError):
            label_package.require_aligned(extracted[:1], [3072, 6144])


class PageContractTests(unittest.TestCase):
    def test_page_exports_exactly_the_import_csv_columns(self):
        page = label_package.PAGE_TEMPLATE.read_text(encoding="utf-8")
        header = re.search(r'const head = "([^"]+)"', page).group(1).split(",")

        self.assertEqual(len(header), len(annotations.CSV_COLUMNS))
        self.assertEqual(set(header), annotations.CSV_COLUMNS)

    def test_config_is_injected_exactly_once(self):
        page = label_package.render_page("a /*CONFIG*/null b", {"fixture_id": "x</script>", "frames": []})

        self.assertEqual(page, 'a {"fixture_id": "x<\\/script>", "frames": []} b')
        with self.assertRaises(label_package.PackageError):
            label_package.render_page("no placeholder", {})

    def test_page_format_csv_imports_with_generated_metadata(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        fixture = manifest["fixtures"][0]
        sidecar = label_package.metadata(fixture, (320, 240), "pass-a", "grid")
        sidecar["provenance"]["annotated_at"] = "2026-10-01T12:00:00Z"
        rows = [
            "timestamp_s,requested_timestamp_s,frame_index,annotation_state,visibility,quality,x_px,y_px,"
            "radius_px,diameter_px,left_px,top_px,right_px,bottom_px,notes",
            "0.000000,,0,labelled,visible,high,100.25,189.75,24.10,,,,,,",
            "0.083333,,1,unlabelable,fully_occluded,unusable,,,,,,,,,",
            "0.166667,,2,not_annotated,visible,not_assessed,,,,,,,,,",
        ]
        with tempfile.TemporaryDirectory() as temp:
            csv_path = Path(temp) / "labels.csv"
            csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            document = annotations.import_csv(sidecar, csv_path, manifest)

        annotations.validate_annotation(document, manifest)
        self.assertEqual(document["provenance"]["tool"], label_package.TOOL)
        self.assertEqual(document["samples"][0]["center_px"], {"x_px": 100.25, "y_px": 189.75})



    @unittest.skipUnless(shutil.which("node"), "SKIPPED: node not on PATH")
    def test_page_blocks_export_until_label_quality_is_explicit(self):
        page = label_package.PAGE_TEMPLATE.read_text(encoding="utf-8")
        function = re.search(r"// BEGIN exportProblem.*?\n(.*?)// END exportProblem", page, re.S).group(1)
        frames = [{"timestamp_s": 0.0, "frame_index": 7}]
        unassessed = {"0": {"state": "labelled", "x": 10.0, "y": 20.0, "quality": None}}
        assessed = {"0": {"state": "labelled", "x": 10.0, "y": 20.0, "quality": "medium"}}

        for labels, expect_problem in [(unassessed, True), (assessed, False)]:
            script = function + (
                f"const value = exportProblem({json.dumps(frames)}, {json.dumps(labels)});"
                "process.stdout.write(value || '');"
            )
            output = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout
            self.assertEqual(bool(output), expect_problem)
            if expect_problem:
                self.assertIn("choose quality", output)

    @unittest.skipUnless(shutil.which("node"), "SKIPPED: node not on PATH")
    def test_page_keys_never_invent_quality_or_radius(self):
        page = label_package.PAGE_TEMPLATE.read_text(encoding="utf-8")
        functions = re.search(r"// BEGIN keyActions.*?\n(.*?)// END keyActions", page, re.S).group(1)
        centred = {"state": "labelled", "visibility": "visible", "quality": None, "x": 10.0, "y": 20.0, "r": None}
        empty = {"state": "not_annotated", "visibility": "visible", "quality": None, "x": None, "y": None, "r": None}
        cases = [
            # (record, key, ring) -> expected {rec, advance} or None
            (centred, "2", None, {"rec": {**centred, "quality": "medium"}, "advance": True}),
            (empty, "1", 40.0, {"rec": {**empty, "quality": "high"}, "advance": False}),
            (centred, "]", 40.0, {"rec": {**centred, "r": 40.0}, "advance": False}),
            (empty, "]", 40.0, None),
            (centred, "]", None, None),
            (centred, "o", 40.0, {"rec": {**centred, "visibility": "partially_occluded"}, "advance": False}),
            (centred, "f", 40.0, {"rec": {**centred, "state": "unlabelable", "visibility": "fully_occluded",
                                          "x": None, "y": None}, "advance": True}),
            (centred, "u", 40.0, {"rec": {**centred, "state": "unlabelable", "x": None, "y": None}, "advance": True}),
            (centred, "s", 40.0, {"rec": {**centred, "state": "not_annotated", "x": None, "y": None}, "advance": True}),
            (centred, "x", 40.0, None),
        ]
        calls = ",".join(f"applyKey({json.dumps(r)}, {json.dumps(k)}, {json.dumps(ring)})" for r, k, ring, _ in cases)
        script = functions + (
            f"process.stdout.write(JSON.stringify({{keys: [{calls}], "
            "ring: [resizeRing(50, 1), resizeRing(50, -0.1), resizeRing(0.15, -1), resizeRing(null, 1)]}));"
        )
        output = json.loads(subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout)

        self.assertEqual(output["keys"], [expected for *_, expected in cases])
        self.assertEqual(output["ring"], [51, 49.9, 0.1, None])

    @unittest.skipUnless(shutil.which("node"), "SKIPPED: node not on PATH")
    def test_page_nudges_centre_one_pixel_inside_the_image(self):
        page = label_package.PAGE_TEMPLATE.read_text(encoding="utf-8")
        functions = re.search(r"// BEGIN keyActions.*?\n(.*?)// END keyActions", page, re.S).group(1)
        centred = {"state": "labelled", "visibility": "visible", "quality": "high", "x": 10.25, "y": 0.5, "r": 30.0}
        empty = {"state": "not_annotated", "visibility": "visible", "quality": None, "x": None, "y": None, "r": None}
        calls = [
            ("centred", "arrowleft"), ("centred", "arrowright"), ("centred", "arrowdown"),
            ("centred", "arrowup"),  # would leave the image (y -0.5): unchanged
            ("empty", "arrowleft"), ("centred", "x"),
        ]
        script = functions + (
            f"const centred = {json.dumps(centred)}, empty = {json.dumps(empty)};"
            "process.stdout.write(JSON.stringify(["
            + ",".join(f"nudgeCentre({name}, '{key}', 320, 240)" for name, key in calls)
            + ", withRing(centred, 31.5), withRing(empty, 31.5), withRing(centred, null)]));"
        )
        output = json.loads(subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout)

        self.assertEqual(
            output,
            [
                {**centred, "x": 9.25}, {**centred, "x": 11.25}, {**centred, "y": 1.5}, centred,
                None, None,
                {**centred, "r": 31.5}, None, None,
            ],
        )

    @unittest.skipUnless(shutil.which("node"), "SKIPPED: node not on PATH")
    def test_page_click_takes_the_visible_ring_radius_only_when_the_frame_has_none(self):
        page = label_package.PAGE_TEMPLATE.read_text(encoding="utf-8")
        functions = re.search(r"// BEGIN keyActions.*?\n(.*?)// END keyActions", page, re.S).group(1)
        fresh = {"state": "not_annotated", "visibility": "fully_occluded", "quality": None, "x": None, "y": None, "r": None}
        sized = {"state": "labelled", "visibility": "visible", "quality": None, "x": 1.0, "y": 2.0, "r": 64.5}
        point = {"x": 100.25, "y": 200.75}
        script = functions + "process.stdout.write(JSON.stringify([" + ",".join(
            f"placeAt({json.dumps(r)}, {json.dumps(point)}, {json.dumps(ring)})"
            for r, ring in [(fresh, 80.1), (fresh, None), (sized, 80.1)]
        ) + "]));"
        output = json.loads(subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout)

        placed = {"state": "labelled", "visibility": "visible", "quality": None, "x": 100.25, "y": 200.75}
        self.assertEqual(output, [
            {**placed, "r": 80.1},          # new frame: the ring drawn at the click
            {**placed, "r": None},          # no ring yet: no radius is invented
            {**sized, **point, "r": 64.5},  # re-click keeps the frame's own radius
        ])

    @unittest.skipUnless(shutil.which("node"), "SKIPPED: node not on PATH")
    def test_page_progress_counts_frames_and_picks_first_sized_frame_as_seed(self):
        page = label_package.PAGE_TEMPLATE.read_text(encoding="utf-8")
        function = re.search(r"// BEGIN progress.*?\n(.*?)// END progress", page, re.S).group(1)
        frames = [{"timestamp_s": t, "frame_index": i} for i, t in enumerate([0.1, 0.2, 0.3, 0.4, 0.5])]
        labels = {
            "0.1": {"state": "labelled", "x": 1.0, "y": 1.0, "r": None, "quality": "high"},     # no radius: not the seed
            "0.2": {"state": "labelled", "x": 1.0, "y": 1.0, "r": 40.0, "quality": None},      # needs quality; seed
            "0.3": {"state": "unlabelable", "x": None, "y": None, "r": None, "quality": None},
            "0.4": {"state": "labelled", "x": 1.0, "y": 1.0, "r": 41.0, "quality": "low"},
        }
        script = function + f"process.stdout.write(JSON.stringify(progress({json.dumps(frames)}, {json.dumps(labels)})));"
        output = json.loads(subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout)

        self.assertEqual(output, {"total": 5, "labelled": 2, "needQuality": 1, "unlabelable": 1,
                                  "seed": {"frame_index": 1, "timestamp_s": 0.2}})

    def test_package_id_separates_annotator_passes_and_grids(self):
        frames = [{"file": "frames/frame_000000.png", "frame_index": 0, "timestamp_s": 0.0}]
        first = label_package.page_config("clip", "pass-a", (320, 240), frames)

        self.assertEqual(first, label_package.page_config("clip", "pass-a", (320, 240), frames))
        self.assertEqual(first["tool_version"], label_package.TOOL["version"])
        self.assertNotEqual(first["package_id"], label_package.page_config("clip", "pass-b", (320, 240), frames)["package_id"])
        self.assertNotEqual(first["package_id"], label_package.page_config("clip", "pass-a", (320, 240), [])["package_id"])

    @unittest.skipUnless(shutil.which("node"), "SKIPPED: node not on PATH")
    def test_page_csv_rows_import_with_generated_metadata(self):
        page = label_package.PAGE_TEMPLATE.read_text(encoding="utf-8")
        function = re.search(r"// BEGIN csvRows.*?\n(.*?)// END csvRows", page, re.S).group(1)
        frames = [{"timestamp_s": t, "frame_index": i} for i, t in enumerate([0.0, 0.083333, 0.166667, 0.25])]
        labels = {
            "0": {"state": "labelled", "visibility": "partially_occluded", "quality": "medium",
                  "x": 100.257, "y": 189.743, "r": 24.1, "notes": 'rim "edge", blurred'},
            "0.083333": {"state": "unlabelable", "visibility": "fully_occluded", "quality": "high",
                         "x": None, "y": None, "r": None},
            "0.166667": {"state": "labelled", "visibility": "visible", "quality": "high",
                         "x": None, "y": None, "r": None},
        }
        script = function + f"process.stdout.write(csvRows({json.dumps(frames)}, {json.dumps(labels)}));"
        csv_text = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        sidecar = label_package.metadata(manifest["fixtures"][0], (320, 240), "pass-a", "grid")
        sidecar["provenance"]["annotated_at"] = "2026-10-01T12:00:00Z"
        with tempfile.TemporaryDirectory() as temp:
            csv_path = Path(temp) / "labels.csv"
            csv_path.write_text(csv_text, encoding="utf-8")
            document = annotations.import_csv(sidecar, csv_path, manifest)

        annotations.validate_annotation(document, manifest)
        states = [sample["annotation_state"] for sample in document["samples"]]
        self.assertEqual(states, ["labelled", "unlabelable", "not_annotated", "not_annotated"])
        self.assertEqual(document["samples"][0]["center_px"], {"x_px": 100.26, "y_px": 189.74})
        self.assertEqual(document["samples"][0]["notes"], "rim  edge   blurred")


class OutputSafetyTests(unittest.TestCase):
    def test_non_redistributable_frames_must_stay_under_validation_private(self):
        fixture = private_fixture()

        with self.assertRaises(label_package.PackageError):
            label_package.require_safe_output(fixture, ROOT / "target" / "frames")
        label_package.require_safe_output(fixture, label_package.PRIVATE_ROOT / "annotations" / "work" / "x")

    def test_redistributable_frames_may_go_anywhere(self):
        fixture = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["fixtures"][0]

        label_package.require_safe_output(fixture, ROOT / "target" / "frames")


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe") or os.environ.get("OPENBAR_REQUIRE_FFMPEG") == "1",
    "SKIPPED: ffmpeg/ffprobe not on PATH",
)
class PackageBuildTests(unittest.TestCase):
    def test_single_frame_package_matches_grid_frame_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            grid, single = Path(temp) / "grid", Path(temp) / "single"
            common = ["--manifest", str(MANIFEST_PATH), "--fixture", FIXTURE_ID, "--annotator-id", "seed"]
            label_package.build(label_package.parser().parse_args(common + ["--step-s", "0.25", "--include-frame", "1", "--output-dir", str(grid)]))
            for flags in [["--frame-index", "3"], ["--at-s", "0.24"]]:
                with self.subTest(flags=flags):
                    label_package.build(label_package.parser().parse_args(common + flags + ["--output-dir", str(single)]))
                    self.assertEqual((grid / "frames/frame_000003.png").read_bytes(),
                                     (single / "frames/frame_000003.png").read_bytes())
                    page = (single / "index.html").read_text(encoding="utf-8")
                    config = json.loads(re.search(r"const CONFIG = (\{.*?\});\n", page).group(1))
                    self.assertEqual(config["frames"], [{"file": "frames/frame_000003.png", "frame_index": 3, "timestamp_s": 0.25}])
                    sidecar = json.loads((single / "metadata.json").read_text(encoding="utf-8"))
                    self.assertEqual(sidecar["provenance"]["notes"], "Single frame 3 at 0.250000 s for a manual target seed.")
                    sidecar["provenance"]["annotated_at"] = "2026-10-01T12:00:00Z"
                    csv_path = Path(temp) / "labels.csv"
                    csv_path.write_text("timestamp_s,requested_timestamp_s,frame_index,annotation_state,visibility,quality,x_px,y_px,radius_px,diameter_px,left_px,top_px,right_px,bottom_px,notes\n"
                                        "0.250000,,3,labelled,visible,high,100,190,24,,,,,,\n", encoding="utf-8")
                    annotations.import_csv(sidecar, csv_path, json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))

    def test_builds_public_fixture_package_with_authoritative_timestamps(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "package"
            args = argparse.Namespace(
                manifest=MANIFEST_PATH, fixture=FIXTURE_ID, start_s=0.0, end_s=None, step_s=0.25,
                include_frame=[1], annotator_id="pass-a", output_dir=output_dir,
            )

            label_package.build(args)

            page = (output_dir / "index.html").read_text(encoding="utf-8")
            config = json.loads(re.search(r"const CONFIG = (\{.*?\});\n", page).group(1))
            frames = config["frames"]
            self.assertEqual(config["annotator_id"], "pass-a")
            self.assertEqual([frame["frame_index"] for frame in frames], [0, 1, 3, 6, 9])
            self.assertEqual([frame["timestamp_s"] for frame in frames], [0.0, 0.083333, 0.25, 0.5, 0.75])
            for frame in frames:
                self.assertEqual(png_size(output_dir / frame["file"]), (320, 240))
            sidecar = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["coordinate_system"]["width_px"], 320)
            self.assertEqual(sidecar["source_video_sha256"],
                             json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["fixtures"][0]["media"]["sha256"])


class SingleFrameSelectionTests(unittest.TestCase):
    COMMON = ["--manifest", str(MANIFEST_PATH), "--fixture", FIXTURE_ID]

    def test_parser_requires_exactly_one_selection_mode(self):
        for flags in [[], ["--frame-index", "0", "--step-s", "0.25"], ["--frame-index", "0", "--at-s", "0"]]:
            with self.subTest(flags=flags), self.assertRaises(SystemExit):
                label_package.parser().parse_args(self.COMMON + flags)

    def test_single_frame_rejects_grid_options_before_media_io(self):
        for mode in [["--frame-index", "0"], ["--at-s", "0"]]:
            for flag, value in [("--start-s", "0"), ("--end-s", "0"), ("--include-frame", "0")]:
                with self.subTest(mode=mode, flag=flag), self.assertRaisesRegex(label_package.PackageError, "grid-only"):
                    label_package.build(label_package.parser().parse_args(self.COMMON + mode + [flag, value]))

    def test_single_frame_selects_index_or_nearest_timestamp(self):
        ts = [0.0, 0.1, 0.2]
        self.assertEqual(label_package.select_frames(ts, ts[1], ts[1], 1.0, [1]), [1])
        self.assertEqual(label_package.select_frames(ts, 0.19, 0.19, 1.0, []), [2])
        with self.assertRaises(label_package.PackageError):
            label_package.select_frames(ts, 0, 0, 1.0, [3])


if __name__ == "__main__":
    unittest.main()
