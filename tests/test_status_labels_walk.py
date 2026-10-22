"""Derived status labels: `ready`, `in-progress` and `gated` on a mirrored issue come from the same status as its board column,
come off when the work finishes, are never written into the ledger, and a human's edit of them is recomputed, not a divergence.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import sync_plan
from qew.ledger_store import Ledger
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\ndivergences = \"report\"\n"
DERIVED = {"ready", "in-progress", "gated"}


class StatusLabelsWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\nselect = "kind:epic,issue"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)

    def sync(self) -> None:
        result = self.world.run("sync", "--all", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def derived(self, title: str) -> set[str]:
        issue = next(i for i in self.fake.issues.values() if i["title"] == title)
        return set(issue["labels"]) & DERIVED

    def make(self, kind: str, title: str, *arguments: str) -> str:
        return self.world.create("--kind", kind, "--title", title, *arguments, "--quote", QUOTE)

    def column(self, item: str) -> str:
        return sync_plan.column(Ledger(self.world.ledger, "TST").snapshot(), item)

    def test_labels_follow_the_column_through_the_whole_life_of_an_issue(self) -> None:
        """Prevents an issue list that cannot tell ready from in progress from gated; the labels leave when the work finishes."""
        issue = self.make("issue", "Work")
        task = self.make("task", "Ledger-only", "--parent", issue)
        self.sync()
        self.assertEqual(self.derived("Work"), {"ready"})
        self.assertEqual(self.world.run("start", task).returncode, 0)
        self.sync()
        self.assertEqual(self.derived("Work"), {"in-progress"})
        self.assertEqual(self.world.run("set", task, "--add-label", "gated", "--reason", "waits on a key").returncode, 0)
        self.assertEqual(self.world.run("release", task, "--reason", "waiting on the key").returncode, 0)
        self.sync()
        self.assertEqual(self.derived("Work"), {"gated"})
        self.assertEqual(self.world.run("set", task, "--remove-label", "gated", "--reason", "key arrived").returncode, 0)
        self.assertEqual(self.world.run("start", task).returncode, 0)
        self.assertEqual(self.world.run("complete", task, "--no-commit", "no code needed").returncode, 0)
        self.assertEqual(self.world.run("close", issue, "--reason", "not_planned").returncode, 0)
        self.sync()
        self.assertEqual(self.derived("Work"), set())
        self.assertEqual(self.world.run("reopen", issue, "--reason", "needed again").returncode, 0)
        self.sync()
        self.assertEqual(self.derived("Work"), {"ready"})

    def test_a_gated_label_always_sits_in_the_gated_column_whatever_the_kind(self) -> None:
        """Prevents a gated epic in Backlog: the label and the column come from one status, and a finished item drops the label."""
        epic = self.make("epic", "Epic")
        issue = self.make("issue", "Gated issue")
        for item in (epic, issue):
            self.assertEqual(self.world.run("set", item, "--add-label", "gated", "--reason", "waits on a key").returncode, 0)
        self.assertEqual((self.column(epic), self.column(issue)), ("Gated", "Gated"))
        self.sync()
        self.assertEqual((self.derived("Epic"), self.derived("Gated issue")), ({"gated"}, {"gated"}))
        self.assertEqual(self.world.run("close", issue, "--reason", "not_planned").returncode, 0)
        self.sync()
        self.assertEqual(self.derived("Gated issue"), set())
        self.assertIn("gated", self.world.run("show", issue, "--json").stdout, "the ledger keeps its own gate")

    def test_the_labels_are_display_only_and_a_humans_edit_is_recomputed_not_a_divergence(self) -> None:
        """Prevents derived labels leaking into the ledger or arguing with it: a hand-removed or hand-added one is put right."""
        issue = self.make("issue", "Work")
        task = self.make("task", "Ledger-only", "--parent", issue)
        self.world.run("start", task)
        self.sync()
        number = next(n for n, i in self.fake.issues.items() if i["title"] == "Work")
        self.fake.issues[number]["labels"].remove("in-progress")
        self.fake.issues[number]["labels"].append("gated")
        self.fake.touch(self.fake.issues[number])
        self.sync()
        self.assertEqual(self.derived("Work"), {"in-progress"})
        data = json.loads(self.world.run("show", issue, "--json").stdout)
        self.assertEqual(data["labels"], [])
        status = json.loads(self.world.run("status", "--json").stdout)
        self.assertEqual(status["divergences"], [])

    def test_the_derived_names_are_reserved_for_qew_except_gated(self) -> None:
        """Prevents a ledger label fighting the derived one: ready and in-progress are refused, gated stays the ledger's own."""
        item = self.make("issue", "Work")
        for label in ("ready", "in-progress"):
            self.assertEqual(self.world.run("set", item, "--add-label", label).returncode, 2, label)
        self.assertEqual(self.world.run("set", item, "--add-label", "gated", "--reason", "waits").returncode, 0)


if __name__ == "__main__":
    unittest.main()
