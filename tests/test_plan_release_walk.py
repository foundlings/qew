"""Walks of how a plan releases work: per-entry quotes, `approved`, and `--draft`.

Adversary: a draft plan leaking approved work into `ready`, an approved line quietly winning under
`--draft`, one human's words attributed to another entry, and a plan half-written before a bad quote.
"""

import json
import unittest

from tests.ledger_harness import QUOTE, World
from tests.plan_support import jsonl, listing, snapshot

OTHER = "Maria: 'we need the second task for the pilot too'"


class PlanReleaseWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def apply(self, text: str, *extra: str):
        return self.world.run("create", "--from-file", "-", *extra, stdin=text)

    def by_title(self) -> dict:
        return {row["title"]: row for row in listing(self.world)}

    def types(self, title: str) -> list:
        return [event["type"] for event in self.world.events(self.by_title()[title]["id"])]

    def test_a_draft_plan_files_every_item_as_a_draft_and_nothing_is_ready(self) -> None:
        """A plan filed for review must not leak into ready work."""
        text = jsonl({"ref": "e", "kind": "epic", "title": "Epic"}, {"kind": "issue", "title": "Task", "parent": "@e"})
        result = self.apply(text, "--draft")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.types("Epic"), self.types("Task")), (["drafted"], ["drafted"]))
        self.assertEqual(self.world.run("ready").stdout, "")

    def test_an_approved_line_under_draft_is_refused_naming_the_flag_and_nothing_is_written(self) -> None:
        """An approved line must not quietly win over the reviewer's `--draft`."""
        before = snapshot(self.world)
        text = jsonl({"title": "Fine"}, {"title": "Pushy", "quote": QUOTE, "approved": True})
        result = self.apply(text, "--draft")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--draft", result.stderr)
        self.assertEqual(snapshot(self.world), before)

    def test_each_entry_carries_its_own_quote_and_an_entry_without_one_is_a_draft(self) -> None:
        """One human's words must never be attributed to work another human asked for."""
        text = jsonl(
            {"title": "First", "quote": QUOTE},
            {"title": "Second", "quote": OTHER},
            {"title": "Unreleased"},
        )
        self.assertEqual(self.apply(text).returncode, 0)
        quotes = {title: [e.get("quote") for e in self.world.events(row["id"])] for title, row in self.by_title().items()}
        self.assertEqual(quotes, {"First": [QUOTE], "Second": [OTHER], "Unreleased": [None]})
        self.assertEqual(self.types("Unreleased"), ["drafted"])

    def test_the_top_level_quote_is_the_default_for_entries_without_their_own(self) -> None:
        """A default quote releases the unquoted entries; an entry keeps its own words over the default."""
        text = jsonl({"title": "Plain"}, {"title": "Own", "quote": OTHER})
        self.assertEqual(self.apply(text, "--quote", QUOTE).returncode, 0)
        self.assertEqual(self.world.events(self.by_title()["Plain"]["id"])[0]["quote"], QUOTE)
        self.assertEqual(self.world.events(self.by_title()["Own"]["id"])[0]["quote"], OTHER)

    def test_a_bad_quote_on_line_two_is_refused_before_line_one_is_written(self) -> None:
        """A half-written plan would hold approved work next to a refused line."""
        before = snapshot(self.world)
        result = self.apply(jsonl({"title": "One", "quote": QUOTE}, {"title": "Two", "quote": "Maria: 'ok'"}))
        self.assertEqual(result.returncode, 2)
        self.assertIn("line 2", result.stderr)
        self.assertEqual(snapshot(self.world), before)

    def test_approved_without_any_quote_and_draft_with_a_default_quote_are_refused(self) -> None:
        """`approved: true` claims a release that no human words back; `--draft --quote` contradicts itself."""
        before = snapshot(self.world)
        no_words = self.apply(jsonl({"title": "A", "approved": True}))
        self.assertEqual(no_words.returncode, 2)
        self.assertIn("line 1", no_words.stderr)
        both = self.apply(jsonl({"title": "A"}), "--draft", "--quote", QUOTE)
        self.assertEqual(both.returncode, 2)
        self.assertEqual(snapshot(self.world), before)


if __name__ == "__main__":
    unittest.main()
