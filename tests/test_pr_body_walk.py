"""Walks of `qew pr body N --body-file FILE`: a lane replaces a stale pull request body through qew.

Adversary: a replacement that drops a qew Progress box (losing what the pull request promises to finish), pre-ticks a box
(skipping the merge gate), closes an issue on merge, or lands on a pull request the caller does not own or that is finished.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld

OLD = "BLOCKED on the parser.\n\n## Progress\n- [x] first <!-- pr-progress:a -->\n- [ ] second <!-- pr-progress:b -->\n"


class BodyWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item()
        self.assertEqual(self.w.open(self.item).code, 0)
        self.w.forge.edit_pull(1, body=OLD)
        self.file = self.w.config.root / "body.md"

    def run_body(self, text: str, *extra: str, actor: str | None = None):
        self.file.write_text(text)
        return self.w.qew("pr", "body", "1", "--body-file", str(self.file), *extra, actor=actor)

    def test_a_stale_body_is_replaced_and_the_progress_boxes_and_ticks_survive(self) -> None:
        """Prevents a lane being stuck with a stale BLOCKED body it cannot change through qew."""
        new = "Unblocked: the parser landed.\n\n## Progress\n- [ ] first <!-- pr-progress:a -->\n- [ ] second <!-- pr-progress:b -->\n"
        run = self.run_body(new)
        self.assertEqual(run.code, 0, run)
        body = self.w.forge.pulls[1].body
        self.assertIn("Unblocked: the parser landed.", body)
        self.assertNotIn("BLOCKED", body)
        self.assertIn("- [x] first <!-- pr-progress:a -->", body, "a tick already earned is carried over")
        self.assertIn("- [ ] second <!-- pr-progress:b -->", body)

    def test_a_replacement_that_drops_a_progress_marker_is_refused_and_nothing_changes(self) -> None:
        """Prevents a body edit silently removing an item the pull request promised to finish."""
        run = self.run_body("Unblocked.\n\n## Progress\n- [ ] first <!-- pr-progress:a -->\n")
        self.assertEqual(run.code, 2, run)
        self.assertIn("pr-progress:b", run.err)
        self.assertEqual(self.w.forge.pulls[1].body, OLD)

    def test_a_replacement_that_ticks_a_box_is_refused(self) -> None:
        """Prevents ticking through the body to pass the merge gate: only `qew pr check` ticks."""
        run = self.run_body("Done.\n\n- [x] first <!-- pr-progress:a -->\n- [x] second <!-- pr-progress:b -->\n")
        self.assertEqual(run.code, 2, run)
        self.assertIn("ticks", run.err)
        self.assertEqual(self.w.forge.pulls[1].body, OLD)

    def test_a_closing_reference_and_an_empty_file_are_refused(self) -> None:
        """Prevents a pull request closing an issue on merge, and a blank body from a typo."""
        keep = "\n- [ ] first <!-- pr-progress:a -->\n- [ ] second <!-- pr-progress:b -->\n"
        closing = self.run_body("Fixes #12" + keep)
        self.assertEqual(closing.code, 2, closing)
        self.assertIn("closing reference", closing.err)
        empty = self.run_body("")
        self.assertEqual(empty.code, 2, empty)
        self.assertIn("empty", empty.err)
        self.assertEqual(self.w.forge.pulls[1].body, OLD)

    def test_json_unchanged_and_finished_pull_requests(self) -> None:
        """Prevents redundant writes, a json shape drift, and touching a merged or closed pull request."""
        same = self.run_body(OLD, "--json")
        self.assertEqual(json.loads(same.out), {"number": 1, "changed": False, "schema_version": 1})
        for state in ("merged", "closed"):
            with self.subTest(state=state):
                self.w.forge.edit_pull(1, state=state)
                run = self.run_body("x\n" + OLD)
                self.assertEqual(run.code, 2, run)
                self.assertIn(state, run.err)

    def test_someone_who_neither_opened_it_nor_holds_the_claims_is_refused(self) -> None:
        """Prevents one agent rewriting another's pull request: the ownership rule of `pr ready` applies."""
        run = self.run_body("Hijacked.\n" + OLD, actor="stranger")
        self.assertEqual(run.code, 3, run)
        self.assertIn("neither opened it", run.err)
        self.assertEqual(self.w.forge.pulls[1].body, OLD)

    def test_on_behalf_of_borrows_the_owners_standing_for_body_and_check_only_when_the_owner_could(self) -> None:
        """Prevents a coordinator being forced to take over items just to fix a body or tick a box, and the flag
        becoming a bypass: naming someone who could not act is still refused."""
        new = "Unblocked.\n\n- [ ] first <!-- pr-progress:a -->\n- [ ] second <!-- pr-progress:b -->\n"
        done = self.run_body(new, "--on-behalf-of", "tester", actor="coordinator")
        self.assertEqual(done.code, 0, done)
        self.assertIn("Unblocked.", self.w.forge.pulls[1].body)
        ticked = self.w.qew("pr", "check", "1", "b", "--on-behalf-of", "tester", actor="coordinator")
        self.assertEqual(ticked.code, 0, ticked)
        self.assertIn("- [x] second", self.w.forge.pulls[1].body)
        before = self.w.forge.pulls[1].body
        refused = self.w.qew("pr", "check", "1", "--all", "--on-behalf-of", "stranger", actor="coordinator")
        self.assertEqual(refused.code, 3, refused)
        refused_body = self.run_body("Hijack.\n" + new, "--on-behalf-of", "stranger", actor="coordinator")
        self.assertEqual(refused_body.code, 3, refused_body)
        self.assertEqual(self.w.forge.pulls[1].body, before)


if __name__ == "__main__":
    unittest.main()
