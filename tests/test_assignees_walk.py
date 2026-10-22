"""Several assignees: assigned and unassigned events, shown, pushed and imported; the claim still has exactly one owner.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class AssigneesWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\nassignees = {{ tester = "octo" }}\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        self.item = self.world.create("--kind", "issue", "--title", "Shared work", "--quote", QUOTE)

    def sync(self) -> None:
        result = self.world.run("sync", "--all", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def forge_assignees(self) -> list:
        return sorted(next(iter(self.fake.issues.values())).get("assignees", []))

    def kinds(self, kind: str) -> list:
        return [e for e in self.world.events(self.item) if e["type"] == kind]

    def test_assign_and_unassign_are_events_shown_and_pushed_to_the_forge(self) -> None:
        """Prevents extra assignees living only on GitHub: they are events, listed by show, pushed, and removable."""
        assigned = self.world.run("item", "assign", self.item, "dana", "erik")
        self.assertEqual(assigned.returncode, 0, assigned.stdout + assigned.stderr)
        self.assertIn("assignees: dana erik", self.world.run("show", self.item).stdout)
        self.sync()
        self.assertEqual(self.forge_assignees(), ["dana", "erik"])
        again = self.world.run("item", "assign", self.item, "dana")
        self.assertIn("unchanged", again.stdout)
        self.assertEqual(len(self.kinds("assigned")), 2)
        self.assertEqual(self.world.run("item", "unassign", self.item, "dana").returncode, 0)
        self.sync()
        self.assertEqual(self.forge_assignees(), ["erik"])
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_the_claim_owner_stays_the_first_assignee_beside_the_extras(self) -> None:
        """Prevents extras displacing the claim: the owner is still assigned, the extras follow."""
        self.world.run("item", "assign", self.item, "dana")
        self.assertEqual(self.world.run("start", self.item).returncode, 0)
        self.sync()
        self.assertEqual(self.forge_assignees(), ["dana", "octo"])
        issue = next(iter(self.fake.issues.values()))
        self.assertEqual(issue["assignee"], "octo")

    def test_assignees_changed_on_the_forge_come_back_as_events_without_a_divergence(self) -> None:
        """Prevents the two sides drifting: a human adding or removing an assignee on GitHub becomes an event."""
        self.world.run("item", "assign", self.item, "dana")
        self.sync()
        issue = next(iter(self.fake.issues.values()))
        issue["assignees"] = ["dana", "frida"]
        self.sync()
        self.assertIn("frida", self.world.run("show", self.item).stdout)
        issue["assignees"] = ["frida"]
        issue["assignee"] = "frida"
        self.sync()
        shown = self.world.run("show", self.item).stdout
        self.assertNotIn("dana", shown.split("assignees:")[1].splitlines()[0])
        provenance = [e.get("provenance") for e in self.world.events(self.item) if e["type"] in ("assigned", "unassigned") and e.get("provenance")]
        self.assertEqual(provenance, ["forge", "forge"])
        self.assertIn("divergences=0", self.world.run("status").stdout)
        self.assertEqual(self.world.run("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
