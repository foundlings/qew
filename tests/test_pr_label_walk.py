"""`qew pr label NUMBER [--add L...] [--remove L...]` changes a recorded pull request's labels through qew, keeps the labels it was not
told about, enforces the same ownership as `qew pr ready`, and records success only once the forge shows the labels.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.pr_harness import PrWorld


class PrLabelWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item("Work")
        self.assertEqual(self.w.open(self.item).code, 0)

    def labels(self) -> tuple[str, ...]:
        return tuple(sorted(self.w.forge.pulls[1].labels))

    def test_labels_are_added_and_removed_and_the_others_stay(self) -> None:
        """Prevents a label operation replacing the whole set: a label a human put on the pull request must survive."""
        self.w.forge.edit_pull(1, labels=("santa",))
        added = self.w.qew("pr", "label", "1", "--add", "elf2", "--add", "owner:elf2")
        self.assertEqual(added.code, 0, added)
        self.assertEqual(self.labels(), ("elf2", "owner:elf2", "santa"))
        removed = self.w.qew("pr", "label", "1", "--remove", "owner:elf2")
        self.assertEqual(removed.code, 0, removed)
        self.assertEqual(self.labels(), ("elf2", "santa"))
        again = self.w.qew("pr", "label", "1", "--remove", "owner:elf2", "--add", "elf2", "--json")
        self.assertEqual(again.code, 0, again)
        self.assertFalse(json.loads(again.out)["changed"])

    def test_a_pull_request_of_another_owner_is_refused_and_nothing_changes(self) -> None:
        """Prevents one agent relabelling another's pull request: the ownership rule of `pr ready` applies."""
        run = self.w.qew("pr", "label", "1", "--add", "elf9", actor="stranger")
        self.assertEqual(run.code, 3, run)
        self.assertEqual(self.labels(), ())

    def test_a_forge_that_accepts_but_does_not_apply_is_reported_not_recorded(self) -> None:
        """Prevents a false success: the readback shows no label, so the command fails with exit 4."""
        self.w.forge.label_write_ignored = True
        run = self.w.qew("pr", "label", "1", "--add", "elf2")
        self.assertEqual(run.code, 4, run)
        self.assertIn("elf2", run.err)

    def test_nothing_to_do_and_contradictions_are_refused(self) -> None:
        """Prevents a silent no-op or an ambiguous request: no change asked, or one label both added and removed, is exit 2."""
        self.assertEqual(self.w.qew("pr", "label", "1").code, 2)
        self.assertEqual(self.w.qew("pr", "label", "1", "--add", "a", "--remove", "a").code, 2)
        self.assertEqual(self.w.qew("pr", "label", "1", "--add", "bad\nlabel").code, 2)
        self.assertEqual(self.labels(), ())
        self.assertEqual(self.w.qew("pr", "label", "99", "--add", "a").code, 2)


if __name__ == "__main__":
    unittest.main()
