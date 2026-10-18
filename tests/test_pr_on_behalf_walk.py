"""`--on-behalf-of ACTOR` on `pr ready`, `pr draft`, `pr close` and `pr sync` (title and --items): the integrator acts for the elf
that opened the pull request.

Adversary: a coordinator forced to take over items just to move another agent's pull request, and the flag turned into a bypass
by naming someone who could not act.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class OnBehalfWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item("Work")
        self.assertEqual(self.w.open(self.item).code, 0)  # opened by "tester"

    def act(self, *argv: str, owner: str) -> object:
        return self.w.qew("pr", *argv, "--on-behalf-of", owner, actor="coordinator")

    def test_ready_then_draft_then_sync_then_close_for_the_opener(self) -> None:
        """Prevents the integrator needing `--take-over` for each routine move on an elf's pull request."""
        self.assertEqual(self.act("ready", "1", owner="tester").code, 0)
        self.assertFalse(self.w.forge.pulls[1].draft)
        self.assertEqual(self.act("draft", "1", owner="tester").code, 0)
        self.assertTrue(self.w.forge.pulls[1].draft)
        self.w.forge.edit_pull(1, title="stale")
        self.assertEqual(self.act("sync", "1", owner="tester").code, 0)
        self.assertNotEqual(self.w.forge.pulls[1].title, "stale")
        self.assertEqual(self.act("sync", "1", "--items", owner="tester").code, 0)
        self.assertEqual(self.act("close", "1", owner="tester").code, 0)
        self.assertEqual(self.w.forge.pulls[1].state, "closed")

    def test_naming_someone_who_could_not_act_is_refused_and_nothing_changes(self) -> None:
        """Prevents the flag being a bypass: the rule is judged for the named actor, so a stranger named is exit 3."""
        for verb in (("ready", "1"), ("draft", "1"), ("sync", "1"), ("sync", "1", "--items"), ("close", "1")):
            with self.subTest(verb=verb):
                run = self.act(*verb, owner="stranger")
                self.assertEqual(run.code, 3, run)
                self.assertIn("neither opened it", run.err)
        pull = self.w.forge.pulls[1]
        self.assertEqual((pull.state, pull.draft), ("open", True))


if __name__ == "__main__":
    unittest.main()
