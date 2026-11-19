"""A write that is rolled back leaves nothing behind and prints nothing a caller could mistake for success.

Adversary: a commit that fails (a held repository lock, a hook) after the command printed the new id, and the empty
`events/_xx/<id>` and `items/_xx/<id>` directories a rollback left on a sharded ledger, which every read then called orphans.
"""

import os
import stat
import unittest
from pathlib import Path

from tests.ledger_harness import World


def empty_directories(ledger: Path) -> list[str]:
    found: list[str] = []
    for top in ("events", "items", "mirrors", "baselines"):
        for directory in sorted((ledger / top).rglob("*")) if (ledger / top).is_dir() else []:
            if directory.is_dir() and not any(directory.iterdir()):
                found.append(str(directory.relative_to(ledger)))
    return found


class RollbackWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git", schema="9")
        self.addCleanup(self.world.close)
        self.world.create("--title", "first")

    def refuse_commits(self) -> None:
        hook = self.world.ledger / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho refused >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(hook.stat().st_mode | stat.S_IEXEC)

    def test_a_create_whose_commit_fails_prints_no_id_and_leaves_no_directories(self) -> None:
        """Prevents an agent filing work against an id that was rolled back, and orphan directories that make the ledger unreadable."""
        self.refuse_commits()
        run = self.world.run("create", "--title", "doomed")
        self.assertNotEqual(run.returncode, 0, run.stdout)
        self.assertEqual(run.stdout.strip(), "", "an id was printed for a write that was rolled back")
        self.assertEqual(empty_directories(self.world.ledger), [])
        (self.world.ledger / ".git" / "hooks" / "pre-commit").unlink()
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_empty_event_and_item_directories_are_not_orphans(self) -> None:
        """Prevents one leftover empty directory (git cannot even carry it) making the whole ledger unreadable for the fleet."""
        (self.world.ledger / "events" / "_dz" / "044rksndz").mkdir(parents=True)
        (self.world.ledger / "items" / "_dz" / "044rksndz").mkdir(parents=True)
        self.assertEqual(self.world.run("check").returncode, 0)
        self.assertEqual(self.world.run("list").returncode, 0)

    def test_a_directory_with_events_but_no_item_is_still_an_orphan(self) -> None:
        """Prevents the empty-directory leniency hiding real orphaned history."""
        home = self.world.ledger / "events" / "_dz" / "044rksndz"
        home.mkdir(parents=True)
        (home / "044rksndz.json").write_text("{}", encoding="utf-8")
        self.assertNotEqual(self.world.run("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
