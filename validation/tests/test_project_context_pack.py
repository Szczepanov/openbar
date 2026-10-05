from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from zipfile import ZIP_STORED, ZipFile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "build_project_context_pack", ROOT / "scripts" / "build_project_context_pack.py"
)
PACK = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(PACK)


class ProjectContextPackTests(unittest.TestCase):
    def test_pack_contains_expected_files_and_current_mit_licensing(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "pack"
            commit = PACK.git_commit(ROOT)
            files = PACK.write_pack(output, root=ROOT)
            names = {path.name for path in files}
            self.assertEqual(names, PACK.GENERATED_NAMES)

            licensing = (output / "06_LICENSING_AND_IP.md").read_text(encoding="utf-8")
            self.assertIn("OpenBar is licensed under the **MIT License**", licensing)
            self.assertIn(f"Repository commit: `{commit}`", licensing)
            self.assertIn("If this snapshot conflicts with the repository", licensing)

    def test_decisions_snapshot_collects_every_committed_adr_and_preserves_supersession(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "pack"
            commit = PACK.git_commit(ROOT)
            PACK.write_pack(output, root=ROOT)
            decisions = (output / "10_DECISIONS_LOG.md").read_text(encoding="utf-8")

            for source in PACK.adr_paths(ROOT, commit):
                self.assertIn(source, decisions)
            self.assertIn("0002-source-available-licensing.md", decisions)
            self.assertIn("0011-mit-licence.md", decisions)
            self.assertIn("Superseded by [ADR-0011]", decisions)
            self.assertIn("# ADR-0011: Relicense OpenBar under the MIT License", decisions)

    def test_render_reads_the_recorded_commit_not_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            self._git(repo, "init", "-q")
            self._git(repo, "config", "user.email", "test@example.com")
            self._git(repo, "config", "user.name", "OpenBar test")
            source = repo / "source.md"
            source.write_text("committed content\n", encoding="utf-8")
            self._git(repo, "add", "source.md")
            self._git(repo, "commit", "-qm", "fixture")
            commit = PACK.git_commit(repo)

            source.write_text("dirty working-tree content\n", encoding="utf-8")
            rendered = PACK.render_sources("snapshot.md", ("source.md",), commit, repo)

            self.assertIn("committed content", rendered)
            self.assertNotIn("dirty working-tree content", rendered)
            self.assertIn(f"Repository commit: `{commit}`", rendered)

    def test_output_directory_rejects_unmanaged_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "pack"
            output.mkdir()
            (output / "stale-file.md").write_text("stale", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unmanaged entries"):
                PACK.write_pack(output, root=ROOT)

    def test_zip_is_byte_deterministic_and_platform_metadata_is_fixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "pack"
            files = PACK.write_pack(output, root=ROOT)
            first = root / "first.zip"
            second = root / "second.zip"
            PACK.write_zip(first, files, output)
            PACK.write_zip(second, files, output)
            self.assertEqual(
                hashlib.sha256(first.read_bytes()).digest(),
                hashlib.sha256(second.read_bytes()).digest(),
            )
            with ZipFile(first) as archive:
                self.assertEqual(sorted(archive.namelist()), sorted(path.name for path in files))
                self.assertTrue(
                    all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
                )
                self.assertTrue(all(info.create_system == 3 for info in archive.infolist()))
                self.assertTrue(all(info.compress_type == ZIP_STORED for info in archive.infolist()))

    @staticmethod
    def _git(root: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
