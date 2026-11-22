"""Walks of the lazy snapshot a write verb takes: it reads the items it touches, not the whole ledger.

Adversary: evidence and start parsing all 28,000 items under the write lock (4 s idle, 20 s under load) to look at one item and
its ancestors; and the cure being wrong: a verb that reads a stale or partial picture, hides damage from `qew check`, or cannot be
switched back to the full snapshot.
"""

import unittest

from tests.ledger_harness import World

TRACE = {"QEW_TRACE": "1"}


def approved(world: World, title: str = "Work") -> str:
    item = world.create("--kind", "issue", "--title", title, "--draft")
    world.approve(item)
    return item


class LazySnapshotWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(schema="9")
        self.addCleanup(self.world.close)
        self.item = approved(self.world)
        self.assertEqual(self.world.run("start", self.item).returncode, 0)
        self.sha = self.world.commit("the work")

    def evidence(self, env: dict[str, str] | None = None):
        return self.world.run("evidence", self.item, "--change", self.sha, env={**TRACE, **(env or {})})

    def test_evidence_takes_the_lazy_snapshot(self) -> None:
        """Prevents evidence parsing the whole ledger to read one item: its trace names a lazy snapshot and no full one."""
        run = self.evidence()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("ledger snapshot (lazy)", run.stderr)
        self.assertNotIn("ledger snapshot (full)", run.stderr)
        self.assertEqual([e["type"] for e in self.world.events(self.item)][-1], "evidence")

    def test_no_lazy_restores_the_full_snapshot_with_the_same_result(self) -> None:
        """Prevents the lazy snapshot having no way back: QEW_NO_LAZY=1 takes the full one and records the same event."""
        run = self.evidence({"QEW_NO_LAZY": "1"})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("ledger snapshot (full)", run.stderr)
        self.assertNotIn("ledger snapshot (lazy)", run.stderr)
        self.assertEqual([e["type"] for e in self.world.events(self.item)][-1], "evidence")

    def test_a_refusal_reads_the_same_under_the_lazy_snapshot(self) -> None:
        """Prevents a lazy read changing a refusal: evidence on a draft is still exit 2 with the same words."""
        draft = self.world.create("--kind", "issue", "--title", "Draft", "--draft")
        lazy = self.world.run("evidence", draft, "--change", self.sha)
        full = self.world.run("evidence", draft, "--change", self.sha, env={"QEW_NO_LAZY": "1"})
        self.assertEqual((lazy.returncode, lazy.stderr), (full.returncode, full.stderr))
        self.assertEqual(lazy.returncode, 2)

    def test_damage_elsewhere_stops_check_but_not_a_write_that_never_reads_it(self) -> None:
        """Prevents one broken item stopping every agent's evidence, while `qew check` still names it."""
        other = approved(self.world, "Other")
        next((self.world.ledger / "items").rglob(f"{other}/item.md")).write_text("not an item file", encoding="utf-8")
        run = self.evidence()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotEqual(self.world.run("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
