"""Walks of epics as campaigns: completing one, and reading its progress.

Adversary: completing a plan that still has open work or no work at all, a
repeat completion writing a second event, and a wrong `complete` suggestion.
"""

import json
import unittest

from tests.ledger_harness import World


def event_count(world: World, item: str) -> int:
    return len(world.events(item))


class CampaignCompleteWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def finished_epic(self):
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Epic")
        child = w.create("--kind", "issue", "--title", "Child", "--parent", epic)
        w.approve(epic, child)
        w.run("start", epic)
        w.run("start", child)
        w.run("evidence", child, "--change", w.commit())
        w.run("complete", child)
        return epic, child

    def test_epic_completes_over_finished_children_and_is_verified(self) -> None:
        """Completing then showing must agree: no unverified state change."""
        epic, _ = self.finished_epic()
        self.assertEqual(self.world.run("complete", epic).returncode, 0)
        self.assertIn("state: completed", self.world.run("show", epic).stdout)

    def test_epic_with_open_children_is_refused_without_writing(self) -> None:
        """The refusal counts the open children and adds no event."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Epic")
        for title in ("A", "B"):
            w.approve(w.create("--kind", "issue", "--title", title, "--parent", epic))
        w.approve(epic)
        w.run("start", epic)
        before = event_count(w, epic)
        result = w.run("complete", epic)
        self.assertEqual(result.returncode, 2)
        self.assertIn("2 open children", result.stderr)
        self.assertEqual(event_count(w, epic), before)

    def test_epic_without_children_is_refused(self) -> None:
        """An empty plan has no completed work; completing it would prove nothing."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Empty")
        w.approve(epic)
        w.run("start", epic)
        result = w.run("complete", epic)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no completed work", result.stderr)

    def test_completing_a_completed_epic_is_a_no_op(self) -> None:
        """The second call is quiet and unchanged: no second event, no error."""
        epic, _ = self.finished_epic()
        self.world.run("complete", epic)
        before = event_count(self.world, epic)
        again = self.world.run("complete", epic)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("unchanged", again.stdout)
        self.assertEqual(event_count(self.world, epic), before)


if __name__ == "__main__":
    unittest.main()


class CampaignProgressWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def epic_with(self, title: str, children: int, done: int) -> str:
        w = self.world
        epic = w.create("--kind", "epic", "--title", title)
        kids = [w.create("--kind", "issue", "--title", f"{title} {n}", "--parent", epic) for n in range(children)]
        w.approve(epic, *kids)
        w.run("start", epic)
        for kid in kids[:done]:
            w.run("start", kid)
            w.run("evidence", kid, "--change", w.commit())
            w.run("complete", kid)
        return epic

    def rows(self) -> dict:
        out = self.world.run("list", "--kind", "epic", "--json").stdout
        return {r["id"]: r for r in map(json.loads, out.splitlines())}

    def test_progress_distinguishes_completable_blocked_and_closed_epics(self) -> None:
        """Only the finished epic is completable and carries the command; a closed epic leaves the default list."""
        finished = self.epic_with("Finished", 2, 2)
        partial = self.epic_with("Partial", 2, 1)
        closed = self.world.create("--kind", "epic", "--title", "Closed")
        self.world.run("close", closed, "--reason", "not_planned")
        rows = self.rows()
        self.assertNotIn(closed, rows)
        self.assertEqual((rows[finished]["children_open"], rows[finished]["children_done"], rows[finished]["completable"]), (0, 2, True))
        self.assertEqual((rows[partial]["children_open"], rows[partial]["children_done"], rows[partial]["completable"]), (1, 1, False))
        text = self.world.run("list", "--kind", "epic").stdout.splitlines()
        by_id = {line.split()[0]: line for line in text}
        self.assertIn(f"qew complete {finished}", by_id[finished])
        self.assertNotIn("qew complete", by_id[partial])
        shown = self.world.run("show", finished).stdout
        self.assertIn("children_done: 2", shown)
        self.assertEqual(json.loads(self.world.run("show", closed, "--json").stdout)["id"], closed, "a named epic shows even when closed")

    def test_empty_epic_is_not_completable(self) -> None:
        """Zero children is never completable, so no complete command is suggested."""
        epic = self.world.create("--kind", "epic", "--title", "Empty")
        self.world.approve(epic)
        self.assertIs(self.rows()[epic]["completable"], False)
        self.assertNotIn("qew complete", self.world.run("list", "--kind", "epic").stdout)

    def test_no_command_is_printed_beside_a_rewritten_title(self) -> None:
        """A runnable command next to a hostile title could be read as an instruction."""
        epic = self.epic_with("Finished", 1, 1)
        path = self.world.ledger / "items" / f"{epic}" / "item.md"
        path.write_text(path.read_text().replace("title: Finished", "title: Fin ished"))
        self.assertNotIn("qew complete", self.world.run("list", "--kind", "epic").stdout)
