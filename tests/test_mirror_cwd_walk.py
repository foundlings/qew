"""The mirror targets of a command come from the ledger's own code repo, never from the directory it was run in.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import QUOTE, World

ENV = {"FORGEJO_TOKEN": TOKEN}


class MirrorCwdWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        self.mirrors = f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.quiet = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\n"
        self.owner = World("", ledger_vcs="git", remote=True, replication=self.quiet)  # the ledger's own repo: no mirror here
        self.addCleanup(self.owner.close)
        self.other = self.owner.clone("b", mirrors=self.mirrors, replication=self.quiet)  # an unrelated code repo that mirrors elsewhere

    def test_a_sync_run_from_another_repo_never_files_the_ledgers_items_there(self) -> None:
        """Prevents QEW_LEDGER=A run from a checkout of repo B creating A's items as issues in B's forge repository."""
        self.owner.create("--title", "Belongs elsewhere", "--quote", QUOTE)
        ledger = {**ENV, "QEW_LEDGER": str(self.owner.ledger)}
        result = self.other.run("sync", "--all", env=ledger)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("QEW_LEDGER", result.stderr)
        self.assertEqual(self.fake.issues, {})
        self.assertEqual(self.other.run("mirror", "list", env=ledger).returncode, 2)

    def test_a_repo_that_names_the_same_ledger_still_syncs(self) -> None:
        """Prevents the refusal catching the ordinary case: QEW_LEDGER pointing at the ledger the repo's own config names."""
        self.other.create("--title", "Mine", "--quote", QUOTE)
        result = self.other.run("sync", "--all", env={**ENV, "QEW_LEDGER": str(self.other.ledger)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.fake.issues), 1)


class MirrorRehomeWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.wrong = FakeForgejo().start()
        self.right = FakeForgejo().start()
        self.addCleanup(self.wrong.stop)
        self.addCleanup(self.right.stop)
        self.quiet = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\n"
        entry = lambda fake: f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'
        self.w = World(entry(self.wrong), ledger_vcs="git", replication=self.quiet)
        self.addCleanup(self.w.close)
        self.item = self.w.create("--title", "Strayed", "--quote", QUOTE)
        self.assertEqual(self.w.run("sync", "--all", env=ENV).returncode, 0)
        self.slug = self.w.run("mirror", "list").stdout.split()[0]
        config = self.w.code / ".qew" / "config.toml"
        config.write_text(config.read_text().replace(entry(self.wrong), entry(self.right)))  # the ledger's real home
        self.record = self.w.home("mirrors", self.item) / f"{self.slug}.json"

    def test_rehome_closes_the_stray_issue_with_a_pointer_forgets_the_record_and_the_next_sync_files_it_right(self) -> None:
        """Prevents a mis-filed issue staying open in the wrong repo, or the item staying bound to it so sync never files it right."""
        result = self.w.run("mirror", "rehome", self.item, "--from", self.slug, env=ENV)
        self.assertEqual(result.returncode, 0, result.stderr)
        stray = self.wrong.issues[1]
        self.assertEqual(stray["state"], "closed")
        self.assertIn(self.item, stray["discussion"][-1]["body"])
        self.assertFalse(self.record.exists())
        self.assertEqual(self.w.ledger_dirty(), [])
        self.assertEqual(self.w.run("sync", "--all", env=ENV).returncode, 0)
        self.assertEqual(len(self.right.issues), 1)
        self.assertEqual(len(self.wrong.issues), 1)

    def test_rehome_refuses_a_target_the_ledger_is_configured_for_and_an_unknown_one(self) -> None:
        """Prevents rehoming the item away from its real home, or inventing a record that is not there."""
        right_slug = self.w.run("mirror", "list").stdout.split()[0]
        self.assertEqual(self.w.run("mirror", "rehome", self.item, "--from", right_slug, env=ENV).returncode, 2)
        self.assertEqual(self.w.run("mirror", "rehome", self.item, "--from", "nowhere", env=ENV).returncode, 2)
        self.assertEqual(self.wrong.issues[1]["state"], "open")
        self.assertTrue(self.record.exists())


if __name__ == "__main__":
    unittest.main()
