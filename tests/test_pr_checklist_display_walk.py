"""From schema 9 the Progress checklist is a display of an agent's own work steps: it gates nothing and covers no item.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class DisplayWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="9")
        self.addCleanup(self.w.close)
        self.main = self.w.item("Main work")
        self.other = self.w.item("Other work")
        self.steps = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
        self.addCleanup(os.unlink, self.steps.name)
        self.steps.write(f"## Progress\n\n- [ ] write the parser\n- [x] read the spec\n- [ ] name an item <!-- pr-progress:{self.other} -->\n")
        self.steps.close()

    def open(self) -> None:
        run = self.w.open(self.main, "--closes", self.main, "--body-file", self.steps.name)
        self.assertEqual(run.code, 0, run)

    def test_unticked_boxes_do_not_refuse_ready_or_the_merge_check(self) -> None:
        """Prevents an agent's own unfinished work steps blocking a pull request whose declaration is complete."""
        self.open()
        ready = self.w.qew("pr", "ready", "1")
        self.assertEqual(ready.code, 0, ready)
        checked = self.w.qew("pr", "merge", "1", "--check")
        self.assertNotIn("unticked", checked.err)

    def test_allow_unticked_is_accepted_and_ignored_with_a_note(self) -> None:
        """Prevents a mid-flight agent's `--allow-unticked` command failing now that nothing needs the override."""
        self.open()
        ready = self.w.qew("pr", "ready", "1", "--allow-unticked", "--reason", "steps are mine")
        self.assertEqual(ready.code, 0, ready)
        self.assertIn("allow-unticked", ready.err)
        self.assertNotIn("Unticked override", self.w.forge.pulls[1].body)

    def test_a_box_that_names_an_item_does_not_cover_it_or_land_it(self) -> None:
        """Prevents a ticked item box completing an item the declaration never said the pull request closes."""
        self.open()
        self.assertEqual(self.w.qew("pr", "check", "1", self.other).code, 0)
        sha = self.w.commit("work")
        self.w.forge.edit_pull(1, head_sha=sha, state="merged")
        self.assertEqual(self.w.qew("evidence", self.main, "--change", sha).code, 0)
        self.assertEqual(self.w.qew("evidence", self.other, "--change", sha).code, 0)
        run = self.w.qew("land", "1")
        self.assertEqual(run.code, 0, run)
        shown = self.w.qew("show", self.other).out
        self.assertIn("state: claimed", shown)
        self.assertIn("state: completed", self.w.qew("show", self.main).out)

    def test_add_takes_free_text_and_check_ticks_it(self) -> None:
        """Prevents `--add` insisting the step be a ledger item, which a work step usually is not."""
        self.open()
        added = self.w.qew("pr", "check", "1", "--add", "run the walk on a clean clone")
        self.assertEqual(added.code, 0, added)
        self.assertIn("run the walk on a clean clone", self.w.forge.pulls[1].body)
        listed = self.w.qew("pr", "check", "1", "--list")
        self.assertIn("run the walk on a clean clone", listed.out)
        self.assertEqual(self.w.qew("pr", "check", "1", "--all").code, 0)
        self.assertNotIn("- [ ]", self.w.forge.pulls[1].body)

    def test_sync_items_points_at_pr_link(self) -> None:
        """Prevents `pr sync --items` re-deriving covered items from boxes when the declaration is the only source."""
        self.open()
        run = self.w.qew("pr", "sync", "1", "--items")
        self.assertEqual(run.code, 2, run)
        self.assertIn("pr link", run.err)


if __name__ == "__main__":
    unittest.main()
