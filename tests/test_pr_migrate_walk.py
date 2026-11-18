"""`qew pr migrate`: pre-schema-9 pull requests get a link declaration from their checklist and recorded items.

The dry run works on a schema 8 ledger (the owner reviews it before the cutover); applying needs schema 9.
Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.ledger_store import Ledger
from tests.ledger_harness import git
from tests.memory_forge import MemoryForge
from tests.pr_harness import PrWorld


class MigrateWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld(schema="8")
        self.addCleanup(self.w.close)
        self.done = self.w.item("Done item")
        self.todo = self.w.item("Todo item")
        self.extra = self.w.item("Extra", claim=False)
        steps = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
        self.addCleanup(os.unlink, steps.name)
        steps.write(f"## Progress\n\n- [ ] done <!-- pr-progress:{self.done} -->\n- [ ] todo <!-- pr-progress:{self.todo} -->\n")
        steps.close()
        self.assertEqual(self.w.open(self.done, "--body-file", steps.name).code, 0)
        self.assertEqual(self.w.qew("pr", "check", "1", "--add", self.todo).code, 2)  # already boxed: the second box came from the body file
        self.assertEqual(self.w.qew("pr", "check", "1", self.done).code, 0)

    def rows(self, *argv: str, forge: MemoryForge | None = None) -> list[dict]:
        run = self.w.qew("pr", "migrate", "--dry-run", "--json", *argv, forge=forge)
        self.assertEqual(run.code, 0, run)
        return json.loads(run.out)["pull_requests"]

    def roles(self, row: dict) -> dict[str, str]:
        return {i["item"]: i["role"] for i in row["items"]}

    def test_dry_run_on_schema_8_maps_ticked_to_closes_and_unticked_to_remains_and_writes_nothing(self) -> None:
        """Prevents a migration guessing: a ticked box was delivered (closes), an unticked one was not (remains)."""
        before = sorted(p.name for p in self.w.world.ledger.rglob("*.json"))
        (row,) = self.rows()
        self.assertEqual(self.roles(row), {self.done: "closes", self.todo: "remains"})
        self.assertEqual(sorted(p.name for p in self.w.world.ledger.rglob("*.json")), before)
        self.assertFalse((self.w.world.ledger / "prs").exists())

    def test_a_ticked_parent_with_open_children_becomes_remains_automatically(self) -> None:
        """Prevents the migration completing a whole parent for a partial-slice pull request."""
        child = self.w.item("Open child", kind="task", parent=self.done, claim=False)
        (row,) = self.rows()
        self.assertEqual(self.roles(row)[self.done], "remains")
        flagged = next(i for i in row["items"] if i["item"] == self.done)
        self.assertIn("parent-with-open-children", flagged["why"])
        self.assertIn(child, flagged["why"])
        text = self.w.qew("pr", "migrate", "--dry-run").out
        self.assertIn("parent-with-open-children", text.split("\n")[0] + text)  # flagged rows are listed

    def test_without_a_reachable_forge_the_recorded_items_become_remains_never_closes(self) -> None:
        """Prevents an unreadable checklist being treated as everything delivered."""
        down = MemoryForge("git.example")
        down.down = True
        (row,) = self.rows(forge=down)
        self.assertEqual(set(self.roles(row).values()), {"remains"})

    def test_apply_needs_schema_9_and_says_how(self) -> None:
        """Prevents a half-migrated ledger: declarations are written only where the ledger can hold them."""
        run = self.w.qew("pr", "migrate")
        self.assertEqual(run.code, 2, run)
        self.assertIn("migrate-layout", run.err)

    def cutover(self) -> None:
        self.assertEqual(self.w.qew("migrate-layout").code, 0)
        self.w.ledger = Ledger(self.w.world.ledger, "TST", schema=9)

    def test_apply_writes_the_declaration_once_and_a_rerun_changes_nothing(self) -> None:
        """Prevents a repeated migration duplicating events, or overwriting a declaration an agent made since."""
        self.cutover()
        run = self.w.qew("pr", "migrate")
        self.assertEqual(run.code, 0, run)
        shown = json.loads(self.w.qew("pr", "links", "1", "--json").out)
        self.assertEqual((shown["closes"], shown["remains"]), ([self.done], [self.todo]))
        files = sorted((self.w.world.ledger / "prs").rglob("*.json"))
        again = self.w.qew("pr", "migrate")
        self.assertEqual(again.code, 0, again)
        self.assertEqual(sorted((self.w.world.ledger / "prs").rglob("*.json")), files)

    def test_a_merged_pull_request_with_an_unfinished_closes_item_is_flagged_pending_never_completed(self) -> None:
        """Prevents the migration completing work, or leaving a merged pull request's unfinished item unnoticed."""
        self.w.forge.edit_pull(1, state="merged")
        self.cutover()
        self.assertEqual(self.w.qew("pr", "migrate").code, 0)
        self.assertIn("state: claimed", self.w.qew("show", self.done).out)
        pending = [json.loads(p.read_text()) for p in (self.w.world.ledger / "prs").rglob("*.json") if "land_pending" in p.read_text()]
        self.assertEqual([e["item"] for e in pending], [self.done])

    def test_the_cutover_order_works_when_merged_pull_requests_exist(self) -> None:
        """Prevents migrate-layout rolling itself back because its own check flags merged pull requests that `pr migrate`, which can only
        run after it, has not declared yet (the runbook's order: migrate-layout, then pr migrate, then check)."""
        from qew import pr_record

        pr_record.update_mirror(self.w.ledger, pr_record.find(self.w.ledger, 1, None), state="merged")
        moved = self.w.qew("migrate-layout")
        self.assertEqual(moved.code, 0, moved)
        self.w.ledger = Ledger(self.w.world.ledger, "TST", schema=9)
        undeclared = self.w.qew("check")
        self.assertNotEqual(undeclared.code, 0, undeclared)  # until `pr migrate` the check names the fix
        self.assertIn("pr migrate", undeclared.out + undeclared.err)
        self.assertEqual(self.w.qew("pr", "migrate").code, 0)
        self.assertEqual(self.w.qew("check").code, 0)

    def test_a_dropped_item_becomes_related(self) -> None:
        """Prevents a deliberately dropped item vanishing from the declaration, or being migrated as work the pull request closes."""
        self.assertEqual(self.w.qew("pr", "check", "1", "--drop", self.todo, "--reason", "next pull request").code, 0)
        (row,) = self.rows()
        self.assertEqual(self.roles(row), {self.done: "closes", self.todo: "related"})


if __name__ == "__main__":
    unittest.main()
