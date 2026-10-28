"""Every qew write records itself in the ledger repo: no forgotten-commit state.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import stat
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World, git, jj_available


class AutoCommitWalk(unittest.TestCase):
    vcs = "git"

    def setUp(self) -> None:
        self.world = World(ledger_vcs=self.vcs)
        self.addCleanup(self.world.close)

    def subjects(self) -> list:
        return self.world.ledger_log()

    def test_each_write_leaves_one_structured_commit_and_a_clean_tree(self) -> None:
        """Prevents work that exists only in a working copy: create, claim, evidence and complete each commit."""
        w = self.world
        item = w.create("--title", "Work", "--quote", QUOTE)
        w.run("start", item)
        w.run("evidence", item, "--change", w.commit("code"))
        w.run("complete", item)
        self.assertEqual(
            self.subjects()[:4],
            [f"qew: complete {item} by tester", f"qew: evidence {item} by tester",
             f"qew: start {item} by tester", f"qew: create {item} by tester"],
        )
        self.assertEqual(w.ledger_dirty(), [])

    def test_a_batch_create_is_one_commit_counting_its_items(self) -> None:
        """Prevents a 400-item import producing 400 commits that nobody can review."""
        import json

        specs = json.dumps([{"title": f"T{n}"} for n in range(3)])
        self.world.run("create", "--from-file", "-", stdin=specs)
        self.assertEqual(self.subjects()[0], "qew: create 3 items by tester")
        self.assertEqual(len(self.subjects()), 2)

    def test_reads_and_refusals_commit_nothing(self) -> None:
        """Prevents noise commits from reads and from refused writes."""
        w = self.world
        item = w.create("--title", "Draft", "--draft")
        before = self.subjects()
        w.run("ready"); w.run("show", item); w.run("check")
        self.assertEqual(w.run("start", item).returncode, 2)
        self.assertEqual(self.subjects(), before)

    def test_a_failing_commit_rolls_the_written_files_back(self) -> None:
        """Prevents an uncommitted-write state: if the commit fails the command fails and leaves no files."""
        w = self.world
        hook = w.ledger / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho refused >&2\nexit 1\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        result = w.run("create", "--title", "Doomed", "--draft")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("commit", result.stderr)
        self.assertFalse((w.ledger / "items").exists() and list((w.ledger / "items").iterdir()))
        self.assertEqual(w.ledger_dirty(), [])

    def test_unrelated_dirty_files_are_never_swept_into_a_qew_commit(self) -> None:
        """Prevents qew committing a human's unfinished notes along with its own files."""
        w = self.world
        (w.ledger / "notes.txt").write_text("mine")
        w.create("--title", "Work", "--draft")
        self.assertEqual(w.ledger_dirty(), ["?? notes.txt"])
        self.assertNotIn("notes.txt", git(w.ledger, "show", "--name-only", "--format=", "HEAD"))

    def test_files_left_by_a_crash_are_recovered_by_the_next_write(self) -> None:
        """Prevents a crash between write and commit leaving files that no command ever commits."""
        w = self.world
        item = w.create("--title", "Crash victim", "--draft")
        orphan = w.home("events", item) / "ZZZZZZZZZ.json"
        orphan.write_text('{"type": "field", "key": "title", "old": "a", "new": "b", "at": "2026-01-01T00:00:00Z", "actor": "x"}\n')
        w.create("--title", "Next", "--draft")
        self.assertTrue(any("qew: recover 1 files" in subject for subject in self.subjects()[:2]), self.subjects())  # before the write's commit on git, swept after it on jj
        self.assertEqual(w.ledger_dirty(), [])

    def test_auto_commit_can_be_switched_off(self) -> None:
        """Prevents the setting being ignored: with auto_commit = false files stay uncommitted."""
        w = World(ledger_vcs="git", replication="\n[replication]\nauto_commit = false\n")
        self.addCleanup(w.close)
        w.create("--title", "Manual", "--draft")
        self.assertEqual(len(w.ledger_log()), 1)
        self.assertTrue(w.ledger_dirty())

    def test_a_ledger_that_is_not_a_repository_still_works(self) -> None:
        """Prevents auto-commit making a plain directory ledger unusable."""
        w = World()
        self.addCleanup(w.close)
        self.assertEqual(w.run("create", "--title", "Plain", "--draft").returncode, 0)


@unittest.skipUnless(jj_available(), "jj is not installed")
class AutoCommitJujutsuWalk(AutoCommitWalk):
    vcs = "jj"

    def test_a_failing_commit_rolls_the_written_files_back(self) -> None:
        self.skipTest("jj has no pre-commit hook; covered by the git walk")

    def test_unrelated_dirty_files_are_never_swept_into_a_qew_commit(self) -> None:
        """Prevents qew committing a human's unfinished notes along with its own files (jj keeps them in the working copy)."""
        w = self.world
        (w.ledger / "notes.txt").write_text("mine")
        w.create("--title", "Work", "--draft")
        self.assertNotIn("notes.txt", git(w.ledger, "show", "--name-only", "--format=", "main"))


if __name__ == "__main__":
    unittest.main()
