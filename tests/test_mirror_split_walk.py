"""Mirror identity is written once; the forge-synced state is a separate committed baseline: machines never conflict on mirror files.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import QUOTE, World, baseline_files, baseline_path

ENV = {"FORGEJO_TOKEN": TOKEN}
IDENTITY = {"forge", "url", "kind", "number", "node_id", "issue_url"}


class MirrorSplitWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        self.mirrors = f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.quiet = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\n"

    def test_the_committed_mirror_file_holds_identity_only_and_never_changes_after_creation(self) -> None:
        """Prevents write-write conflicts: sync state (hashes, event ids, timestamps) stays out of the mirror file, in the baseline."""
        w = World(self.mirrors, ledger_vcs="git", replication=self.quiet)
        self.addCleanup(w.close)
        item = w.create("--title", "Work", "--quote", QUOTE)
        w.run("sync", "--all", env=ENV)
        path = next((w.home("mirrors", item)).glob("*.json"))
        committed = json.loads(path.read_text())
        self.assertEqual(set(committed), IDENTITY)
        w.run("set", item, "--title", "Renamed")
        w.run("sync", "--all", env=ENV)
        self.assertEqual(json.loads(path.read_text()), committed)
        self.assertEqual(w.ledger_dirty(), [])
        self.assertEqual(len(baseline_files(w.ledger, item)), 1)  # a sync replaces the baseline, never piles them up
        self.assertIn("synced_hash", json.loads(baseline_path(w.ledger, item).read_text()))

    def test_two_checkouts_syncing_and_editing_the_same_items_merge_without_conflicts(self) -> None:
        """Prevents the conflicted-ledger failure seen live: both machines sync the same target and the ledger still converges."""
        a = World(self.mirrors, ledger_vcs="git", remote=True, replication=self.quiet)
        self.addCleanup(a.close)
        b = a.clone("b", mirrors=self.mirrors, replication=self.quiet)
        item = a.create("--title", "Shared", "--quote", QUOTE)
        a.run("sync", "--all", env=ENV)
        b.run("list")
        a.run("set", item, "--title", "From A")
        b.run("set", item, "--priority", "1")
        self.assertEqual(a.run("sync", "--all", env=ENV).returncode, 0)
        self.assertEqual(b.run("sync", "--all", env=ENV).returncode, 0)
        for world in (a, b, a):
            self.assertEqual(world.run("check").returncode, 0)
            world.run("ready")
        self.assertEqual(a.run("show", item).stdout, b.run("show", item).stdout)
        self.assertEqual(a.ledger_dirty(), [])

    def test_an_old_format_mirror_file_still_reads_and_is_not_rewritten(self) -> None:
        """Prevents breaking ledgers written before the split: volatile keys in the file seed the state."""
        w = World(self.mirrors, replication=self.quiet)
        self.addCleanup(w.close)
        item = w.create("--title", "Old", "--quote", QUOTE)
        w.run("sync", "--all", env=ENV)
        path = next((w.home("mirrors", item)).glob("*.json"))
        old = {**json.loads(path.read_text()), "synced_hash": "abc", "synced_event": "zzzzzzzzz", "linked": []}
        path.write_text(json.dumps(old))
        shutil.rmtree(w.ledger / ".qew" / "state", ignore_errors=True)  # a ledger from before the split has no sync state of its own
        shutil.rmtree(w.ledger / "baselines")
        self.assertEqual(w.run("check").returncode, 1)  # a future synced_event is still caught
        old["synced_event"] = ""
        path.write_text(json.dumps(old))
        self.assertEqual(w.run("check").returncode, 0)
        self.assertEqual(w.run("sync", "--all", env=ENV).returncode, 0)
        self.assertEqual(json.loads(path.read_text()), old)


if __name__ == "__main__":
    unittest.main()
