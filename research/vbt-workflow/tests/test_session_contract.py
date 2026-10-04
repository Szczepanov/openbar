#!/usr/bin/env python3
"""The session CSV contract (#95): valid rows parse; everything else fails closed. Stdlib only."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_fakes as fakes  # noqa: E402
from session_fakes import session_contract  # noqa: E402


class ValidCsvTests(unittest.TestCase):
    def test_accepted_row_parses_with_statuses(self) -> None:
        state = fakes.session()
        [decision] = session_contract.parse_session_csv(fakes.csv_bytes([fakes.accepted_row(state["clips"][0], state)]),
                                                        state)
        self.assertEqual(decision["decision"], "confirmed")
        self.assertEqual(decision["exercise"], "snatch")
        self.assertEqual(set(decision["statuses"].values()), {"accepted"})
        self.assertEqual(decision["values"]["plate_radius_px"], 180.25)

    def test_adjusted_items_are_recorded_per_item(self) -> None:
        state = fakes.session()
        row = {**fakes.accepted_row(state["clips"][0], state), "plate_radius_px": "182.00",
               "plate_radius_status": "adjusted", "stick_high_y_px": "577.50", "stick_high_status": "adjusted"}
        [decision] = session_contract.parse_session_csv(fakes.csv_bytes([row]), state)
        self.assertEqual(decision["statuses"], {"plate_center": "accepted", "plate_radius": "adjusted",
                                                "stick_low": "accepted", "stick_high": "adjusted"})

    def test_failed_suggestions_require_manual_items(self) -> None:
        clip = fakes.clip(plate=fakes.FAILED_PLATE, stick=fakes.FAILED_STICK)
        state = fakes.session([clip])
        row = {**fakes.accepted_row(fakes.clip(), state), "plate_suggestion_id": "", "stick_suggestion_id": "",
               **{column: "manual" for column in ("plate_center_status", "plate_radius_status", "stick_low_status",
                                                  "stick_high_status")}}
        [decision] = session_contract.parse_session_csv(fakes.csv_bytes([row]), state)
        self.assertEqual(set(decision["statuses"].values()), {"manual"})

    def test_skipped_and_confirmed_clips(self) -> None:
        clips = [fakes.clip(0), fakes.clip(1, sha="cd" * 32)]
        state = fakes.session(clips)
        rows = [fakes.skipped_row(clips[0], state), fakes.accepted_row(clips[1], state, "back_squat")]
        decisions = session_contract.parse_session_csv(fakes.csv_bytes(rows), state)
        self.assertEqual([d["decision"] for d in decisions], ["skipped", "confirmed"])

    def test_crlf_line_endings_are_accepted(self) -> None:
        state = fakes.session()
        data = fakes.csv_bytes([fakes.accepted_row(state["clips"][0], state)]).replace(b"\n", b"\r\n")
        self.assertEqual(len(session_contract.parse_session_csv(data, state)), 1)


class FailClosedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = fakes.session()
        self.row = fakes.accepted_row(self.state["clips"][0], self.state)

    def refused(self, data: bytes, needle: str) -> None:
        with self.assertRaises(session_contract.SessionCsvError) as caught:
            session_contract.parse_session_csv(data, self.state)
        self.assertIn(needle, str(caught.exception))

    def refused_row(self, needle: str, **changes: str) -> None:
        self.refused(fakes.csv_bytes([{**self.row, **changes}]), needle)

    def test_wrong_session(self) -> None:
        self.refused_row("not '2026-10-03'", session_id="2026-10-04")

    def test_page_rebuilt_since_download(self) -> None:
        self.refused_row("page", page_id="0000000000000000")

    def test_wrong_video(self) -> None:
        self.refused_row("wrong video or session", source_video_sha256="ef" * 32)
        self.refused_row("wrong video or session", fixture_id="vbt-efefefefefefefef")
        self.refused_row("wrong video or session", package_id="ffffffffffffffff")
        self.refused_row("timestamp_s", timestamp_s="0.5")

    def test_missing_confirmation(self) -> None:
        self.refused_row("confirmed or skipped", decision="")
        self.refused_row("confirmed or skipped", decision="maybe")

    def test_missing_exercise_or_item(self) -> None:
        self.refused_row("exercise", exercise="")
        self.refused_row("exercise", exercise="deadlift")
        self.refused_row("plate_radius_px is missing", plate_radius_px="", plate_radius_status="manual")

    def test_accepted_claim_with_changed_values(self) -> None:
        self.refused_row("values are changed", plate_center_x_px="401.50")

    def test_accepted_means_exactly_equal_at_two_decimals(self) -> None:
        self.refused_row("values are changed", plate_center_x_px="400.504")
        self.refused_row("values are changed", plate_radius_px="180.26")

    def test_adjusted_claim_with_unchanged_values(self) -> None:
        self.refused_row("values are unchanged", stick_low_status="adjusted")

    def test_manual_claim_when_a_suggestion_existed(self) -> None:
        self.refused_row("cannot be manual", plate_center_status="manual")

    def test_unknown_status_and_suggestion_id(self) -> None:
        self.refused_row("must be one of", stick_high_status="ok")
        self.refused_row("suggestion_id does not match", plate_suggestion_id="plate-hough-edge-v1:999999999999")

    def test_geometry_outside_the_frame(self) -> None:
        self.refused_row("fit inside the frame", plate_radius_px="500.00", plate_radius_status="adjusted")
        self.refused_row("outside", stick_low_x_px="1080.00", stick_low_status="adjusted")
        self.refused_row("outside", stick_high_y_px="-1.00", stick_high_status="adjusted")
        self.refused_row("too close", stick_high_x_px="830.00", stick_high_y_px="1631.00",
                         stick_high_status="adjusted")

    def test_non_finite_values(self) -> None:
        self.refused_row("finite", plate_center_x_px="nan", plate_center_status="adjusted")
        self.refused_row("finite", plate_radius_px="inf", plate_radius_status="adjusted")
        self.refused_row("must be a number", stick_low_y_px="abc", stick_low_status="adjusted")

    def test_malformed_csv(self) -> None:
        good = fakes.csv_bytes([self.row])
        self.refused(b"", "empty")
        self.refused(good.replace(b"format,", b"formats,", 1), "header")
        self.refused(good.replace(b"\n", b",extra\n"), "header")
        self.refused(good[:-1] + b",extra\n", "cells")
        self.refused(b"\xff\xfe" + good, "UTF-8")
        self.refused(good.replace(b"snatch", b"\"snatch"), "malformed")

    def test_row_count_and_order(self) -> None:
        self.refused(fakes.csv_bytes([self.row, self.row]), "1 clips")
        self.refused(fakes.csv_bytes([]), "0 clip rows")
        self.refused_row("clip_index", clip_index="1")

    def test_nothing_confirmed(self) -> None:
        self.refused(fakes.csv_bytes([fakes.skipped_row(self.state["clips"][0], self.state)]), "no clip is confirmed")

    def test_skipped_row_with_geometry(self) -> None:
        row = {**fakes.skipped_row(self.state["clips"][0], self.state), "plate_radius_px": "10.00"}
        self.refused(fakes.csv_bytes([row]), "skipped clip")

    def test_session_id_rules(self) -> None:
        for good in ("2026-10-03", "a", "s1.v2_x"):
            self.assertEqual(session_contract.require_session_id(good), good)
        for bad in ("", "../x", "a/b", "-x", "x" * 65, "2026 10 03"):
            with self.assertRaises(session_contract.SessionCsvError):
                session_contract.require_session_id(bad)


if __name__ == "__main__":
    unittest.main()
