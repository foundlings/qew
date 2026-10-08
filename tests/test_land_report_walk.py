"""`qew land` as a loop can use it: a versioned result, a quiet repeat, stacked merges and unusable merge data.

Adversary: callers parsing prose, a retry completing twice, completing work whose merge did not reach the
target branch, landing on a merge commit that is not an id, and silently taking over a teammate's claim.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.lifecycle_support import event_types
from tests.pr_harness import PrWorld


class LandReportWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item("Work")
        self.assertEqual(self.w.open(self.item).code, 0)
        self.sha = self.w.commit("work")
        self.w.forge.edit_pull(1, head_sha=self.sha, draft=False, state="merged")

    def test_json_lists_landed_held_and_clean_items_exactly(self) -> None:
        """Prevents callers parsing prose: the holder of a held item is named and nothing of theirs is touched."""
        w = self.w
        theirs = w.item("Theirs", claim=False)
        w.qew("start", theirs, actor="other")
        finished = w.item("Finished")
        w.qew("evidence", finished, "--change", self.sha)
        w.qew("complete", finished)
        w.world.ledger  # records are read from the ledger's own pull request files
        for item in (theirs, finished):
            self.extend_record(item)
        run = w.qew("land", "1", "--json")
        self.assertEqual(run.code, 0, run)
        result = json.loads(run.out)
        self.assertEqual((result["schema_version"], result["state"]), (1, "merged"))
        self.assertEqual(result["landed"], [self.item])
        self.assertEqual(result["held"], [{"id": theirs, "holder": "other"}])
        self.assertEqual(result["clean"], [finished])
        self.assertNotIn("completed", event_types(w.world, theirs))

    def extend_record(self, item: str) -> None:
        """Make the pull request cover `item` too, the way an epic pull request records its covered items."""
        directory = self.w.world.ledger / "mirrors" / self.item
        record = next(directory.glob("*-pr-1.json"))
        data = json.loads(record.read_text())
        data["items"] = sorted({*data.get("items", [self.item]), item})
        record.write_text(json.dumps(data))

    def test_a_held_item_is_reported_with_the_take_over_command(self) -> None:
        """Prevents automation silently taking over a teammate's claim."""
        w = self.w
        w.qew("release", self.item, "--reason", "handover")
        w.qew("start", self.item, actor="other")
        run = w.qew("land", "1")
        self.assertEqual(run.code, 0, run)
        self.assertIn(f"qew complete {self.item} --take-over", run.out)

    def test_a_second_run_says_there_is_nothing_to_land_and_writes_nothing(self) -> None:
        """Prevents duplicate completion when a hook retries."""
        self.w.qew("land", "1")
        before = len(self.w.events(self.item))
        again = self.w.qew("land", "1")
        self.assertEqual(again.code, 0, again)
        self.assertIn("nothing to land", again.out)
        self.assertEqual(len(self.w.events(self.item)), before)

    def test_a_merge_into_a_stacked_branch_leaves_the_item_open(self) -> None:
        """Prevents completing work that has not reached the target branch."""
        self.w.forge.edit_pull(1, base_branch="stack")
        before = len(self.w.events(self.item))
        run = self.w.qew("land", "1")
        self.assertEqual(run.code, 0, run)
        self.assertIn("still open", run.out)
        self.assertEqual(len(self.w.events(self.item)), before)

    def test_a_merge_head_that_is_not_an_id_is_indeterminate_and_writes_nothing(self) -> None:
        """Prevents landing on guessed data."""
        self.w.forge.edit_pull(1, head_sha="not-a-commit-id")
        before = len(self.w.events(self.item))
        run = self.w.qew("land", "1")
        self.assertEqual(run.code, 4, run)
        self.assertEqual(len(self.w.events(self.item)), before)

    def test_a_pull_request_number_that_is_not_positive_is_refused(self) -> None:
        """Prevents argument confusion."""
        for value in ("0", "-3", "abc"):
            self.assertEqual(self.w.qew("land", value).code, 2, value)


if __name__ == "__main__":
    unittest.main()
