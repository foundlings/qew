"""Ready ranking must explain priority and actual dependency impact rather than silently guessing urgency."""

import json
import re
import unittest

from tests.ledger_harness import World
from tests.spec_loader import load, spec


class ReadyImpactWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = World()
        self.addCleanup(self.w.close)
        load(self.w, spec("E", kind="epic"), spec("U", priority=0), spec("R", parent="E"),
             spec("S", parent="E", priority=1), spec("O"), spec("B", needs=["R"]),
             spec("C", needs=["R"]), spec("D", needs=["B", "C"]), spec("T", needs=["S"]),
             spec("G", needs=["R", "O"]), spec("H", needs=["R"], labels=["gated"]))
        path = self.w.home("items", "E") / "item.md"
        path.write_text(path.read_text().replace("\n---", "\naliases: ADU\n---", 1))

    def ready(self, *args: str) -> dict:
        run = self.w.run("ready", *args, "--json")
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_priority_and_dependents_are_explicit_different_orders_with_visible_facts(self) -> None:
        """Prevents high-priority leaves silently hiding useful chains, or impact ordering discarding priority."""
        default = self.ready()
        priority = self.ready("--by-priority")
        self.assertEqual(default, priority)
        self.assertEqual(priority["order"], "priority")
        self.assertEqual([r["id"] for r in priority["items"]], ["U", "S", "R", "O"])
        impact = self.ready("--by-dependents")
        self.assertEqual(impact["order"], "dependents")
        self.assertEqual([r["id"] for r in impact["items"]], ["R", "S", "O", "U"])
        row = impact["items"][0]
        self.assertEqual((row["priority"], row["rank"]), (2, 1))
        self.assertEqual((row["direct_dependents"], row["transitive_dependents"], row["unblocks_now"], row["chain_depth"]), (4, 5, 2, 2))
        text = self.w.run("ready", "--by-dependents", "--limit", "1")
        self.assertEqual(text.returncode, 0, text.stderr)
        for fact in ("p2", "rank=1", "direct=4", "trans=5", "unlocks=2", "depth=2"):
            self.assertIn(fact, text.stdout)
        self.assertTrue(text.stdout.startswith("R "))

    def test_root_limits_candidates_but_preserves_global_cross_cutting_impact(self) -> None:
        """Prevents a phase queue mixing in the whole ledger or dropping real dependents outside its ownership tree."""
        body = self.ready("ADU", "--by-dependents", "--limit", "1")
        self.assertEqual((body["roots"], body["total"], body["truncated"]), (["E"], 2, True))
        self.assertEqual(body["items"][0]["id"], "R")
        self.assertEqual(body["items"][0]["transitive_dependents"], 5)
        self.assertEqual([r["id"] for r in self.ready("ADU", "--by-priority")["items"]], ["S", "R"])

    def test_shared_and_gated_waiters_are_not_all_immediate_unlocks(self) -> None:
        """Prevents promising an AND-gate or a human-gated item will be ready when only one prerequisite finishes."""
        rows = {r["id"]: r for r in self.ready("--by-dependents")["items"]}
        self.assertEqual(rows["O"]["unblocks_now"], 0)
        self.assertEqual(rows["O"]["transitive_dependents"], 1)
        self.w.run("close", "H", "--reason", "not_planned")
        row = next(r for r in self.ready("--by-dependents")["items"] if r["id"] == "R")
        self.assertEqual((row["direct_dependents"], row["transitive_dependents"]), (3, 4))

    def test_unknown_roots_mutually_exclusive_order_and_cycles_refuse_loudly(self) -> None:
        """Prevents an ambiguous ranking choice, silently ignored root, or cyclic reachability count."""
        for args in (("UNKNOWN",), ("--by-dependents", "--by-priority")):
            run = self.w.run("ready", *args, "--json")
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertEqual(run.stdout, "")
        path = self.w.home("items", "B") / "item.md"
        path.write_text(re.sub(r"(?m)^needs:.*$", "needs: R D", path.read_text()))
        run = self.w.run("ready", "ADU", "--by-dependents", "--json")
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertIn("cycle", run.stderr)
        self.assertEqual(run.stdout, "")

    def test_agent_context_explains_default_priority_and_the_impact_command(self) -> None:
        """Prevents agents inferring an unsupported ranking policy from item titles or a vague goal."""
        run = self.w.run("prime")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("priority first", run.stdout)
        self.assertIn("--by-dependents", run.stdout)
