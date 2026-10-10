"""Walks of `qew init`: created once, rooted at the repository, refusing links, atomic.

Adversary: a second config in a subdirectory, a rerun clobbering a hand-edited
mirror, a write through a symlink, and a half-written config after a failed rename.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.ledger_harness import PROJECT_ROOT, git


def init(cwd: Path, *arguments: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src"), "QEW_OFFLINE": "1"}
    env.pop("QEW_LEDGER", None)
    return subprocess.run([sys.executable, "-m", "qew.cli", "init", *arguments], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=60)


class InitWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.repo = self.base / "code"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        self.config = self.repo / ".qew" / "config.toml"
        self.marker = self.base / "ledger" / "qew-ledger.toml"

    def test_golden_init_creates_config_and_ledger_then_rerun_is_a_no_op(self) -> None:
        """A rerun prints `already initialized` and leaves every byte alone; the guidance names the trust rule."""
        first = init(self.repo, "--ledger", "../ledger")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("created", first.stdout)
        self.assertIn("untrusted", first.stdout)
        self.assertIn("qew complete", first.stdout)
        self.assertIn("schema_version = 5", self.marker.read_text(), "a new ledger declares the attachment layout from day one")
        before = (self.config.read_bytes(), self.marker.read_bytes())
        again = init(self.repo, "--ledger", "../ledger")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("already initialized", again.stdout)
        self.assertEqual((self.config.read_bytes(), self.marker.read_bytes()), before)

    def test_init_from_a_subdirectory_configures_the_repository_root(self) -> None:
        """No stray .qew appears in the subdirectory."""
        sub = self.repo / "src" / "deep"
        sub.mkdir(parents=True)
        result = init(sub, "--ledger", "../ledger")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.config.exists())
        self.assertFalse((sub / ".qew").exists())

    def test_init_outside_a_repository_is_refused(self) -> None:
        """Without a .git or .jj above, nothing is written and the exit code is 2."""
        loose = self.base / "loose"
        loose.mkdir()
        result = init(loose)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(list(loose.iterdir()), [])

    def test_init_refuses_a_symlinked_config_or_directory(self) -> None:
        """Writing through a link could land anywhere."""
        target = self.base / "elsewhere"
        target.mkdir()
        (self.repo / ".qew").symlink_to(target)
        self.assertEqual(init(self.repo, "--ledger", "../ledger").returncode, 2)
        self.assertEqual(list(target.iterdir()), [])
        (self.repo / ".qew").unlink()
        (self.repo / ".qew").mkdir()
        (target / "real.toml").write_text("x")
        (self.repo / ".qew" / "config.toml").symlink_to(target / "real.toml")
        self.assertEqual(init(self.repo, "--ledger", "../ledger").returncode, 2)
        self.assertEqual((target / "real.toml").read_text(), "x")

    def test_init_keeps_hand_edited_mirrors_and_unknown_fields(self) -> None:
        """A rerun after editing the config must not unlink a board or drop a field."""
        init(self.repo, "--ledger", "../ledger")
        edited = self.config.read_text() + '\n[future]\nkey = 1\n[[mirror]]\nforge = "github"\nrepo = "https://github.com/o/r"\nproject = 3\n'
        self.config.write_text(edited)
        self.assertIn("already initialized", init(self.repo).stdout)
        self.assertEqual(self.config.read_text(), edited)

    def test_an_existing_ledger_file_is_never_overwritten(self) -> None:
        """qew-ledger.toml may carry id modes and a prefix chosen by a human."""
        self.marker.parent.mkdir()
        self.marker.write_text('name = "mine"\nprefix = "ZZ"\nschema_version = 1\n')
        init(self.repo, "--ledger", "../ledger")
        self.assertIn('prefix = "ZZ"', self.marker.read_text())

    def test_failed_atomic_replace_leaves_no_config_and_no_temp_files(self) -> None:
        """A rename that fails must not leave a half-written config or a stray temp file."""
        from qew import cmd_items
        from qew.errors import QewError

        os.chdir(self.repo)
        self.addCleanup(os.chdir, PROJECT_ROOT)
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(QewError):
                cmd_items.init("../ledger")
        self.assertFalse(self.config.exists())
        self.assertEqual(list((self.repo / ".qew").iterdir()) if (self.repo / ".qew").exists() else [], [])


if __name__ == "__main__":
    unittest.main()
