"""Sync keeps its books straight: a clean sync clears pending, and a stale baseline is not a divergence.

Each test is a scenario; its docstring names the failure it prevents (both seen live on foundlings: `qew status` kept
pending=2 after a clean sync, and a completed item was reported as `forge=closed ledger=open`).
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_config
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class SyncBaselineWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        self.slug = ledger_config.mirror_slug(ledger_config.load(self.world.code).mirrors[0])

    def sync(self):
        result = self.world.run("sync", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def pending(self) -> str:
        line = next(line for line in self.world.run("status").stdout.splitlines() if line.startswith("target "))
        return next(word for word in line.split() if word.startswith("pending="))

    def test_an_event_that_changes_nothing_on_the_forge_does_not_leave_the_item_pending(self) -> None:
        """Prevents `pending=N` forever: a comment (or an accepted divergence) after the last push changes no issue field,
        yet the item stayed pending because the unchanged branch never recorded the newest event as synced."""
        item = self.world.create("--kind", "issue", "--title", "Quiet", "--quote", QUOTE)
        self.sync()
        self.assertEqual(self.pending(), "pending=0")
        self.assertEqual(self.world.run("comment", item, "--body", "a note").returncode, 0)
        self.assertEqual(self.pending(), "pending=1")
        self.sync()
        self.assertEqual(self.pending(), "pending=0")

    def test_a_stale_baseline_is_not_a_divergence_when_the_forge_agrees_with_the_ledger(self) -> None:
        """Prevents the false `forge=closed ledger=open`: the ledger closed the item, the forge shows it closed, only the
        recorded baseline said open (the close landed after it was written). Nothing is wrong, so nothing diverges."""
        item = self.world.create("--kind", "issue", "--title", "Closed here", "--quote", QUOTE)
        self.assertEqual(self.world.run("close", item, "--reason", "not_planned").returncode, 0)
        self.sync()
        state = self.world.ledger / ".qew" / "state" / self.slug / f"{item}.json"
        data = json.loads(state.read_text())
        data["seen"]["open"] = True  # the stale baseline
        state.write_text(json.dumps(data))
        self.sync()
        status = self.world.run("status").stdout
        self.assertIn("divergences=0", status)
        self.assertFalse(json.loads(state.read_text())["seen"]["open"])

    def test_a_real_mismatch_names_the_ledgers_own_state(self) -> None:
        """Prevents a misleading report: a human closing an open item shows ledger=open from the ledger, not the baseline."""
        item = self.world.create("--kind", "issue", "--title", "Open here", "--quote", QUOTE)
        self.sync()
        issue = next(iter(self.fake.issues.values()))
        self.fake.set_state(issue, "closed", "completed")
        self.sync()
        status = self.world.run("status").stdout
        self.assertIn("divergences=1", status)
        self.assertIn("forge=closed ledger=open", status)
        self.assertIn(item, status)


if __name__ == "__main__":
    unittest.main()
