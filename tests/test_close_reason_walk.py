"""The forge's close reason survives a sync: completed stays completed, duplicates stay duplicates, damage is repaired.

Each test is a scenario; its docstring names the failure it prevents. Driven through the `qew` commands against the
GitHub fake, which keeps GitHub's `state_reason` and its issue event timeline.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.ledger_store import Ledger
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class CloseReasonWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)

    def sync(self, *extra: str):
        result = self.world.run("sync", *extra, env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def closed_on_forge(self, title: str, reason):
        issue = self.fake.new_issue({"title": title, "body": "written by a human"})
        self.fake.set_state(issue, "closed", reason)
        return issue

    def state_of(self, title: str) -> str:
        for state in ("completed", "closed", "approved", "draft", "claimed"):
            if title in self.world.run("list", "--state", state).stdout:
                return state
        return "missing"

    def test_issues_closed_on_the_forge_keep_their_reason_across_a_sync(self) -> None:
        """Prevents every pulled closed issue becoming not_planned and the push rewriting GitHub to match."""
        done = self.closed_on_forge("Done by a human", "completed")
        bare = self.closed_on_forge("Closed with no reason shown", None)
        skipped = self.closed_on_forge("Skipped by a human", "not_planned")
        twin = self.closed_on_forge("Twin by a human", "duplicate")
        self.sync()
        self.sync()
        checked = self.world.run("check")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertEqual(self.state_of("Done by a human"), "completed")
        self.assertEqual(self.state_of("Closed with no reason shown"), "completed")
        self.assertEqual(self.state_of("Skipped by a human"), "closed")
        self.assertEqual(self.state_of("Twin by a human"), "closed")
        self.assertEqual([i["state_reason"] for i in (done, bare, skipped, twin)],
                         ["completed", "completed", "not_planned", "duplicate"])

    def test_a_ledger_duplicate_or_superseded_close_reaches_github_as_duplicate_never_not_planned(self) -> None:
        """Prevents `qew close --reason duplicate|superseded` showing as 'not planned' on GitHub."""
        first = self.world.create("--kind", "issue", "--title", "First twin", "--quote", QUOTE)
        second = self.world.create("--kind", "issue", "--title", "Second twin", "--quote", QUOTE)
        third = self.world.create("--kind", "issue", "--title", "Third", "--quote", QUOTE)
        self.sync()
        self.assertEqual(self.world.run("close", first, "--reason", "duplicate", "--of", second).returncode, 0)
        self.assertEqual(self.world.run("close", third, "--reason", "superseded", "--by", second).returncode, 0)
        self.sync()
        by_title = {i["title"]: i["state_reason"] for i in self.fake.issues.values()}
        self.assertEqual([by_title["First twin"], by_title["Third"]], ["duplicate", "duplicate"])

    def test_repair_closes_restores_items_a_pull_wrongly_closed_as_not_planned(self) -> None:
        """Prevents damage already done (completed on GitHub, then not_planned in the ledger and on GitHub) staying."""
        item = self.world.create("--kind", "issue", "--title", "Wrongly skipped", "--quote", QUOTE)
        self.sync()
        ledger = Ledger(self.world.ledger, "TST")
        ledger.append_event(item, "closed", reason="not_planned", provenance="forge-import")  # what the old pull wrote
        issue = self.fake.issues[1]
        issue["timeline"] = [{"event": "closed", "state_reason": "completed"}, {"event": "reopened", "state_reason": "reopened"},
                             {"event": "closed", "state_reason": "not_planned"}]
        self.fake.set_state(issue, "closed", "not_planned")
        dry = self.world.run("repair-closes", "--dry-run", env=ENV)
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertIn(item, dry.stdout)
        self.assertEqual(self.state_of("Wrongly skipped"), "closed")
        done = self.world.run("repair-closes", env=ENV)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.state_of("Wrongly skipped"), "completed")
        self.sync()
        self.assertEqual(self.fake.issues[1]["state_reason"], "completed")
        self.assertIn("nothing to repair", self.world.run("repair-closes", env=ENV).stdout)

    def test_repair_closes_leaves_a_human_not_planned_close_alone(self) -> None:
        """Prevents the repair overriding a close that really was not planned."""
        item = self.world.create("--kind", "issue", "--title", "Really skipped", "--quote", QUOTE)
        self.sync()
        Ledger(self.world.ledger, "TST").append_event(item, "closed", reason="not_planned", provenance="forge-import")
        issue = self.fake.issues[1]
        self.fake.set_state(issue, "closed", "not_planned")
        result = self.world.run("repair-closes", env=ENV)
        self.assertIn("nothing to repair", result.stdout + result.stderr)
        self.assertEqual(self.state_of("Really skipped"), "closed")

    def test_accepting_a_forge_side_completed_close_records_completed_not_not_planned(self) -> None:
        """Prevents `qew accept` turning a human's 'closed as completed' into a not_planned close."""
        item = self.world.create("--kind", "issue", "--title", "Finished on the forge", "--quote", QUOTE)
        self.sync()
        self.fake.set_state(self.fake.issues[1], "closed", "completed")
        self.sync()
        accepted = self.world.run("accept", item)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertEqual(self.state_of("Finished on the forge"), "completed")


if __name__ == "__main__":
    unittest.main()
