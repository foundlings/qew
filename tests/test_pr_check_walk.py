"""Walks of `qew pr check`: tick progress items in a pull request body, in place and verified.

Adversary: a rebuilt body that drops text, a teammate's concurrent edit overwritten, a write the forge
accepted but did not keep, and a hidden open box (inside a code fence, or never addressable) that lets
`qew pr merge` pass.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld

MARKED = "## Progress\n\n- [ ] RED test <!-- pr-progress:red -->\n- [ ] implementation <!-- pr-progress:green -->\n\nTrailer: kept as is.\n"


class CheckWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item()
        self.assertEqual(self.w.open(self.item).code, 0)

    def body(self, text: str) -> None:
        self.w.forge.edit_pull(1, body=text)

    def forge_body(self) -> str:
        return self.w.forge.pulls[1].body

    def test_ticking_one_marked_item_changes_only_that_box_and_is_read_back(self) -> None:
        """Prevents a rebuilt body that drops trailers or text."""
        self.body(MARKED)
        run = self.w.qew("pr", "check", "1", "red")
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(self.forge_body(), MARKED.replace("- [ ] RED test", "- [x] RED test"))
        self.assertIn("red", run.out)

    def test_position_addressing_and_the_at_form_when_a_marker_already_uses_the_number(self) -> None:
        """Prevents ambiguous or unreadable bodies."""
        self.body("- [ ] one <!-- pr-progress:2 -->\n- [ ] two\n")
        listing = self.w.qew("pr", "check", "1", "--list").out.splitlines()
        self.assertEqual(listing, ["[ ] 2 one", "[ ] @2 two"])
        self.assertEqual(self.w.qew("pr", "check", "1", "@2").code, 0)
        self.assertEqual(self.forge_body(), "- [ ] one <!-- pr-progress:2 -->\n- [x] two\n")
        self.body("- [ ] plain a\n- [ ] plain b\n")
        self.assertEqual(self.w.qew("pr", "check", "1", "2").code, 0)
        self.assertEqual(self.forge_body(), "- [ ] plain a\n- [x] plain b\n")

    def test_all_ticks_every_open_item_and_an_already_ticked_item_writes_nothing(self) -> None:
        """Prevents spurious edits."""
        self.body("- [x] done <!-- pr-progress:a -->\n- [ ] open <!-- pr-progress:b -->\n")
        self.assertEqual(self.w.qew("pr", "check", "1", "--all").code, 0)
        self.assertEqual(self.forge_body(), "- [x] done <!-- pr-progress:a -->\n- [x] open <!-- pr-progress:b -->\n")
        writes = len(self.w.forge.body_writes)
        again = self.w.qew("pr", "check", "1", "--all")
        named = self.w.qew("pr", "check", "1", "a")
        self.assertEqual((again.code, named.code), (0, 0))
        self.assertIn("nothing to tick", again.out)
        self.assertEqual(len(self.w.forge.body_writes), writes)

    def test_an_unknown_or_repeated_item_is_refused_with_zero_writes(self) -> None:
        """Prevents partial ticking."""
        self.body(MARKED)
        for names in (["red", "nope"], ["red", "red"]):
            with self.subTest(names=names):
                run = self.w.qew("pr", "check", "1", *names)
                self.assertEqual(run.code, 2)
        self.assertEqual(self.w.forge.body_writes, [])

    def test_boxes_inside_code_fences_do_not_count(self) -> None:
        """Prevents a hidden open box letting `qew pr merge` pass (or a hidden box being ticked)."""
        self.body(
            "- [ ] real one\n```\n- [ ] hidden in backticks\n```\n~~~\n- [ ] hidden in tildes\n~~~\n"
            "```x``` is not a fence\n- [ ] real two\n````\n- [ ] still hidden\n```\n- [ ] still hidden\n````\n- [ ] real three\n"
        )
        listing = self.w.qew("pr", "check", "1", "--list").out.splitlines()
        self.assertEqual(listing, ["[ ] 1 real one", "[ ] 2 real two", "[ ] 3 real three"])

    def test_a_crlf_body_keeps_its_line_endings(self) -> None:
        """Prevents whole-body diffs."""
        self.body("- [ ] a <!-- pr-progress:a -->\r\n- [ ] b <!-- pr-progress:b -->\r\n")
        self.assertEqual(self.w.qew("pr", "check", "1", "a").code, 0)
        self.assertEqual(self.forge_body(), "- [x] a <!-- pr-progress:a -->\r\n- [ ] b <!-- pr-progress:b -->\r\n")

    def test_a_teammates_edit_between_read_and_write_is_not_overwritten(self) -> None:
        """Prevents losing a teammate's edit."""
        self.body(MARKED)
        self.w.forge.after_read.append(lambda forge: forge.edit_pull(1, body=MARKED + "A teammate added this.\n"))
        run = self.w.qew("pr", "check", "1", "red")
        self.assertEqual(run.code, 3)
        self.assertIn("changed", run.err)
        self.assertEqual(self.w.forge.body_writes, [])
        self.assertIn("A teammate added this.", self.forge_body())

    def test_a_write_the_forge_did_not_keep_is_reported_not_celebrated(self) -> None:
        """Prevents reporting success for a failed write."""
        self.body(MARKED)
        self.w.forge.body_write_ignored = True
        run = self.w.qew("pr", "check", "1", "red")
        self.assertEqual(run.code, 3)
        self.assertNotIn("checked: red", run.out)

    def test_list_shows_state_and_writes_nothing_and_a_body_without_a_checklist_says_so(self) -> None:
        self.body("- [x] done <!-- pr-progress:a -->\n- [ ] open <!-- pr-progress:b -->\n")
        self.assertEqual(self.w.qew("pr", "check", "1", "--list").out.splitlines(), ["[x] a done", "[ ] b open"])
        self.body("Just prose.\n")
        self.assertIn("no checklist", self.w.qew("pr", "check", "1", "--list").out)
        self.assertEqual(self.w.forge.body_writes, [])

    def test_naming_items_with_all_or_list_or_with_nothing_is_refused_before_any_forge_call(self) -> None:
        """Prevents ambiguous intent."""
        reads = self.w.forge.count("read_pr")
        for argv in (["red", "--all"], ["red", "--list"], ["--all", "--list"], []):
            with self.subTest(argv=argv):
                self.assertEqual(self.w.qew("pr", "check", "1", *argv).code, 2)
        self.assertEqual(self.w.forge.count("read_pr"), reads)

    def test_a_merged_or_closed_pull_request_is_not_edited(self) -> None:
        """Prevents editing history."""
        self.body(MARKED)
        for state in ("merged", "closed"):
            with self.subTest(state=state):
                self.w.forge.edit_pull(1, state=state)
                self.assertEqual(self.w.qew("pr", "check", "1", "red").code, 2)
        self.assertEqual(self.w.forge.body_writes, [])


if __name__ == "__main__":
    unittest.main()
