"""Walks of `qew show`: related items with state and title, the epic's next step, draft wording, comments.

Adversary: an agent opening four more items to understand one, guessing where an epic starts, reading
a draft as live work, a hidden dangling edge, and a comment forging a state change.
"""

import json
import unittest

from tests.ledger_harness import World


class ShowBase(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def make(self, title: str, *extra: str, approve: bool = True) -> str:
        item = self.world.create("--title", title, *extra)
        if approve:
            self.world.approve(item)
        return item

    def show(self, item: str) -> dict:
        result = self.world.run("show", item, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)


class RelatedItemsWalk(ShowBase):
    def test_need_dependent_parent_and_child_come_with_state_and_title(self) -> None:
        """One show answers what blocks this, what it blocks, where it sits and what is below it."""
        epic = self.make("The plan", "--kind", "epic")
        need = self.make("The need")
        middle = self.make("Middle", "--parent", epic, "--needs", need)
        after = self.make("After", "--needs", middle)
        child = self.make("Sub task", "--kind", "task", "--parent", middle)
        data = self.show(middle)
        self.assertEqual(data["blocked_by"], [{"id": need, "state": "approved", "title": "The need"}])
        self.assertEqual(data["blocking"], [{"id": after, "state": "approved", "title": "After"}])
        self.assertEqual(data["parent"], {"id": epic, "state": "approved", "title": "The plan"})
        self.assertEqual(data["children"], [{"id": child, "state": "approved", "title": "Sub task"}])

    def test_a_need_that_no_longer_exists_is_reported_missing(self) -> None:
        """A dangling edge is shown, not an empty slot."""
        item = self.make("Waiting")
        path = self.world.ledger / "items" / f"{item}" / "item.md"
        path.write_text(path.read_text().replace("needs: \n", "needs: zzzzzzzzz\n", 1))
        self.assertEqual(self.show(item)["blocked_by"], [{"id": "zzzzzzzzz", "state": "missing"}])

    def test_unknown_and_ambiguous_references_are_refused_with_candidates(self) -> None:
        """Acting on the wrong item is prevented: exit 2, and the candidates are named."""
        first, second = self.make("one"), self.make("two")
        prefix = first[:1]
        unknown = self.world.run("show", "nothing")
        self.assertEqual((unknown.returncode, unknown.stdout), (2, ""))
        ambiguous = self.world.run("show", prefix)
        self.assertEqual((ambiguous.returncode, ambiguous.stdout), (2, ""))
        self.assertIn(first, ambiguous.stderr)
        self.assertIn(second, ambiguous.stderr)

    def test_an_epic_names_its_highest_priority_ready_child_as_next(self) -> None:
        """An agent opening a plan is told where to start."""
        epic = self.make("Plan", "--kind", "epic")
        self.make("Later", "--parent", epic, "--priority", "3")
        first = self.make("First", "--parent", epic, "--priority", "1")
        self.assertEqual(self.show(epic)["next"], {"id": first, "priority": 1, "title": "First"})
        self.assertIn(f"next: {first} p1 First", self.world.run("show", epic).stdout)


class DraftWordingWalk(ShowBase):
    def test_the_first_line_names_the_state_and_json_says_whether_it_is_a_draft(self) -> None:
        """A draft is never read as live; live work has no draft wording."""
        draft = self.make("Idea", approve=False)
        live = self.make("Real")
        self.assertTrue(self.world.run("show", draft).stdout.splitlines()[0].startswith(f"{draft} draft "))
        self.assertTrue(self.show(draft)["draft"])
        shown = self.world.run("show", live).stdout
        self.assertNotIn("draft", shown)
        self.assertFalse(self.show(live)["draft"])

    def test_epic_progress_counts_drafts_and_a_draft_epic_is_never_completable(self) -> None:
        """`0/3 completed, 2 drafts`; a draft epic whose children are done says so and offers no completion."""
        w = self.world
        epic = self.make("Plan", "--kind", "epic")
        self.make("live", "--parent", epic)
        self.make("d1", "--parent", epic, approve=False)
        self.make("d2", "--parent", epic, approve=False)
        self.assertIn("progress: 0/3 completed, 2 drafts", w.run("show", epic).stdout)
        plain = self.make("Plain plan", "--kind", "epic")
        self.make("only", "--parent", plain)
        self.assertIn("progress: 0/1 completed\n", w.run("show", plain).stdout)
        draft_epic = self.make("Draft plan", "--kind", "epic", approve=False)
        child = self.make("done child", "--parent", draft_epic)
        w.run("start", child)
        w.run("evidence", child, "--change", w.commit("work"))
        self.assertEqual(w.run("complete", child).returncode, 0)
        data = self.show(draft_epic)
        self.assertIsNone(data["complete_command"])
        text = w.run("show", draft_epic).stdout
        self.assertIn("(draft epic)", text)
        self.assertNotIn("qew complete", text)


if __name__ == "__main__":
    unittest.main()
