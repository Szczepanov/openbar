from __future__ import annotations

import json
import re
import runpy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "tools"))
import held_out_guard  # noqa: E402

RESEARCH = ROOT / "research"
PRIVATE_MANIFESTS = (
    ROOT / "validation" / "private" / "manifest.json",
    ROOT / "validation" / "private" / "vbt" / "manifest.json",
)


def fixture(purpose: str, tags: list[str] | None = None) -> dict:
    return {"id": "f", "purpose": purpose, "conditions": {"challenge_tags": tags or []}}


class IsHeldOutTests(unittest.TestCase):
    def test_validation_purpose_is_held_out(self) -> None:
        self.assertTrue(held_out_guard.is_held_out(fixture("validation")))

    def test_reserved_tag_is_held_out_whatever_the_purpose(self) -> None:
        for purpose in ("development", "control", "boundary", "ood"):
            with self.subTest(purpose=purpose):
                self.assertTrue(held_out_guard.is_held_out(fixture(purpose, ["split-reserved-57"])))

    def test_ordinary_development_fixture_is_not_held_out(self) -> None:
        self.assertFalse(held_out_guard.is_held_out(fixture("development", ["split-exploration"])))

    def test_missing_conditions_or_tags_fail_open_only_for_non_validation(self) -> None:
        self.assertFalse(held_out_guard.is_held_out({"purpose": "development"}))
        self.assertFalse(held_out_guard.is_held_out({"purpose": "development", "conditions": {}}))
        self.assertTrue(held_out_guard.is_held_out({"purpose": "validation"}))


class EntryPointRefusalTests(unittest.TestCase):
    """compare.py is stdlib-only, so its real guard runs here; cv2/torch scripts are covered below."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.namespace = runpy.run_path(str(RESEARCH / "opencv-tracking" / "compare.py"))

    def run_fixture_inputs(self, doc: dict, allow: bool) -> None:
        namespace = self.namespace
        with tempfile.TemporaryDirectory(prefix="openbar-guard-") as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({"fixtures": [doc]}), encoding="utf-8")
            namespace["fixture_inputs"](manifest, doc["id"], allow)

    def test_compare_refuses_validation_and_reserved_tag(self) -> None:
        for doc in (fixture("validation"), fixture("development", ["split-reserved-57"])):
            with self.subTest(doc=doc):
                with self.assertRaisesRegex(SystemExit, "held-out"):
                    self.run_fixture_inputs(doc, allow=False)

    def test_compare_explicit_allow_passes_the_guard(self) -> None:
        # Past the guard it fails on the missing annotation files, which is not a held-out error.
        with self.assertRaises(SystemExit) as caught:
            self.run_fixture_inputs(fixture("validation"), allow=True)
        self.assertNotIn("held-out", str(caught.exception))


class NoUnguardedEntryPointTests(unittest.TestCase):
    def test_every_allow_held_out_script_uses_the_shared_guard(self) -> None:
        offenders = []
        checked = 0
        for path in sorted(RESEARCH.rglob("*.py")):
            if "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if not re.search(r"\ballow_held_out\b", text):
                continue
            checked += 1
            if "held_out_guard.is_held_out" not in text or re.search(r'\["purpose"\]\s*==\s*"validation"', text):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertGreaterEqual(checked, 4, "expected the four tracker entry points to be scanned")
        self.assertEqual(offenders, [], "entry points must use held_out_guard.is_held_out, not a local purpose check")


class PrivateManifestConsistencyTests(unittest.TestCase):
    def test_reserved_tag_matches_validation_purpose(self) -> None:
        present = [p for p in PRIVATE_MANIFESTS if p.is_file()]
        if not present:
            self.skipTest("private manifests are not available in this checkout")
        for path in present:
            for item in json.loads(path.read_text(encoding="utf-8"))["fixtures"]:
                tags = item["conditions"].get("challenge_tags", [])
                with self.subTest(manifest=path.name, fixture=item["id"]):
                    self.assertEqual(
                        item["purpose"] == "validation",
                        held_out_guard.RESERVED_TAG in tags,
                        "validation purpose and split-reserved-57 must agree",
                    )


if __name__ == "__main__":
    unittest.main()
