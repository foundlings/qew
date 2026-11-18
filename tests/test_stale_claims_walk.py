"""`qew check --stale-claims`: claimed items nobody is visibly working, from the ledger and the commit log alone.

The claim identity is shared, so `claimed` says nothing about a live worker; this lists the ones with no open pull request, no
commit citing them and no ledger activity (their own or a descendant's) within a window.
Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import subprocess
import sys
import unittest
from datetime import timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import instants
from tests.pr_harness import PrWorld


class StaleClaimsWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="9")
        self.addCleanup(self.w.close)

    def later(self, hours: float, *argv: str):
        moment = instants.now() + timedelta(hours=hours)
        with mock.patch("qew.stale_claim_report.now", lambda: moment):
            return self.w.qew("check", "--stale-claims", *argv)

    def rows(self, hours: float, *argv: str) -> dict[str, dict]:
        run = self.later(hours, "--json", *argv)
        self.assertEqual(run.code, 0, run)
        return {row["item"]: row for row in json.loads(run.out)["stale_claims"]}

    def test_a_claimed_item_with_no_pull_request_commit_or_activity_is_listed_with_its_age(self) -> None:
        """Prevents 151 claimed items looking alive when nobody has touched them."""
        item = self.w.item("Idle work")
        shown = self.rows(48)
        self.assertEqual(list(shown), [item])
        self.assertEqual(shown[item]["owner"], "tester")
        self.assertGreaterEqual(shown[item]["hours"], 47)
        text = self.later(48).out
        self.assertIn(item, text)
        self.assertIn("Idle work", text)

    def test_recent_activity_inside_the_window_keeps_it_out(self) -> None:
        """Prevents listing work that was touched an hour ago."""
        self.w.item("Fresh work")
        self.assertEqual(self.rows(1), {})

    def test_an_open_pull_request_keeps_the_item_out(self) -> None:
        """Prevents calling work idle while its pull request is open."""
        item = self.w.item("Has a PR")
        self.assertEqual(self.w.open(item, "--closes", item).code, 0)
        self.assertEqual(self.rows(48), {})

    def test_a_recent_commit_citing_the_item_keeps_it_out(self) -> None:
        """Prevents calling work idle while commits cite it (the ledger may lag the code)."""
        item = self.w.item("Committed to")
        moment = instants.now() + timedelta(hours=5)
        env = {**os.environ, "GIT_COMMITTER_DATE": moment.isoformat(), "GIT_AUTHOR_DATE": moment.isoformat()}
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", f"work\n\nRefs: {item}"], cwd=self.w.world.code, env=env, check=True)
        self.assertEqual(self.rows(10, "--hours", "8"), {})

    def test_activity_on_a_child_keeps_its_epic_out(self) -> None:
        """Prevents an epic whose children are being worked being listed as abandoned."""
        epic = self.w.item("Epic", kind="epic")
        self.w.item("Child", parent=epic)
        self.assertEqual(self.rows(1), {})
        shown = self.rows(48)
        self.assertIn(epic, shown)

    def test_unclaimed_and_finished_items_are_not_listed(self) -> None:
        """Prevents the list naming work that is not claimed."""
        self.w.item("Waiting", claim=False)
        self.assertEqual(self.rows(48), {})


if __name__ == "__main__":
    unittest.main()
