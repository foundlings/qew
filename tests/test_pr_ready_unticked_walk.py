"""Walks of `qew pr ready` over a Progress checklist with unticked boxes.

Adversary: a pull request marked ready while a box is still unticked, so the refusal surfaces only later at the integrator's
`pr merge --check`; and an override that is silent, reasonless, or leaves no trace for the integrator.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class ReadyUntickedWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item("Work")
        self.assertEqual(self.w.open(self.item).code, 0)
        self.shared = self.w.item("HCM consumer migration")
        self.assertEqual(self.w.qew("pr", "check", "1", "--add", self.shared).code, 0)  # one unticked box

    def body(self) -> str:
        return self.w.forge.pulls[1].body

    def nothing_changed(self) -> None:
        self.assertTrue(self.w.forge.pulls[1].draft)
        self.assertEqual(self.w.forge.count("ready_pr"), 0)

    def test_an_unticked_box_refuses_ready_naming_the_item_the_reason_and_the_fixes(self) -> None:
        """Prevents ready on unfinished work: exit 2, nothing changed, each box by id and title, and the three fix commands."""
        run = self.w.qew("pr", "ready", "1")
        self.assertEqual(run.code, 2, run)
        self.assertIn("pr #1 has unticked checklist items; a merge of this PR is refused until every box is ticked or dropped", run.err)
        self.assertIn(f"{self.shared} HCM consumer migration", run.err)
        self.assertIn(f"qew pr check 1 {self.shared}", run.err)
        self.assertIn(f"qew pr check 1 --drop {self.shared} --reason TEXT", run.err)
        self.assertIn("qew pr close 1", run.err)
        self.nothing_changed()

    def test_a_ticked_box_lets_ready_through(self) -> None:
        """Prevents the guard refusing finished work."""
        self.assertEqual(self.w.qew("pr", "check", "1", self.shared).code, 0)
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)
        self.assertFalse(self.w.forge.pulls[1].draft)

    def test_a_dropped_box_lets_ready_through(self) -> None:
        """Prevents the drop path being a dead end: a box the PR does not finish is dropped with a reason, then ready works."""
        self.assertEqual(self.w.qew("pr", "check", "1", "--drop", self.shared, "--reason", "not here").code, 0)
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)

    def test_the_override_needs_a_reason_and_the_reason_needs_the_override(self) -> None:
        """Prevents a silent bypass: --allow-unticked without --reason, and --reason alone, are refused with nothing changed."""
        for argv in (("--allow-unticked",), ("--allow-unticked", "--reason", "  "), ("--reason", "because")):
            with self.subTest(argv):
                run = self.w.qew("pr", "ready", "1", *argv)
                self.assertEqual(run.code, 2, run)
                self.nothing_changed()

    def test_the_override_with_a_reason_proceeds_and_leaves_a_trace_for_the_integrator(self) -> None:
        """Prevents an invisible bypass: the body carries who, when and why, and the unticked item carries a comment."""
        run = self.w.qew("pr", "ready", "1", "--allow-unticked", "--reason", "shared parent finishes in a later PR")
        self.assertEqual(run.code, 0, run)
        self.assertFalse(self.w.forge.pulls[1].draft)
        line = next(line for line in self.body().splitlines() if "unticked" in line and "shared parent finishes" in line)
        self.assertIn(self.w.actor, line)
        self.assertIn(self.shared, line)
        events = self.w.events(self.shared)
        self.assertTrue(any("shared parent finishes in a later PR" in str(e.get("text", "")) for e in events))

    def test_an_already_ready_pull_request_is_left_alone(self) -> None:
        """Prevents the guard turning a no-op into a failure: ready on a ready PR stays 'unchanged'."""
        self.assertEqual(self.w.qew("pr", "ready", "1", "--allow-unticked", "--reason", "later").code, 0)
        run = self.w.qew("pr", "ready", "1")
        self.assertEqual(run.code, 0, run)
        self.assertIn("already ready", run.out)

    def test_merge_check_prints_the_same_explanation(self) -> None:
        """Prevents the backstop speaking differently: --check names each box, the reason and the fixes."""
        self.assertEqual(self.w.qew("pr", "ready", "1", "--allow-unticked", "--reason", "later").code, 0)
        run = self.w.qew("pr", "merge", "1", "--check")
        self.assertNotEqual(run.code, 0)
        self.assertIn("has unticked checklist items; a merge of this PR is refused until every box is ticked or dropped", run.err)
        self.assertIn(f"{self.shared} HCM consumer migration", run.err)
        self.assertIn(f"qew pr check 1 --drop {self.shared} --reason TEXT", run.err)


if __name__ == "__main__":
    unittest.main()
