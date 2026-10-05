from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "build_project_context_pack", ROOT / "scripts" / "build_project_context_pack.py"
)
PACK = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(PACK)


class ProjectContextPackTests(unittest.TestCase):
    def test_pack_contains_expected_numbered_files_and_current_mit_licensing(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "pack"
            files = PACK.write_pack(output, root=ROOT, commit="test-commit")
            names = {path.name for path in files}
            self.assertEqual(
                names,
                {
                    "README.md",
                    "00_PROJECT_CONTEXT.md",
                    "01_VISION_AND_PRODUCT.md",
                    "02_ARCHITECTURE.md",
                    "03_M0_ROADMAP.md",
                    "04_VALIDATION_PROTOCOL.md",
                    "05_DOMAIN_MODEL.md",
                    "06_LICENSING_AND_IP.md",
                    "07_CLEAN_ROOM_BOUNDARIES.md",
                    "08_ENGINEERING_PRINCIPLES.md",
                    "09_AGENT_WORKFLOW.md",
                    "10_DECISIONS_LOG.md",
                },
            )
            licensing = (output / "06_LICENSING_AND_IP.md").read_text(encoding="utf-8")
            self.assertIn("OpenBar is licensed under the **MIT License**", licensing)
            self.assertIn("ADR-0011", licensing)
            self.assertIn("If this snapshot conflicts with the repository", licensing)

    def test_decisions_log_preserves_history_and_supersession(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "pack"
            PACK.write_pack(output, root=ROOT, commit="test-commit")
            decisions = (output / "10_DECISIONS_LOG.md").read_text(encoding="utf-8")
            self.assertIn("0002-source-available-licensing.md", decisions)
            self.assertIn("0011-mit-licence.md", decisions)
            self.assertIn("ADR-0011 is the current accepted licensing decision", decisions)
            self.assertIn("superseded", decisions.lower())

    def test_zip_is_byte_deterministic_for_same_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "pack"
            files = PACK.write_pack(output, root=ROOT, commit="test-commit")
            first = root / "first.zip"
            second = root / "second.zip"
            PACK.write_zip(first, files, output)
            PACK.write_zip(second, files, output)
            self.assertEqual(hashlib.sha256(first.read_bytes()).digest(), hashlib.sha256(second.read_bytes()).digest())
            with ZipFile(first) as archive:
                self.assertEqual(sorted(archive.namelist()), sorted(path.name for path in files))
                self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()))


if __name__ == "__main__":
    unittest.main()
