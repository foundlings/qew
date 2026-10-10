"""Walks of re-running a plan: a crashed run is finished by the same plan, never duplicated.

Adversary: a killed run leaving some items, so the rerun duplicates every one; an edited plan silently
creating a second copy; two items claiming one plan entry; and attacker-supplied text posing as provenance.
"""

import hashlib
import json
import unittest
from unittest import mock

from tests.ledger_harness import World  # first: it puts src/ on the path
from tests.plan_support import jsonl, listing, snapshot
from qew import ledger_config, plan_apply, plan_file
from qew.ledger_store import Ledger

PLAN = jsonl(
    {"ref": "e", "kind": "epic", "title": "Epic"},
    {"ref": "a", "kind": "issue", "title": "First", "parent": "@e"},
    {"ref": "b", "kind": "issue", "title": "Second", "parent": "@e", "needs": ["@a"]},
)


class Killed(BaseException):
    """Stands in for a kill: nothing in the command can catch or roll it back."""


class DiesAtSecondItem(Ledger):
    def write_item(self, item, overwrite: bool = False) -> None:
        if len(list(self.items_dir.glob("*/item.md"))) >= 1 and not overwrite:
            raise Killed()
        super().write_item(item, overwrite)


class PlanRerunWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.path = self.world.base / "plan.jsonl"
        self.path.write_text(PLAN, encoding="utf-8")

    def run_plan(self, *extra: str):
        return self.world.run("create", "--from-file", str(self.path), *extra)

    def titles(self) -> list:
        return sorted(row["title"] for row in listing(self.world))

    def kill_midway(self) -> None:
        config = ledger_config.load(self.world.code)
        real = plan_apply.ledger_for(config)
        crashing = DiesAtSecondItem(real.path, real.prefix, real.id_modes)
        plan = plan_file.parse(PLAN.encode(), "plan.jsonl")
        with mock.patch.object(plan_apply, "ledger_for", return_value=crashing):
            with self.assertRaises(Killed):
                plan_apply.apply(config, plan, None)

    def test_a_run_killed_after_the_first_item_is_finished_by_the_same_plan_without_duplicates(self) -> None:
        """A rerun that creates everything again doubles the work after any crash."""
        self.kill_midway()
        self.assertEqual(self.world.run("check").returncode, 1, "the killed run left an orphan event")
        result = self.run_plan("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(sorted(report["created"]), ["a", "b", "e"])
        self.assertEqual(sorted(report["adopted"]), ["a", "e"])
        self.assertEqual(self.titles(), ["Epic", "First", "Second"])
        self.assertEqual(self.world.run("check").returncode, 0)
        rows = {row["title"]: row for row in listing(self.world)}
        self.assertEqual(rows["Second"]["needs"], [rows["First"]["id"]])
        self.assertEqual(rows["Second"]["parent"], rows["Epic"]["id"])

    def test_rerunning_a_finished_plan_changes_nothing(self) -> None:
        """Idempotence: the second identical run must not write or duplicate anything."""
        self.assertEqual(self.run_plan().returncode, 0)
        before = snapshot(self.world)
        again = self.run_plan()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(snapshot(self.world), before)

    def test_two_items_carrying_one_marker_make_the_rerun_ambiguous_and_create_nothing(self) -> None:
        """Linking the wrong one of two lookalike items would attach edges to the wrong work."""
        self.assertEqual(self.run_plan().returncode, 0)
        epic = next(row["id"] for row in listing(self.world) if row["title"] == "Epic")
        copy = "copyof" + epic
        (self.world.ledger / "events" / copy).mkdir()
        for event in (self.world.ledger / "events" / epic).glob("*.json"):
            (self.world.ledger / "events" / copy / event.name).write_text(event.read_text())
        text = (self.world.ledger / "items" / f"{epic}" / "item.md").read_text().replace(f"id: {epic}", f"id: {copy}", 1)
        (self.world.ledger / "items" / f"{copy}" / "item.md").parent.mkdir(exist_ok=True)
        (self.world.ledger / "items" / f"{copy}" / "item.md").write_text(text)
        before = snapshot(self.world)
        result = self.run_plan()
        self.assertEqual(result.returncode, 2)
        self.assertIn("ambiguous", result.stderr)
        self.assertEqual(snapshot(self.world), before)

    def test_an_edited_plan_over_items_of_the_old_revision_is_refused_naming_them(self) -> None:
        """An edit must not silently duplicate or overwrite what the first revision created."""
        self.assertEqual(self.run_plan().returncode, 0)
        epic = next(row["id"] for row in listing(self.world) if row["title"] == "Epic")
        self.path.write_text(PLAN + jsonl({"ref": "c", "title": "Third"}), encoding="utf-8")
        before = snapshot(self.world)
        result = self.run_plan()
        self.assertEqual(result.returncode, 2)
        self.assertIn("plan revision mismatch", result.stderr)
        self.assertIn(epic, result.stderr)
        self.assertEqual(snapshot(self.world), before)

    def test_marker_like_text_in_a_body_or_a_malformed_event_is_not_provenance(self) -> None:
        """Only the canonical event fields adopt; an attacker-supplied lookalike must not hijack a key."""
        digest = hashlib.sha256(PLAN.encode()).hexdigest()
        lookalike = self.world.create("--title", "Lookalike", "--body", f"plan: {digest}\nkey: e\nsource: plan.jsonl", "--draft")
        decoy = self.world.create("--title", "Decoy", "--draft")
        planted = {"type": "approved", "at": "2026-01-01T00:00:00Z", "actor": "x", "quote": "A: 'twelve chars or more here'",
                   "plan": digest.upper(), "key": "e", "source": "plan.jsonl"}
        (self.world.ledger / "events" / decoy / "zzzzzzzzzzzzzzzzzzzzzzzzzz.json").write_text(json.dumps(planted))
        before = {row["id"] for row in listing(self.world)}
        self.assertEqual(self.run_plan().returncode, 0)
        after = {row["id"] for row in listing(self.world)}
        self.assertEqual(len(after - before), 3, "all three entries are created fresh")
        self.assertIn(lookalike, after)


if __name__ == "__main__":
    unittest.main()
