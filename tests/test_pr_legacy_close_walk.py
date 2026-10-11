"""`qew pr close N --item ID --reason TEXT` closes an unmerged pull request qew never recorded (a legacy draft) and records it
on the item that carries its remainder. A merged one stays refused.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.forge import PullRequestSpec
from tests.pr_harness import PrWorld


class LegacyCloseWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item("Remainder", claim=False)
        self.legacy = self.w.forge.open_pull_request(PullRequestSpec("old-branch", "main", "Old draft", "pre-qew body", True, "someone")).number

    def mirror(self) -> dict:
        return next(m for m in self.w.ledger.load_mirrors(self.item) if m["_target"].endswith(f"-pr-{self.legacy}"))

    def test_an_unrecorded_pull_request_is_closed_and_recorded_on_the_named_item(self) -> None:
        """Prevents legacy drafts living on forever: --item adopts the pull request, closes it and keeps the reason in the ledger."""
        run = self.w.qew("pr", "close", str(self.legacy), "--item", self.item, "--reason", "carried by #4375")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.w.forge.pulls[self.legacy].state, "closed")
        record = self.mirror()
        self.assertEqual((record["kind"], record["state"], record["number"], record["items"]), ("pr", "closed", self.legacy, [self.item]))
        self.assertIn("carried by #4375", json.dumps(self.w.events(self.item)))
        again = self.w.qew("pr", "close", str(self.legacy), "--item", self.item, "--reason", "carried by #4375")
        self.assertEqual(again.code, 0, again)

    def test_the_refusals_stay_and_say_what_to_pass(self) -> None:
        """Prevents a quiet adoption: no --item, no reason, an unknown item or a merged pull request are all refused."""
        plain = self.w.qew("pr", "close", str(self.legacy))
        self.assertEqual(plain.code, 2, plain)
        self.assertIn("--item", plain.err)
        no_reason = self.w.qew("pr", "close", str(self.legacy), "--item", self.item)
        self.assertEqual(no_reason.code, 2, no_reason)
        unknown = self.w.qew("pr", "close", str(self.legacy), "--item", "nope", "--reason", "x")
        self.assertEqual(unknown.code, 2, unknown)
        self.assertEqual(self.w.forge.pulls[self.legacy].state, "open")
        self.w.forge.pulls[self.legacy] = self.w.forge._with(self.legacy, state="merged", merge_sha="m" * 40)
        merged = self.w.qew("pr", "close", str(self.legacy), "--item", self.item, "--reason", "carried")
        self.assertEqual(merged.code, 2, merged)
        self.assertEqual(self.w.mirror_files(self.item), [])

    def test_a_recorded_pull_request_does_not_take_a_second_item(self) -> None:
        """Prevents re-homing a pull request qew already tracks: --item is only for the unrecorded."""
        other = self.w.item("Other")
        self.assertEqual(self.w.open(other).code, 0)
        run = self.w.qew("pr", "close", "2", "--item", self.item, "--reason", "x")
        self.assertEqual(run.code, 2, run)


if __name__ == "__main__":
    unittest.main()
