"""`qew block` and `qew unblock`: dependency edges that are each their own event.

Adversary: an edge that is not visible or not audited, a cycle written by one command, finished work edited by
accident, and a lost edge when two branches add different edges to the same item.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import World
from tests.lifecycle_support import approved, event_types, started, tree


def needs_of(world: World, item: str) -> list:
    return json.loads(world.run("show", item, "--json").stdout).get("needs", [])


class BlockWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_block_adds_a_visible_audited_edge_and_unblock_removes_it(self) -> None:
        """Prevents an edge that is not shown by blocked and graph, not audited, or not removable."""
        w = self.world
        dep = approved(w, title="Dependency")
        item = approved(w, title="Item")
        result = w.run("block", item, "--on", dep)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{item} -> {dep}", w.run("graph").stdout)
        self.assertIn(item, w.run("blocked").stdout)
        audit = w.events(item)[-1]
        self.assertEqual((audit["type"], audit["op"], audit["need"]), ("edge", "add", dep))
        self.assertEqual(w.run("check").returncode, 0)
        self.assertNotIn(item, w.run("ready").stdout)
        gone = w.run("unblock", item, "--from", dep)
        self.assertEqual(gone.returncode, 0, gone.stderr)
        self.assertEqual(needs_of(w, item), [])
        self.assertIn(item, w.run("ready").stdout)

    def test_repeating_either_command_is_unchanged_and_writes_nothing(self) -> None:
        """Prevents errors and noisy events on a retry."""
        w = self.world
        dep, item = approved(w, title="Dep"), approved(w, title="Item")
        w.run("block", item, "--on", dep)
        before = tree(w)
        again = w.run("block", item, "--on", dep)
        self.assertEqual(again.returncode, 0)
        self.assertIn("unchanged", again.stdout)
        w.run("unblock", item, "--from", dep)
        mid = tree(w)
        repeated = w.run("unblock", item, "--from", dep)
        self.assertIn("unchanged", repeated.stdout)
        self.assertEqual(tree(w), mid)
        self.assertNotEqual(before, mid)

    def test_a_cycle_a_self_edge_and_an_unknown_item_are_refused_before_any_write(self) -> None:
        """Prevents a deadlocked graph from one command."""
        w = self.world
        a = approved(w, title="A")
        b = approved(w, "--needs", a, title="B")
        before = tree(w)
        cycle = w.run("block", a, "--on", b)
        self.assertEqual(cycle.returncode, 2, cycle.stderr)
        self.assertIn("cycle", cycle.stderr)
        for arguments in (["block", a, "--on", a], ["block", a, "--on", "nosuchitem"], ["unblock", a, "--from", "nosuchitem"]):
            self.assertEqual(w.run(*arguments).returncode, 2, arguments)
        self.assertEqual(tree(w), before)

    def test_finished_work_is_edited_only_with_the_closed_flag(self) -> None:
        """Prevents editing finished work by accident."""
        w = self.world
        dep = approved(w, title="Dep")
        done = approved(w, "--needs", dep, title="Done")
        w.run("close", done, "--reason", "not_planned")
        before = tree(w)
        refused = w.run("unblock", done, "--from", dep)
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertIn("--closed", refused.stderr)
        self.assertEqual(tree(w), before)
        self.assertEqual(w.run("unblock", done, "--from", dep, "--closed").returncode, 0)
        self.assertEqual(w.run("block", done, "--on", dep).returncode, 2)

    def test_set_needs_after_a_block_replaces_the_effective_list(self) -> None:
        """Prevents a stale header list reviving an edge that block or set already changed."""
        w = self.world
        a, b = approved(w, title="A"), approved(w, title="B")
        item = approved(w, title="Item")
        w.run("block", item, "--on", a)
        self.assertEqual(w.run("set", item, "--needs", b).returncode, 0)
        self.assertEqual(needs_of(w, item), [b])
        self.assertEqual(w.run("set", item, "--needs").returncode, 0)
        self.assertEqual(needs_of(w, item), [])
        self.assertEqual(w.run("check").returncode, 0)


class ConcurrentBlockWalk(unittest.TestCase):
    def test_two_branches_adding_different_edges_keep_both(self) -> None:
        """Prevents the lost edge of replace-the-list editing: each edge is its own event file."""
        every_time = "\n[replication]\nfetch_interval_s = 0\n"
        a = World(ledger_vcs="git", remote=True, replication=every_time)
        self.addCleanup(a.close)
        b = a.clone("b", replication=every_time)
        first, second = approved(a, title="First"), approved(a, title="Second")
        item = approved(a, title="Item")
        b.run("list")
        offline = {"QEW_OFFLINE": "1"}
        self.assertEqual(a.run("block", item, "--on", first, env=offline).returncode, 0)
        self.assertEqual(b.run("block", item, "--on", second, env=offline).returncode, 0)
        for agent in (a, b, a):
            self.assertEqual(agent.run("ready").returncode, 0)
        self.assertEqual(sorted(needs_of(a, item)), sorted([first, second]))
        self.assertEqual(a.run("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
