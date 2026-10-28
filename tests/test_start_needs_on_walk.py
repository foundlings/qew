"""`qew start X --needs-on BRANCH`: work stacked on a collection branch starts before its needs reach main.

Adversary: a pipeline that deadlocks because needs only complete when the collection branch merges; a flag that waves through a
need with no evidence, evidence on another branch, or an unknown branch; a staging nobody can audit; and a completion that
no longer waits for main.
"""

import json
import unittest

from tests.ledger_harness import World, git


class StartNeedsOnWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        w = self.world
        self.need = w.create("--title", "Spec group")
        self.work = w.create("--title", "Implement the spec", "--needs", self.need)
        git(w.code, "checkout", "-q", "-b", "adu-fat")
        self.on_branch = w.commit("spec lands on the collection branch")
        git(w.code, "checkout", "-q", "main")
        self.stray = w.commit("main only")
        self.assertEqual(w.run("start", self.need).returncode, 0)

    def start(self, *arguments: str) -> object:
        return self.world.run("start", self.work, *arguments)

    def test_without_the_flag_the_unmet_need_still_refuses_the_start(self) -> None:
        """Prevents the flag becoming the default: a plain start keeps waiting for completed needs."""
        refused = self.start()
        self.assertEqual(refused.returncode, 2)  # type: ignore[attr-defined]
        self.assertIn("needs", refused.stderr)  # type: ignore[attr-defined]

    def test_a_need_evidenced_on_the_branch_lets_the_work_start_and_the_staging_is_recorded(self) -> None:
        """Prevents the pipeline deadlock, and an unauditable bypass: the claim event names the branch and the staged needs."""
        self.assertEqual(self.world.run("evidence", self.need, "--change", self.on_branch).returncode, 0)
        started = self.start("--needs-on", "adu-fat")
        self.assertEqual(started.returncode, 0, started.stderr)  # type: ignore[attr-defined]
        claim = next(event for event in self.world.events(self.work) if event["type"] == "claimed")
        self.assertEqual((claim["needs_on"], claim["staged_needs"]), ("adu-fat", self.need))
        shown = self.world.run("show", self.work).stdout
        self.assertIn("adu-fat", shown)
        self.assertIn(self.need, shown)
        self.assertEqual(json.loads(self.world.run("show", self.work, "--json").stdout)["id"], self.work)

    def test_completing_the_staged_work_still_waits_for_the_need_to_complete(self) -> None:
        """Prevents the staging reaching the finish: complete keeps requiring completed needs (and so main)."""
        self.world.run("evidence", self.need, "--change", self.on_branch)
        self.start("--needs-on", "adu-fat")
        self.world.run("evidence", self.work, "--change", self.on_branch)
        refused = self.world.run("complete", self.work)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn(f"needs {self.need}", refused.stderr)

    def test_a_staged_claim_is_not_reported_as_a_stuck_blocked_item(self) -> None:
        """Prevents `qew blocked` and the work state calling deliberate staged work stuck."""
        self.world.run("evidence", self.need, "--change", self.on_branch)
        self.start("--needs-on", "adu-fat")
        listed = self.world.run("list", "--work-state", "claimed").stdout
        self.assertIn(self.work, listed)
        self.assertNotIn(self.work, self.world.run("blocked").stdout)

    def test_a_need_without_evidence_or_evidenced_elsewhere_refuses(self) -> None:
        """Prevents waving through a need that is not on the branch: no evidence, and evidence only on main, both name the need."""
        none = self.start("--needs-on", "adu-fat")
        self.assertEqual(none.returncode, 2)  # type: ignore[attr-defined]
        self.assertIn(self.need, none.stderr)  # type: ignore[attr-defined]
        self.world.run("evidence", self.need, "--change", self.stray)
        elsewhere = self.start("--needs-on", "adu-fat")
        self.assertEqual(elsewhere.returncode, 2)  # type: ignore[attr-defined]
        self.assertIn(self.need, elsewhere.stderr)  # type: ignore[attr-defined]
        self.assertFalse([event for event in self.world.events(self.work) if event["type"] == "claimed"])

    def test_an_unknown_branch_and_misuse_are_refused(self) -> None:
        """Prevents a typo starting work: an absent branch is an environment failure; a take-over or a bad name is a usage error."""
        self.world.run("evidence", self.need, "--change", self.on_branch)
        self.assertEqual(self.start("--needs-on", "no-such-branch").returncode, 3)  # type: ignore[attr-defined]
        self.assertEqual(self.start("--needs-on", "--bad").returncode, 2)  # type: ignore[attr-defined]
        self.assertEqual(self.start("--needs-on", "adu-fat", "--take-over", "--reason", "x").returncode, 2)  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
