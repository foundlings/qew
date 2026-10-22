"""`qew pull` brings another machine's ledger work in without a forge reconcile, and a read that misses an id pulls before it refuses.

Each test is a scenario; its docstring names the failure it prevents (seen live: another machine merged a pull request, and here
`qew show ID` said 'no item matches' and `qew pr merge` said the pull request was not recorded, until a `qew sync` of minutes).
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

REPLICATION = "\n[replication]\nauto_forge_sync = false\nforge_concurrency = 1\nfetch_interval_s = 3600\ndivergences = \"report\"\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class PullWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.a = World(mirrors, ledger_vcs="git", remote=True, replication=REPLICATION)
        self.addCleanup(self.a.close)
        self.b = self.a.clone("b", mirrors=mirrors, replication=REPLICATION)
        self.b.run("list", env=ENV)  # b has fetched now: its next read is inside the fetch interval

    def listed(self) -> list[str]:
        out = self.b.run("list", "--json", env=ENV).stdout
        return [row["id"] for row in json.loads(out)] if out.strip() else []

    def filed_elsewhere(self) -> str:
        item = self.a.create("--kind", "issue", "--title", "Landed on a", "--quote", QUOTE)
        self.assertNotIn(item, self.listed())
        return item

    def test_pull_brings_in_the_new_items_and_touches_no_forge(self) -> None:
        """Prevents a machine waiting minutes for a full forge reconcile to see work another machine landed: pull is one ledger
        fetch, names what arrived, and makes no forge call."""
        item = self.filed_elsewhere()
        seen = len(self.fake.requests)
        result = self.b.run("pull", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(item, result.stdout)
        self.assertIn(item, self.listed())
        self.assertEqual(len(self.fake.requests), seen)
        again = self.b.run("pull", env=ENV)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("up to date", again.stdout)

    def test_a_read_that_misses_an_item_pulls_once_before_refusing(self) -> None:
        """Prevents `no item matches` for an item that is on the ledger remote: inside the fetch interval a miss fetches anyway,
        and a reference that is truly unknown still refuses."""
        item = self.filed_elsewhere()
        shown = self.b.run("show", item, env=ENV)
        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
        missing = self.b.run("show", "zzzzzzzzz", env=ENV)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("no item matches", missing.stderr)

    def test_pull_with_the_remote_unreachable_fails_fast_and_says_so(self) -> None:
        """Prevents a pull that quietly reports success while it fetched nothing."""
        self.filed_elsewhere()
        self.a.remote.rename(self.a.remote.with_name("gone"))
        result = self.b.run("pull", env=ENV)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("unreachable", result.stderr)


if __name__ == "__main__":
    unittest.main()
