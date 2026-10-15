"""Lanes, take-over and the claim cap: the extra ways a claim is held, taken and limited.

Adversary: an unexplained takeover, freeing work and then failing to claim it, a sibling lane of one actor
silently believing it owns an item, junk in the claim record, and an agent fleet claiming without limit.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import World
from tests.lifecycle_support import approved, started, tree

BOB = {"QEW_ACTOR": "bob"}
WORKSPACE = "/work/lane-a"


class TakeOverWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_take_over_moves_the_claim_and_records_whose_it_was(self) -> None:
        """Prevents a takeover that leaves no record: a forced release names the previous owner, then a claim."""
        w = self.world
        item = started(w)
        result = w.run("start", item, "--take-over", "--reason", "tester is offline", env=BOB)
        self.assertEqual(result.returncode, 0, result.stderr)
        released, claimed = w.events(item)[-2:]
        self.assertEqual((released["type"], released["forced"], released["previous_owner"], released["reason"]),
                         ("released", True, "tester", "tester is offline"))
        self.assertEqual((claimed["type"], claimed["owner"]), ("claimed", "bob"))
        self.assertIn("owner: bob", w.run("show", item).stdout)

    def test_take_over_without_a_reason_or_without_a_claim_to_take_writes_nothing(self) -> None:
        """Prevents an unexplained takeover and a takeover of work nobody else holds."""
        w = self.world
        held = started(w, title="Held")
        idle = approved(w, title="Idle")
        before = tree(w)
        no_reason = w.run("start", held, "--take-over", env=BOB)
        own = w.run("start", held, "--take-over", "--reason", "mine")
        unclaimed = w.run("start", idle, "--take-over", "--reason", "x")
        self.assertEqual([r.returncode for r in (no_reason, own, unclaimed)], [2, 2, 2])
        self.assertIn("nothing to take over", own.stderr)
        self.assertIn("nothing to take over", unclaimed.stderr)
        self.assertEqual(tree(w), before)

    def test_take_over_of_work_with_an_open_need_records_the_reason_and_the_previous_holder(self) -> None:
        """Prevents a handoff losing who held the claim and why, when the work gained an open need after the claim."""
        w = self.world
        item = started(w)
        blocker = approved(w, title="Blocker")
        self.assertEqual(w.run("set", item, "--needs", blocker).returncode, 0)
        result = w.run("start", item, "--take-over", "--reason", "handoff", env=BOB)
        self.assertEqual(result.returncode, 0, result.stderr)
        released = [e for e in w.events(item) if e["type"] == "released"][-1]
        self.assertEqual((released["reason"], released["previous_owner"], released["forced"]), ("handoff", "tester", True))
        self.assertIn(blocker, w.run("show", item).stdout)


class LaneWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_a_lane_claim_stores_lane_workspace_and_kind_and_show_exposes_it(self) -> None:
        """Prevents a lane that its sibling lanes cannot see."""
        w = self.world
        item = approved(w)
        result = w.run("start", item, "--lane", "lane-a", "--workspace", WORKSPACE, "--kind", "token")
        self.assertEqual(result.returncode, 0, result.stderr)
        claimed = w.events(item)[-1]
        self.assertEqual((claimed["lane"], claimed["workspace"], claimed["kind"]), ("lane-a", WORKSPACE, "token"))
        shown = json.loads(w.run("show", item, "--json").stdout)
        self.assertEqual(shown.get("claim", "no claim key"), {"by": "tester", "kind": "token", "lane": "lane-a", "workspace": WORKSPACE})
        self.assertIn("lane-a", w.run("show", item).stdout)

    def test_a_claim_without_kind_reads_as_compute_and_unclaimed_work_has_a_null_claim(self) -> None:
        """Prevents ambiguous defaults."""
        w = self.world
        item = approved(w)
        self.assertIsNone(json.loads(w.run("show", item, "--json").stdout).get("claim", "no claim key"))
        w.run("start", item, "--lane", "lane-a")
        claim = json.loads(w.run("show", item, "--json").stdout).get("claim") or {}
        self.assertEqual((claim.get("kind"), claim.get("workspace")), ("compute", None))

    def test_malformed_lane_fields_are_refused_before_any_write(self) -> None:
        """Prevents junk in the claim record."""
        w = self.world
        item = approved(w)
        before = tree(w)
        bad = (
            ["--lane", "a--b"], ["--lane=-x"], ["--lane", "x" * 65], ["--lane", "has space"],
            ["--lane", "ok", "--workspace", "relative/path"], ["--lane", "ok", "--workspace", "/a--b"],
            ["--workspace", WORKSPACE], ["--kind", "token"], ["--lane", "ok", "--kind", "gpu"],
        )
        for arguments in bad:
            result = w.run("start", item, *arguments)
            self.assertEqual(result.returncode, 2, (arguments, result.stderr))
        self.assertEqual(tree(w), before)

    def test_a_hand_edited_claim_with_a_malformed_lane_fails_check_naming_the_event(self) -> None:
        """Prevents silently ignored bad data in a merged ledger."""
        w = self.world
        item = started(w)
        event = sorted((w.ledger / "events" / item).glob("*.json"))[-1]
        data = json.loads(event.read_text())
        data["lane"] = "a--b"
        event.write_text(json.dumps(data))
        checked = w.run("check")
        self.assertEqual(checked.returncode, 1)
        self.assertIn(event.name, checked.stdout + checked.stderr)

    def test_a_second_lane_of_the_same_actor_is_refused_naming_the_holding_lane(self) -> None:
        """Prevents a sibling lane believing it owns work another lane holds."""
        w = self.world
        item = approved(w)
        w.run("start", item, "--lane", "lane-a")
        before = tree(w)
        other = w.run("start", item, "--lane", "lane-b")
        self.assertEqual(other.returncode, 3, other.stderr)
        self.assertIn("lane-a", other.stderr)
        laneless = w.run("start", item)
        self.assertEqual(laneless.returncode, 3, laneless.stderr)
        self.assertIn("lane-a", laneless.stderr)
        same = w.run("start", item, "--lane", "lane-a")
        self.assertEqual(same.returncode, 5, same.stderr)
        self.assertEqual(tree(w), before)

    def test_a_lane_start_on_your_laneless_claim_is_refused_as_without_a_lane(self) -> None:
        """Prevents a loop reading exit 5 as 'I hold it' for a claim made by a lane-less sibling."""
        w = self.world
        item = started(w)
        result = w.run("start", item, "--lane", "lane-a")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("without a lane", result.stderr)

    def test_releasing_another_lanes_claim_needs_force_and_is_recorded(self) -> None:
        """Prevents freeing a sibling lane's work by accident."""
        w = self.world
        item = approved(w)
        w.run("start", item, "--lane", "lane-a")
        before = tree(w)
        refused = w.run("release", item, "--reason", "tidy", "--lane", "lane-b")
        self.assertEqual(refused.returncode, 3, refused.stderr)
        self.assertEqual(tree(w), before)
        forced = w.run("release", item, "--reason", "lane-a died", "--lane", "lane-b", "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertTrue(w.events(item)[-1]["forced"])


class ClaimCapWalk(unittest.TestCase):
    def capped(self, value: str) -> World:
        world = World(f"\n[claims]\nmax_active_claims = {value}\n")
        self.addCleanup(world.close)
        return world

    def test_start_at_the_cap_is_refused_before_any_write_and_below_it_claims(self) -> None:
        """Prevents runaway claiming: the second claim is refused with exit 6, a released slot is reusable."""
        w = self.capped("1")
        first = approved(w, title="First")
        second = approved(w, title="Second")
        self.assertEqual(w.run("start", first).returncode, 0)
        before = tree(w)
        refused = w.run("start", second)
        self.assertEqual(refused.returncode, 6, refused.stderr)
        self.assertIn("max_active_claims", refused.stderr)
        self.assertEqual(tree(w), before)
        self.assertEqual(w.run("start", second, env=BOB).returncode, 0)
        w.run("release", first, "--reason", "free the slot")
        self.assertEqual(w.run("start", second).returncode, 3)  # bob holds it; the cap is per actor, not global
        third = approved(w, title="Third")
        self.assertEqual(w.run("start", third).returncode, 0)

    def test_take_over_counts_toward_the_takers_cap(self) -> None:
        """Prevents bypassing the cap through take-over."""
        w = self.capped("1")
        mine = approved(w, title="Mine")
        theirs = approved(w, title="Theirs")
        w.run("start", mine, env=BOB)
        w.run("start", theirs)
        before = tree(w)
        result = w.run("start", mine, "--take-over", "--reason", "x")
        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertEqual(tree(w), before)

    def test_a_malformed_cap_is_invalid_configuration_naming_the_key(self) -> None:
        """Prevents a typo silently disabling or breaking the cap."""
        for value in ("0", "-1", '"3"', "true", "2.5"):
            w = self.capped(value)
            result = w.run("list")
            self.assertEqual(result.returncode, 2, (value, result.stdout, result.stderr))
            self.assertIn("max_active_claims", result.stderr)


if __name__ == "__main__":
    unittest.main()
