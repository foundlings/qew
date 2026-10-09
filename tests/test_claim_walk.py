"""Claims: who holds an item, how a start or release is refused, and what a double claim folds to.

Adversary: stealing or freeing a sibling's claim by accident, a loop mistaking someone else's claim for its
own, a concurrent double claim that invalidates the whole ledger, and completion by a non-holder.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.ledger_ids import event_id
from tests.ledger_harness import World, git
from tests.lifecycle_support import approved, event_types, started, tree

ALICE = {"QEW_ACTOR": "alice"}
BOB = {"QEW_ACTOR": "bob"}


def inject(world: World, item: str, event_type: str, **data) -> None:
    """Write an event file the way a merge of another branch would bring it in: a later event id."""
    directory = world.ledger / "events" / item
    newest = sorted(path.stem for path in directory.glob("*.json"))[-1]
    body = {"type": event_type, "at": "2026-01-01T00:00:00Z", "actor": data.pop("actor", "carol"), **data}
    (directory / f"{event_id(newest)}.json").write_text(json.dumps(body), encoding="utf-8")


class StartWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_start_claims_ready_work_and_names_the_actor(self) -> None:
        """Prevents a silent no-op: one claimed event carries the actor, and the output says who holds it."""
        w = self.world
        item = approved(w)
        result = w.run("start", item)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{item} claimed by tester", result.stdout)
        claimed = [e for e in w.events(item) if e["type"] == "claimed"]
        self.assertEqual([e["owner"] for e in claimed], ["tester"])

    def test_start_on_another_actors_claim_exits_3_naming_the_holder_and_writes_nothing(self) -> None:
        """Prevents stealing a claim with a plain start."""
        w = self.world
        item = started(w)
        before = tree(w)
        result = w.run("start", item, env=BOB)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("tester", result.stderr)
        self.assertIn("--take-over", result.stderr)
        self.assertEqual(tree(w), before)

    def test_start_on_your_own_claim_exits_5_and_writes_nothing(self) -> None:
        """Prevents a loop treating a sibling's claim as its own, and duplicate claimed events."""
        w = self.world
        item = started(w)
        before = tree(w)
        result = w.run("start", item)
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertEqual(tree(w), before)

    def test_start_refuses_draft_blocked_finished_and_unknown_items_with_exit_2(self) -> None:
        """Prevents claiming work that may not start; every refusal writes nothing."""
        w = self.world
        draft = w.create("--title", "Draft")
        first = approved(w, title="First")
        second = approved(w, "--needs", first, title="Second")
        closed = approved(w, title="Closed")
        w.run("close", closed, "--reason", "not_planned")
        before = tree(w)
        for reference, fragment in ((draft, "draft"), (second, first), (closed, "closed"), ("nosuchitem", "no item")):
            result = w.run("start", reference)
            self.assertEqual(result.returncode, 2, (reference, result.stderr))
            self.assertIn(fragment, result.stderr)
        self.assertEqual(tree(w), before)


class ReleaseWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_release_records_the_reason_and_returns_the_item_to_ready(self) -> None:
        """Prevents anonymous recovery of work: the event keeps the reason and the previous owner."""
        w = self.world
        item = started(w)
        result = w.run("release", item, "--reason", "switching lanes")
        self.assertEqual(result.returncode, 0, result.stderr)
        released = w.events(item)[-1]
        self.assertEqual((released["type"], released["reason"], released["previous_owner"]), ("released", "switching lanes", "tester"))
        self.assertIn(item, w.run("ready").stdout)

    def test_release_of_another_actors_claim_needs_force_and_a_forced_release_is_marked(self) -> None:
        """Prevents accidentally freeing a sibling's work, and an unrecorded takeover."""
        w = self.world
        item = started(w)
        before = tree(w)
        refused = w.run("release", item, "--reason", "tidy", env=BOB)
        self.assertEqual(refused.returncode, 3, refused.stderr)
        self.assertEqual(tree(w), before)
        forced = w.run("release", item, "--reason", "tester is gone", "--force", env=BOB)
        self.assertEqual(forced.returncode, 0, forced.stderr)
        last = w.events(item)[-1]
        self.assertEqual((last["forced"], last["previous_owner"], last["actor"]), (True, "tester", "bob"))

    def test_release_of_unclaimed_work_and_a_missing_reason_write_nothing(self) -> None:
        """Prevents phantom released events and an audit trail with no reason."""
        w = self.world
        idle = approved(w, title="Idle")
        held = started(w, title="Held")
        before = tree(w)
        self.assertEqual(w.run("release", idle, "--reason", "x").returncode, 2)
        self.assertEqual(w.run("release", held).returncode, 2)
        self.assertEqual(tree(w), before)


class CompleteOwnershipWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_only_the_holder_completes_and_a_retry_adds_no_second_event(self) -> None:
        """Prevents completing work you do not hold and duplicate completed events after a lost response."""
        w = self.world
        item = started(w)
        w.run("evidence", item, "--change", w.commit("the work"))
        other = w.run("complete", item, env=BOB)
        self.assertEqual(other.returncode, 3, other.stderr)
        self.assertIn("tester", other.stderr)
        self.assertIn("--take-over", other.stderr)
        self.assertEqual(w.run("complete", item).returncode, 0)
        again = w.run("complete", item)
        self.assertEqual(again.returncode, 0)
        self.assertIn("unchanged", again.stdout)
        self.assertEqual(event_types(w, item).count("completed"), 1)

    def test_complete_of_never_claimed_work_names_start(self) -> None:
        """Prevents skipping the claim step."""
        w = self.world
        item = approved(w)
        result = w.run("complete", item)
        self.assertEqual(result.returncode, 2)
        self.assertIn("qew start", result.stderr)


class DoubleClaimWalk(unittest.TestCase):
    """Two branches claim the same item without arbitration; the merged ledger stays valid and the oldest claim wins."""

    def setUp(self) -> None:
        manual = "\n[replication]\nauto_push = false\nauto_forge_sync = false\n"  # no authority: the branches merge by hand
        self.a = World(ledger_vcs="git", remote=True, replication=manual)
        self.addCleanup(self.a.close)
        self.b = self.a.clone("b", replication=manual)
        self.item = approved(self.a)
        git(self.a.ledger, "push", "-q", "origin", "main")
        git(self.b.ledger, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")

    def merge_b_into_a(self) -> None:
        git(self.b.ledger, "push", "-q", "origin", "main")
        git(self.a.ledger, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")

    def test_a_merged_double_claim_folds_valid_to_the_oldest_and_check_names_the_loser(self) -> None:
        """Prevents one concurrent start making the whole ledger invalid for everyone."""
        self.assertEqual(self.a.run("start", self.item, env=ALICE).returncode, 0)
        self.assertEqual(self.b.run("start", self.item, env=BOB).returncode, 0)
        self.merge_b_into_a()
        claims = {json.loads(p.read_text())["owner"]: p.stem
                  for p in (self.a.ledger / "events" / self.item).glob("*.json") if '"claimed"' in p.read_text()}
        self.assertEqual(sorted(claims), ["alice", "bob"])
        winner, loser = ("alice", "bob") if claims["alice"] < claims["bob"] else ("bob", "alice")
        checked = self.a.run("check")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertRegex(checked.stdout, rf"lost claim.*{loser}")
        self.assertIn(f"owner: {winner}", self.a.run("show", self.item).stdout)

    def test_a_claim_after_a_release_starts_a_new_period_and_a_stale_late_claim_never_wins(self) -> None:
        """Prevents a pre-release claim winning forever: A claims, releases, B claims, then a late C arrives."""
        w = self.a
        w.run("start", self.item, env=ALICE)
        w.run("release", self.item, "--reason", "reassign", env=ALICE)
        w.run("start", self.item, env=BOB)
        inject(w, self.item, "claimed", owner="carol", actor="carol")
        self.assertIn("owner: bob", w.run("show", self.item).stdout)
        checked = w.run("check")
        self.assertEqual(checked.returncode, 0, checked.stdout)
        self.assertIn("carol", checked.stdout)

    def test_the_same_actor_claiming_twice_on_two_branches_is_one_claim_without_a_note(self) -> None:
        """Prevents false alarms on a benign duplicate start."""
        w = self.a
        w.run("start", self.item, env=ALICE)
        inject(w, self.item, "claimed", owner="alice", actor="alice")
        checked = w.run("check")
        self.assertEqual(checked.returncode, 0, checked.stdout)
        self.assertNotIn("lost claim", checked.stdout)


if __name__ == "__main__":
    unittest.main()
