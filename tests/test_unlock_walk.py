"""`qew unlock --stale`: the one explicit, audited way a repository lock a crashed git left behind is removed."""

import json
import os
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World

APPROVAL = "Alexander: 'yes, remove that dead lock, nothing is running'"


def age(path: Path, seconds: int) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


class UnlockWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = World(ledger_vcs="git")
        self.addCleanup(self.w.close)
        self.git = self.w.ledger / ".git"
        self.index = self.git / "index.lock"
        self.log = self.w.ledger / ".qew" / "unlock.log"

    def stale(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        age(path, 3600)
        return path

    def test_a_stale_lock_is_removed_with_an_audit_line_and_writes_work_again(self) -> None:
        """Prevents a crashed git blocking the ledger forever, and a removal nobody can trace."""
        self.stale(self.index)
        self.stale(self.git / "refs" / "heads" / "topic.lock")
        blocked = self.w.run("create", "--title", "Blocked", "--quote", QUOTE, env={"QEW_REPO_LOCK_WAIT_S": "1"})
        self.assertEqual(blocked.returncode, 3)
        result = self.w.run("unlock", "--stale", "--quote", APPROVAL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("index.lock", result.stdout)
        self.assertFalse(self.index.exists())
        self.assertFalse((self.git / "refs" / "heads" / "topic.lock").exists())
        entry = json.loads(self.log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(entry["actor"], "tester")
        self.assertEqual(entry["quote"], APPROVAL)
        self.assertEqual(sorted(Path(r["path"]).name for r in entry["removed"]), ["index.lock", "topic.lock"])
        self.assertGreaterEqual(entry["removed"][0]["age_s"], 3000)
        self.assertEqual(self.w.run("create", "--title", "After", "--quote", QUOTE).returncode, 0)

    def test_it_is_explicit_and_needs_a_humans_words(self) -> None:
        """Prevents an unattended removal: --stale and a quoted approval are both required (exit 2), nothing is touched."""
        self.stale(self.index)
        self.assertEqual(self.w.run("unlock").returncode, 2)
        self.assertEqual(self.w.run("unlock", "--stale").returncode, 2)
        self.assertEqual(self.w.run("unlock", "--stale", "--quote", "not a quote").returncode, 2)
        self.assertTrue(self.index.exists())
        self.assertFalse(self.log.exists())

    def test_a_young_lock_is_refused(self) -> None:
        """Prevents removing a lock a running git may own: a lock younger than a minute is never stale."""
        self.index.write_text("", encoding="utf-8")
        result = self.w.run("unlock", "--stale", "--quote", APPROVAL)
        self.assertEqual(result.returncode, 3)
        self.assertIn("not stale", result.stderr)
        self.assertTrue(self.index.exists())

    def test_a_lock_a_live_process_has_open_is_refused(self) -> None:
        """Prevents breaking a lock under a running git: an old lock that some process still holds open stays."""
        self.stale(self.index)
        with open(self.index, "rb"):
            result = self.w.run("unlock", "--stale", "--quote", APPROVAL)
        self.assertEqual(result.returncode, 3)
        self.assertIn("in use", result.stderr)
        self.assertTrue(self.index.exists())

    def test_a_running_qew_writer_blocks_it(self) -> None:
        """Prevents racing a qew writer: while the ledger write lock is held, unlock refuses."""
        from qew import filelock
        self.stale(self.index)
        with filelock.hold(self.w.ledger / ".qew" / "lock", verb="complete", who="agent9"):
            result = self.w.run("unlock", "--stale", "--quote", APPROVAL)
        self.assertEqual(result.returncode, 3)
        self.assertIn("agent9", result.stderr)
        self.assertTrue(self.index.exists())

    def test_dry_run_lists_without_removing_and_other_files_are_never_touched(self) -> None:
        """Prevents collateral damage: --dry-run removes nothing; only *.lock files inside the ledger's .git are candidates."""
        self.stale(self.index)
        keep = self.git / "config.lockfile"
        keep.write_text("x", encoding="utf-8")
        age(keep, 3600)
        outside = self.w.ledger / "items" / "foo.lock"
        outside.parent.mkdir(exist_ok=True)
        outside.write_text("x", encoding="utf-8")
        age(outside, 3600)
        dry = self.w.run("unlock", "--stale", "--dry-run", "--quote", APPROVAL)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("index.lock", dry.stdout)
        self.assertTrue(self.index.exists())
        self.assertFalse(self.log.exists())
        self.w.run("unlock", "--stale", "--quote", APPROVAL)
        self.assertTrue(keep.exists())
        self.assertTrue(outside.exists())

    def test_nothing_to_remove_is_a_quiet_success_and_the_command_works_while_a_lock_blocks_reads(self) -> None:
        """Prevents the repair command needing the repository it repairs: with no lock it says so (exit 0)."""
        result = self.w.run("unlock", "--stale", "--quote", APPROVAL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no stale lock", result.stdout)
        self.stale(self.index)
        again = self.w.run("unlock", "--stale", "--quote", APPROVAL, "--json")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(json.loads(again.stdout)["removed"]), 1)


if __name__ == "__main__":
    unittest.main()
