"""Needs changes are edge events: each added or removed need is its own file.

Adversary: a header list rewritten from a stale view that drops a concurrently added edge, and a header
that revives an edge a later command removed.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import World
from tests.lifecycle_support import approved


def needs_of(world: World, item: str) -> list:
    return json.loads(world.run("show", item, "--json").stdout).get("needs", [])


class EdgeWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_block_leaves_one_edge_event_that_names_the_list_before_and_after(self) -> None:
        """Prevents an edge that cannot be audited or merged on its own."""
        w = self.world
        dep, item = approved(w, title="Dep"), approved(w, title="Item")
        self.assertEqual(w.run("block", item, "--on", dep).returncode, 0)
        edge = [e for e in w.events(item) if e["type"] == "edge"]
        self.assertEqual([(e["op"], e["need"], e["old"], e["new"]) for e in edge], [("add", dep, "", dep)])
        self.assertEqual(w.run("check").returncode, 0)

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

    def test_two_branches_adding_different_edges_keep_both(self) -> None:
        """Prevents the lost edge of replace-the-list editing."""
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
