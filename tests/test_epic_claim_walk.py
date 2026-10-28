"""Claiming an epic and working under it: the cascade is all-or-nothing, and work needs a claim.

Adversary: a half-claimed plan, a descendant stolen from another actor, a reservation left on the primary
forge after a refusal, evidence on work you do not hold, and a claim the authority threw out finishing work.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.ledger_ids import event_id
from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import QUOTE, World
from tests.lifecycle_support import event_types, tree

ALICE = {"QEW_ACTOR": "alice"}
BOB = {"QEW_ACTOR": "bob"}


def plan(world: World):
    """An approved epic with an issue, a task under the issue, a draft child and a child blocked on the issue."""
    epic = world.create("--kind", "epic", "--title", "Plan", "--draft")
    issue = world.create("--kind", "issue", "--title", "Issue", "--parent", epic, "--draft")
    task = world.create("--kind", "task", "--title", "Task", "--parent", issue, "--draft")
    blocked = world.create("--kind", "issue", "--title", "Blocked", "--parent", epic, "--needs", issue, "--draft")
    draft = world.create("--kind", "issue", "--title", "Draft", "--parent", epic, "--draft")
    world.approve(epic, issue, task, blocked)
    return epic, issue, task, blocked, draft


def inject(world: World, item: str, event_type: str, **data) -> str:
    directory = world.home("events", item)
    newest = sorted(path.stem for path in directory.glob("*.json"))[-1]
    identifier = event_id(newest)
    body = {"type": event_type, "at": "2026-01-01T00:00:00Z", "actor": data.pop("actor", "carol"), **data}
    (directory / f"{identifier}.json").write_text(json.dumps(body), encoding="utf-8")
    return identifier


class EpicCascadeWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_claiming_an_epic_claims_its_open_startable_descendants(self) -> None:
        """Prevents an epic holder who still has to claim every piece by hand; drafts and blocked work are left."""
        w = self.world
        epic, issue, task, blocked, draft = plan(w)
        result = w.run("start", epic)
        self.assertEqual(result.returncode, 0, result.stderr)
        for item in (epic, issue, task):
            self.assertEqual(event_types(w, item)[-1], "claimed", item)
        self.assertNotIn("claimed", event_types(w, blocked))
        self.assertNotIn("claimed", event_types(w, draft))
        self.assertEqual(w.run("check").returncode, 0)

    def test_a_descendant_held_by_another_refuses_everything_and_writes_nothing(self) -> None:
        """Prevents a half-claimed plan and a descendant taken from its holder."""
        w = self.world
        epic, issue, task, blocked, draft = plan(w)
        self.assertEqual(w.run("start", task, env=BOB).returncode, 0)
        before = tree(w)
        result = w.run("start", epic, env=ALICE)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn(task, result.stderr)
        self.assertIn("bob", result.stderr)
        self.assertEqual(tree(w), before)

    def test_releasing_the_epic_releases_the_cascade(self) -> None:
        """Prevents descendants staying claimed after the plan was given up."""
        w = self.world
        epic, issue, task, blocked, draft = plan(w)
        w.run("start", epic)
        result = w.run("release", epic, "--reason", "reprioritised")
        self.assertEqual(result.returncode, 0, result.stderr)
        for item in (epic, issue, task):
            self.assertEqual(event_types(w, item)[-1], "released", item)
        self.assertIn(issue, w.run("ready").stdout)


class EpicCascadeOnPrimaryWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        self.slug = f"forgejo-{self.fake.repo_url.split('//')[1].replace('/', '-')}"
        mirrors = f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        quiet = "\n[replication]\nauto_forge_sync = false\nauto_push = false\n"
        self.world = World(mirrors, replication=quiet + f'\n[authority]\nprimary = "{self.slug}"\n')
        self.addCleanup(self.world.close)
        self.env = {"FORGEJO_TOKEN": TOKEN}

    def test_a_refusal_midway_leaves_no_event_and_releases_the_reservations_already_posted(self) -> None:
        """Prevents a reservation left on the primary after the claim was refused: the unsynced child stops the cascade."""
        w = self.world
        epic = w.create("--kind", "epic", "--title", "Plan", "--quote", QUOTE)
        first = w.create("--title", "Synced", "--parent", epic, "--quote", QUOTE)
        self.assertEqual(w.run("sync", "--all", env=self.env).returncode, 0)
        w.create("--title", "Unsynced", "--parent", epic, "--quote", QUOTE)
        before = tree(w)
        result = w.run("start", epic, env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("qew push", result.stderr)
        self.assertEqual(tree(w), before)
        bodies = [c["body"] for issue in self.fake.issues.values() for c in issue["discussion"]]
        claims = [b for b in bodies if "qew:claim" in b]
        releases = [b for b in bodies if "qew:release" in b]
        self.assertGreaterEqual(len(claims), 1, bodies)
        self.assertEqual(len(releases), len(claims), bodies)


class WorkNeedsAClaimWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_evidence_on_another_actors_claimed_item_is_refused(self) -> None:
        """Prevents recording delivery on work somebody else holds."""
        w = self.world
        item = w.create("--title", "Work", "--quote", QUOTE)
        w.run("start", item, env=BOB)
        before = tree(w)
        result = w.run("evidence", item, "--change", w.commit("x"), env=ALICE)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("bob", result.stderr)
        self.assertEqual(tree(w), before)

    def test_the_holder_of_an_epic_finishes_a_descendant_offline_with_evidence(self) -> None:
        """Prevents the network being needed to finish work: completion by the holder syncs later."""
        w = self.world
        epic, issue, task, blocked, draft = plan(w)
        w.run("start", epic, env=ALICE)
        offline = {**ALICE, "QEW_OFFLINE": "1"}
        sha = w.commit("work")
        self.assertEqual(w.run("evidence", task, "--change", sha, env=offline).returncode, 0)
        done = w.run("complete", task, env=offline)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_claim_the_authority_threw_out_cannot_finish_the_work(self) -> None:
        """Prevents a stale claim_lost owner completing: the fold ignores their claim, so they hold nothing."""
        w = self.world
        item = w.create("--title", "Work", "--quote", QUOTE)
        w.run("start", item, env=BOB)
        lost = inject(w, item, "claimed", owner="alice", actor="alice")
        inject(w, item, "claim_lost", ref=lost, winner="bob", actor="alice")
        sha = w.commit("work")
        evidence = w.run("evidence", item, "--change", sha, env=ALICE)
        complete = w.run("complete", item, env=ALICE)
        self.assertEqual((evidence.returncode, complete.returncode), (3, 3), evidence.stderr + complete.stderr)
        self.assertNotIn("completed", event_types(w, item))


if __name__ == "__main__":
    unittest.main()
