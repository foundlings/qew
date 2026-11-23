"""Closing an item that open work still needs.

Adversary (seen live after an accounting batch closed items as superseded): 17 open items kept needing closed ones, so they could
never become ready, and `qew check` failed on all of them, which refused every push of every agent. A close now names the open
dependants and stops, unless the caller moves them (`--repoint`) or says they stay (`--leave-dependants`); a need on a closed item
is a note in `qew check`, not a failure.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import World
from tests.spec_loader import load, spec


class CloseDependantsWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        load(self.world, spec("OLD"), spec("NEW"), spec("P", needs=["OLD"]), spec("Q", needs=["OLD", "NEW"]))

    def state(self, item_id: str) -> str:
        return self.world.run("show", item_id, "--json").stdout

    def test_a_close_names_the_open_dependants_and_writes_nothing(self) -> None:
        """Prevents open work being stranded behind a closed need: the close is refused, naming P and Q and the ways out."""
        result = self.world.run("close", "OLD", "--reason", "superseded", "--by", "NEW")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        for name in ("P", "Q", "--repoint", "--leave-dependants"):
            self.assertIn(name, result.stderr)
        self.assertIn('"state":"approved"', self.state("OLD"))

    def test_repoint_moves_every_dependant_to_the_replacement_in_the_same_step(self) -> None:
        """Prevents the stranded need: P now needs NEW, Q (which already needed NEW) loses the duplicate edge, and check is clean."""
        result = self.world.run("close", "OLD", "--reason", "superseded", "--by", "NEW", "--repoint")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NEW", self.world.run("show", "P").stdout)
        self.assertNotIn("OLD", self.world.run("show", "Q", "--json").stdout.split('"needs"')[1].split("]")[0])
        checked = self.world.run("check")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertNotIn("needs closed item", checked.stdout)

    def test_repoint_needs_a_replacement(self) -> None:
        """Prevents repointing at nothing: a not_planned close has no item to move the needs to."""
        result = self.world.run("close", "OLD", "--reason", "not_planned", "--repoint")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--repoint", result.stderr)

    def test_repoint_that_would_make_a_cycle_is_refused(self) -> None:
        """Prevents a cycle: NEW needs P, so P cannot be moved onto NEW."""
        load(self.world, spec("LOOP", needs=["P"]))
        self.assertEqual(self.world.run("set", "NEW", "--needs", "LOOP").returncode, 0)
        result = self.world.run("close", "OLD", "--reason", "superseded", "--by", "NEW", "--repoint")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cycle", result.stderr)
        self.assertIn('"state":"approved"', self.state("OLD"))

    def test_leave_dependants_closes_and_check_only_notes_it(self) -> None:
        """Prevents a push blocker: the need on the closed item is named by check with exit 0, so every agent can still push."""
        result = self.world.run("close", "OLD", "--reason", "not_planned", "--leave-dependants")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        checked = self.world.run("check")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn("items/P/item.md: needs closed item OLD", checked.stdout)

    def test_finished_dependants_do_not_stop_a_close(self) -> None:
        """Prevents a false refusal: a completed or closed dependant is history."""
        self.assertEqual(self.world.run("close", "P", "--reason", "not_planned").returncode, 0)
        self.assertEqual(self.world.run("close", "Q", "--reason", "not_planned").returncode, 0)
        self.assertEqual(self.world.run("close", "OLD", "--reason", "not_planned").returncode, 0)


if __name__ == "__main__":
    unittest.main()
