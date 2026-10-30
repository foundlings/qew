"""`qew progress --tree`: every root line has the same layout, and a claimed leaf shows how far its work has come.

Adversary: a childless epic printed as the bare word `plan` (it looks broken beside epics with bars), a claimed leaf that
shows only `claimed` whatever its evidence, and the stage leaking into the totals (a claimed leaf is still not done).
"""

import unittest

from qew import cmd_progress
from qew.cmd_progress import Node
from tests.ledger_harness import World


class ProgressStageWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        w = self.world
        self.root = w.create("--kind", "epic", "--title", "Root", "--draft")
        self.empty = w.create("--kind", "epic", "--title", "Nothing yet", "--parent", self.root, "--draft")
        self.leaf = w.create("--title", "Under way", "--parent", self.root, "--draft")
        w.approve(self.root, self.empty, self.leaf)
        w.run("start", self.leaf)

    def rows(self) -> dict[str, str]:
        result = self.world.run("progress", self.root, "--tree", env={"COLUMNS": "100", "NO_COLOR": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        return {item: row for row in result.stdout.splitlines() for item in (self.root, self.empty, self.leaf) if item in row}

    def test_a_childless_epic_has_the_bar_percent_and_count_of_every_other_epic(self) -> None:
        """Not the bare word `plan`: the row reads `[----------]   0%    0/0`."""
        self.assertRegex(self.rows()[self.empty], r"^\[-{10}\]\s+0%\s+0/0\s")

    def test_a_claimed_leaf_shows_zero_until_evidence_then_half(self) -> None:
        """A fresh claim is 0%; recorded evidence moves it to 50%; the word claimed stays and the epic totals do not move."""
        before = self.rows()
        self.assertRegex(before[self.leaf], r"^\[-{10}\]\s+0%\s+claimed\s")
        self.world.run("evidence", self.leaf, "--change", self.world.commit())
        after = self.rows()
        self.assertRegex(after[self.leaf], r"^\[#{5}-{5}\]\s+50%\s+claimed\s")
        self.assertEqual(after[self.root].split()[:3], before[self.root].split()[:3])

    def test_the_stage_of_a_claimed_leaf_follows_its_pull_request(self) -> None:
        """A draft pull request is 25%, a ready one 75%, a merged one 90%, evidence lifts any of them to at least 50%."""
        zero = dict.fromkeys(cmd_progress.COUNTED, 0)
        for stage, bars in ((25, "##"), (75, "#######"), (90, "#########")):
            node = Node("L", "Leaf", "Leaf", "task", "claimed", zero | {"claimed": 1}, [], stage)
            row = cmd_progress.tree_lines(node, None, False, False, 100, False)[0]
            self.assertTrue(row.startswith("[" + bars.ljust(10, "-") + f"]  {stage}%  claimed"), row)


if __name__ == "__main__":
    unittest.main()
