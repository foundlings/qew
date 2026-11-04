"""Batch landing walks: one refused PR must not hide or prevent independent completions."""

import json
import unittest

from tests.pr_harness import PrWorld


class LandBatchWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)

    def pull(self, title: str, merged: bool = True) -> tuple[int, str]:
        item = self.w.item(title)
        number = len(self.w.forge.pulls) + 1
        self.w.head = f"feature-{number}"
        self.assertEqual(self.w.open(item).code, 0)
        sha = self.w.commit(title)
        self.w.forge.edit_pull(number, head_sha=sha, state="merged" if merged else "open", draft=False)
        return number, item

    def state(self, item: str) -> str:
        return self.w.qew("show", item).out.split("state: ")[1].split()[0]

    def test_batch_completes_in_input_order_deduplicates_and_retries_without_new_events(self) -> None:
        """Prevents duplicate input or retries generating repeated completions or evidence."""
        first, a = self.pull("First")
        second, b = self.pull("Second")
        run = self.w.qew("land", str(second), str(first), str(second), "--json")
        self.assertEqual(run.code, 0, run)
        self.assertTrue(run.out, run)
        rows = json.loads(run.out)["results"]
        self.assertEqual([row["number"] for row in rows], [second, first])
        self.assertEqual([row["landed"] for row in rows], [[b], [a]])
        counts = [len(self.w.events(item)) for item in (a, b)]
        retried = self.w.qew("land", str(first), str(second))
        self.assertEqual(retried.code, 0, retried)
        self.assertIn(f"pr #{first}:", retried.out)
        self.assertIn(f"pr #{second}:", retried.out)
        self.assertEqual([len(self.w.events(item)) for item in (a, b)], counts)

    def test_unmerged_and_missing_prs_are_reported_without_stopping_later_completions(self) -> None:
        """Prevents one refusal aborting a batch or disappearing from machine output."""
        first, a = self.pull("Open", merged=False)
        second, b = self.pull("Merged")
        run = self.w.qew("land", str(first), "999", str(second), "--json")
        self.assertEqual(run.code, 2, run)
        self.assertTrue(run.out, run)
        rows = json.loads(run.out)["results"]
        self.assertEqual([row["number"] for row in rows], [first, 999, second])
        self.assertEqual([row["exit_code"] for row in rows], [2, 2, 0])
        self.assertIn("not merged", rows[0]["error"])
        self.assertEqual(rows[2]["landed"], [b])
        self.assertEqual(self.state(a), "claimed")
        self.assertEqual(self.state(b), "completed")

    def test_an_item_completion_failure_keeps_other_prs_landed(self) -> None:
        """Prevents a failed item gate rolling back successful members of a batch."""
        first, a = self.pull("Has unfinished child")
        self.w.item("Child", kind="task", parent=a, claim=False)
        second, b = self.pull("Independent")
        run = self.w.qew("land", str(first), str(second), "--json")
        self.assertEqual(run.code, 2, run)
        self.assertTrue(run.out, run)
        rows = json.loads(run.out)["results"]
        self.assertEqual(rows[0]["failures"], 1)
        self.assertEqual(rows[1]["landed"], [b])
        self.assertEqual(self.state(a), "claimed")
        self.assertEqual(self.state(b), "completed")

    def test_held_items_make_batch_incomplete_and_integrator_reason_applies_to_every_pr(self) -> None:
        """Prevents exit zero masking held work or a batch bypassing takeover auditing."""
        first, a = self.pull("First")
        second, b = self.pull("Second")
        refused = self.w.qew("land", str(first), str(second), "--json", actor="rudolph")
        self.assertEqual(refused.code, 2, refused)
        self.assertTrue(refused.out, refused)
        self.assertEqual([row["held"][0]["id"] for row in json.loads(refused.out)["results"]], [a, b])
        run = self.w.qew("land", str(first), str(second), "--integrator", "--reason", "union sweep", actor="rudolph")
        self.assertEqual(run.code, 0, run)
        for item in (a, b):
            completed = [event for event in self.w.events(item) if event["type"] == "completed"]
            self.assertEqual(len(completed), 1)
            self.assertIn("union sweep", str(completed[0]))

    def test_invalid_numbers_and_options_refuse_the_whole_input_before_writes(self) -> None:
        """Prevents a malformed batch being partly applied before discovering bad input."""
        first, a = self.pull("First")
        second, b = self.pull("Second")
        for extra in (("0",), ("--integrator",), ("--reason", "unexplained")):
            run = self.w.qew("land", str(first), str(second), *extra)
            self.assertEqual(run.code, 2, run)
            self.assertEqual([self.state(item) for item in (a, b)], ["claimed", "claimed"])
