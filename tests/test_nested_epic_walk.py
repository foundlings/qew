"""An epic may sit under an epic: the parent relation, the sub-issue mirror, the derived claim and the completion cascade all follow.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World
from tests.plan_support import snapshot

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\ndivergences = \"report\"\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class NestedEpicWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def epic(self, title: str, *extra: str) -> str:
        return self.world.create("--kind", "epic", "--title", title, *extra, "--quote", QUOTE)

    def state(self, item: str) -> str:
        return json.loads(self.world.run("show", item, "--json").stdout)["state"]

    def test_an_epic_can_be_created_and_moved_under_an_epic(self) -> None:
        """Prevents a closeout epic being unable to contain the phase epics doing its work."""
        outer = self.epic("Closeout")
        inner = self.epic("Phase", "--parent", outer)
        loose = self.epic("Loose")
        self.assertEqual(self.world.run("set", loose, "--parent", outer).returncode, 0)
        shown = json.loads(self.world.run("show", outer, "--json").stdout)
        self.assertEqual(sorted(c["id"] for c in shown["children"]), sorted([inner, loose]))
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_a_parent_cycle_through_epics_is_refused_and_writes_nothing(self) -> None:
        """Prevents A under B under A: nesting no longer rules loops out, so the write must."""
        outer = self.epic("Outer")
        inner = self.epic("Inner", "--parent", outer)
        deepest = self.epic("Deepest", "--parent", inner)
        for moved, under in ((outer, inner), (outer, deepest), (outer, outer)):
            before = snapshot(self.world)
            result = self.world.run("set", moved, "--parent", under)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertRegex(result.stderr, "cycle|own parent")
            self.assertEqual(snapshot(self.world), before)

    def test_other_nesting_is_still_refused(self) -> None:
        """Prevents allowing an issue or task to hold an epic."""
        epic = self.epic("E")
        issue = self.world.create("--kind", "issue", "--title", "I", "--parent", epic, "--quote", QUOTE)
        self.assertEqual(self.world.run("create", "--kind", "epic", "--title", "x", "--parent", issue, "--quote", QUOTE).returncode, 2)

    def test_the_last_child_finishing_completes_every_epic_above_it(self) -> None:
        """Prevents a nested epic completing but leaving its parent open, so completable never rolls up."""
        outer = self.epic("Outer")
        inner = self.epic("Inner", "--parent", outer)
        work = self.world.create("--kind", "issue", "--title", "Work", "--parent", inner, "--quote", QUOTE)
        self.assertEqual(self.world.run("start", work).returncode, 0)
        self.world.run("evidence", work, "--change", self.world.commit())
        done = self.world.run("complete", work)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.state(inner), self.state(outer)), ("completed", "completed"))

    def test_a_claimed_grandchild_shows_every_epic_above_it_in_progress(self) -> None:
        """Prevents a closeout epic reading idle while the work under its phase epics is being done."""
        outer = self.epic("Outer")
        inner = self.epic("Inner", "--parent", outer)
        work = self.world.create("--kind", "issue", "--title", "Work", "--parent", inner, "--quote", QUOTE)
        self.world.run("start", work)
        self.assertEqual((self.state(inner), self.state(outer)), ("claimed", "claimed"))
        shown = json.loads(self.world.run("show", outer, "--json").stdout)
        self.assertEqual(shown["holders"], ["tester"])


class NestedEpicMirrorWalk(unittest.TestCase):
    def test_a_child_epic_is_a_sub_issue_of_its_parent_epic(self) -> None:
        """Prevents containment showing only as blocked-by: the child epic is GitHub's sub-issue of the parent epic."""
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'
        world = World(mirrors, replication=OFF)
        self.addCleanup(world.close)
        outer = world.create("--kind", "epic", "--title", "Closeout", "--quote", QUOTE)
        world.create("--kind", "epic", "--title", "Phase", "--parent", outer, "--quote", QUOTE)
        self.assertEqual(world.run("sync", "--all", env=ENV).returncode, 0)
        number = {i["title"]: n for n, i in fake.issues.items()}
        self.assertEqual(fake.issues[number["Phase"]].get("parent"), number["Closeout"])


if __name__ == "__main__":
    unittest.main()
