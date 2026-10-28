"""Walks of epics mirrored as forge milestones (opt-in per target).

Adversary: duplicate milestones, an unapproved plan pulled into a campaign, an
issue silently moved out of another campaign, and a milestone closed over open work.
"""

import json
import unittest
from dataclasses import replace

from qew import ledger_config
from qew.forge import MilestoneSpec
from qew.ledger_store import Ledger
from qew.sync import Target, sync_target
from tests.ledger_harness import QUOTE, World
from tests.memory_forge import MemoryForge

MIRRORS = '\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\nmilestones = true\n'
OFF = "\n[replication]\nauto_forge_sync = false\n"
MILESTONE_CALLS = ("create_milestone", "update_milestone", "set_milestone_open", "assign_milestone")


class MilestoneSyncWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(MIRRORS, replication=OFF)
        self.addCleanup(self.world.close)
        self.forge = MemoryForge("git.example")
        self.config = ledger_config.load(self.world.code)
        mirror = self.config.mirrors[0]
        self.target = Target(mirror, ledger_config.mirror_slug(mirror), self.forge)

    def sync(self):
        return sync_target(Ledger(self.world.ledger, "TST"), self.target, self.config)

    def writes(self) -> int:
        return sum(self.forge.count(name) for name in MILESTONE_CALLS)

    def epic_with_children(self, count: int = 2):
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Campaign", "--quote", QUOTE)
        kids = [w.create("--title", f"K{n}", "--parent", epic, "--quote", QUOTE) for n in range(count)]
        return epic, kids

    def test_sync_creates_the_milestone_once_and_assigns_children(self) -> None:
        """A second sync issues zero milestone writes, so no duplicate milestone appears."""
        self.epic_with_children()
        self.sync()
        self.assertEqual(len(self.forge.milestones), 1)
        self.assertEqual(set(self.forge.milestone_of.values()), {1})
        self.assertEqual(len(self.forge.milestone_of), 2)
        before = self.writes()
        self.sync()
        self.assertEqual(self.writes(), before)

    def test_repeat_run_reports_no_change(self) -> None:
        """The report says unchanged, so callers do not believe the forge was altered."""
        self.epic_with_children()
        first = self.sync()
        self.assertEqual((first.milestones_created, first.milestones_unchanged), (1, 0))
        again = self.sync()
        self.assertEqual((again.milestones_created, again.milestones_updated, again.milestones_unchanged), (0, 0, 1))

    def test_draft_epic_is_not_mirrored_as_a_milestone(self) -> None:
        """An unapproved plan stays out of every campaign."""
        self.world.create("--kind", "epic", "--title", "Draft plan", "--draft")
        self.sync()
        self.assertEqual(self.forge.milestones, {})

    def test_issue_in_another_milestone_is_refused_not_moved(self) -> None:
        """An issue already in a different milestone stays there; the refusal is reported and nothing is reassigned."""
        epic, (first, second) = self.epic_with_children()
        self.sync()
        other = self.forge.create_milestone(MilestoneSpec("Elsewhere", "x"))
        number = next(n for n, m in self.forge.milestone_of.items())
        self.forge.milestone_of[number] = other.number
        self.forge.issues[number] = replace(self.forge.issues[number], milestone=other.number)
        before = self.writes()
        report = self.sync()
        self.assertEqual(self.forge.milestone_of[number], other.number)
        self.assertTrue(any("not moved" in failure for failure in report.failures), report.failures)
        self.assertEqual(self.writes(), before)

    def test_milestone_closes_only_when_the_epic_is_completed_and_no_issue_is_open(self) -> None:
        """Completing the epic closes the milestone, verified by a re-read; an open attached issue keeps it open."""
        w = self.world
        epic, (kid,) = self.epic_with_children(1)
        stray = w.create("--title", "stray", "--quote", QUOTE)
        self.sync()
        number = self.forge_number(stray)
        self.forge.milestone_of[number] = 1
        self.forge.issues[number] = replace(self.forge.issues[number], milestone=1)
        w.run("start", epic)
        w.run("start", kid)
        w.run("evidence", kid, "--change", w.commit())
        w.run("complete", kid)
        w.run("complete", epic)
        self.sync()
        self.assertTrue(self.forge.milestones[1].open, "an open issue still sits in the milestone")
        self.forge.issues[number] = replace(self.forge.issues[number], open=False)
        self.sync()
        self.assertFalse(self.forge.milestones[1].open)

    def forge_number(self, item: str) -> int:
        return json.loads(next((self.world.home("mirrors", item)).glob("*.json")).read_text())["number"]

    def test_milestone_description_carries_the_approvers_quote_and_the_due_date(self) -> None:
        """The recorded approval quote and the due header reach the forge, so a campaign is never anonymous."""
        epic, _ = self.epic_with_children(1)
        self.assertEqual(self.world.run("set", epic, "--due", "2026-12-31").returncode, 0)
        self.sync()
        milestone = self.forge.milestones[1]
        self.assertIn(QUOTE, milestone.description)
        self.assertEqual(milestone.due, "2026-12-31")

    def test_a_forge_without_milestones_is_skipped_not_failed(self) -> None:
        """Unsupported is reported as skipped."""
        self.forge.supports_milestones = False
        self.epic_with_children(1)
        report = self.sync()
        self.assertIn("milestones", report.skipped)
        self.assertEqual(report.failures, [])


if __name__ == "__main__":
    unittest.main()
