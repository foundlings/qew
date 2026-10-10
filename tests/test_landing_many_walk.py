"""Resolving many cited changes costs a few read-only VCS calls, not one snapshotting call per id.

Each test is a scenario; its docstring names the failure it prevents. Foundlings' working copy has ~60k files and every
plain jj call snapshots it, so thousands of per-id calls took hours.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import code_vcs
from qew.code_vcs import Landing

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def run(root: Path, *argv: str) -> str:
    done = subprocess.run(argv, cwd=root, env=ENV, capture_output=True, text=True, check=True)
    return done.stdout.strip()


class LandingManyWalk(unittest.TestCase):
    def counted(self, root: Path, vcs: str, changes: list[str]):
        calls: list[list[str]] = []
        real = code_vcs._run

        def spy(where, argv):
            calls.append(list(argv))
            return real(where, argv)

        with mock.patch.object(code_vcs, "_run", spy):
            return code_vcs.landing_many(root, vcs, changes, "main"), calls

    def test_git_ids_resolve_in_a_few_calls_and_agree_with_the_single_lookup(self) -> None:
        """Prevents one process per commit: landed, not-an-ancestor and unknown commits in a handful of git calls."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run(root, "git", "init", "-q", "-b", "main")
            landed = []
            for number in range(15):
                run(root, "git", "commit", "-q", "--allow-empty", "-m", f"c{number}")
                landed.append(run(root, "git", "rev-parse", "HEAD"))
            run(root, "git", "checkout", "-q", "-b", "side")
            run(root, "git", "commit", "-q", "--allow-empty", "-m", "side")
            side = run(root, "git", "rev-parse", "HEAD")
            run(root, "git", "checkout", "-q", "main")
            changes = [*landed, side, "0123456789abcdef0123456789abcdef01234567"]
            found, calls = self.counted(root, "git", changes)
            self.assertLessEqual(len(calls), 4, calls)
            for change in changes:
                self.assertEqual(found[change][0], code_vcs.landing(root, "git", change, "main")[0], change)
            self.assertEqual((found[landed[0]][0], found[side][0], found[changes[-1]][0]), (Landing.LANDED, Landing.NOT_ANCESTOR, Landing.UNKNOWN))

    def test_jj_ids_resolve_in_a_few_read_only_calls_and_agree_with_the_single_lookup(self) -> None:
        """Prevents a snapshot per id: every jj call ignores the working copy, and 15 changes cost a handful of calls."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run(root, "jj", "git", "init", "--colocate")
            for number in range(15):
                run(root, "jj", "commit", "-m", f"c{number}")
            run(root, "jj", "bookmark", "set", "main", "-r", "@-")
            run(root, "jj", "new", "main", "-m", "side work")
            run(root, "jj", "describe", "-m", "side work not in main")
            ids = run(root, "jj", "log", "--no-graph", "-r", "all() & ~root()", "-T", 'change_id.short(12) ++ "\\n"').split()
            on_main = set(run(root, "jj", "log", "--no-graph", "-r", "::main & ~root()", "-T", 'change_id.short(12) ++ "\\n"').split())
            changes = [*ids, "xxxxxxxxxxxx"]
            found, calls = self.counted(root, "jj", changes)
            self.assertLessEqual(len(calls), 5, len(calls))
            for call in calls:
                self.assertIn("--ignore-working-copy", call)
            for change in changes:
                self.assertEqual(found[change][0], code_vcs.landing(root, "jj", change, "main")[0], change)
            self.assertEqual(found["xxxxxxxxxxxx"][0], Landing.UNKNOWN)
            self.assertTrue(any(found[c][0] is Landing.NOT_ANCESTOR for c in ids if c not in on_main))
            self.assertTrue(all(found[c][0] is Landing.LANDED for c in on_main))


if __name__ == "__main__":
    unittest.main()
