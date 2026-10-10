"""Walks of `qew plan status`: a deterministic rollup of one epic's direct children, from files only.

Adversary: derived state living in prose, drafts offered as next work, an agent guessing the closing
step, and a rollup guessed from partial or malformed data.
"""

import json
import unittest

from tests.ledger_harness import QUOTE, World


class PlanStatusWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def child(self, epic: str, title: str, *extra: str, release: bool = True) -> str:
        return self.world.create("--title", title, "--parent", epic, *extra, *(["--quote", QUOTE] if release else ["--draft"]))

    def finish(self, item: str) -> None:
        w = self.world
        self.assertEqual(w.run("start", item).returncode, 0)
        w.run("evidence", item, "--change", w.commit())
        self.assertEqual(w.run("complete", item).returncode, 0)

    def status(self, *arguments: str) -> dict:
        result = self.world.run("plan", "status", *arguments, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def plan(self) -> str:
        epic = self.world.create("--kind", "epic", "--title", "Plan", "--quote", QUOTE)
        self.epic = epic
        return epic

    def test_golden_rollup_counts_every_class_and_names_the_best_ready_child(self) -> None:
        """Counts and the next child must come from files, not from anyone's summary."""
        w, epic = self.world, self.plan()
        done = self.child(epic, "Done")
        self.finish(done)
        held = self.child(epic, "Held")
        w.run("start", held)
        gate = self.child(epic, "Gate")
        waits = self.child(epic, "Waits", "--needs", gate)
        second = self.child(epic, "Second", "--priority", "2")
        first = self.child(epic, "First", "--priority", "1")
        self.child(epic, "Unreleased", release=False)
        plan = self.status(epic)["plans"][0]
        self.assertEqual(self.status(epic)["schema_version"], 1)
        self.assertEqual(plan["counts"], {"total": 7, "draft": 1, "ready": 3, "blocked": 1, "claimed": 1, "closed": 1})
        self.assertEqual(plan["next"], {"id": first, "title": "First", "reason": "highest-priority ready child"})
        self.assertFalse(plan["completable"])
        self.assertIsNone(plan["complete_command"])
        self.assertIn(second, w.run("ready").stdout)
        self.assertNotIn(waits, w.run("ready").stdout)

    def test_a_draft_child_with_the_best_priority_is_counted_and_never_selected(self) -> None:
        """Drafts leaking into next-work advice would start unapproved work."""
        epic = self.plan()
        draft = self.child(epic, "Draft", "--priority", "0", release=False)
        live = self.child(epic, "Live", "--priority", "3")
        plan = self.status(epic)["plans"][0]
        self.assertEqual(plan["counts"]["draft"], 1)
        self.assertEqual(plan["next"]["id"], live)
        self.assertNotEqual(plan["next"]["id"], draft)

    def test_completable_names_the_ledger_command_once_every_child_is_closed(self) -> None:
        """Agents must not guess the closing step."""
        epic = self.plan()
        self.finish(self.child(epic, "Only"))
        plan = self.status(epic)["plans"][0]
        self.assertTrue(plan["completable"])
        self.assertEqual(plan["complete_command"], f"qew complete {epic}")
        self.assertIsNone(plan["next"])

    def test_a_draft_epic_is_never_completable_and_says_draft_plan(self) -> None:
        """Offering a command that will refuse wastes the agent's turn."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Plan", "--draft")
        child = self.child(epic, "Done early")
        self.finish(child)
        plan = self.status(epic)["plans"][0]
        self.assertTrue(plan["draft"])
        self.assertFalse(plan["completable"])
        text = w.run("plan", "status", epic).stdout
        self.assertIn("(draft plan)", text)

    def test_text_output_counts_drafts_only_when_there_are_some(self) -> None:
        """A reader must not mistake drafts for live work, and unchanged output must stay unchanged."""
        w, epic = self.world, self.plan()
        self.child(epic, "A")
        self.assertEqual(w.run("plan", "status", epic).stdout.splitlines()[1], "0/1 closed")
        self.child(epic, "B", release=False)
        self.child(epic, "C", release=False)
        lines = w.run("plan", "status", epic).stdout.splitlines()
        self.assertEqual(lines[1], "0/3 closed, 2 drafts")
        self.assertEqual(lines[0].split()[0], epic)

    def test_a_dangling_need_or_illegal_event_order_fails_loudly_without_a_rollup(self) -> None:
        """A ready state guessed from partial data is worse than an error."""
        w, epic = self.world, self.plan()
        child = self.child(epic, "Child")
        path = w.ledger / "items" / f"{child}" / "item.md"
        path.write_text(path.read_text().replace("needs: \n", "needs: zzzzzzzzz\n", 1))
        result = w.run("plan", "status", epic)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_no_argument_lists_every_open_epic_and_a_non_epic_or_unknown_id_is_refused(self) -> None:
        """A task must not be rolled up as if it were a plan."""
        w, epic = self.world, self.plan()
        other = w.create("--kind", "epic", "--title", "Other", "--draft")
        task = self.child(epic, "Task")
        listed = self.status()["plans"]
        self.assertEqual(sorted(p["id"] for p in listed), sorted([epic, other]))
        self.assertEqual(w.run("plan", "status", task).returncode, 2)
        self.assertEqual(w.run("plan", "status", "nothere").returncode, 2)


if __name__ == "__main__":
    unittest.main()
