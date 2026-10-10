"""Walks of the requester-quote refusal table on every entry that takes a quote.

Adversary: an unattended agent satisfying the human-release gate with junk (empty, short, unattributed,
placeholder, multi-line), or the gate firing only after a file was already written.
"""

import json
import unittest

from tests.ledger_harness import QUOTE, World

BAD_QUOTES = {
    "empty": "",
    "too short": "A: 'ok'",
    "no name": "'please track this work in the Qew ledger now'",
    "unquoted": "Alexander please track this work in the Qew ledger now",
    "placeholder": "Alexander: '<their words go here, honestly>'",
    "dots": "Alexander: '..................'",
    "todo": "Alexander: 'TODO: ask the human what they want'",
    "multi-line": "Alexander: 'please track this work\nin the Qew ledger now'",
}


def tree(world: World) -> list:
    return sorted(str(path.relative_to(world.ledger)) for path in world.ledger.rglob("*") if path.is_file() and ".qew" not in path.parts)


class QuoteWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_create_quote_table_refuses_every_junk_form_and_writes_no_file(self) -> None:
        """Junk satisfying `create --quote` would mint approved work with no human behind it."""
        before = tree(self.world)
        for name, quote in BAD_QUOTES.items():
            result = self.world.run("create", "--title", "Work", "--quote", quote)
            self.assertEqual(result.returncode, 2, name)
            self.assertIn("Name: 'their words'" if name == "empty" else "quote", result.stderr, name)
        self.assertEqual(tree(self.world), before)

    def test_create_without_a_quote_drafts_and_with_one_approves_with_the_trimmed_words(self) -> None:
        """Approved work must carry the exact words that released it; a missing quote must stay a draft."""
        w = self.world
        draft = w.create("--title", "Unreleased", "--draft")
        released = w.create("--title", "Released", "--quote", f"  {QUOTE}  ")
        self.assertEqual([e["type"] for e in w.events(draft)], ["drafted"])
        self.assertEqual([e["type"] for e in w.events(released)], ["approved"])
        self.assertEqual(w.events(released)[0]["quote"], QUOTE)

    def test_from_file_with_a_bad_quote_writes_nothing_and_a_good_one_releases_every_item(self) -> None:
        """A half-approved file would release some items on words that never covered the rest."""
        w = self.world
        specs = json.dumps([{"title": "One"}, {"title": "Two"}])
        before = tree(w)
        refused = w.run("create", "--from-file", "-", "--quote", "Alexander: 'ok'", stdin=specs)
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(tree(w), before)
        accepted = w.run("create", "--from-file", "-", "--quote", QUOTE, stdin=specs)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        ids = [line.split()[0] for line in accepted.stdout.splitlines()]
        self.assertEqual([w.events(i)[0]["type"] for i in ids], ["approved", "approved"])

    def test_approve_quote_table_leaves_the_item_a_draft(self) -> None:
        """An approval with no human words must never produce an `approved` event."""
        w = self.world
        item = w.create("--title", "Needs a human", "--draft")
        result = w.run("draft", "approve", item)
        self.assertNotEqual(result.returncode, 0, "the quote flag is required")
        for name, quote in BAD_QUOTES.items():
            result = w.run("draft", "approve", item, "--quote", quote)
            self.assertEqual(result.returncode, 2, name)
        self.assertEqual([e["type"] for e in w.events(item)], ["drafted"])

    def test_revert_quote_table_writes_nothing(self) -> None:
        """Withdrawing approved work on an invented quote would be as bad as releasing on one."""
        w = self.world
        item = w.create("--title", "Live", "--quote", QUOTE)
        for name, quote in BAD_QUOTES.items():
            result = w.run("draft", "revert", item, "--reason", "changed our minds", "--quote", quote)
            self.assertEqual(result.returncode, 2, name)
        self.assertEqual([e["type"] for e in w.events(item)], ["approved"])

    def test_a_bad_quote_is_reported_before_an_unknown_item_is_resolved(self) -> None:
        """Validation order: the quote problem comes first so no resolution side effect precedes it."""
        w = self.world
        for args in (("approve", "nothere"), ("revert", "nothere", "--reason", "why not")):
            result = w.run("draft", *args, "--quote", "Alexander: 'ok'")
            self.assertEqual(result.returncode, 2)
            self.assertIn("quote", result.stderr)
            self.assertNotIn("no item matches", result.stderr)


if __name__ == "__main__":
    unittest.main()
