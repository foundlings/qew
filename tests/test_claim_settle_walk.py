"""Provisional claims settle for every item the primary can arbitrate, and a claim comment left on the primary is not a dead end.

Each test is a scenario; its docstring names the failure it prevents (seen live on foundlings: sync kept reporting 'claim provisional:
the arbiter is unreachable' for claims on items, and `qew start` said an item was claimed by the actor's own login while the
ledger said unclaimed, until `qew release --force`).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import claim_authority, ledger_config
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class ClaimSettleWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\nselect = "kind:epic,issue"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        slug = ledger_config.mirror_slug(ledger_config.load(self.world.code).mirrors[0])
        config = self.world.code / ".qew" / "config.toml"
        config.write_text(config.read_text() + f'\n[authority]\nprimary = "{slug}"\n', encoding="utf-8")
        self.epic = self.world.create("--kind", "epic", "--title", "Phase", "--quote", QUOTE)
        self.task = self.world.create("--kind", "task", "--title", "Ledger-only work", "--parent", self.epic, "--quote", QUOTE)
        self.issue = self.world.create("--kind", "issue", "--title", "Mirrored work", "--parent", self.epic, "--quote", QUOTE)
        self.assertEqual(self.world.run("sync", env=ENV).returncode, 0)

    def number_of(self, title: str) -> int:
        return next(n for n, issue in self.fake.issues.items() if issue["title"] == title)

    def test_a_provisional_claim_on_a_task_is_settled_through_the_issue_above_it(self) -> None:
        """Prevents a claim that stays provisional forever with 'the arbiter is unreachable': a task has no issue of its own, and
        its reservation lives on the nearest mirrored ancestor, so that is where the sync must ask."""
        self.assertEqual(self.world.run("start", self.task, "--offline", env=ENV).returncode, 0)
        synced = self.world.run("sync", env=ENV)
        self.assertEqual(synced.returncode, 0, synced.stdout + synced.stderr)
        self.assertNotIn("provisional", synced.stdout)
        self.assertEqual([e["type"] for e in self.world.events(self.task)][-2:], ["claimed", "claim_confirmed"])

    def test_the_same_actor_left_holding_the_primary_by_a_stale_comment_can_start(self) -> None:
        """Prevents `qew start` refusing 'already claimed by <you> on the primary' while the ledger holds no claim: the claim
        comment is the actor's own, so the start adopts it."""
        number = self.number_of("Mirrored work")
        self.fake.add_comment(number, "tester", claim_authority.claim_comment(self.issue, "tester", "stale001"))
        started = self.world.run("start", self.issue, env=ENV)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        shown = self.world.run("show", self.issue, "--json", env=ENV).stdout
        self.assertIn('"state": "claimed"', shown.replace('":"', '": "'))

    def test_another_actors_stale_comment_refuses_and_names_the_way_out(self) -> None:
        """Prevents a dead end: when someone else's claim comment holds the primary, the refusal says which command clears it."""
        number = self.number_of("Mirrored work")
        self.fake.add_comment(number, "rival", claim_authority.claim_comment(self.issue, "rival", "stale002"))
        refused = self.world.run("start", self.issue, env=ENV)
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("rival", refused.stderr)
        self.assertIn(f"qew release {self.issue} --force", refused.stderr)


if __name__ == "__main__":
    unittest.main()
