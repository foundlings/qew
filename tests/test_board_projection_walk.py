"""Walks of the board projection: each synced issue's Status and Priority follow the ledger.

Adversary: a card moved by hand, eventual consistency on the forge that hides a write,
and an item leaving the target's select (its card goes, its issue must not close).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

from qew import cmd_project, cmd_sync, ledger_config, sync as sync_module
from qew.forge_github import GitHub
from qew.forge_http import urllib_transport
from qew.ledger_store import Ledger
from qew.sync import Target, sync_target

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class ProjectionWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        self.select = ""
        self.world = World(self.config(""))
        self.addCleanup(self.world.close)
        sync_module.READBACK_DELAY = 0.0
        sync_module.READBACK_ATTEMPTS = 3

    def config(self, select: str) -> str:
        line = f'select = "{select}"\n' if select else ""
        return f'[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n{line}'

    def sync(self):
        config = ledger_config.load(self.world.code)
        if not (self.world.ledger / "boards").exists():
            cmd_project.ensure(config, None, build=lambda m: GitHub(m, urllib_transport(), ENV))
        mirror = cmd_sync.mirrors_with_boards(Ledger(self.world.ledger, "TST"), config)[0]
        forge = GitHub(mirror, urllib_transport(), ENV)
        return sync_target(Ledger(self.world.ledger, "TST"), Target(mirror, ledger_config.mirror_slug(mirror), forge), config)

    def card(self, number: int):
        node = self.fake.issues[number]["node_id"]
        return (
            self.fake.item_value("1", node, "Status"),
            self.fake.item_value("1", node, "Priority"),
        )

    def test_claim_and_completion_move_the_card_and_priority_changes_follow(self) -> None:
        """Prevents a board that shows yesterday's status or priority."""
        w = self.world
        item = w.create("--kind", "issue", "--title", "T", "--priority", "1", "--quote", QUOTE)
        self.sync()
        self.assertEqual(self.card(1), ("Ready", "P1"))
        w.run("start", item)
        self.sync()
        self.assertEqual(self.card(1), ("In progress", "P1"))
        w.run("set", item, "--priority", "0")
        w.run("evidence", item, "--change", w.commit())
        w.run("complete", item)
        self.sync()
        self.assertEqual(self.card(1), ("Done", "P0"))

    def test_an_oversized_priority_clamps_to_p4(self) -> None:
        """Prevents a priority beyond the field's options failing the whole sync."""
        self.world.create("--kind", "issue", "--title", "T", "--priority", "9", "--quote", QUOTE)
        report = self.sync()
        self.assertEqual(report.failures, [])
        self.assertEqual(self.card(1), ("Ready", "P4"))
        self.assertTrue(any("priority 9" in w for w in report.warnings))

    def test_a_card_moved_by_hand_is_moved_back_on_the_next_sync(self) -> None:
        """Prevents drift on the forge outliving the next sync."""
        self.world.create("--kind", "issue", "--title", "T", "--quote", QUOTE)
        self.sync()
        project = self.fake.project("1")
        status = next(f for f in project["fields"] if f["name"] == "Status")
        done = next(o["id"] for o in status["options"] if o["name"] == "Done")
        next(iter(project["items"].values()))["values"][status["id"]] = done
        self.assertEqual(self.card(1)[0], "Done")
        self.sync()
        self.assertEqual(self.card(1)[0], "Ready")

    def test_forge_lag_is_retried_then_reported_and_never_recorded_as_synced(self) -> None:
        """Prevents a mirror record claiming a placement the forge never showed."""
        item = self.world.create("--kind", "issue", "--title", "T", "--quote", QUOTE)
        self.sync()
        self.world.run("start", item)
        self.fake.lag = 100
        report = self.sync()
        self.assertTrue(any("not visible" in f for f in report.failures))
        record = Ledger(self.world.ledger, "TST").load_mirrors(item)[0]
        self.assertEqual(record["column"], "Ready", "the unseen move is not recorded")
        self.fake.lag = 0
        self.assertEqual(self.sync().failures, [])
        self.assertEqual(self.card(1)[0], "In progress")

    def test_a_card_already_on_the_board_without_a_status_gets_one_when_the_forge_refuses_a_second_add(self) -> None:
        """Prevents cards that sit on the board with no Status staying blank because the forge says the content exists."""
        self.world.create("--kind", "issue", "--title", "T", "--quote", QUOTE)
        self.sync()
        project = self.fake.project("1")
        next(iter(project["items"].values()))["values"] = {}
        self.fake.strict_add = True
        report = self.sync()
        self.assertEqual(report.failures, [])
        self.assertEqual(self.card(1), ("Ready", "P2"))

    def test_an_item_leaving_the_select_loses_its_card_but_keeps_its_open_issue(self) -> None:
        """Prevents pruning from closing or deleting the human-visible issue."""
        w = self.world = World(self.config("label:board"))
        self.addCleanup(w.close)
        item = w.create("--kind", "issue", "--title", "T", "--label", "board", "--quote", QUOTE)
        self.sync()
        self.assertEqual(len(self.fake.project("1")["items"]), 1)
        w.run("set", item, "--labels")
        report = self.sync()
        self.assertEqual(self.fake.project("1")["items"], {})
        self.assertTrue(self.fake.issues[1]["state"] == "open")
        self.assertEqual(report.pruned, 1)


if __name__ == "__main__":
    unittest.main()
