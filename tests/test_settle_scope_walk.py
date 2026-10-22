"""A targeted push settles only the divergences of the items it touches, and reads the ledger once to do it.

Each test is a scenario; its docstring names the failure it prevents (seen live: `qew push --item X` on a 23k-item ledger ran
for minutes with a full ledger snapshot every 2.6 s, because every open divergence of the target was rejected one by one).
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

REPLICATION = "\n[replication]\nauto_forge_sync = false\nforge_concurrency = 1\nfetch_interval_s = 0\ndivergences = \"{mode}\"\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
OTHERS = 8


class SettleScopeWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, ledger_vcs="git", replication=REPLICATION.format(mode="report"))
        self.addCleanup(self.world.close)
        self.others = [self.world.create("--kind", "issue", "--title", f"Other {n}", "--quote", QUOTE) for n in range(OTHERS)]
        self.mine = self.world.create("--kind", "issue", "--title", "Mine", "--quote", QUOTE)
        self.assertEqual(self.world.run("sync", "--full", env=ENV).returncode, 0)
        for item in self.others:  # both sides change the title since the baseline: a divergence, not a plain forge change
            self.assertEqual(self.world.run("set", item, "--title", f"Local {item}", env=ENV).returncode, 0)
        for issue in self.fake.issues.values():
            if issue["title"].startswith("Other"):
                issue["title"] += " (edited on the forge)"
                self.fake.touch(issue)
        self.assertEqual(self.world.run("sync", "--full", env=ENV).returncode, 0)
        self.assertEqual(len(self.divergences()), OTHERS)
        config = self.world.code / ".qew" / "config.toml"
        config.write_text(config.read_text().replace('divergences = "report"', 'divergences = "ledger"'))

    def divergences(self) -> list[object]:
        return json.loads(self.world.run("status", "--json", env=ENV).stdout)["divergences"]

    def test_a_push_of_one_item_reads_the_ledger_a_few_times_not_once_per_open_divergence(self) -> None:
        """Prevents the stall: pushing one item with many open divergences elsewhere rebuilt the whole snapshot once per divergence."""
        self.assertEqual(self.world.run("comment", self.mine, "--body", "pending", env=ENV).returncode, 0)
        result = self.world.run("push", "--item", self.mine, env={**ENV, "QEW_TRACE": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLess(result.stderr.count("ledger snapshot (full)"), OTHERS, result.stderr)

    def test_a_push_of_one_item_leaves_the_divergences_of_other_items_open(self) -> None:
        """Prevents a targeted pass doing the whole board's settling: the divergences of untouched items wait for a full sync."""
        self.assertEqual(self.world.run("comment", self.mine, "--body", "pending", env=ENV).returncode, 0)
        self.assertEqual(self.world.run("push", "--item", self.mine, env=ENV).returncode, 0)
        self.assertEqual(len(self.divergences()), OTHERS)
        full = self.world.run("sync", "--full", env=ENV)
        self.assertEqual(full.returncode, 0, full.stdout + full.stderr)
        self.assertEqual(len(self.divergences()), 0)


if __name__ == "__main__":
    unittest.main()
