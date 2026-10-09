"""Repository mode keeps the issue's labels quiet: the user's own, P0..P4, author:ACTOR and `epic`/`task` for those kinds. Nothing else.

Adversary: Status or state pushed into labels (the board column carries them), `kind:issue` on every plain issue,
noise labels from older syncs staying on issues or flooding divergences, overlay mode losing its labels, and a
card on the board without its Status or Priority.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World
from tests.test_label_style_walk import forget_last_sync

QUIET = "\n[replication]\nauto_forge_sync = false\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
NOISE = {"status:Ready", "kind:issue", "kind:task", "kind:epic", "qew:closed", "qew:draft", "qew:approved", "qew:claimed"}


class RepositoryLabelsWalk(unittest.TestCase):
    mode = ""

    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mode = f'mode = "{self.mode}"\n' if self.mode else ""
        mirror = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n{mode}'
        self.w = World(mirror, replication=QUIET)
        self.addCleanup(self.w.close)

    def run_ok(self, *args: str) -> str:
        result = self.w.run(*args, env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def labels(self, number: int) -> set:
        return set(self.fake.issues[number]["labels"])


class RepositoryModeWalk(RepositoryLabelsWalk):
    def test_a_plain_issue_carries_no_kind_label_and_an_epic_or_task_carries_its_plain_name(self) -> None:
        """Prevents status, state and kind:issue labels on every issue, and the retired `kind:` spelling (the board column carries state)."""
        self.w.create("--title", "Plain", "--label", "area:x", "--priority", "2", "--quote", QUOTE)
        epic = self.w.create("--kind", "epic", "--title", "Epic", "--priority", "1", "--quote", QUOTE)
        self.w.create("--kind", "task", "--title", "Child", "--parent", epic, "--priority", "3", "--quote", QUOTE)
        self.run_ok("sync")
        self.assertEqual(self.labels(1), {"area:x", "P2", "author:tester"})
        self.assertEqual(self.labels(2), {"P1", "author:tester", "epic"})
        self.assertEqual(self.labels(3), {"P3", "author:tester", "task"})
        self.assertFalse(NOISE & {name for n in (1, 2, 3) for name in self.labels(n)})

    def test_noise_labels_from_older_syncs_are_removed_without_divergence_and_prune_deletes_them(self) -> None:
        """Prevents status:/kind:/qew: noise surviving the upgrade, a divergence flood, or definitions lingering."""
        old = self.w.create("--title", "Old", "--label", "area:x", "--priority", "2", "--quote", QUOTE)
        self.run_ok("sync")
        forget_last_sync(self.w, old)
        self.fake.issues[1]["labels"] = sorted({*self.labels(1), *NOISE, "priority:2"})
        for name in (*NOISE, "priority:2"):
            self.fake.label_store[name] = {"color": "ededed", "description": "Managed by qew"}
        self.run_ok("sync")
        self.assertEqual(json.loads(self.run_ok("status", "--json"))["divergences"], [])
        self.assertEqual(self.labels(1), {"area:x", "P2", "author:tester"})
        self.run_ok("sync", "--prune-labels")
        self.assertFalse((NOISE | {"priority:2"}) & set(self.fake.label_store))
        self.assertIn("area:x", self.fake.label_store)


class OverlayModeWalk(RepositoryLabelsWalk):
    mode = "overlay"

    def test_overlay_mode_keeps_its_managed_labels_with_the_new_priority_spelling(self) -> None:
        """Prevents overlay mirrors losing the labels that tell qew's issues from humans' ones."""
        self.w.create("--title", "Plain", "--priority", "2", "--quote", QUOTE)
        self.run_ok("sync")
        self.assertEqual(self.labels(1), {"qew:managed", "qew:approved", "status:Ready", "P2", "author:tester"})


class BoardStatusWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirror = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.w = World(mirror, replication=QUIET)
        self.addCleanup(self.w.close)

    def card(self, number: int):
        node = self.fake.issues[number]["node_id"]
        return self.fake.item_value("1", node, "Status"), self.fake.item_value("1", node, "Priority")

    def test_every_card_gets_status_and_priority_from_the_ledger(self) -> None:
        """Prevents a card without Status or Priority, an unblocked approved item that is not Ready, or a draft that is."""
        draft = self.w.create("--draft", "--title", "Draft", "--priority", "4")
        first = self.w.create("--title", "Ready one", "--priority", "0", "--quote", QUOTE)
        second = self.w.create("--title", "Blocked", "--needs", first, "--priority", "1", "--quote", QUOTE)
        third = self.w.create("--title", "Ready two", "--priority", "3", "--quote", QUOTE)
        self.assertEqual(self.w.run("project", "ensure", env=ENV).returncode, 0)
        self.assertEqual(self.w.run("sync", env=ENV).returncode, 0)
        numbers = {item: n for n, item in enumerate((draft, first, second, third), start=1)}
        self.assertEqual(self.card(numbers[draft]), ("Draft", "P4"))
        self.assertEqual(self.card(numbers[first]), ("Ready", "P0"))
        self.assertEqual(self.card(numbers[second]), ("Backlog", "P1"))
        self.assertEqual(self.card(numbers[third]), ("Ready", "P3"))
        self.assertEqual(self.w.run("start", first).returncode, 0)
        self.assertEqual(self.w.run("sync", env=ENV).returncode, 0)
        self.assertEqual(self.card(numbers[first]), ("In progress", "P0"))


if __name__ == "__main__":
    unittest.main()
