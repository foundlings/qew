"""Walks of very long dependency chains: nothing recurses per node.

Adversary: a recursive cycle search or ordering helper overflowing the stack on a real-sized plan, or a
cycle of the same size going unreported.
"""

import time
import unittest

from tests.ledger_harness import World
from tests.plan_support import jsonl, listing

LENGTH = 3000


class DeepChainWalk(unittest.TestCase):
    """One 3000-link world shared by both walks (the second mutates it, so they run in name order)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = World()
        entries = [{"ref": "n0", "title": "Link 0"}]
        entries += [{"ref": f"n{i}", "title": f"Link {i}", "needs": [f"@n{i - 1}"]} for i in range(1, LENGTH)]
        started = time.monotonic()
        result = cls.world.run("create", "--from-file", "-", "--quote", "Alexander: 'file the long chain for the walk'", stdin=jsonl(*entries))
        assert result.returncode == 0, result.stderr[-300:]
        cls.elapsed = time.monotonic() - started

    @classmethod
    def tearDownClass(cls) -> None:
        cls.world.close()

    def test_a_long_chain_is_accepted_and_every_read_command_answers_without_a_traceback(self) -> None:
        """A recursive cycle search would overflow the stack on a plan this long."""
        self.assertLess(self.elapsed, 60, "a chain of this size must apply in bounded time")
        checked = self.world.run("check")  # the depth invariant reports a chain this long; it must not crash
        self.assertIn(checked.returncode, (0, 1))
        self.assertIn("depth", checked.stdout)
        self.assertNotIn("Traceback", checked.stderr)
        for command in (("ready",), ("blocked",), ("graph",)):
            result = self.world.run(*command)
            self.assertEqual(result.returncode, 0, f"{command}: {result.stderr[-300:]}")
            self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(len(self.world.run("ready", "--json").stdout) > 0, True)

    def test_the_same_chain_closed_into_a_ring_reports_a_cycle_without_a_traceback(self) -> None:
        """The opposite failure: a cycle of this size must still be found and named, not crash the check."""
        rows = listing(self.world)
        first = next(row for row in rows if row["title"] == "Link 0")
        last = next(row for row in rows if row["title"] == f"Link {LENGTH - 1}")
        path = self.world.home("items", f"{first['id']}") / "item.md"  # a hand merge closes the ring; `set` would refuse
        path.write_text(path.read_text().replace("needs: \n", f"needs: {last['id']}\n", 1))
        result = self.world.run("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("cycle", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
