"""Work an agent files lands in the normal queue; a draft exists only when a human asks for one.

Each test is a scenario; its docstring names the failure it prevents. The creator stays on record (the item's author and the
`filed_by` of its approved event); a human's quote, when given, is still recorded as before.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


class QueueDefaultWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def show(self, item: str) -> dict:
        return json.loads(self.world.run("show", item, "--json").stdout)

    def test_a_plain_create_is_ready_with_its_creator_on_record(self) -> None:
        """Prevents findings piling up as drafts: no quote, no --draft means approved and startable."""
        item = self.world.create("--kind", "task", "--title", "Found while reading")
        shown = self.show(item)
        self.assertEqual(shown["state"], "approved")
        self.assertEqual(shown["author"], "tester")
        self.assertIn(item, self.world.run("ready").stdout)
        approvals = [e for e in self.world.events(item) if e["type"] == "approved"]
        self.assertEqual([(e.get("filed_by"), "quote" in e) for e in approvals], [("tester", False)])
        self.assertIn("filed by tester", self.world.run("show", item).stdout)
        self.assertEqual(self.world.run("check").returncode, 0)
        self.assertEqual(self.world.run("start", item).returncode, 0)

    def test_work_with_unmet_needs_is_backlog_and_becomes_ready_when_they_finish(self) -> None:
        """Prevents starting work out of order: a needing item is queued but not ready until its need is completed."""
        first = self.world.create("--kind", "task", "--title", "First")
        second = self.world.create("--kind", "task", "--title", "Second", "--needs", first)
        ready = self.world.run("ready").stdout
        self.assertIn(first, ready)
        self.assertNotIn(second, ready)
        self.assertEqual(self.show(second)["state"], "approved")
        self.assertIn(second, self.world.run("blocked").stdout)

    def test_draft_is_only_for_a_human_who_asks_and_still_needs_a_quote_to_release(self) -> None:
        """Prevents agents releasing human-requested drafts: --draft files a draft, `draft approve --quote` releases it."""
        item = self.world.create("--kind", "task", "--title", "Asked for a draft", "--draft")
        self.assertEqual(self.show(item)["state"], "draft")
        self.assertNotIn(item, self.world.run("ready").stdout)
        self.assertEqual(self.world.run("start", item).returncode, 2)
        self.assertEqual(self.world.run("draft", "approve", item, "--quote", QUOTE).returncode, 0)
        self.assertEqual(self.show(item)["state"], "approved")

    def test_a_given_quote_is_still_recorded_and_discovered_from_is_kept(self) -> None:
        """Prevents losing provenance: a human's words stay on the approval, and a finding keeps its source item."""
        source = self.world.create("--kind", "task", "--title", "Source")
        item = self.world.create("--kind", "task", "--title", "Finding", "--discovered-from", source, "--quote", QUOTE)
        approvals = [e for e in self.world.events(item) if e["type"] == "approved"]
        self.assertTrue(approvals[0].get("quote"))
        self.assertNotIn("filed_by", approvals[0])
        self.assertEqual(self.show(item).get("discovered_from"), source)

    def test_plan_files_follow_the_same_default_and_can_ask_for_drafts(self) -> None:
        """Prevents bulk paths behaving differently: a plan files to the queue, an entry with approved: false is a draft."""
        plan = self.world.base / "plan.jsonl"
        plan.write_text("\n".join(json.dumps(row) for row in (
            {"kind": "task", "title": "Queued by plan", "ref": "a"},
            {"kind": "task", "title": "Drafted by plan", "ref": "b", "approved": False},
        )) + "\n", encoding="utf-8")
        result = self.world.run("create", "--from-file", str(plan))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        listing = self.world.run("list", "--json").stdout
        states = {}
        for line in listing.splitlines():
            row = json.loads(line)
            states[row["title"]] = row["state"]
        self.assertEqual(states, {"Queued by plan": "approved", "Drafted by plan": "draft"})

    def test_draft_flag_makes_every_entry_of_a_plan_a_draft(self) -> None:
        """Prevents a human-requested draft plan leaking into the queue: --draft files all of it as drafts."""
        plan = self.world.base / "plan.jsonl"
        plan.write_text(json.dumps({"kind": "task", "title": "Only a draft", "ref": "a"}) + "\n", encoding="utf-8")
        self.assertEqual(self.world.run("create", "--from-file", str(plan), "--draft").returncode, 0)
        states = [json.loads(line)["state"] for line in self.world.run("list", "--json").stdout.splitlines()]
        self.assertEqual(states, ["draft"])


if __name__ == "__main__":
    unittest.main()
