"""Command-level walk: hooks remove on a hook-free checkout writes nothing."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class HooksRemoveNoopWalk(unittest.TestCase):
    def test_remove_on_clean_checkout_creates_no_files_or_directories(self) -> None:
        # Failure prevented: a no-op removal creating empty agent config
        # directories (.claude, .codex, .cursor, .opencode/...) in the checkout.
        for agent in ("claude", "codex", "cursor", "opencode"):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as directory:
                repository = Path(directory)
                self._run(repository, "git", "init", "--quiet")
                before = sorted(p.name for p in repository.iterdir())

                result = self._run(repository, sys.executable, "-m", "qew", "hooks", "remove", agent)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"unchanged {agent} hook\n")
                self.assertEqual(sorted(p.name for p in repository.iterdir()), before)

    def _run(self, repository: Path, *command: str) -> subprocess.CompletedProcess:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        return subprocess.run(
            command, cwd=repository, env=environment,
            capture_output=True, text=True, check=False,
        )


if __name__ == "__main__":
    unittest.main()
