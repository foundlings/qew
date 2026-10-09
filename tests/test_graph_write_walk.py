"""Walks of edge validation on `create` and `set`: nothing invalid ever reaches storage.

Adversary: a cycle, self edge, dangling or duplicate reference, or impossible nesting written to disk
(where only `qew check` would find it later), and finished work edited by accident.
"""

import unittest

from tests.ledger_harness import World
from tests.plan_support import snapshot


class GraphWriteWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def item(self, title: str, *extra: str, kind: str = "issue") -> str:
        return self.world.create("--kind", kind, "--title", title, *extra)

    def refused(self, *arguments: str, fragment: str = "") -> None:
        before = snapshot(self.world)
        result = self.world.run(*arguments)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(fragment, result.stderr)
        self.assertEqual(snapshot(self.world), before, "a refused write must change nothing, events included")

    def test_a_direct_and_a_transitive_need_cycle_are_refused_before_any_write(self) -> None:
        """A cycle on disk makes every later readiness answer wrong until someone hand-repairs files."""
        a = self.item("A")
        b = self.item("B", "--needs", a)
        c = self.item("C", "--needs", b)
        self.refused("set", a, "--needs", b, fragment="cycle")
        self.refused("set", a, "--needs", c, fragment="cycle")
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_self_unknown_and_duplicate_edges_are_refused(self) -> None:
        """Malformed edges must be named at the write, not discovered by a later `check`."""
        a = self.item("A")
        b = self.item("B")
        self.refused("set", a, "--needs", a, fragment="itself")
        self.refused("set", a, "--parent", a, fragment="its own parent")
        self.refused("set", a, "--needs", "nothere", fragment="no item matches")
        self.refused("set", a, "--needs", b, b, fragment="duplicate")

    def test_nesting_that_check_would_fail_is_refused_on_create_and_set(self) -> None:
        """epic > issue > task: any other parent leaves a ledger that `check` rejects."""
        epic = self.item("E", kind="epic")
        issue = self.item("I", "--parent", epic)
        task = self.item("T", "--parent", issue, kind="task")
        self.refused("create", "--kind", "task", "--title", "x", "--parent", task, fragment="cannot sit under")
        self.refused("create", "--kind", "issue", "--title", "x", "--parent", issue, fragment="cannot sit under")
        self.refused("create", "--kind", "epic", "--title", "x", "--parent", epic, fragment="cannot sit under")
        self.refused("set", epic, "--parent", issue, fragment="cannot sit under")
        self.refused("set", issue, "--parent", task, fragment="cannot sit under")
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_a_parent_cycle_is_refused(self) -> None:
        """Parent loops would make a plan its own child."""
        outer = self.item("Outer", kind="epic")
        inner = self.item("Inner", "--parent", outer)
        self.refused("set", outer, "--parent", inner, fragment="cannot sit under")

    def test_finished_work_is_edited_only_with_closed_and_the_audit_event_is_written(self) -> None:
        """Stale edits of finished work by accident are refused; the deliberate form works and is audited."""
        w = self.world
        a = self.item("A")
        w.approve(a)
        w.run("close", a, "--reason", "not_planned")
        self.refused("set", a, "--title", "Renamed", fragment="closed; pass --closed")
        result = w.run("set", a, "--title", "Renamed", "--closed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("title: A -> Renamed", result.stdout)
        self.assertEqual(w.events(a)[-1]["type"], "field")
        self.assertEqual(w.events(a)[-1]["new"], "Renamed")


if __name__ == "__main__":
    unittest.main()
