#!/usr/bin/env python3
"""`vbt_session.py init-research` (#113): research-only machine initialization, fail closed. Stdlib only."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_fakes as fakes  # noqa: E402
from session_harness import SessionTestCase  # noqa: E402

import analyze_lift  # noqa: E402
import session_contract  # noqa: E402
import session_ingest  # noqa: E402
import session_machine_init as smi  # noqa: E402
from vbt_process import WorkflowError  # noqa: E402

PROFILE_FIELDS = {
    "format": '"openbar-research-vbt-init-profile"', "format_version": "1", "profile_id": '"lab-profile-1"',
    "exercise": '"snatch"', "plate_diameter_m": "0.45", "stick_length_m": "1.30",
}
RECOMPUTED = "does not match the record recomputed"


def profile_text(changes: dict[str, str | None] | None = None) -> str:
    """Profile JSON text. A change is raw JSON text (so NaN, true or 1e400 can be written); None drops the key."""
    fields = {**PROFILE_FIELDS, **(changes or {})}
    body = ", ".join(f'"{key}": {value}' for key, value in fields.items() if value is not None)
    return "{" + body + "}\n"


def plate(**changes: Any) -> dict[str, Any]:
    return {**fakes.PLATE, "parameters": {"edge_threshold": 0.5}, **changes}


def stick(**changes: Any) -> dict[str, Any]:
    return {**fakes.STICK, "parameters": {"marker_threshold": 0.7}, **changes}


def without(document: dict[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in document.items() if name != key}


def pair(plate_suggestion: dict[str, Any] | None = None,
         stick_suggestion: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"plate": plate_suggestion or plate(), "stick": stick_suggestion or stick()}


def make_clip(plate_suggestion: dict[str, Any] | None = None, stick_suggestion: dict[str, Any] | None = None,
              registered_exercise: str | None = None) -> dict[str, Any]:
    clip = fakes.clip(0, plate=plate_suggestion or plate(), stick=stick_suggestion or stick())
    return {**clip, "registered_exercise": registered_exercise}


class MachineInitTestCase(SessionTestCase):
    @property
    def record_path(self) -> Path:
        return self.session_dir / smi.RECORD_NAME

    def write_profile(self, text: str | None = None) -> Path:
        self.profile_file = self.dir / "profile.json"
        self.profile_file.write_text(profile_text() if text is None else text, encoding="utf-8")
        return self.profile_file

    def init(self, profile: Path | None = None, *extra: str) -> tuple[int, str, str]:
        chosen = profile if profile is not None else self.write_profile()
        return self.main(["init-research", *self.common(), "--profile", str(chosen), *extra])

    def ingest_clips(self, *suggestions: dict[str, Any], extra: tuple[str, ...] = ()) -> tuple[int, str, str]:
        """One inbox video per suggestion pair (VID_<n>.mp4, in clip order), with a stub suggester."""
        for index in range(len(suggestions)):
            self.add_video(f"VID_{index}.mp4", f"video {index}".encode("utf-8"))
        queue = [copy.deepcopy(item) for item in suggestions]
        argv = ["ingest", *self.common(), "--inbox", str(self.inbox), "--media-dir", str(self.media), *extra]
        return self.main(argv, suggester=lambda frame: queue.pop(0))

    def record(self) -> dict[str, Any]:
        return json.loads(self.record_path.read_text(encoding="utf-8"))

    def state_document(self) -> dict[str, Any]:
        return session_ingest.load_session(self.session_dir) or {}

    def loaded(self, profile_bytes: bytes | None = None) -> dict[str, Any]:
        """The reader with the profile bytes the test wrote (default: the last profile written)."""
        chosen = self.profile_file.read_bytes() if profile_bytes is None else profile_bytes
        return smi.load_machine_init(self.session_dir, chosen)

    def write_record_document(self, document: dict[str, Any]) -> None:
        """Canonical encoding, so a refusal comes from the edited content, never from formatting."""
        self.record_path.write_bytes(smi.render_record(document).encode("utf-8"))


class RecordContractTests(MachineInitTestCase):
    def test_good_suggestions_initialize_every_clip_and_the_record_matches_the_contract(self) -> None:
        self.ingest_clips(pair())
        profile = self.write_profile()
        code, _, err = self.init(profile)
        self.assertEqual(code, 0, err)
        state = self.state_document()
        clip = state["clips"][0]
        plate_suggestion, stick_suggestion = clip["suggestions"]["plate"], clip["suggestions"]["stick"]

        def item(suggestion: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
            return {"origin": "machine", "values": values,
                    "suggestion": {key: suggestion[key] for key in ("id", "method", "parameters", "confidence")}}

        expected = {
            "format": "openbar-research-vbt-machine-init", "format_version": 1, "origin": "machine",
            "human_confirmed": False, "research_only": True, "consumer_eligible": False,
            "session_id": self.session_id, "page_id": state["page_id"],
            "inputs": {"session_state_sha256": hashlib.sha256(
                (self.session_dir / session_ingest.STATE_NAME).read_bytes()).hexdigest(),
                "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest()},
            "profile": {"profile_id": "lab-profile-1", "exercise": "snatch", "plate_diameter_m": 0.45,
                        "stick_length_m": 1.3},
            "implementation": {"name": "session_machine_init", "version": 1,
                               "source_sha256": hashlib.sha256(Path(smi.__file__).read_bytes()).hexdigest()},
            "suggester_environment": None,
            "clips": [{
                "clip_index": 0, "fixture_id": clip["fixture_id"], "source_video_sha256": clip["sha256"],
                "package_id": clip["package_id"], "frame_index": 0, "timestamp_s": 0.0, "width_px": 1080,
                "height_px": 1920, "outcome": "initialized", "reasons": [],
                "items": {
                    "plate_center": item(plate_suggestion, {"center_x_px": 400.5, "center_y_px": 1500.0}),
                    "plate_radius": item(plate_suggestion, {"radius_px": 180.25}),
                    "stick_low": item(stick_suggestion, {"low_x_px": 830.0, "low_y_px": 1630.0}),
                    "stick_high": item(stick_suggestion, {"high_x_px": 840.0, "high_y_px": 580.0}),
                },
            }],
            "summary": {"initialized": 1, "rejected": 0},
        }
        self.assertEqual(self.record(), expected)
        rendered = json.dumps(expected, indent=2, sort_keys=True) + "\n"
        self.assertEqual(self.record_path.read_bytes(), rendered.encode("utf-8"))
        self.assertEqual(self.loaded(), expected)

    def test_mixed_session_initializes_one_clip_and_rejects_the_other_to_the_page(self) -> None:
        self.ingest_clips(pair(), pair(plate_suggestion=fakes.FAILED_PLATE))
        code, out, err = self.init()
        self.assertEqual(code, 0, err)
        record = self.record()
        accepted_id, rejected_id = (clip["fixture_id"] for clip in self.state_document()["clips"])
        self.assertEqual(record["summary"], {"initialized": 1, "rejected": 1})
        self.assertEqual([clip["outcome"] for clip in record["clips"]], ["initialized", "rejected"])
        self.assertEqual(record["clips"][1]["reasons"], ["plate_suggestion_missing"])
        self.assertEqual(record["clips"][1]["items"], {})
        self.assertIn(f"{accepted_id}: initialized\n", out)
        self.assertIn(f"{rejected_id}: rejected (plate_suggestion_missing)", out)
        self.assertIn("#95 confirmation page", out)
        self.assertIn("not consumer-eligible", out)
        self.assertIn("wrote: validation/private/vbt/", out)
        self.assertNotIn("no clip initialized", out)
        self.assertEqual(self.loaded(), record)

    def test_all_clips_rejected_exits_zero_with_an_explicit_line(self) -> None:
        self.ingest_clips(pair(plate_suggestion=fakes.FAILED_PLATE))
        code, out, err = self.init()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.record()["summary"], {"initialized": 0, "rejected": 1})
        self.assertIn("no clip initialized; use the #95 confirmation page for every clip", out)

    def test_init_needs_no_ffmpeg_or_opencv(self) -> None:
        self.ingest_clips(pair())
        with mock.patch.object(analyze_lift, "require_tools",
                               side_effect=AssertionError("init-research must not probe tools")):
            code, _, err = self.init()
        self.assertEqual(code, 0, err)


class ProfileRefusalTests(MachineInitTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ingest_clips(pair())
        self.before = self.snapshot()

    def assert_refused(self, text: str, fragment: str) -> None:
        code, _, err = self.init(self.write_profile(text))
        self.assertEqual(code, 1, text[:80])
        self.assertTrue(err.startswith("error: "), err)
        self.assertIn(fragment, err)
        self.assertFalse(self.record_path.exists())
        self.assertEqual(self.snapshot(), self.before, "a refused profile must write nothing")

    def test_each_missing_key_is_refused_by_name(self) -> None:
        for key in smi.PROFILE_KEYS:
            with self.subTest(key=key):
                self.assert_refused(profile_text({key: None}), f"missing {key}")

    def test_unknown_and_typo_keys_are_refused_by_name(self) -> None:
        self.assert_refused(profile_text({"extra": '"x"'}), "unknown extra")
        self.assert_refused(profile_text({"plate_diameter": "0.45", "plate_diameter_m": None}),
                            "unknown plate_diameter")

    def test_wrong_format_and_version_are_refused(self) -> None:
        self.assert_refused(profile_text({"format": '"openbar-research-vbt-init-profile-v2"'}),
                            "profile format must be")
        for value in ('"1"', "true", "false", "2", "1.0", "null"):
            with self.subTest(format_version=value):
                self.assert_refused(profile_text({"format_version": value}), "format_version must be the integer 1")

    def test_profile_id_must_match_the_session_id_pattern(self) -> None:
        for value in ("5", '""', '"bad id!"', '"../escape"', "null", '"-leading"'):
            with self.subTest(profile_id=value):
                self.assert_refused(profile_text({"profile_id": value}), "profile profile_id must match")

    def test_exercise_other_and_unknown_exercises_are_refused(self) -> None:
        self.assert_refused(profile_text({"exercise": '"other"'}), "exercise 'other' is ambiguous")
        for value in ('"Snatch"', '"deadlift"', "5", "null"):
            with self.subTest(exercise=value):
                self.assert_refused(profile_text({"exercise": value}), "profile exercise must be one of")

    def test_lengths_must_be_finite_positive_json_numbers(self) -> None:
        bad_values = ("true", "false", '"0.45"', "null", "[0.45]", "0", "0.0", "-0.45")
        for key in ("plate_diameter_m", "stick_length_m"):
            for value in bad_values:
                with self.subTest(key=key, value=value):
                    self.assert_refused(profile_text({key: value}), f"profile {key} must be a finite JSON number")
            with self.subTest(key=key, value="1e400"):
                self.assert_refused(profile_text({key: "1e400"}), "non-finite number 1e400")
            for value in ("NaN", "Infinity", "-Infinity"):
                with self.subTest(key=key, value=value):
                    self.assert_refused(profile_text({key: value}), "not strict JSON")

    def test_integers_too_large_for_a_float_are_refused_not_raised(self) -> None:
        huge = "1" + "0" * 400  # an exact JSON integer that float() cannot represent
        self.assert_refused(profile_text({"plate_diameter_m": huge}), "profile plate_diameter_m must be a finite")
        self.assert_refused(profile_text({"stick_length_m": huge}), "profile stick_length_m must be a finite")

    def test_duplicate_keys_and_non_object_or_invalid_json_are_refused(self) -> None:
        self.assert_refused(profile_text().replace("}", ', "exercise": "clean"}'), "duplicate key 'exercise'")
        self.assert_refused("[]", "must hold a JSON object")
        self.assert_refused("not json", "not strict JSON")

    def test_missing_profile_file_is_refused(self) -> None:
        code, _, err = self.init(self.dir / "absent.json")
        self.assertEqual(code, 1)
        self.assertIn("cannot read --profile absent.json", err)
        self.assertFalse(self.record_path.exists())

    def test_integer_lengths_are_valid_numbers(self) -> None:
        code, _, err = self.init(self.write_profile(profile_text({"plate_diameter_m": "1", "stick_length_m": "2"})))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.record()["profile"]["plate_diameter_m"], 1.0)


class ClipReasonTests(unittest.TestCase):
    def test_a_clean_clip_has_no_reasons(self) -> None:
        self.assertEqual(smi.clip_reasons(make_clip(), "snatch"), [])

    def test_each_reason_is_raised_alone(self) -> None:
        cases = {
            "plate_suggestion_missing": make_clip(plate_suggestion=fakes.FAILED_PLATE),
            "stick_suggestion_missing": make_clip(stick_suggestion=fakes.FAILED_STICK),
            "exercise_conflict": make_clip(registered_exercise="clean"),
            "suggestion_confidence_invalid": make_clip(plate_suggestion=plate(confidence=1.5)),
            "suggestion_provenance_invalid": make_clip(plate_suggestion=without(plate(), "id")),
            "plate_geometry_invalid": make_clip(plate_suggestion=plate(radius_px=0.0)),
            "stick_geometry_invalid": make_clip(stick_suggestion=stick(low_y_px=1920.0)),
        }
        for code, clip in cases.items():
            with self.subTest(code=code):
                self.assertEqual(smi.clip_reasons(clip, "snatch"), [code])

    def test_a_registered_exercise_that_matches_the_profile_is_not_a_conflict(self) -> None:
        self.assertEqual(smi.clip_reasons(make_clip(registered_exercise="snatch"), "snatch"), [])
        self.assertEqual(smi.clip_reasons(make_clip(registered_exercise="other"), "snatch"), ["exercise_conflict"])

    def test_invalid_confidence_values_are_refused(self) -> None:
        for value in (True, False, "0.5", None, 1.01, -0.01, float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                clip = make_clip(stick_suggestion=stick(confidence=value))
                self.assertEqual(smi.clip_reasons(clip, "snatch"), ["suggestion_confidence_invalid"])
        self.assertEqual(smi.clip_reasons(make_clip(plate_suggestion=without(plate(), "confidence")), "snatch"),
                         ["suggestion_confidence_invalid"])

    def test_confidence_bounds_are_inclusive_and_carry_no_threshold(self) -> None:
        for value in (0, 0.0, 0.5, 1, 1.0):
            with self.subTest(value=value):
                self.assertEqual(smi.clip_reasons(make_clip(plate_suggestion=plate(confidence=value)), "snatch"), [])

    def test_provenance_must_be_a_non_empty_id_method_and_a_parameters_object(self) -> None:
        bad = {
            "id missing": without(plate(), "id"),
            "id empty": plate(id=""),
            "id not a string": plate(id=7),
            "method missing": without(plate(), "method"),
            "method empty": plate(method=""),
            "parameters missing": without(plate(), "parameters"),
            "parameters null": plate(parameters=None),
            "parameters a string": plate(parameters="x"),
        }
        for label, suggestion in bad.items():
            with self.subTest(label=label):
                self.assertEqual(smi.clip_reasons(make_clip(plate_suggestion=suggestion), "snatch"),
                                 ["suggestion_provenance_invalid"])
        self.assertEqual(smi.clip_reasons(make_clip(stick_suggestion=without(stick(), "id")), "snatch"),
                         ["suggestion_provenance_invalid"])
        self.assertEqual(smi.clip_reasons(make_clip(plate_suggestion=plate(parameters={})), "snatch"), [])

    def test_plate_geometry_violations_are_refused(self) -> None:
        bad_plates = {
            "centre missing": without(plate(), "center_x_px"),
            "centre not finite": plate(center_x_px=float("nan")),
            "centre not a number": plate(center_y_px="1500"),
            "centre at the right edge": plate(center_x_px=1080.0, radius_px=10.0),
            "centre above the top": plate(center_y_px=-1.0),
            "radius zero": plate(radius_px=0.0),
            "radius negative": plate(radius_px=-3.0),
            "radius missing": without(plate(), "radius_px"),
            "radius infinite": plate(radius_px=float("inf")),
            "circle crosses the left edge": plate(center_x_px=100.0, radius_px=180.25),
            "circle crosses the bottom edge": plate(center_y_px=1900.0, radius_px=180.0),
            "circle centred at the origin": plate(center_x_px=0.0, center_y_px=0.0, radius_px=0.5),
        }
        for label, suggestion in bad_plates.items():
            with self.subTest(label=label):
                self.assertEqual(smi.clip_reasons(make_clip(plate_suggestion=suggestion), "snatch"),
                                 ["plate_geometry_invalid"])

    def test_plate_circle_touching_the_frame_edges_is_inside(self) -> None:
        touching = plate(center_x_px=540.0, center_y_px=1380.0, radius_px=540.0)
        self.assertEqual(smi.clip_reasons(make_clip(plate_suggestion=touching), "snatch"), [])

    def test_stick_geometry_violations_are_refused(self) -> None:
        bad_sticks = {
            "low marker missing": without(stick(), "low_x_px"),
            "high marker not finite": stick(high_y_px=float("nan")),
            "low marker at the bottom edge": stick(low_y_px=1920.0),
            "high marker left of the frame": stick(high_x_px=-0.5),
            "markers exactly 2 px apart": stick(high_x_px=832.0, high_y_px=1630.0),
            "markers coincide": stick(high_x_px=830.0, high_y_px=1630.0),
        }
        for label, suggestion in bad_sticks.items():
            with self.subTest(label=label):
                self.assertEqual(smi.clip_reasons(make_clip(stick_suggestion=suggestion), "snatch"),
                                 ["stick_geometry_invalid"])
        touching = make_clip(stick_suggestion=stick(high_x_px=832.5, high_y_px=1630.0))
        self.assertEqual(smi.clip_reasons(touching, "snatch"), [])

    def test_geometry_is_not_checked_for_a_failed_suggestion(self) -> None:
        failed = {"method": "plate-hough-edge-v1", "status": "failed", "center_x_px": "bogus"}
        self.assertEqual(smi.clip_reasons(make_clip(plate_suggestion=failed), "snatch"), ["plate_suggestion_missing"])

    def test_several_reasons_come_back_sorted_once_each(self) -> None:
        clip = make_clip(plate_suggestion=fakes.FAILED_PLATE, registered_exercise="clean",
                         stick_suggestion=stick(high_x_px=830.0, high_y_px=1630.0))
        self.assertEqual(smi.clip_reasons(clip, "snatch"),
                         ["exercise_conflict", "plate_suggestion_missing", "stick_geometry_invalid"])
        both_missing = make_clip(plate_suggestion=fakes.FAILED_PLATE, stick_suggestion=fakes.FAILED_STICK)
        self.assertEqual(smi.clip_reasons(both_missing, "snatch"),
                         ["plate_suggestion_missing", "stick_suggestion_missing"])


class ProvenanceTests(MachineInitTestCase):
    def test_suggested_without_an_id_is_rejected_with_the_provenance_reason(self) -> None:
        self.ingest_clips(pair(plate_suggestion=without(plate(), "id")))
        code, out, err = self.init()
        self.assertEqual(code, 0, err)
        clip = self.record()["clips"][0]
        self.assertEqual((clip["outcome"], clip["reasons"], clip["items"]),
                         ("rejected", ["suggestion_provenance_invalid"], {}))
        self.assertIn("rejected (suggestion_provenance_invalid)", out)

    def test_parameters_are_recorded_as_given_and_never_defaulted_to_null(self) -> None:
        self.ingest_clips(pair(plate_suggestion=plate(parameters={"edge_threshold": 0.5})))
        self.assertEqual(self.init()[0], 0)
        plate_item = self.record()["clips"][0]["items"]["plate_center"]
        self.assertEqual(plate_item["suggestion"]["parameters"], {"edge_threshold": 0.5})


class MalformedStateTests(MachineInitTestCase):
    def test_structural_damage_to_session_json_is_an_error_line_not_a_traceback(self) -> None:
        self.ingest_clips(pair())
        state_path = self.session_dir / session_ingest.STATE_NAME
        original = state_path.read_bytes()
        cases = {
            "suggestions is a list": lambda s: s["clips"][0].update(suggestions=[]),
            "plate suggestion missing": lambda s: s["clips"][0]["suggestions"].pop("plate"),
            "width_px is a string": lambda s: s["clips"][0].update(width_px="1080"),
            "width_px is a float": lambda s: s["clips"][0].update(width_px=1080.0),
            "frame_index is a bool": lambda s: s["clips"][0].update(frame_index=False),
            "original_name missing": lambda s: s["clips"][0].pop("original_name"),
            "clip is not an object": lambda s: s["clips"].__setitem__(0, 5),
            "clips is not a list": lambda s: s.update(clips={}),
            "clips is empty": lambda s: s.update(clips=[]),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                state_path.write_bytes(original)
                document = json.loads(original)
                mutate(document)
                state_path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
                code, _, err = self.init()
                self.assertEqual(code, 1)
                self.assertTrue(err.startswith("error: "), err)
                self.assertFalse(self.record_path.exists())
        state_path.write_bytes(original)

    def test_an_overflowing_number_in_session_json_is_an_error_line_not_a_traceback(self) -> None:
        # 1e400 parses to inf, which the record writer (allow_nan=False) cannot serialize.
        self.ingest_clips(pair())
        state_path = self.session_dir / session_ingest.STATE_NAME
        document = json.loads(state_path.read_bytes())
        document["clips"][0]["suggestions"]["plate"]["parameters"] = {"scale": "OVERFLOW"}
        state_path.write_text(json.dumps(document, indent=2, sort_keys=True).replace('"OVERFLOW"', "1e400"),
                              encoding="utf-8")
        code, _, err = self.init()
        self.assertEqual(code, 1)
        self.assertIn("non-finite number 1e400", err)
        self.assertFalse(self.record_path.exists())


class RecordLifecycleTests(MachineInitTestCase):
    def test_identical_inputs_in_a_fresh_root_give_identical_bytes(self) -> None:
        self.ingest_clips(pair())
        profile = self.write_profile()
        self.assertEqual(self.init(profile)[0], 0)
        first = self.record_path.read_bytes()
        # The record depends only on session.json, the profile and the module, so an identical session
        # state in another sessions root must produce the same bytes.
        other_root = tempfile.TemporaryDirectory(prefix="test-sessions-b-", dir=analyze_lift.PERSONAL_ROOT)
        self.addCleanup(other_root.cleanup)
        other_dir = Path(other_root.name) / self.session_id
        other_dir.mkdir()
        (other_dir / session_ingest.STATE_NAME).write_bytes(
            (self.session_dir / session_ingest.STATE_NAME).read_bytes())
        code, _, err = self.main(["init-research", "--session", self.session_id, "--sessions-root", other_root.name,
                                  "--profile", str(profile)])
        self.assertEqual(code, 0, err)
        self.assertEqual((other_dir / smi.RECORD_NAME).read_bytes(), first)

    def test_rerun_with_the_same_inputs_is_unchanged_and_writes_nothing(self) -> None:
        self.ingest_clips(pair())
        profile = self.write_profile()
        self.assertEqual(self.init(profile)[0], 0)
        first = self.record_path.read_bytes()
        stamp = os.stat(self.record_path).st_mtime_ns
        with mock.patch.object(smi.session_ingest, "write_text", side_effect=AssertionError("must not write")):
            for extra in ((), ("--force",)):
                with self.subTest(extra=extra):
                    code, out, err = self.init(profile, *extra)
                    self.assertEqual(code, 0, err)
                    self.assertIn("unchanged: ", out)
                    self.assertNotIn("wrote", out)
        self.assertEqual(self.record_path.read_bytes(), first)
        self.assertEqual(os.stat(self.record_path).st_mtime_ns, stamp)

    def test_changed_profile_is_refused_without_force_and_replaced_with_force(self) -> None:
        self.ingest_clips(pair())
        self.init(self.write_profile())
        first = self.record_path.read_bytes()
        changed = self.write_profile(profile_text({"plate_diameter_m": "0.46"}))
        code, _, err = self.init(changed)
        self.assertEqual(code, 1)
        self.assertIn("--force", err)
        self.assertEqual(self.record_path.read_bytes(), first)
        code, out, err = self.init(changed, "--force")
        self.assertEqual(code, 0, err)
        self.assertIn("replaced: ", out)
        self.assertEqual(self.record()["profile"]["plate_diameter_m"], 0.46)
        self.assertNotEqual(self.record_path.read_bytes(), first)

    def test_a_record_with_other_content_is_refused_without_force(self) -> None:
        self.ingest_clips(pair())
        self.record_path.write_bytes(b"not a record")
        code, _, err = self.init()
        self.assertEqual(code, 1)
        self.assertIn("already exists with different content", err)
        self.assertEqual(self.record_path.read_bytes(), b"not a record")
        self.assertEqual(self.init(None, "--force")[0], 0)
        self.assertEqual(self.loaded()["summary"], {"initialized": 1, "rejected": 0})

    def test_a_leftover_tmp_file_does_not_break_a_rerun(self) -> None:
        self.ingest_clips(pair())
        tmp = self.session_dir / f".{smi.RECORD_NAME}.tmp"
        tmp.write_bytes(b'{"truncated": ')
        code, out, err = self.init()
        self.assertEqual(code, 0, err)
        self.assertIn("wrote: ", out)
        self.assertFalse(tmp.exists())
        code, out, err = self.init()
        self.assertEqual(code, 0, err)
        self.assertIn("unchanged: ", out)
        self.assertEqual(self.loaded()["summary"], {"initialized": 1, "rejected": 0})

    def test_only_the_record_is_written_and_the_manifest_is_untouched(self) -> None:
        self.media.mkdir(parents=True, exist_ok=True)
        old = self.media / "old.mp4"
        old.write_bytes(b"old video")
        analyze_lift.register_video(old, self.manifest, analyze_lift.file_sha256(old), 0.45, "snatch")
        manifest_before = self.manifest.read_bytes()
        self.ingest_clips(pair(), pair(plate_suggestion=fakes.FAILED_PLATE))
        before = self.snapshot()
        code, _, err = self.init()
        self.assertEqual(code, 0, err)
        after = self.snapshot()
        self.assertEqual(set(after) - set(before), {smi.RECORD_NAME})
        self.assertEqual({name: after[name] for name in before}, before)
        self.assertEqual(self.manifest.read_bytes(), manifest_before)


class WriterSelfCheckTests(MachineInitTestCase):
    def test_status_key_inside_parameters_is_refused_before_any_write(self) -> None:
        self.ingest_clips(pair(plate_suggestion=plate(parameters={"status": "x"})))
        code, _, err = self.init()
        self.assertEqual(code, 1)
        self.assertIn("must not carry a status key", err)
        self.assertFalse(self.record_path.exists())

    def test_a_refused_status_key_leaves_an_existing_record_untouched(self) -> None:
        self.ingest_clips(pair())
        self.assertEqual(self.init()[0], 0)
        good = self.record_path.read_bytes()
        self.ingest_clips(pair(plate_suggestion=plate(parameters={"status": "x"})), extra=("--force",))
        code, _, err = self.init(None, "--force")
        self.assertEqual(code, 1)
        self.assertIn("must not carry a status key", err)
        self.assertEqual(self.record_path.read_bytes(), good)


class ReaderTests(MachineInitTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ingest_clips(pair(), pair(plate_suggestion=fakes.FAILED_PLATE))
        self.assertEqual(self.init()[0], 0)
        self.good = self.record()

    def assert_refused(self, document: dict[str, Any], fragment: str = "") -> None:
        self.write_record_document(document)
        with self.assertRaisesRegex(WorkflowError, fragment or "."):
            self.loaded()

    def test_the_written_record_loads(self) -> None:
        self.assertEqual(self.loaded(), self.good)

    def test_promotion_attempts_are_refused(self) -> None:
        mutations = {
            "human_confirmed true": (lambda d: d.update(human_confirmed=True), "not human-confirmed"),
            "consumer_eligible true": (lambda d: d.update(consumer_eligible=True), "consumer-eligible"),
            "research_only false": (lambda d: d.update(research_only=False), "research-only"),
            "record origin human": (lambda d: d.update(origin="human"), "machine origin"),
            "added status key": (lambda d: d.update(status="accepted"), "must not carry a status key"),
            "nested status key": (lambda d: d["clips"][0]["items"]["plate_center"].update(status="accepted"),
                                  "status key"),
            "statuses key in a clip": (lambda d: d["clips"][0].update(statuses=["accepted"]), "status key"),
            "item origin human": (lambda d: d["clips"][0]["items"]["plate_center"].update(origin="human"),
                                  "only machine values"),
            "unknown top-level key": (lambda d: d.update(extra=1), "unknown extra"),
            "unknown clip key": (lambda d: d["clips"][0].update(extra=1), "unknown extra"),
            "unknown item key": (lambda d: d["clips"][0]["items"]["stick_low"].update(extra=1), "unknown extra"),
            "item value edited": (lambda d: d["clips"][0]["items"]["plate_radius"]["values"].update(radius_px=181.0),
                                  RECOMPUTED),
            "rejected clip given items": (lambda d: d["clips"][1].update(items=d["clips"][0]["items"]),
                                          "needs reasons and no items"),
            "rejected clip reasons dropped": (lambda d: d["clips"][1].update(reasons=[]), "needs reasons"),
            "initialized clip claims a reason": (lambda d: d["clips"][0].update(reasons=["exercise_conflict"]),
                                                 "carries reasons"),
            "outcome flipped": (lambda d: d["clips"][1].update(outcome="initialized"), "carries reasons"),
            "clip index moved": (lambda d: d["clips"][0].update(clip_index=1), "page order"),
            "summary edited": (lambda d: d["summary"].update(initialized=2), RECOMPUTED),
            "page id mismatch": (lambda d: d.update(page_id="0" * 16), "stale"),
            "session id mismatch": (lambda d: d.update(session_id="2026-10-04"), "is for session"),
            "state hash mismatch": (lambda d: d["inputs"].update(session_state_sha256="0" * 64), "stale"),
            "profile hash malformed": (lambda d: d["inputs"].update(profile_sha256="xyz"), "SHA-256"),
            "profile exercise other": (lambda d: d["profile"].update(exercise="other"), "ambiguous"),
            "unknown implementation": (lambda d: d["implementation"].update(name="other"), "unknown implementation"),
            "implementation hash malformed": (lambda d: d["implementation"].update(source_sha256="ABC"),
                                              "SHA-256"),
            "format version 2": (lambda d: d.update(format_version=2), "version 1"),
            "format version true": (lambda d: d.update(format_version=True), "version 1"),
        }
        for label, (mutate, fragment) in mutations.items():
            with self.subTest(label=label):
                document = copy.deepcopy(self.good)
                mutate(document)
                self.assert_refused(document, fragment)

    def test_a_session_json_edited_after_the_record_makes_it_stale(self) -> None:
        state_path = self.session_dir / session_ingest.STATE_NAME
        state_path.write_bytes(state_path.read_bytes() + b"\n")
        with self.assertRaisesRegex(WorkflowError, "session.json changed"):
            self.loaded()

    def test_a_reingest_with_force_makes_the_record_stale(self) -> None:
        code, _, err = self.ingest_clips(pair(stick_suggestion=stick(low_x_px=831.0)),
                                         pair(plate_suggestion=fakes.FAILED_PLATE), extra=("--force",))
        self.assertEqual(code, 0, err)
        with self.assertRaisesRegex(WorkflowError, "stale"):
            self.loaded()

    def test_a_missing_record_is_refused(self) -> None:
        self.record_path.unlink()
        with self.assertRaisesRegex(WorkflowError, "has no machine-init.json"):
            self.loaded()

    def test_a_session_csv_cannot_carry_a_machine_status(self) -> None:
        state = self.state_document()
        row = fakes.accepted_row(state["clips"][0], state)
        row["plate_center_status"] = "machine"
        rows = [row, fakes.skipped_row(state["clips"][1], state)]
        self.assertNotIn("machine", session_contract.STATUSES)
        with self.assertRaisesRegex(session_contract.SessionCsvError, "plate_center_status must be one of"):
            session_contract.parse_session_csv(fakes.csv_bytes(rows), state)


class ForgeryTests(MachineInitTestCase):
    """Forged records that pass the header and flag checks must fail the byte-exact recomputation."""

    def setUp(self) -> None:
        super().setUp()
        self.ingest_clips(pair(), pair(plate_suggestion=fakes.FAILED_PLATE))
        self.assertEqual(self.init()[0], 0)
        self.good = self.record()
        self.profile_bytes = self.profile_file.read_bytes()

    def assert_forged_refused(self, mutate: Any, fragment: str, profile_bytes: bytes | None = None) -> None:
        document = copy.deepcopy(self.good)
        mutate(document)
        self.write_record_document(document)
        with self.assertRaisesRegex(WorkflowError, fragment):
            self.loaded(profile_bytes)

    def test_an_unmodified_record_rewritten_by_the_helper_is_accepted(self) -> None:
        # Control: without it, every RECOMPUTED refusal below could come from the encoding alone.
        self.write_record_document(copy.deepcopy(self.good))
        self.assertEqual(self.loaded(self.profile_bytes), self.good)

    def test_a_forged_clip_claiming_initialized_for_a_failed_plate_is_refused(self) -> None:
        def claim_initialized(document: dict[str, Any]) -> None:
            document["clips"][1].update(outcome="initialized", reasons=[],
                                        items=copy.deepcopy(document["clips"][0]["items"]))
        self.assert_forged_refused(claim_initialized, RECOMPUTED)

    def test_an_edited_suggester_environment_is_refused(self) -> None:
        self.assert_forged_refused(lambda d: d.update(suggester_environment={"opencv": "4.10.0"}), RECOMPUTED)
        self.assert_forged_refused(lambda d: d.update(suggester_environment="x"), RECOMPUTED)

    def test_edited_profile_fields_in_the_record_are_refused(self) -> None:
        self.assert_forged_refused(lambda d: d["profile"].update(plate_diameter_m=0.46), RECOMPUTED)
        self.assert_forged_refused(lambda d: d["profile"].update(stick_length_m=1.4), RECOMPUTED)
        self.assert_forged_refused(lambda d: d["profile"].update(profile_id="other-profile"), RECOMPUTED)

    def test_a_changed_profile_hash_in_the_record_is_refused(self) -> None:
        self.assert_forged_refused(lambda d: d["inputs"].update(profile_sha256="0" * 64), "different profile")

    def test_a_profile_file_whose_bytes_differ_from_the_recorded_hash_is_refused(self) -> None:
        with self.assertRaisesRegex(WorkflowError, "different profile"):
            self.loaded(self.profile_bytes + b"\n")
        with self.assertRaisesRegex(WorkflowError, "different profile"):
            self.loaded(profile_text({"plate_diameter_m": "0.46"}).encode("utf-8"))

    def test_json_type_confusion_is_refused_even_when_the_values_are_equal(self) -> None:
        self.assert_forged_refused(lambda d: d["clips"][0].update(frame_index=False), RECOMPUTED)
        self.assert_forged_refused(lambda d: d["clips"][0].update(width_px=1080.0), RECOMPUTED)
        self.assert_forged_refused(lambda d: d["summary"].update(initialized=True), RECOMPUTED)

    def test_an_integer_plate_diameter_in_the_record_is_refused_although_numerically_equal(self) -> None:
        profile_bytes = self.write_profile(profile_text({"plate_diameter_m": "1"})).read_bytes()
        self.assertEqual(self.init(self.profile_file, "--force")[0], 0)
        document = copy.deepcopy(self.record())
        document["profile"]["plate_diameter_m"] = 1
        self.write_record_document(document)
        with self.assertRaisesRegex(WorkflowError, RECOMPUTED):
            self.loaded(profile_bytes)


class SessionIntegrityTests(MachineInitTestCase):
    def test_a_session_whose_page_id_or_template_hash_was_edited_is_refused(self) -> None:
        self.ingest_clips(pair())
        state_path = self.session_dir / session_ingest.STATE_NAME
        original = state_path.read_bytes()
        state = json.loads(original)
        edits = {
            "page id edited": (lambda s: s.update(page_id="0" * 16), "does not match its page id"),
            "template hash removed": (lambda s: s.pop("template_sha256"), "template_sha256 must be a string"),
        }
        for label, (edit, fragment) in edits.items():
            with self.subTest(label=label):
                edited = copy.deepcopy(state)
                edit(edited)
                state_path.write_text(json.dumps(edited, indent=2, sort_keys=True), encoding="utf-8")
                code, _, err = self.init()
                self.assertEqual(code, 1)
                self.assertIn(fragment, err)
                self.assertFalse(self.record_path.exists())
        state_path.write_bytes(original)

    def test_a_session_without_ingest_is_refused(self) -> None:
        code, _, err = self.main(["init-research", "--session", "2026-10-09", "--sessions-root", str(self.sessions),
                                  "--profile", str(self.write_profile())])
        self.assertEqual(code, 1)
        self.assertIn("has no session.json", err)
        self.assertFalse((self.sessions / "2026-10-09").exists())


if __name__ == "__main__":
    unittest.main()
