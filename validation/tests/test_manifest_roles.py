from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location("openbar_manifest_roles", ROOT / "validation" / "tools" / "manifest_roles.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


roles = load()


def fixture(identifier, tags=(), reference=None, path=None):
    result = {"id": identifier, "conditions": {"challenge_tags": list(tags)}}
    if reference:
        result["source"] = {"reference": reference}
    if path:
        result["media"] = {"repository_path": path}
    return result


class SessionAndRoleTests(unittest.TestCase):
    def test_session_comes_from_the_tag_before_the_file_name(self):
        item = fixture("a", ["session-20230203"], reference="Local recording: VID_20991231_000000.mp4")
        self.assertEqual(roles.session_of(item), "session-20230203")

    def test_session_falls_back_to_the_phone_file_name_date(self):
        self.assertEqual(roles.session_of(fixture("a", reference="Local recording: VID_20261002_075848~2.mp4")),
                         "session-20261002")
        self.assertEqual(roles.session_of(fixture("a", path="validation/private/media/VID_20230915_131032.mp4")),
                         "session-20230915")

    def test_unrecognised_session_is_none(self):
        self.assertIsNone(roles.session_of(fixture("a", reference="synthetic")))
        self.assertIsNone(roles.session_of({"id": "a"}))

    def test_roles_are_the_split_tags_only(self):
        self.assertEqual(roles.roles_of(fixture("a", ["60fps", "split-exploration"])), ["split-exploration"])
        self.assertEqual(roles.roles_of({"id": "a"}), [])


class CheckTests(unittest.TestCase):
    def test_a_consistent_manifest_has_no_violations(self):
        report = roles.check([
            fixture("a", ["session-20230203", "split-frozen-eval"]),
            fixture("b", ["session-20230203", "split-frozen-eval"]),
            fixture("c", ["session-20230206", "split-exploration"]),
        ])
        self.assertEqual(report["violations"], [])
        self.assertEqual(report["sessions"]["session-20230203"], {"split-frozen-eval": ["a", "b"]})

    def test_one_session_with_two_roles_is_a_breach(self):
        report = roles.check([
            fixture("dev", ["split-exploration"], reference="VID_20261002_075848~2.mp4"),
            fixture("held", ["split-reserved-57"], reference="VID_20261002_075819~2.mp4"),
        ])
        (violation,) = report["violations"]
        self.assertEqual(violation["kind"], "session_crosses_roles")
        self.assertEqual(violation["session"], "session-20261002")
        self.assertEqual(violation["roles"], {"split-exploration": ["dev"], "split-reserved-57": ["held"]})

    def test_a_fixture_with_two_role_tags_is_a_breach(self):
        report = roles.check([fixture("a", ["split-exploration", "split-reserved-57"])])
        self.assertEqual(report["violations"], [{"kind": "multiple_roles", "fixture": "a",
                                                 "roles": ["split-exploration", "split-reserved-57"]}])

    def test_an_unassigned_clip_in_an_assigned_session_is_a_breach(self):
        report = roles.check([fixture("a", ["session-20230203", "split-frozen-eval"]), fixture("b", ["session-20230203"])])
        (violation,) = report["violations"]
        self.assertEqual(violation["kind"], "unassigned_in_assigned_session")
        self.assertEqual(violation["fixtures"], ["b"])

    def test_wholly_unassigned_sessions_and_unknown_sessions_are_not_breaches(self):
        report = roles.check([fixture("a", ["session-20230203"]), fixture("b", ["session-20230203"]), fixture("c")])
        self.assertEqual(report["violations"], [])

    def test_accepted_sessions_are_filtered_but_multiple_roles_never_are(self):
        report = roles.check([
            fixture("dev", ["split-exploration"], reference="VID_20261002_1.mp4"),
            fixture("held", ["split-reserved-57"], reference="VID_20261002_2.mp4"),
            fixture("two", ["split-exploration", "split-reserved-57"]),
        ])
        left = roles.unaccepted(report, ["session-20261002"])
        self.assertEqual([v["kind"] for v in left], ["multiple_roles"])

    def test_the_public_manifest_is_consistent(self):
        manifest = json.loads((ROOT / "validation" / "fixtures" / "public" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(roles.check(manifest["fixtures"])["violations"], [])


class CliTests(unittest.TestCase):
    def run_main(self, fixtures, *extra):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.json"
            path.write_text(json.dumps({"fixtures": fixtures}), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = roles.main(["--manifest", str(path), *extra])
            return code, out.getvalue(), err.getvalue()

    def test_exit_codes_and_accepted_marker(self):
        bad = [fixture("a", ["split-exploration"], reference="VID_20261002_1.mp4"),
               fixture("b", ["split-reserved-57"], reference="VID_20261002_2.mp4")]
        code, out, _ = self.run_main(bad)
        self.assertEqual(code, 1)
        self.assertIn("BREACH", out)
        code, out, _ = self.run_main(bad, "--accept", "session-20261002")
        self.assertEqual(code, 0)
        self.assertIn("ACCEPTED", out)
        self.assertEqual(self.run_main([fixture("a", ["split-exploration"])])[0], 0)

    def test_unreadable_manifest_is_exit_two(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(roles.main(["--manifest", str(ROOT / "does-not-exist.json")]), 2)
        self.assertIn("error:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
