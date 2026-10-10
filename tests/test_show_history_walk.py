"""Walks of who asked for an item and why it was released or withdrawn, as `qew show` reports it.

Adversary: work with no visible requester, and a revert nobody can explain without opening event files.
"""

import json
import unittest

from tests.ledger_harness import QUOTE, World

REVERT = "Maria: 'pause this until the pilot is decided'"


class ShowHistoryWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_an_approved_item_shows_the_exact_quote_and_the_approver(self) -> None:
        """Work without a visible requester cannot be questioned."""
        w = self.world
        item = w.create("--title", "Work", "--quote", QUOTE)
        text = w.run("show", item).stdout
        self.assertIn(f"released: {QUOTE} (by tester at ", text)
        data = json.loads(w.run("show", item, "--json").stdout)
        self.assertEqual(len(data["approvals"]), 1)
        self.assertEqual({k: data["approvals"][0][k] for k in ("type", "actor", "quote")}, {"type": "approved", "actor": "tester", "quote": QUOTE})

    def test_a_reverted_item_shows_the_reason_and_the_reverting_quote_and_is_a_draft(self) -> None:
        """An unexplained revert sends the next reader to the event files."""
        w = self.world
        item = w.create("--title", "Work", "--quote", QUOTE)
        w.run("draft", "revert", item, "--reason", "pilot undecided", "--quote", REVERT)
        text = w.run("show", item).stdout
        self.assertIn("state: draft", text)
        self.assertIn(f"withdrawn: pilot undecided - {REVERT} (by tester at ", text)
        self.assertNotIn("released:", text)
        kinds = [a["type"] for a in json.loads(w.run("show", item, "--json").stdout)["approvals"]]
        self.assertEqual(kinds, ["approved", "reverted"])
        self.assertEqual(json.loads(w.run("show", item, "--json").stdout)["approvals"][1]["reason"], "pilot undecided")

    def test_a_plain_draft_has_no_release_lines_and_says_draft(self) -> None:
        """A draft must be distinguishable from released work at a glance."""
        w = self.world
        item = w.create("--title", "Work", "--draft")
        text = w.run("show", item).stdout
        self.assertIn("state: draft", text)
        self.assertNotIn("released:", text)
        self.assertNotIn("withdrawn:", text)
        self.assertEqual(json.loads(w.run("show", item, "--json").stdout).get("approvals", []), [])


if __name__ == "__main__":
    unittest.main()
