#!/usr/bin/env python3
"""The session page's pure JavaScript (#95), run with node; skips when node is not on PATH.

The CSV the page writes is parsed by session_contract.py, so the two sides of the contract are
tested together: what the page can export is exactly what `run` accepts.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_fakes as fakes  # noqa: E402
from session_fakes import WORKFLOW_DIR, session_contract  # noqa: E402

PAGE = WORKFLOW_DIR / "session_page.html"


def pure_source() -> str:
    page = PAGE.read_text(encoding="utf-8")
    match = re.search(r"// BEGIN sessionPure.*?\n(.*?)// END sessionPure", page, re.S)
    assert match is not None
    return match.group(1)


def run_node(body: str, config: dict[str, Any]) -> Any:
    script = f"{pure_source()}\nconst config = {json.dumps(config)};\nconst out = (() => {{ {body} }})();\n" \
             "process.stdout.write(JSON.stringify(out));"
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(completed.stdout)


def config_for(state: dict[str, Any]) -> dict[str, Any]:
    return {"format": session_contract.FORMAT, "session_id": state["session_id"], "page_id": state["page_id"],
            "clips": state["clips"]}


class TemplateTests(unittest.TestCase):
    def test_placeholders_appear_once(self) -> None:
        page = PAGE.read_text(encoding="utf-8")
        self.assertEqual(page.count("/*CONFIG*/null"), 1)
        self.assertEqual(page.count("/*IMAGES*/null"), 1)

    def test_page_header_matches_the_contract(self) -> None:
        header = re.search(r"const head = \[(.*?)\];", pure_source(), re.S).group(1)
        self.assertEqual(re.findall(r'"([a-z_0-9]+)"', header), session_contract.COLUMNS)

    def test_page_exercises_match_the_contract(self) -> None:
        choices = re.search(r"const EXERCISES = \[(.*?)\];", pure_source()).group(1)
        self.assertEqual(tuple(re.findall(r'"([a-z_]+)"', choices)), session_contract.EXERCISES)


@unittest.skipUnless(shutil.which("node"), "SKIPPED: node not on PATH")
class PurePageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = fakes.session([fakes.clip(0), fakes.clip(1, sha="cd" * 32, plate=fakes.FAILED_PLATE,
                                                               stick=fakes.FAILED_STICK)])
        self.config = config_for(self.state)

    def test_whole_page_script_parses(self) -> None:
        script = re.search(r"<script>\n(.*?)</script>", PAGE.read_text(encoding="utf-8"), re.S).group(1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "page.js"
            path.write_text(script, encoding="utf-8")
            completed = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_suggestions_prefill_and_statuses(self) -> None:
        out = run_node("""
          const c = config.clips[0], s = initialState(c);
          const moved = movePoint(s, "plate_radius", c.suggestions.plate.center_x_px + 190, c.suggestions.plate.center_y_px);
          return [ITEMS.map(i => itemStatus(c, s, i)), itemStatus(c, moved, "plate_radius"),
                  itemStatus(c, moved, "plate_center"), nextMissing(s)];
        """, self.config)
        self.assertEqual(out, [["accepted"] * 4, "adjusted", "accepted", None])

    def test_status_compares_at_two_decimals_like_the_csv(self) -> None:
        out = run_node("""
          const c = config.clips[0], s = initialState(c), x = c.suggestions.plate.center_x_px;
          const status = v => itemStatus(c, withEdit(s, {values: {plate_center: [v, c.suggestions.plate.center_y_px]}}),
                                         "plate_center");
          return [status(x + 0.004), status(x + 0.006), status(x - 0.004)];
        """, self.config)
        self.assertEqual(out, ["accepted", "adjusted", "accepted"])

    def test_failed_suggestion_needs_manual_clicks(self) -> None:
        out = run_node("""
          const c = config.clips[1]; let s = initialState(c);
          const before = [nextMissing(s), ITEMS.map(i => itemStatus(c, s, i)), canConfirm(c, s)];
          s = withEdit(s, {values: {plate_center: [400, 1500]}});
          s = movePoint(s, "plate_radius", 580, 1500);
          s = withEdit(s, {values: {stick_low: [830, 1630], stick_high: [840, 580]}, exercise: "clean"});
          return [before, ITEMS.map(i => itemStatus(c, s, i)), canConfirm(c, s)];
        """, self.config)
        self.assertEqual(out[0], ["plate_center", ["missing"] * 4, False])
        self.assertEqual(out[1], ["manual"] * 4)
        self.assertTrue(out[2])

    def test_confirm_is_disabled_until_every_item_and_the_lift(self) -> None:
        out = run_node("""
          const c = config.clips[0], s = initialState(c);
          const noLift = canConfirm(c, s), withLift = withEdit(s, {exercise: "snatch"});
          const bigCircle = movePoint(withLift, "plate_radius", c.suggestions.plate.center_x_px + 900, c.suggestions.plate.center_y_px);
          return [noLift, canConfirm(c, withLift), canConfirm(c, bigCircle), decide(c, s, "confirmed").decision,
                  decide(c, withLift, "confirmed").decision];
        """, self.config)
        self.assertEqual(out, [False, True, False, "", "confirmed"])

    def test_an_edit_withdraws_confirmation(self) -> None:
        out = run_node("""
          const c = config.clips[0];
          const s = decide(c, withEdit(initialState(c), {exercise: "snatch"}), "confirmed");
          return [s.decision, movePoint(s, "stick_low", 831, 1630).decision, withEdit(s, {exercise: "clean"}).decision];
        """, self.config)
        self.assertEqual(out, ["confirmed", "", ""])

    def test_download_is_blocked_until_every_clip_is_decided(self) -> None:
        out = run_node("""
          const a = decide(config.clips[0], withEdit(initialState(config.clips[0]), {exercise: "snatch"}), "confirmed");
          const b = initialState(config.clips[1]);
          return [downloadProblem(config.clips, [a, b]), downloadProblem(config.clips, [a, decide(config.clips[1], b, "skipped")]),
                  downloadProblem(config.clips, [decide(config.clips[0], a, "skipped"), decide(config.clips[1], b, "skipped")])];
        """, self.config)
        self.assertIn("still need Confirm or Skip", out[0])
        self.assertIsNone(out[1])
        self.assertEqual(out[2], "confirm at least one clip")

    def test_pixel_centre_window(self) -> None:
        out = run_node("""
          return [imagePointFromContentOffset(0, 0, 540, 960, 1080, 1920), imagePointFromContentOffset(540, 960, 540, 960, 1080, 1920),
                  pointInWindow(-0.5, 0, 1080, 1920), pointInWindow(1079.4, 1919.4, 1080, 1920), pointInWindow(1079.5, 0, 1080, 1920)];
        """, self.config)
        self.assertEqual(out, [{"x": -0.5, "y": -0.5}, {"x": 1079.5, "y": 1919.5}, False, True, False])

    def test_exported_csv_round_trips_through_the_parser(self) -> None:
        text = run_node("""
          const a = decide(config.clips[0], movePoint(withEdit(initialState(config.clips[0]), {exercise: "snatch"}),
                           "stick_high", 841.333, 579.0), "confirmed");
          let b = initialState(config.clips[1]);
          b = withEdit(b, {values: {plate_center: [400.123, 1500.5], stick_low: [830, 1630], stick_high: [840, 580]},
                           exercise: "back_squat"});
          b = decide(config.clips[1], movePoint(b, "plate_radius", 580.4, 1500.5), "confirmed");
          return sessionCsv(config, [a, b]);
        """, self.config)
        decisions = session_contract.parse_session_csv(text.encode("utf-8"), self.state)
        self.assertEqual([d["exercise"] for d in decisions], ["snatch", "back_squat"])
        self.assertEqual(decisions[0]["statuses"], {"plate_center": "accepted", "plate_radius": "accepted",
                                                    "stick_low": "accepted", "stick_high": "adjusted"})
        self.assertEqual(set(decisions[1]["statuses"].values()), {"manual"})
        self.assertEqual(decisions[1]["values"]["plate_center_x_px"], 400.12)

    def test_skipped_clip_round_trips(self) -> None:
        text = run_node("""
          const a = decide(config.clips[0], withEdit(initialState(config.clips[0]), {exercise: "clean"}), "confirmed");
          return sessionCsv(config, [a, decide(config.clips[1], initialState(config.clips[1]), "skipped")]);
        """, self.config)
        decisions = session_contract.parse_session_csv(text.encode("utf-8"), self.state)
        self.assertEqual([d["decision"] for d in decisions], ["confirmed", "skipped"])


if __name__ == "__main__":
    unittest.main()
