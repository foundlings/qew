"""Two clones that each completed the same item merge into one readable ledger.

Adversary: the same item completed on two machines before either saw the other's push (a union landed on one while an agent ran
`qew complete` on the other): git merges the two event files cleanly, and the fold then called the second `completed` illegal, which
made every read of the whole ledger fail with "the ledger is not readable".
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_ids
from tests.ledger_harness import World


class DuplicateCompletionWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.item = self.world.create("--title", "Done twice")
        self.assertEqual(self.world.run("start", self.item).returncode, 0)
        self.sha = self.world.commit("work")
        self.assertEqual(self.world.run("evidence", self.item, "--change", self.sha).returncode, 0)
        self.assertEqual(self.world.run("complete", self.item).returncode, 0)

    def merge_in_a_second_completion(self) -> None:
        """What a git merge of the other clone's completion leaves: one more `completed` event file, later id."""
        directory = self.world.home("events", self.item)
        names = sorted(p.name for p in directory.glob("*.json"))
        first = next(directory / n for n in names if json.loads((directory / n).read_text())["type"] == "completed")
        body = json.loads(first.read_text())
        body["at"] = "2099-01-01T00:00:00Z"
        later = ledger_ids.event_id(names[-1][:-5])
        (directory / f"{later}.json").write_text(json.dumps(body, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    def test_a_second_completion_of_a_completed_item_leaves_every_read_working(self) -> None:
        """Prevents one duplicated `completed` event making the whole ledger unreadable for the fleet."""
        self.merge_in_a_second_completion()
        for argv in (("show", self.item), ("list",), ("ready",), ("check",)):
            run = self.world.run(*argv)
            self.assertEqual(run.returncode, 0, f"{argv}: {run.stderr}")
        self.assertIn("completed", self.world.run("show", self.item).stdout)

    def test_a_completion_after_a_close_is_still_an_error(self) -> None:
        """Prevents the leniency hiding a real contradiction: completing a closed item stays illegal."""
        closed = self.world.create("--title", "Closed one")
        self.assertEqual(self.world.run("close", closed, "--reason", "not_planned").returncode, 0)
        directory = self.world.home("events", self.item)
        body = json.loads(next(p for p in sorted(directory.glob("*.json")) if json.loads(p.read_text())["type"] == "completed").read_text())
        target = self.world.home("events", closed)
        later = ledger_ids.event_id(sorted(p.stem for p in target.glob("*.json"))[-1])
        (target / f"{later}.json").write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
        self.assertNotEqual(self.world.run("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
