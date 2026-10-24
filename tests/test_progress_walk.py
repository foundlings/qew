"""`qew progress ROOT`: how far along an epic is, from one ledger read.

Adversary: a percentage that counts epics or closed-as-not-planned work as work to do, a gated or blocked leaf
counted as done, a tree walked with one command per node, and a root or breakdown row that disagrees with its leaves.
"""

import json
import unittest

from tests.ledger_harness import World


class ProgressWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        w = self.world
        self.root = w.create("--kind", "epic", "--title", "Root", "--draft")
        self.done = self.make("Done", self.root)
        self.sub = w.create("--kind", "epic", "--title", "Sub", "--parent", self.root, "--draft")
        self.sub_done = self.make("Sub done", self.sub)
        self.sub_ready = self.make("Sub ready", self.sub)
        self.sub_closed = self.make("Sub closed", self.sub)
        self.claimed = self.make("Claimed", self.root)
        self.undecided = w.create("--title", "Undecided", "--parent", self.root, "--draft")
        self.blocked = w.create("--title", "Blocked", "--parent", self.root, "--needs", self.undecided, "--draft")
        w.approve(self.root, self.sub, self.blocked)
        for item in (self.done, self.sub_done):
            w.run("start", item)
            w.run("evidence", item, "--change", w.commit())
            self.assertEqual(w.run("complete", item).returncode, 0)
        w.run("close", self.sub_closed, "--reason", "not_planned")
        w.run("start", self.claimed)

    def make(self, title: str, parent: str) -> str:
        item = self.world.create("--title", title, "--parent", parent, "--draft")
        self.world.approve(item)
        return item

    def test_percent_is_completed_over_leaves_less_closed_and_the_json_is_the_nested_tree(self) -> None:
        """7 leaves, 1 closed, 2 completed: 2 of 6 is 33%; the sub-epic reads 1 of 2 from its own leaves; leaves are nodes too."""
        result = self.world.run("progress", self.root, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual((body["id"], body["leaves"], body["done"], body["total"], body["percent"]), (self.root, 7, 2, 6, 33))
        self.assertEqual(body["counts"], {"completed": 2, "closed": 1, "claimed": 1, "ready": 1, "blocked": 1, "plan": 0, "needs_decision": 1})
        kids = {node["id"]: node for node in body["children"]}
        self.assertEqual(set(kids), {self.done, self.sub, self.claimed, self.undecided, self.blocked})
        sub = kids[self.sub]
        self.assertEqual((sub["leaves"], sub["done"], sub["total"], sub["percent"]), (3, 1, 2, 50))
        self.assertEqual({node["id"]: node["work_state"] for node in sub["children"]},
                         {self.sub_done: "completed", self.sub_ready: "ready", self.sub_closed: "closed"})
        self.assertEqual((kids[self.done]["children"], kids[self.done]["percent"], kids[self.claimed]["percent"]), ([], 100, 0))

    def test_text_prints_the_whole_tree_indented_with_bars_and_leaf_states(self) -> None:
        """Every node gets a line indented by depth; epics carry bar, percent and done/total, leaves their state."""
        lines = self.world.run("progress", self.root).stdout.splitlines()
        self.assertTrue(lines[0].startswith(self.root) and "[###-------] 33%  (2/6)" in lines[0], lines[0])
        sub = next(line for line in lines if line.lstrip().startswith(self.sub))
        self.assertIn("[#####-----] 50%  (1/2)", sub)
        self.assertTrue(sub.startswith("  ") and not sub.startswith("   "))
        leaf = next(line for line in lines if self.sub_ready in line)
        self.assertTrue(leaf.startswith("    ") and leaf.rstrip().endswith("ready"), leaf)
        self.assertEqual(len(lines), 1 + 5 + 3)

    def test_epics_only_hides_leaves_and_depth_cuts_the_tree(self) -> None:
        """--epics-only keeps just the nodes that have children; --depth 1 drops everything below the direct children."""
        only = json.loads(self.world.run("progress", self.root, "--epics-only", "--json").stdout)
        self.assertEqual([node["id"] for node in only["children"]], [self.sub])
        self.assertEqual(only["children"][0]["children"], [])
        self.assertEqual(only["leaves"], 7)
        cut = json.loads(self.world.run("progress", self.root, "--depth", "1", "--json").stdout)
        sub = next(node for node in cut["children"] if node["id"] == self.sub)
        self.assertEqual((sub["children"], sub["leaves"]), ([], 3))
        lines = self.world.run("progress", self.root, "--depth", "1").stdout.splitlines()
        self.assertEqual(len(lines), 6)

    def test_several_roots_print_in_order(self) -> None:
        """Two roots give two answers, one JSON object per line, in the order asked."""
        lines = self.world.run("progress", self.sub, self.root, "--json").stdout.splitlines()
        self.assertEqual([json.loads(line)["id"] for line in lines], [self.sub, self.root])

    def test_a_root_without_children_is_its_own_single_leaf_and_nothing_to_do_has_no_percent(self) -> None:
        """A lone item reads 0% or 100%; a tree whose every leaf is closed has no percentage, not a division error."""
        body = json.loads(self.world.run("progress", self.sub_done, "--json").stdout)
        self.assertEqual((body["leaves"], body["percent"], body["children"]), (1, 100, []))
        lone = self.world.create("--kind", "epic", "--title", "Lone", "--draft")
        only = self.make("Only", lone)
        self.world.approve(lone)
        self.world.run("close", only, "--reason", "not_planned")
        self.assertIsNone(json.loads(self.world.run("progress", lone, "--json").stdout)["percent"])
        self.assertIn("n/a", self.world.run("progress", lone).stdout)

    def test_an_unknown_root_and_a_bad_depth_are_refused(self) -> None:
        """Nothing is guessed: exit 2 for a missing root or a depth below one."""
        self.assertEqual(self.world.run("progress", "nosuchitem").returncode, 2)
        self.assertEqual(self.world.run("progress", self.root, "--depth", "0").returncode, 2)

    def test_list_json_names_the_parent_of_each_child(self) -> None:
        """One `list --json` call is enough to rebuild the tree: every child row carries its parent."""
        rows = {row["id"]: row for row in map(json.loads, self.world.run("list", "--json").stdout.splitlines())}
        self.assertEqual(rows[self.sub_ready]["parent"], self.sub)
        self.assertEqual(rows[self.sub]["parent"], self.root)
        self.assertNotIn("parent", rows[self.root])
