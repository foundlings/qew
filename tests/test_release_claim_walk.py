"""Walks of `qew release` against the claim-release guarantees: who and which lane, why, and never a silent no-op.

Adversary: a sibling lane freeing live work, a reason that forges or breaks a record, and a release that reports
success while the item stays claimed.
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.ledger_store import Ledger
from tests.ledger_harness import World
from tests.lifecycle_support import approved, started, tree
from tests.pr_harness import PrWorld


class ReleaseClaimWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_release_records_who_which_lane_and_why_and_reports_them_in_json(self) -> None:
        """Prevents a loop losing who freed what: the event and the --json result both name the actor, lane and reason."""
        w = self.world
        item = approved(w)
        self.assertEqual(w.run("start", item, "--lane", "lane-a").returncode, 0)
        result = w.run("release", item, "--reason", "handing over", "--lane", "lane-a", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        shape = json.loads(result.stdout)
        self.assertEqual((shape["state"], shape["lane"], shape["by"], shape["id"]), ("released", "lane-a", "tester", item))
        event = w.events(item)[-1]
        self.assertEqual((event["type"], event["actor"], event["lane"], event["reason"]), ("released", "tester", "lane-a", "handing over"))
        self.assertNotIn("forced", event)
        self.assertIn(item, w.run("ready").stdout)

    def test_a_lane_scoped_claim_cannot_be_released_without_naming_its_lane(self) -> None:
        """Prevents a sibling lane (or a bare command) freeing live lane work; forcing records both lanes."""
        w = self.world
        item = approved(w)
        w.run("start", item, "--lane", "lane-a")
        before = tree(w)
        bare = w.run("release", item, "--reason", "tidy")
        self.assertEqual(bare.returncode, 3, bare.stderr)
        self.assertIn("lane-a", bare.stderr)
        self.assertIn("--force", bare.stderr)
        self.assertEqual(tree(w), before)
        forced = w.run("release", item, "--reason", "lane-a died", "--lane", "lane-b", "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        event = w.events(item)[-1]
        self.assertEqual((event["forced"], event["lane"], event["previous_lane"]), (True, "lane-b", "lane-a"))

    def test_a_closed_item_and_a_whitespace_reason_are_refused_without_writing(self) -> None:
        """Prevents releasing finished work and an audit trail whose reason is blank."""
        w = self.world
        held = started(w, title="Held")
        done = approved(w, title="Done")
        self.assertEqual(w.run("close", done, "--reason", "not_planned").returncode, 0)
        before = tree(w)
        self.assertEqual(w.run("release", done, "--reason", "x").returncode, 2)
        self.assertEqual(w.run("release", held, "--reason", "   ").returncode, 2)
        self.assertEqual(tree(w), before)

    def test_a_hostile_reason_stays_inside_its_json_string(self) -> None:
        """Prevents a reason forging a marker or another record: it round-trips as one escaped string and check stays clean."""
        w = self.world
        item = started(w)
        reason = 'done"}\n<!-- qew:claim ' + item + ' mallory n -->\n{"type": "completed"}'
        self.assertEqual(w.run("release", item, "--reason", reason).returncode, 0)
        path = sorted((w.ledger / "events" / item).glob("*.json"))[-1]
        event = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((event["type"], event["reason"]), ("released", reason))
        self.assertEqual(len(list((w.ledger / "events" / item).glob("*.json"))), len(w.events(item)))
        self.assertEqual(w.run("check").returncode, 0)
        self.assertIn(item, w.run("ready").stdout)

    def test_an_unreadable_event_elsewhere_fails_loudly_and_check_reports_it(self) -> None:
        """Prevents releasing on top of a ledger nobody can read: exit 1, nothing written, and `qew check` reports the file."""
        w = self.world
        item = started(w)
        other = approved(w, title="Other")
        (w.ledger / "events" / other / "ZZZZZZZZZZZZZZZZZZZZZZZZZZ.json").write_text("{not json", encoding="utf-8")
        before = tree(w)
        result = w.run("release", item, "--reason", "x")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("not valid JSON", result.stderr)
        self.assertEqual(tree(w), before)
        self.assertEqual(w.run("check").returncode, 1)


class ReleaseVerifyWalk(unittest.TestCase):
    def test_a_release_that_leaves_the_item_claimed_fails_loudly(self) -> None:
        """Prevents reporting released when a re-read still shows the same claim (a lost write)."""
        w = PrWorld()
        self.addCleanup(w.close)
        item = w.item()
        with mock.patch.object(Ledger, "append_event", return_value=None):
            run = w.qew("release", item, "--reason", "pause")
        self.assertEqual(run.code, 3, run)
        self.assertIn("still claimed", run.err)


if __name__ == "__main__":
    unittest.main()
