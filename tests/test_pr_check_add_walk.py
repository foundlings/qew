"""`qew pr check N --add ITEM` adds a marked Progress entry for an item that joined the work after the pull request opened: every
existing byte and tick stays, the entry can be ticked like any other, and each refusal writes nothing.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld

BODY = "## Progress\n\n- [x] RED test <!-- pr-progress:red -->\n- [ ] implementation <!-- pr-progress:green -->\n\nTrailer: kept as is.\n"


class CheckAddWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item()
        self.later = self.w.item("Discovered follow-up", claim=False)
        self.assertEqual(self.w.open(self.item).code, 0)
        self.w.forge.edit_pull(1, body=BODY)

    def body(self) -> str:
        return self.w.forge.pulls[1].body

    def test_an_entry_is_added_after_the_last_box_ticks_stay_and_it_can_be_ticked(self) -> None:
        """Prevents a PR that gained work after opening having no Progress entry for it, or an add that rewrites the checklist."""
        run = self.w.qew("pr", "check", "1", "--add", self.later)
        self.assertEqual(run.code, 0, run)
        added = f"- [ ] Discovered follow-up <!-- pr-progress:{self.later} -->\n"
        self.assertEqual(self.body(), BODY.replace("\n\nTrailer", "\n" + added + "\nTrailer"))
        self.assertIn(self.later, json.dumps(self.w.events(self.item)))
        self.assertEqual(self.w.qew("pr", "check", "1", "--list").code, 0)
        self.assertEqual(self.w.qew("pr", "check", "1", self.later).code, 0)
        self.assertIn(f"- [x] Discovered follow-up <!-- pr-progress:{self.later} -->", self.body())
        self.assertIn("- [x] RED test", self.body())

    def test_a_body_without_a_checklist_gets_a_progress_section_at_the_end(self) -> None:
        """Prevents the add failing, or landing mid-prose, on a body that has no checklist yet."""
        self.w.forge.edit_pull(1, body="Delivers it.\n\n## Why\n\nBecause.\n")
        self.assertEqual(self.w.qew("pr", "check", "1", "--add", self.later).code, 0)
        self.assertEqual(self.body(), f"Delivers it.\n\n## Why\n\nBecause.\n\n## Progress\n\n- [ ] Discovered follow-up <!-- pr-progress:{self.later} -->\n")

    def test_every_refusal_writes_nothing(self) -> None:
        """Prevents a duplicate marker, an unknown item, a stranger, a finished PR or a combined flag from touching the body."""
        self.assertEqual(self.w.qew("pr", "check", "1", "--add", self.later).code, 0)
        after = self.body()
        self.assertEqual(self.w.qew("pr", "check", "1", "--add", self.later).code, 2)  # the marker is already there
        self.assertEqual(self.w.qew("pr", "check", "1", "--add", "NOPE").code, 2)
        self.assertEqual(self.w.qew("pr", "check", "1", "--add", self.later, "--all").code, 2)
        self.assertEqual(self.w.qew("pr", "check", "1", "--add", self.item, "--add", self.item).code, 2)
        self.assertEqual(self.w.qew("pr", "check", "1", "--add", self.item, actor="stranger").code, 3)
        self.assertEqual(self.body(), after)
        self.w.forge.edit_pull(1, state="closed")
        self.assertEqual(self.w.qew("pr", "check", "1", "--add", self.item).code, 2)
        self.assertEqual(self.body(), after)

    def test_a_title_with_a_closing_reference_is_refused(self) -> None:
        """Prevents an item title ('closes #12') becoming a forge-side close when the pull request merges."""
        risky = self.w.item("fixes #12 for good", claim=False)
        before = self.body()
        run = self.w.qew("pr", "check", "1", "--add", risky)
        self.assertEqual(run.code, 2, run)
        self.assertEqual(self.body(), before)

    def test_a_teammates_edit_between_read_and_write_is_not_overwritten(self) -> None:
        """Prevents losing a concurrent body edit."""
        self.w.forge.after_read.append(lambda forge: forge.edit_pull(1, body=BODY + "A teammate added this.\n"))
        run = self.w.qew("pr", "check", "1", "--add", self.later)
        self.assertEqual(run.code, 3, run)
        self.assertEqual(self.w.forge.body_writes, [])
        self.assertIn("A teammate added this.", self.body())


if __name__ == "__main__":
    unittest.main()
