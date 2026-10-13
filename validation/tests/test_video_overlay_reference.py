"""Consistency checks for the #181 video playback and bar-path overlay reference cases.

The reference file (`validation/reference-cases/video-overlay-v1.json`) pins *expected values* that
follow from docs/validation/VIDEO_OVERLAY_CONTRACT.md. This module re-derives every one of them:

- Section 2: coded -> display rotation, the `contain` content rect, the `+ 0.5` pixel-centre
  transform to screen edge units, user zoom and pan, the exact inverse, and the overlay
  verification checks (sample aspect ratio, raster, orientation);
- Section 3: timeline binding (canonical microseconds, constant player offset, tolerance, frame
  count), the floor lookup from a playback clock, the lookup of the observation by frame index,
  the seeking state and frame-exact seek;
- Section 4: path runs, edge and marker styles, and the trail cut at the displayed frame;
- Section 6: rep navigation derived from `rep-metrics-v1.json` (#180).

The functions below are a specification oracle for the reference file. They are not a measurement
pipeline and must not be imported by product code; the expected values in the JSON were derived
independently of this module and the future implementation stays authoritative (ADR-0001,
ADR-0013). Everything is synthetic engineering expectation, not accuracy evidence.
"""
from __future__ import annotations

import bisect
import json
import math
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "validation" / "reference-cases" / "video-overlay-v1.json"
REP_METRICS_PATH = ROOT / "validation" / "reference-cases" / "rep-metrics-v1.json"
SCHEMA_PATH = ROOT / "validation" / "schema" / "video-overlay-reference-cases-v1.schema.json"

TIMELINE_BINDING_TOLERANCE_US = 1
RASTER_TOLERANCE_PX = 1

Point = tuple[float, float]


# ---------------------------------------------------------------------------------------------
# Section 2: coordinate spaces and display transform
# ---------------------------------------------------------------------------------------------


def rotated_size(coded_w: int, coded_h: int, rotation_deg: int) -> tuple[int, int]:
    """Size of S1 (display) for a W_c x H_c coded raster."""
    return (coded_w, coded_h) if rotation_deg % 180 == 0 else (coded_h, coded_w)


def rotate_coded_to_display(cx: float, cy: float, coded_w: int, coded_h: int, rotation_deg: int) -> Point:
    """Counter-clockwise display-matrix rotation of a coded point (pixel-centre convention)."""
    if rotation_deg == 0:
        return (cx, cy)
    if rotation_deg == 90:
        return (cy, coded_w - 1 - cx)
    if rotation_deg == 180:
        return (coded_w - 1 - cx, coded_h - 1 - cy)
    if rotation_deg == 270:
        return (coded_h - 1 - cy, cx)
    raise ValueError(f"unsupported rotation {rotation_deg}")


def content_rect_contain(wa: int, ha: int, view_w: float, view_h: float) -> tuple[float, tuple[float, float, float, float]]:
    """`contain` fit: scale k and the content rect (left, top, width, height) in screen units."""
    k = min(view_w / wa, view_h / ha)
    width, height = k * wa, k * ha
    return k, ((view_w - width) / 2, (view_h - height) / 2, width, height)


def display_to_screen(
    x: float, y: float, wa: int, ha: int, view_w: float, view_h: float, zoom: float = 1, pan: Point = (0, 0)
) -> Point:
    """S1 -> S3: `u = c_left + k (x + 0.5)`, then `u' = V/2 + z (u - V/2) + pan`."""
    k, (left, top, _, _) = content_rect_contain(wa, ha, view_w, view_h)
    u = left + k * (x + 0.5)
    v = top + k * (y + 0.5)
    return (view_w / 2 + zoom * (u - view_w / 2) + pan[0], view_h / 2 + zoom * (v - view_h / 2) + pan[1])


def screen_to_display(
    u: float, v: float, wa: int, ha: int, view_w: float, view_h: float, zoom: float = 1, pan: Point = (0, 0)
) -> Point:
    """Exact inverse of `display_to_screen` (hit testing): `x = (u - c_left) / k - 0.5`."""
    k, (left, top, _, _) = content_rect_contain(wa, ha, view_w, view_h)
    base_u = view_w / 2 + (u - view_w / 2 - pan[0]) / zoom
    base_v = view_h / 2 + (v - view_h / 2 - pan[1]) / zoom
    return ((base_u - left) / k - 0.5, (base_v - top) / k - 0.5)


def check_source(sha256_present: bool) -> str | None:
    """An Analysis without `identity.source_sha256` cannot be tied to the playing file."""
    return None if sha256_present else "source_unverifiable"


def check_sample_aspect(sar: list[int]) -> str | None:
    """Pixel aspect must be square (n:n, n != 0) or exactly the unknown ratio 0:1."""
    numerator, denominator = sar
    if (numerator, denominator) == (0, 1) or (numerator == denominator != 0):
        return None
    return "unsupported_sample_aspect_ratio"


def check_player_raster(wa: int, ha: int, wp: int, hp: int, tolerance: float = RASTER_TOLERANCE_PX) -> str | None:
    """`|W_p - W_a H_p / H_a| <= 1` and `|H_p - H_a W_p / W_a| <= 1`."""
    if abs(wp - wa * hp / ha) <= tolerance and abs(hp - ha * wp / wa) <= tolerance:
        return None
    return "raster_mismatch"


def check_orientation(
    source_rotation_deg: int, applied_rotation_deg: int | None, conformance_pass: bool = False
) -> str | None:
    """Applied rotation must equal `source_rotation_deg` (mod 360); if the player cannot report it,
    a recorded conformance pass for the device class is required."""
    if applied_rotation_deg is None:
        return None if conformance_pass else "orientation_unverifiable"
    return None if applied_rotation_deg % 360 == source_rotation_deg % 360 else "orientation_mismatch"


def verify_transform_case(case: dict[str, Any]) -> str:
    """First failing check in contract order (source, aspect, raster, orientation), else `accepted`."""
    analysis, video, player = case["analysis"], case["video"], case["player"]
    return (
        check_source(analysis["source_sha256_present"])
        or check_sample_aspect(video["sample_aspect_ratio"])
        or check_player_raster(
            analysis["display_width_px"], analysis["display_height_px"], player["raster_width_px"], player["raster_height_px"]
        )
        or check_orientation(video["source_rotation_deg"], player["applied_rotation_deg"], player["conformance_pass"])
        or "accepted"
    )


# ---------------------------------------------------------------------------------------------
# Section 3: timeline binding, frame lookup, seek
# ---------------------------------------------------------------------------------------------


def canonical_timestamp_s(pts: int, start_pts: int, time_base: list[int]) -> float:
    """Integer ticks first, then one division: `(pts - start_pts) * num / den`."""
    return (pts - start_pts) * time_base[0] / time_base[1]


def canonical_us(timestamp_s: float) -> int:
    """`round_half_up(t * 1e6)` in integer microseconds."""
    return math.floor(timestamp_s * 1e6 + 0.5)


def bind_timeline(
    observations: list[dict[str, Any]],
    player_us: list[int] | None,
    probed_frame_count: int | None,
    tolerance_us: int = TIMELINE_BINDING_TOLERANCE_US,
) -> dict[str, Any]:
    """Once-per-import binding; holds iff `max(d_i) - min(d_i) <= tolerance`, offset `d = min(d_i)`.

    `d` only labels canonical time for a frame; lookup and seek targets use `P_{f_i}` directly."""
    if player_us is None or any("frame_index" not in o for o in observations):
        return {"status": "timeline_unavailable"}
    canonical = [canonical_us(o["timestamp_s"]) for o in observations]
    if any(o["frame_index"] >= len(player_us) for o in observations):
        return {"status": "timeline_mismatch", "canonical_us": canonical}
    offsets = [player_us[o["frame_index"]] - c for o, c in zip(observations, canonical)]
    result: dict[str, Any] = {"canonical_us": canonical, "offsets_us": offsets}
    if max(offsets) - min(offsets) > tolerance_us:
        return {"status": "timeline_mismatch", **result}
    if probed_frame_count is not None and probed_frame_count != len(player_us):
        return {"status": "timeline_mismatch", **result}
    return {"status": "ok", "offset_us": min(offsets), **result}


def displayed_frame_from_clock(player_us: list[int], position_us: int) -> int | None:
    """Last frame `f` with `P_f <= pos` (floor); `None` before the first frame."""
    index = bisect.bisect_right(player_us, position_us) - 1
    return index if index >= 0 else None


def marker_for_frame(observations: list[dict[str, Any]], frame_index: int | None) -> tuple[str, list[float] | None]:
    """Marker state and coordinate for a displayed frame, looked up by frame index (not time)."""
    if frame_index is None:
        return ("none", None)
    for observation in observations:
        if observation.get("frame_index") == frame_index:
            if observation["tracking_state"] == "lost":
                return ("lost", None)
            return (observation["tracking_state"], [observation["x_px"], observation["y_px"]])
    return ("not_analysed", None)


def marker_after_seek(
    observations: list[dict[str, Any]], presented_frame_index: int | None
) -> tuple[str, int | None, list[float] | None]:
    """Pending seek hides the marker; a completed seek follows the frame actually presented."""
    if presented_frame_index is None:
        return ("seeking", None, None)
    state, point = marker_for_frame(observations, presented_frame_index)
    return (state, presented_frame_index, point)


def check_exact_seek(player_us: list[int], target_frame_index: int, presented_frame_index: int) -> str:
    """Success only if the presented `P_f` equals the target's `P_f` (independent of the offset `d`)."""
    return "ok" if player_us[presented_frame_index] == player_us[target_frame_index] else "seek_not_exact"


def naive_nominal_fps_index(position_us: int, nominal_fps: float) -> int:
    """The forbidden `round(pos * fps)` frame guess, kept only to show it differs."""
    return math.floor(position_us / 1e6 * nominal_fps + 0.5)


# ---------------------------------------------------------------------------------------------
# Section 4: path
# ---------------------------------------------------------------------------------------------


def path_runs(observations: list[dict[str, Any]]) -> list[list[int]]:
    """Runs of observation indices: both measured and consecutive `frame_index`; one is a dot."""
    runs: list[list[int]] = []
    current: list[int] = []
    for index, observation in enumerate(observations):
        if observation["tracking_state"] == "lost":
            current = []
            continue
        if current and observations[current[-1]]["frame_index"] + 1 == observation["frame_index"]:
            current.append(index)
        else:
            current = [index]
            runs.append(current)
    return runs


def edge_styles(observations: list[dict[str, Any]], run: list[int]) -> list[str]:
    """`dashed` if either endpoint is low_confidence, else `solid`."""
    return [
        "dashed" if "low_confidence" in (observations[a]["tracking_state"], observations[b]["tracking_state"]) else "solid"
        for a, b in zip(run, run[1:])
    ]


def marker_style(tracking_state: str) -> str:
    """Filled for tracked, ring for low_confidence, none for lost."""
    return {"tracked": "filled", "low_confidence": "ring", "lost": "none"}[tracking_state]


def run_object(observations: list[dict[str, Any]], run: list[int]) -> dict[str, Any]:
    return {"observation_indices": run, "edge_styles": edge_styles(observations, run)}


def cut_runs(
    observations: list[dict[str, Any]], runs: list[list[int]], displayed_index: int
) -> tuple[list[list[int]], list[list[int]]]:
    """Section 4 Trail: the same runs cut at the displayed observation index.

    A run wholly after the cut is dimmed; a run ending at or before it is emphasised (so a lost
    displayed observation cuts the run before it and emphasises nothing at the loss); a run the cut
    falls inside is split, and a cut at the run's first observation emphasises that observation alone
    (a dot) while the whole run is dimmed from there."""
    emphasised: list[list[int]] = []
    dimmed: list[list[int]] = []
    for run in runs:
        if run[0] > displayed_index:
            dimmed.append(run)
        elif run[-1] <= displayed_index:
            emphasised.append(run)
        else:
            emphasised.append([i for i in run if i <= displayed_index])  # one index: the dot rule
            dimmed.append([i for i in run if i >= displayed_index])
    return emphasised, dimmed


# ---------------------------------------------------------------------------------------------
# Section 6: rep navigation
# ---------------------------------------------------------------------------------------------


def nav_from_rep_case(expected: dict[str, Any], observation_timestamps_s: list[float]) -> list[dict[str, Any]]:
    """Navigation list for `expected.reps[]`; rejected runs are never passed in or listed."""
    known = set(observation_timestamps_s)
    items = []
    for rep in expected["reps"]:  # expected["rejected_runs"], if present, is deliberately not read
        start_ok = rep["interval_start_timestamp_s"] in known
        end_ok = rep["end_timestamp_s"] in known
        flags = [] if rep["completeness"] == "complete" else [rep["completeness"]]
        if not (start_ok and end_ok):
            flags.append("rep_boundary_not_an_observation")
        highlighted = start_ok and end_ok
        items.append(
            {
                "rep_index": rep["rep_index"],
                "seek_timestamp_s": rep["interval_start_timestamp_s"] if start_ok else None,
                "highlight_start_timestamp_s": rep["interval_start_timestamp_s"] if highlighted else None,
                "highlight_end_timestamp_s": rep["end_timestamp_s"] if highlighted else None,
                "availability": rep["availability"],
                "show_numbers": rep["availability"] == "reported",
                "flags": flags,
                "withheld_reasons": list(rep["withheld_reasons"]),
            }
        )
    return items


# ---------------------------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------------------------


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class OverlayReferenceTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = load(CASES_PATH)
        cls.schema = load(SCHEMA_PATH)
        cls.rep_metrics = load(REP_METRICS_PATH)

    def by_id(self, group: str, case_id: str) -> dict[str, Any]:
        matches = [case for case in self.document[group] if case["id"] == case_id]
        self.assertEqual(len(matches), 1, case_id)
        return matches[0]


class StructureTests(OverlayReferenceTestCase):
    def test_header_and_constants(self) -> None:
        self.assertEqual(self.document["schema_version"], 1)
        self.assertEqual(self.document["case_set_id"], "video-overlay-v1")
        self.assertEqual(self.document["status"], "reference_expectations_not_produced_by_an_openbar_implementation")
        self.assertEqual(
            self.document["constants"],
            {"timeline_binding_tolerance_us": TIMELINE_BINDING_TOLERANCE_US, "raster_tolerance_px": RASTER_TOLERANCE_PX},
        )

    def test_ids_are_unique_within_and_across_groups(self) -> None:
        seen: set[str] = set()
        for group in ("transform_cases", "timeline_cases", "path_cases", "navigation_cases"):
            ids = [case["id"] for case in self.document[group]]
            self.assertEqual(len(ids), len(set(ids)), group)
            self.assertFalse(seen & set(ids), group)
            seen |= set(ids)
            for case in self.document[group]:
                self.assertTrue(case["description"], case["id"])

    def test_required_cases_are_present(self) -> None:
        required = {
            "transform_cases": [
                "landscape-exact-fit-half-scale",
                "landscape-in-portrait-view-letterbox",
                "portrait-in-landscape-view-pillarbox",
                "portrait-native-no-rotation",
                "rotation-90-coded-landscape",
                "rotation-180-coded-landscape",
                "rotation-270-coded-landscape",
                "player-downscaled-surface",
                "zoom-and-pan",
                "refuse-source-hash-absent",
                "refuse-orientation-not-reportable",
                "orientation-unreportable-with-conformance-pass",
                "refuse-non-square-pixels",
                "refuse-transposed-player-raster",
                "refuse-padded-decoder-raster",
                "refuse-wrong-applied-rotation",
                "raster-tolerance-accept-1px",
                "refuse-raster-tolerance-2px",
            ],
            "timeline_cases": [
                "nonzero-start-player-rebased",
                "nonzero-start-player-absolute",
                "irregular-vfr-lookup",
                "rotated-portrait-vfr-nonzero-start",
                "trimmed-analysis-range",
                "lost-frame-no-coordinate",
                "seek-presented-frame-wins",
                "exact-seek-to-sample",
                "binding-frame-2ms-off",
                "binding-tolerance-accept-1us",
                "binding-tolerance-refuse-2us",
                "binding-missing-frame-index",
                "binding-frame-count-disagreement",
                "binding-frame-index-beyond-player",
                "binding-player-timestamps-unavailable",
            ],
            "path_cases": [
                "lost-breaks-path",
                "low-confidence-styling",
                "single-point-run",
                "frame-index-hole",
                "leading-and-trailing-loss",
                "trail-cut-at-displayed-frame",
            ],
            "navigation_cases": [
                "three-rep-sticking-point-and-incomplete",
                "long-gap-inside-rep",
                "no-rep-only-a-rejected-run",
                "boundary-not-an-observation",
            ],
        }
        for group, ids in required.items():
            present = {case["id"] for case in self.document[group]}
            self.assertEqual(set(ids) - present, set(), group)

    def test_every_reason_in_the_cases_is_listed_by_the_schema(self) -> None:
        listed = set(self.schema["$defs"]["refusal_reason"]["enum"])
        self.assertEqual(len(listed), 13)
        used: set[str] = set()
        for case in self.document["transform_cases"]:
            used.add(case["expected_status"])
        for case in self.document["timeline_cases"]:
            used.add(case["expected_binding"]["status"])
            for check in case.get("exact_seek_checks", []):
                used.add(check["expected_status"])
        for case in self.document["navigation_cases"]:
            for item in case["expected_navigation"]:
                used.update(flag for flag in item["flags"] if flag.startswith("rep_"))
        used -= {"accepted", "ok"}
        self.assertTrue(used)
        self.assertEqual(used - listed, set())

    def test_states_are_listed_by_the_schema(self) -> None:
        marker_states = set(self.schema["$defs"]["marker_state"]["enum"])
        tracking_states = set(self.schema["$defs"]["tracking_state"]["enum"])
        self.assertEqual(tracking_states, {"tracked", "low_confidence", "lost"})
        self.assertEqual(marker_states, tracking_states | {"not_analysed", "seeking", "none"})
        for case in self.document["timeline_cases"]:
            for group in ("clock_queries", "seek_queries"):
                for query in case.get(group, []):
                    self.assertIn(query["expected_marker_state"], marker_states, case["id"])

    def test_groups_are_exercised(self) -> None:
        statuses = {case["expected_status"] for case in self.document["transform_cases"]}
        self.assertEqual(
            statuses,
            {
                "accepted",
                "source_unverifiable",
                "unsupported_sample_aspect_ratio",
                "raster_mismatch",
                "orientation_mismatch",
                "orientation_unverifiable",
            },
        )
        binding = {case["expected_binding"]["status"] for case in self.document["timeline_cases"]}
        self.assertEqual(binding, {"ok", "timeline_mismatch", "timeline_unavailable"})


class TransformTests(OverlayReferenceTestCase):
    def test_verification_matches_expected_status(self) -> None:
        for case in self.document["transform_cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(verify_transform_case(case), case["expected_status"])

    def test_refusals_carry_no_geometry(self) -> None:
        for case in self.document["transform_cases"]:
            if case["expected_status"] != "accepted":
                for key in ("expected_scale", "expected_content_rect", "points"):
                    self.assertNotIn(key, case, case["id"])

    def test_analysis_raster_is_the_rotated_coded_raster(self) -> None:
        for case in self.document["transform_cases"]:
            video, analysis = case["video"], case["analysis"]
            size = rotated_size(video["coded_width_px"], video["coded_height_px"], video["source_rotation_deg"])
            self.assertEqual(size, (analysis["display_width_px"], analysis["display_height_px"]), case["id"])

    def test_accepted_cases_match_the_oracle_exactly(self) -> None:
        for case in self.document["transform_cases"]:
            if case["expected_status"] != "accepted":
                continue
            with self.subTest(case=case["id"]):
                analysis, video, view = case["analysis"], case["video"], case["view"]
                wa, ha = analysis["display_width_px"], analysis["display_height_px"]
                vw, vh = view["width_px"], view["height_px"]
                k, (left, top, width, height) = content_rect_contain(wa, ha, vw, vh)
                self.assertEqual(case["expected_scale"], k)
                self.assertEqual(
                    case["expected_content_rect"],
                    {"left_px": left, "top_px": top, "width_px": width, "height_px": height},
                )
                pan = (view["pan_x_px"], view["pan_y_px"])
                for point in case["points"]:
                    x, y = point["display_px"]
                    if "coded_px" in point:
                        cx, cy = point["coded_px"]
                        coded = (video["coded_width_px"], video["coded_height_px"])
                        self.assertEqual(rotate_coded_to_display(cx, cy, *coded, video["source_rotation_deg"]), (x, y))
                    base = display_to_screen(x, y, wa, ha, vw, vh)
                    if "unzoomed_screen_px" in point:
                        self.assertEqual(list(base), point["unzoomed_screen_px"])
                    screen = display_to_screen(x, y, wa, ha, vw, vh, view["zoom"], pan)
                    self.assertEqual(list(screen), point["screen_px"], point)
                    back = screen_to_display(*screen, wa, ha, vw, vh, view["zoom"], pan)
                    self.assertEqual(back, (x, y), point)

    def test_probes_are_labelled_by_whether_they_are_valid_observations(self) -> None:
        for case in self.document["transform_cases"]:
            wa = case["analysis"]["display_width_px"]
            ha = case["analysis"]["display_height_px"]
            for point in case.get("points", []):
                x, y = point["display_px"]
                # Geometry probes are the outer pixel edges (-0.5 and size - 0.5), never observations.
                is_corner = x in (-0.5, wa - 0.5) and y in (-0.5, ha - 0.5)
                inside = 0 <= x < wa and 0 <= y < ha
                self.assertEqual(point["probe"] == "geometry_probe", is_corner, (case["id"], point))
                if point["probe"] == "observation":
                    self.assertTrue(inside, (case["id"], point))

    def test_hand_computed_anchors(self) -> None:
        def screen(case_id: str, display: list[float]) -> list[float]:
            matches = [p for p in self.by_id("transform_cases", case_id)["points"] if p["display_px"] == display]
            self.assertEqual(len(matches), 1, (case_id, display))
            return matches[0]["screen_px"]

        half = self.by_id("transform_cases", "landscape-exact-fit-half-scale")
        self.assertEqual(half["expected_scale"], 0.5)
        self.assertEqual(half["expected_content_rect"], {"left_px": 0, "top_px": 0, "width_px": 960, "height_px": 540})
        self.assertEqual(screen("landscape-exact-fit-half-scale", [0, 0]), [0.25, 0.25])
        self.assertEqual(screen("landscape-exact-fit-half-scale", [1919, 1079]), [959.75, 539.75])
        self.assertEqual(screen("landscape-exact-fit-half-scale", [960, 540]), [480.25, 270.25])
        self.assertEqual(screen("landscape-exact-fit-half-scale", [-0.5, -0.5]), [0, 0])
        self.assertEqual(screen("landscape-exact-fit-half-scale", [1919.5, 1079.5]), [960, 540])
        letterbox = self.by_id("transform_cases", "landscape-in-portrait-view-letterbox")
        self.assertEqual(letterbox["expected_scale"], 0.5625)
        self.assertEqual(
            letterbox["expected_content_rect"], {"left_px": 0, "top_px": 896.25, "width_px": 1080, "height_px": 607.5}
        )
        self.assertEqual(screen("landscape-in-portrait-view-letterbox", [0, 0]), [0.28125, 896.53125])
        pillar = self.by_id("transform_cases", "portrait-in-landscape-view-pillarbox")
        self.assertEqual(
            pillar["expected_content_rect"], {"left_px": 656.25, "top_px": 0, "width_px": 607.5, "height_px": 1080}
        )
        self.assertEqual(screen("portrait-in-landscape-view-pillarbox", [0, 0]), [656.53125, 0.28125])
        self.assertEqual(screen("rotation-90-coded-landscape", [0, 0]), [0.25, 0.25])
        self.assertEqual(screen("rotation-90-coded-landscape", [0, 1279]), [0.25, 639.75])
        self.assertEqual(screen("rotation-90-coded-landscape", [719, 1279]), [359.75, 639.75])
        self.assertEqual(screen("rotation-90-coded-landscape", [719, 0]), [359.75, 0.25])
        self.assertEqual(screen("rotation-180-coded-landscape", [1279, 719]), [639.75, 359.75])
        self.assertEqual(screen("rotation-270-coded-landscape", [719, 0]), [359.75, 0.25])
        self.assertEqual(screen("rotation-270-coded-landscape", [719, 1279]), [359.75, 639.75])
        self.assertEqual(screen("rotation-270-coded-landscape", [0, 0]), [0.25, 0.25])
        self.assertEqual(self.by_id("transform_cases", "player-downscaled-surface")["expected_scale"], 0.5)
        zoom_point = self.by_id("transform_cases", "zoom-and-pan")["points"][0]
        self.assertEqual(zoom_point["display_px"], [960, 540])
        self.assertEqual(zoom_point["unzoomed_screen_px"], [480.25, 270.25])
        self.assertEqual(zoom_point["screen_px"], [380.5, 320.5])

    def test_rotation_keeps_only_asymmetric_corners_distinct(self) -> None:
        for rotation in (0, 90, 180, 270):
            images = {rotate_coded_to_display(cx, cy, 1280, 720, rotation) for cx in (0, 1279) for cy in (0, 719)}
            self.assertEqual(len(images), 4, rotation)
            w, h = rotated_size(1280, 720, rotation)
            self.assertEqual({(x, y) for x in (0, w - 1) for y in (0, h - 1)}, images, rotation)

    def test_raster_tolerance_boundary(self) -> None:
        self.assertIsNone(check_player_raster(1920, 1080, 959, 540))
        self.assertEqual(check_player_raster(1920, 1080, 958, 540), "raster_mismatch")
        self.assertIsNone(check_player_raster(1920, 1080, 960, 540))
        self.assertEqual(check_player_raster(1920, 1080, 1920, 1088), "raster_mismatch")
        self.assertEqual(check_player_raster(1080, 1920, 1920, 1080), "raster_mismatch")
        # The scale never depends on the player raster.
        self.assertEqual(content_rect_contain(1080, 1920, 540, 960)[0], 0.5)

    def test_sample_aspect_and_orientation_rules(self) -> None:
        self.assertIsNone(check_sample_aspect([1, 1]))
        self.assertIsNone(check_sample_aspect([0, 1]))
        self.assertEqual(check_sample_aspect([4, 3]), "unsupported_sample_aspect_ratio")
        self.assertEqual(check_sample_aspect([0, 0]), "unsupported_sample_aspect_ratio")
        self.assertEqual(check_sample_aspect([0, 2]), "unsupported_sample_aspect_ratio")
        self.assertEqual(check_sample_aspect([3, 0]), "unsupported_sample_aspect_ratio")
        self.assertIsNone(check_sample_aspect([2, 2]))
        self.assertIsNone(check_orientation(90, 90))
        self.assertEqual(check_orientation(0, None), "orientation_unverifiable")
        self.assertIsNone(check_orientation(0, None, conformance_pass=True))
        self.assertEqual(check_orientation(90, 270, conformance_pass=True), "orientation_mismatch")
        self.assertIsNone(check_source(True))
        self.assertEqual(check_source(False), "source_unverifiable")
        self.assertIsNone(check_orientation(270, -90 % 360))
        self.assertEqual(check_orientation(90, 270), "orientation_mismatch")
        self.assertEqual(check_orientation(0, 180), "orientation_mismatch")


class TimelineTests(OverlayReferenceTestCase):
    def test_streams_and_observations_follow_the_decoder_timeline(self) -> None:
        for case in self.document["timeline_cases"]:
            with self.subTest(case=case["id"]):
                stream = case["stream"]
                pts = stream["frame_pts"]
                self.assertEqual(pts, sorted(set(pts)), "strictly increasing pts")
                if case["player_frame_timestamps_us"] is not None:
                    player = case["player_frame_timestamps_us"]
                    self.assertEqual(player, sorted(set(player)))
                for observation in case["observations"]:
                    if "frame_index" not in observation:
                        continue
                    expected = canonical_timestamp_s(pts[observation["frame_index"]], stream["start_pts"], stream["time_base"])
                    self.assertEqual(observation["timestamp_s"], expected)

    def test_binding_matches_the_oracle(self) -> None:
        for case in self.document["timeline_cases"]:
            with self.subTest(case=case["id"]):
                bound = bind_timeline(case["observations"], case["player_frame_timestamps_us"], case["probed_frame_count"])
                self.assertEqual(bound, case["expected_binding"])

    def test_binding_tolerance_boundary(self) -> None:
        accepted = self.by_id("timeline_cases", "binding-tolerance-accept-1us")["expected_binding"]
        self.assertEqual(accepted["status"], "ok")
        self.assertEqual(accepted["offsets_us"][0], 1)
        self.assertEqual(accepted["offset_us"], 0, "d is the minimum offset, not the first")
        self.assertEqual(
            self.by_id("timeline_cases", "binding-tolerance-refuse-2us")["expected_binding"]["status"], "timeline_mismatch"
        )
        two_ms = self.by_id("timeline_cases", "binding-frame-2ms-off")["expected_binding"]
        self.assertEqual(max(two_ms["offsets_us"]) - min(two_ms["offsets_us"]), 2000)
        count = self.by_id("timeline_cases", "binding-frame-count-disagreement")
        self.assertEqual(count["expected_binding"]["status"], "timeline_mismatch")
        self.assertEqual(len(set(count["expected_binding"]["offsets_us"])), 1, "offsets agree; only the count differs")
        self.assertNotEqual(count["probed_frame_count"], len(count["player_frame_timestamps_us"]))

    def test_player_offset_is_removed_by_binding(self) -> None:
        rebased = self.by_id("timeline_cases", "nonzero-start-player-rebased")
        absolute = self.by_id("timeline_cases", "nonzero-start-player-absolute")
        self.assertEqual(rebased["stream"], absolute["stream"])
        self.assertEqual(rebased["stream"]["start_pts"], 126000)
        self.assertEqual(rebased["expected_binding"]["offset_us"], 0)
        self.assertEqual(absolute["expected_binding"]["offset_us"], 1400000)
        self.assertEqual(
            [t - 1400000 for t in absolute["player_frame_timestamps_us"]], rebased["player_frame_timestamps_us"]
        )
        self.assertEqual(rebased["player_frame_timestamps_us"][:6], [0, 33333, 66667, 100000, 133333, 166667])

        def strip(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [{k: v for k, v in q.items() if k != "player_position_us"} for q in queries]

        self.assertEqual(strip(rebased["clock_queries"]), strip(absolute["clock_queries"]))

    def test_clock_queries_match_the_oracle(self) -> None:
        checked = 0
        for case in self.document["timeline_cases"]:
            for query in case.get("clock_queries", []):
                with self.subTest(case=case["id"], position=query["canonical_position_us"]):
                    offset = case["expected_binding"]["offset_us"]
                    self.assertEqual(query["player_position_us"], query["canonical_position_us"] + offset)
                    frame = displayed_frame_from_clock(case["player_frame_timestamps_us"], query["player_position_us"])
                    self.assertEqual(query["expected_frame_index"], frame)
                    state, point = marker_for_frame(case["observations"], frame)
                    self.assertEqual(query["expected_marker_state"], state)
                    self.assertEqual(query["expected_marker_px"], point)
                    checked += 1
        self.assertGreater(checked, 30)

    def test_position_before_the_first_frame_shows_no_marker(self) -> None:
        for case_id in ("irregular-vfr-lookup", "nonzero-start-player-absolute"):
            case = self.by_id("timeline_cases", case_id)
            before = [q for q in case["clock_queries"] if q["canonical_position_us"] == -1]
            self.assertEqual(len(before), 1, case_id)
            self.assertEqual(
                (before[0]["expected_frame_index"], before[0]["expected_marker_state"], before[0]["expected_marker_px"]),
                (None, "none", None),
            )

    def test_frame_index_beyond_the_player_is_a_mismatch(self) -> None:
        case = self.by_id("timeline_cases", "binding-frame-index-beyond-player")
        self.assertGreaterEqual(max(o["frame_index"] for o in case["observations"]), len(case["player_frame_timestamps_us"]))
        self.assertEqual(case["expected_binding"]["status"], "timeline_mismatch")
        self.assertNotIn("offset_us", case["expected_binding"])

    def test_floor_lookup_edges(self) -> None:
        player = [0, 33000, 75000]
        self.assertIsNone(displayed_frame_from_clock(player, -1))
        self.assertEqual(displayed_frame_from_clock(player, 0), 0)
        self.assertEqual(displayed_frame_from_clock(player, 32999), 0)
        self.assertEqual(displayed_frame_from_clock(player, 33000), 1)
        self.assertEqual(displayed_frame_from_clock(player, 10**9), 2)

    def test_vfr_nominal_fps_would_pick_the_wrong_frame(self) -> None:
        case = self.by_id("timeline_cases", "irregular-vfr-lookup")
        self.assertEqual(case["metadata_nominal_fps"], 30)
        for case_id in ("irregular-vfr-lookup", "rotated-portrait-vfr-nonzero-start"):
            vfr = self.by_id("timeline_cases", case_id)
            wrong = 0
            for query in vfr["clock_queries"]:
                naive = naive_nominal_fps_index(query["canonical_position_us"], vfr["metadata_nominal_fps"])
                self.assertEqual(query["naive_nominal_fps_index"], naive)
                if query["expected_frame_index"] is not None and naive != query["expected_frame_index"]:
                    wrong += 1
            self.assertGreaterEqual(wrong, 1, case_id)
        at_120ms = [q for q in case["clock_queries"] if q["canonical_position_us"] == 120000][0]
        self.assertEqual((at_120ms["naive_nominal_fps_index"], at_120ms["expected_frame_index"]), (4, 3))

    def test_rotated_portrait_vfr_nonzero_start_clip(self) -> None:
        case = self.by_id("timeline_cases", "rotated-portrait-vfr-nonzero-start")
        video = case["video"]
        self.assertEqual(
            rotated_size(video["coded_width_px"], video["coded_height_px"], video["source_rotation_deg"]),
            (video["display_width_px"], video["display_height_px"]),
        )
        self.assertEqual((video["source_rotation_deg"], case["stream"]["start_pts"]), (90, 126000))
        pts = case["stream"]["frame_pts"]
        self.assertEqual([b - a for a, b in zip(pts, pts[1:])], [3000, 3300, 2700, 3600, 3000, 2400])
        self.assertEqual(case["expected_binding"]["status"], "ok")
        self.assertEqual(case["expected_binding"]["offset_us"], 1400000)
        states = {q["expected_frame_index"]: q["expected_marker_state"] for q in case["clock_queries"]}
        self.assertEqual(states[0], "not_analysed", "before the seed frame")
        self.assertEqual(states[3], "lost")
        self.assertEqual(states[6], "not_analysed", "not emitted")

    def test_trimmed_range_and_lost_frames_are_distinct_states(self) -> None:
        trimmed = self.by_id("timeline_cases", "trimmed-analysis-range")
        self.assertEqual([o["frame_index"] for o in trimmed["observations"]], [3, 4, 5, 6])
        states = {q["expected_frame_index"]: q["expected_marker_state"] for q in trimmed["clock_queries"]}
        self.assertEqual(states[2], "not_analysed")
        self.assertEqual(states[7], "not_analysed")
        self.assertEqual(states[9], "not_analysed", "hold-last after the end is still outside the analysed range")
        lost = self.by_id("timeline_cases", "lost-frame-no-coordinate")
        self.assertEqual({q["expected_marker_state"] for q in lost["clock_queries"]}, {"tracked", "lost", "low_confidence"})
        for observation in lost["observations"]:
            if observation["tracking_state"] == "lost":
                self.assertNotIn("x_px", observation)
                self.assertNotIn("y_px", observation)

    def test_seek_queries_follow_the_presented_frame(self) -> None:
        for case in self.document["timeline_cases"]:
            for query in case.get("seek_queries", []):
                with self.subTest(case=case["id"], presented=query["presented_frame_index"]):
                    state, frame, point = marker_after_seek(case["observations"], query["presented_frame_index"])
                    self.assertEqual(query["expected_marker_state"], state)
                    self.assertEqual(query["expected_observation_frame_index"], frame)
                    self.assertEqual(query["expected_marker_px"], point)
                    floor = displayed_frame_from_clock(case["player_frame_timestamps_us"], query["requested_position_us"])
                    self.assertEqual(query["requested_floor_frame_index"], floor)
                    if frame is not None:
                        self.assertNotEqual(frame, floor, "the presented frame differs from the requested position")

    def test_seek_pending_then_completed(self) -> None:
        case = self.by_id("timeline_cases", "seek-presented-frame-wins")
        pending, done = case["seek_queries"]
        self.assertEqual((pending["presented_frame_index"], pending["expected_marker_state"]), (None, "seeking"))
        self.assertEqual(done["presented_frame_index"], 1)
        self.assertEqual(pending["requested_position_us"], 85000)

    def test_exact_seek_reports_success_only_on_the_target_frame(self) -> None:
        for case in self.document["timeline_cases"]:
            for check in case.get("exact_seek_checks", []):
                with self.subTest(case=case["id"], presented=check["presented_frame_index"]):
                    stream = case["stream"]
                    target = canonical_timestamp_s(
                        stream["frame_pts"][check["target_frame_index"]], stream["start_pts"], stream["time_base"]
                    )
                    self.assertEqual(check["target_timestamp_s"], target)
                    status = check_exact_seek(
                        case["player_frame_timestamps_us"], check["target_frame_index"], check["presented_frame_index"]
                    )
                    self.assertEqual(check["expected_status"], status)
        statuses = [c["expected_status"] for c in self.by_id("timeline_cases", "exact-seek-to-sample")["exact_seek_checks"]]
        self.assertEqual(statuses, ["seek_not_exact", "ok"])


class PathTests(OverlayReferenceTestCase):
    def test_runs_and_styles_match_the_oracle(self) -> None:
        for case in self.document["path_cases"]:
            with self.subTest(case=case["id"]):
                observations = case["observations"]
                runs = path_runs(observations)
                self.assertEqual(case["expected_runs"], [run_object(observations, run) for run in runs])
                self.assertEqual(case["expected_marker_styles"], [marker_style(o["tracking_state"]) for o in observations])

    def test_lost_observations_carry_no_coordinate_and_belong_to_no_run(self) -> None:
        for case in self.document["path_cases"]:
            in_runs = {i for run in case["expected_runs"] for i in run["observation_indices"]}
            for index, observation in enumerate(case["observations"]):
                if observation["tracking_state"] == "lost":
                    self.assertNotIn("x_px", observation, case["id"])
                    self.assertNotIn("y_px", observation, case["id"])
                    self.assertNotIn(index, in_runs, case["id"])
                else:
                    self.assertIn(index, in_runs, case["id"])

    def test_pinned_run_shapes(self) -> None:
        def indices(case_id: str) -> list[list[int]]:
            return [r["observation_indices"] for r in self.by_id("path_cases", case_id)["expected_runs"]]

        self.assertEqual(indices("lost-breaks-path"), [[0, 1, 2], [5, 6, 7]])
        self.assertEqual(indices("single-point-run"), [[0], [2], [4, 5]])
        self.assertEqual(indices("frame-index-hole"), [[0, 1], [2]])
        self.assertEqual(indices("leading-and-trailing-loss"), [[2, 3, 4]])
        self.assertEqual(indices("low-confidence-styling"), [[0, 1, 2, 3, 4, 5, 6, 7]])
        styled = self.by_id("path_cases", "low-confidence-styling")
        self.assertEqual(
            styled["expected_runs"][0]["edge_styles"],
            ["solid", "dashed", "dashed", "solid", "dashed", "dashed", "dashed"],
        )
        self.assertEqual(
            styled["expected_marker_styles"], ["filled", "filled", "ring", "filled", "filled", "ring", "ring", "filled"]
        )
        self.assertEqual([o["frame_index"] for o in self.by_id("path_cases", "frame-index-hole")["observations"]], [10, 11, 13])

    def test_trail_cuts_match_the_oracle(self) -> None:
        case = self.by_id("path_cases", "trail-cut-at-displayed-frame")
        runs = path_runs(case["observations"])
        self.assertEqual([c["displayed_observation_index"] for c in case["trail_cuts"]], [0, 1, 3, 4, 5, 7])
        for cut in case["trail_cuts"]:
            with self.subTest(displayed=cut["displayed_observation_index"]):
                emphasised, dimmed = cut_runs(case["observations"], runs, cut["displayed_observation_index"])
                self.assertEqual(cut["emphasised_runs"], [run_object(case["observations"], r) for r in emphasised])
                self.assertEqual(cut["dimmed_runs"], [run_object(case["observations"], r) for r in dimmed])
                # Same segments: every edge of the full runs is emphasised or dimmed, exactly once.
                edges = {(a, b) for r in runs for a, b in zip(r, r[1:])}
                cut_edges = [(a, b) for r in emphasised + dimmed for a, b in zip(r, r[1:])]
                self.assertEqual(sorted(cut_edges), sorted(edges))
        by_index = {c["displayed_observation_index"]: c for c in case["trail_cuts"]}
        self.assertEqual(by_index[5]["emphasised_runs"][-1]["observation_indices"], [4, 5])
        self.assertEqual(by_index[5]["dimmed_runs"][-1]["observation_indices"], [5, 6, 7])
        self.assertEqual(by_index[5]["dimmed_runs"][-1]["edge_styles"], ["dashed", "dashed"])
        self.assertEqual(by_index[3]["dimmed_runs"], [run_object(case["observations"], [4, 5, 6, 7])])
        # Dot rule: a cut at a run's first observation emphasises that observation alone.
        self.assertEqual(by_index[0]["emphasised_runs"], [{"observation_indices": [0], "edge_styles": []}])
        self.assertEqual(by_index[0]["dimmed_runs"][0]["observation_indices"], [0, 1, 2])
        self.assertEqual(by_index[4]["emphasised_runs"][-1], {"observation_indices": [4], "edge_styles": []})
        self.assertEqual(by_index[4]["dimmed_runs"], [run_object(case["observations"], [4, 5, 6, 7])])
        # Cut at a lost observation: the earlier run is fully emphasised, nothing at the loss.
        self.assertEqual(by_index[3]["emphasised_runs"], [run_object(case["observations"], [0, 1, 2])])
        self.assertNotIn(3, [i for r in by_index[3]["emphasised_runs"] for i in r["observation_indices"]])
        self.assertEqual(by_index[7]["dimmed_runs"], [])


class NavigationTests(OverlayReferenceTestCase):
    def rep_case(self, case_id: str) -> dict[str, Any]:
        return [c for c in self.rep_metrics["cases"] if c["id"] == case_id][0]

    def test_every_rep_metrics_case_yields_the_navigation_rule(self) -> None:
        cases = self.rep_metrics["cases"]
        self.assertEqual(len(cases), 17)
        for case in cases:
            with self.subTest(case=case["id"]):
                sample_times = [s["timestamp_s"] for s in case["samples"]]
                reps = case["expected"]["reps"]
                rule = [
                    {
                        "rep_index": rep["rep_index"],
                        "seek_timestamp_s": rep["interval_start_timestamp_s"],
                        "highlight_start_timestamp_s": rep["interval_start_timestamp_s"],
                        "highlight_end_timestamp_s": rep["end_timestamp_s"],
                        "availability": rep["availability"],
                        "show_numbers": rep["availability"] == "reported",
                        "flags": [] if rep["completeness"] == "complete" else [rep["completeness"]],
                        "withheld_reasons": rep["withheld_reasons"],
                    }
                    for rep in reps
                ]
                navigation = nav_from_rep_case(case["expected"], sample_times)
                self.assertEqual(navigation, rule)
                self.assertEqual(len(navigation), len(reps), "rejected runs are not listed")
                rejected = case["expected"]["rejected_runs"]
                if rejected:
                    self.assertNotEqual(len(navigation), len(reps) + len(rejected))
                    self.assertTrue(all(isinstance(n["rep_index"], int) for n in navigation))
                self.assertEqual([n["rep_index"] for n in navigation], list(range(len(reps))))

    def test_every_rep_boundary_is_a_sample_timestamp(self) -> None:
        for case in self.rep_metrics["cases"]:
            sample_times = [s["timestamp_s"] for s in case["samples"]]
            for rep in case["expected"]["reps"]:
                with self.subTest(case=case["id"], rep=rep["rep_index"]):
                    self.assertIn(rep["interval_start_timestamp_s"], sample_times)
                    self.assertIn(rep["end_timestamp_s"], sample_times)
                    self.assertEqual(rep["interval_start_timestamp_s"], sample_times[rep["interval_start_sample_index"]])
                    self.assertEqual(rep["end_timestamp_s"], sample_times[rep["end_sample_index"]])

    def test_pinned_navigation_cases_match_the_oracle(self) -> None:
        for case in self.document["navigation_cases"]:
            with self.subTest(case=case["id"]):
                if "rep_metrics_case_id" in case:
                    self.assertNotIn("reps", case)
                    self.assertNotIn("observation_timestamps_s", case)
                    source = self.rep_case(case["rep_metrics_case_id"])
                    expected = source["expected"]
                    times = [s["timestamp_s"] for s in source["samples"]]
                else:
                    expected, times = {"reps": case["reps"]}, case["observation_timestamps_s"]
                self.assertEqual(case["expected_navigation"], nav_from_rep_case(expected, times))

    def test_pinned_cases_show_the_documented_behaviour(self) -> None:
        three = self.by_id("navigation_cases", "three-rep-sticking-point-and-incomplete")["expected_navigation"]
        self.assertEqual([n["flags"] for n in three], [[], [], ["incomplete"]])
        self.assertTrue(all(n["show_numbers"] for n in three))
        self.assertEqual([n["seek_timestamp_s"] for n in three], [0.5625, 1.53125, 2.34375])
        gap = self.by_id("navigation_cases", "long-gap-inside-rep")["expected_navigation"]
        self.assertEqual(len(gap), 1)
        self.assertFalse(gap[0]["show_numbers"])
        self.assertEqual(gap[0]["availability"], "withheld")
        self.assertEqual(gap[0]["withheld_reasons"], ["unsupported_segment", "spans_gap"])
        self.assertEqual(self.by_id("navigation_cases", "no-rep-only-a-rejected-run")["expected_navigation"], [])
        rejected = self.rep_case("no-rep-only-a-rejected-run")["expected"]
        self.assertEqual(rejected["reps"], [])
        self.assertEqual(len(rejected["rejected_runs"]), 1)

    def test_boundary_that_is_not_an_observation_is_flagged_and_not_snapped(self) -> None:
        case = self.by_id("navigation_cases", "boundary-not-an-observation")
        times = case["observation_timestamps_s"]
        first, second, third = case["expected_navigation"]
        self.assertEqual(first["flags"], [])
        self.assertEqual(first["highlight_start_timestamp_s"], 0.25)
        self.assertEqual(second["flags"], ["rep_boundary_not_an_observation"])
        self.assertEqual((second["highlight_start_timestamp_s"], second["highlight_end_timestamp_s"]), (None, None))
        self.assertEqual(second["seek_timestamp_s"], 0.625)
        self.assertEqual(third["flags"], ["incomplete", "rep_boundary_not_an_observation"])
        self.assertIsNone(third["seek_timestamp_s"])
        self.assertFalse(third["show_numbers"])
        for rep in case["reps"][1:]:
            self.assertNotIn(rep["interval_start_timestamp_s"] if rep["rep_index"] == 2 else rep["end_timestamp_s"], times)


if __name__ == "__main__":
    unittest.main()
