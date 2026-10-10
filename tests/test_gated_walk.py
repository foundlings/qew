"""A `gated` item waits on something outside the repository (keys, credentials, hardware, a live account).

Each test is a scenario; its docstring names the failure it prevents. It is its own board column, never offered by `qew ready`,
and the reason is kept on the item. `needs:approval` keeps its own meaning: waiting for a human.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import sync_plan
from qew.forge import COLUMNS
from qew.ledger_store import Ledger
from tests.ledger_harness import World


class GatedWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def column(self, item: str) -> str:
        return sync_plan.column(Ledger(self.world.ledger, "TST").snapshot(), item)

    def test_a_gated_item_has_its_own_column_and_is_never_ready(self) -> None:
        """Prevents gated work being offered to agents or hidden among Backlog: its own Gated column, absent from ready."""
        item = self.world.create("--kind", "task", "--title", "Needs the live key")
        self.assertEqual(self.column(item), "Ready")
        gated = self.world.run("set", item, "--add-label", "gated", "--reason", "needs the live Stripe key")
        self.assertEqual(gated.returncode, 0, gated.stdout + gated.stderr)
        self.assertEqual(self.column(item), "Gated")
        self.assertNotIn(item, self.world.run("ready").stdout)
        self.assertLess(COLUMNS.index("Needs approval"), COLUMNS.index("Gated"))
        self.assertLess(COLUMNS.index("Gated"), COLUMNS.index("In progress"))
        self.assertEqual(self.world.run("set", item, "--remove-label", "gated").returncode, 0)
        self.assertEqual(self.column(item), "Ready")

    def test_the_reason_is_required_and_kept_on_the_item(self) -> None:
        """Prevents unexplained gates: adding the label needs --reason, which is recorded as a comment."""
        item = self.world.create("--kind", "task", "--title", "Needs hardware")
        bare = self.world.run("set", item, "--add-label", "gated")
        self.assertEqual(bare.returncode, 2, bare.stdout + bare.stderr)
        self.assertIn("--reason", bare.stderr)
        self.assertEqual(self.column(item), "Ready")
        self.assertEqual(self.world.run("set", item, "--add-label", "gated", "--reason", "needs the HSM on the build host").returncode, 0)
        self.assertIn("gated: needs the HSM on the build host", self.world.run("show", item).stdout)

    def test_gated_and_needs_approval_stay_different_things(self) -> None:
        """Prevents confusing a missing key with a missing human: needs:approval keeps its own column."""
        approval = self.world.create("--kind", "task", "--title", "Waiting for a human", "--label", "needs:approval")
        self.assertEqual(self.column(approval), "Needs approval")


if __name__ == "__main__":
    unittest.main()
