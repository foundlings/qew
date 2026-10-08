"""Claims whose pull request already merged are reported, never silently fixed."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import World
from tests.lifecycle_support import started, tree


class StaleClaimWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def record(self, item: str, number: int, **fields) -> None:
        directory = self.world.ledger / "mirrors" / item
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"forgejo-x-o-r-pr-{number}.json").write_text(json.dumps(
            {"forge": "forgejo", "url": "https://x/o/r", "kind": "pr", "number": number, **fields}))

    def test_a_claim_whose_pull_request_merged_is_reported_with_the_fix_and_nothing_is_written(self) -> None:
        """Prevents leftovers nobody sees: check notes it and list flags it."""
        w = self.world
        stale, open_pr, done = started(w, title="Stale"), started(w, title="Open"), started(w, title="Done")
        self.record(stale, 3, state="merged")
        self.record(open_pr, 4, state="open")
        w.run("evidence", done, "--change", w.commit("d"))
        w.run("complete", done)
        self.record(done, 5, state="merged")
        before = tree(w)
        checked = w.run("check")
        self.assertEqual(checked.returncode, 0, checked.stdout)
        self.assertIn(f"claimed but its pull request merged: {stale}", checked.stdout)
        self.assertNotIn(open_pr, checked.stdout)
        self.assertNotIn(done, checked.stdout)
        rows = [json.loads(line) for line in w.run("list", "--state", "claimed", "--json").stdout.splitlines()]
        flagged = {row["id"]: row.get("stale") for row in rows}
        self.assertEqual(flagged, {stale: True, open_pr: False})
        self.assertIn("qew land 3", w.run("list", "--state", "claimed").stdout)
        self.assertEqual(tree(w), before)


if __name__ == "__main__":
    unittest.main()
