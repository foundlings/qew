"""Walks of the milestone calls on every backend: find, create, update, read, close, assign.

Adversary: a duplicate milestone from a lookup that misses, a due date lost in
translation, an assignment the forge accepted but did not keep.
"""

import unittest

from qew.forge import IssueSpec, MilestoneSpec
from tests.test_label_creation_walk import adapters

ISSUE = IssueSpec(title="T", body="b", labels=(), open=True)


class MilestoneBackendWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.backends = adapters(self)

    def test_create_find_update_read_round_trip_with_a_due_date(self) -> None:
        """Title, description and due date survive each backend's own shape."""
        for b in self.backends:
            forge = b.forge
            self.assertIsNone(forge.find_milestone("Campaign"), b.name)
            made = forge.create_milestone(MilestoneSpec("Campaign", "Approved by Alex: 'yes'", "2026-12-31"))
            self.assertEqual((made.title, made.description, made.due, made.open), ("Campaign", "Approved by Alex: 'yes'", "2026-12-31", True), b.name)
            self.assertEqual(forge.find_milestone("Campaign").number, made.number, b.name)
            changed = forge.update_milestone(made.number, MilestoneSpec("Campaign", "new text", ""))
            self.assertEqual((changed.description, changed.due), ("new text", ""), b.name)
            self.assertEqual(forge.read_milestone(made.number), changed, b.name)

    def test_assign_close_and_reopen(self) -> None:
        """An issue shows the milestone it sits in; a milestone closes and reopens, confirmed by a read."""
        for b in self.backends:
            forge = b.forge
            made = forge.create_milestone(MilestoneSpec("Campaign", "d"))
            issue = forge.create_issue(ISSUE)
            self.assertEqual(issue.milestone, 0, b.name)
            assigned = forge.assign_milestone(issue.number, made.number)
            self.assertEqual(assigned.milestone, made.number, b.name)
            self.assertEqual(forge.read_issue(issue.number).milestone, made.number, b.name)
            self.assertFalse(forge.set_milestone_open(made.number, False).open, b.name)
            self.assertFalse(forge.read_milestone(made.number).open, b.name)
            self.assertTrue(forge.set_milestone_open(made.number, True).open, b.name)


if __name__ == "__main__":
    unittest.main()
