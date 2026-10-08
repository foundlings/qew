"""Walks of releasing and withdrawing work: `draft approve`, `draft revert`, `draft list`.

Adversary: a release or withdrawal that double-writes, silently no-ops, pulls work from under its
claimant or reviewer, or half-moves a plan with no map of what is left.
"""

import json
import os
import stat
import unittest

from tests.ledger_harness import QUOTE, World

REVERT_QUOTE = "Alexander: 'hold this one back until we decide more'"


def types(world: World, item: str) -> list:
    return [event["type"] for event in world.events(item)]


class ReleaseBase(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def draft(self, title: str, kind: str = "issue", parent: str = "") -> str:
        extra = ["--parent", parent] if parent else []
        return self.world.create("--kind", kind, "--title", title, *extra)

    def plan(self) -> dict:
        """Epic with a draft issue holding a draft task, an approved sibling issue and a closed draft issue."""
        w = self.world
        epic = self.draft("Plan", "epic")
        issue = self.draft("Issue", parent=epic)
        task = self.draft("Task", "task", parent=issue)
        sibling = self.draft("Sibling", parent=epic)
        gone = self.draft("Gone", parent=epic)
        w.approve(sibling)
        self.assertEqual(w.run("close", gone, "--reason", "not_planned").returncode, 0)
        return {"epic": epic, "issue": issue, "task": task, "sibling": sibling, "gone": gone}

    def check_passes(self) -> None:
        result = self.world.run("check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ApproveWalk(ReleaseBase):
    def test_golden_approve_writes_one_event_with_the_quote(self) -> None:
        """A release must leave the human's words in the ledger, exactly once."""
        item = self.draft("Work")
        result = self.world.run("draft", "approve", item, "--quote", QUOTE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"{item} approved")
        self.assertEqual(types(self.world, item), ["drafted", "approved"])
        self.assertEqual(self.world.events(item)[1]["quote"], QUOTE)

    def test_tree_approves_each_draft_once_and_reports_what_it_skipped(self) -> None:
        """A tree release must not double-write descendants or touch work that is already live or closed."""
        w, p = self.world, self.plan()
        result = w.run("draft", "approve", p["epic"], "--tree", "--quote", QUOTE, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["approved"], [p["epic"], p["issue"], p["task"]])
        self.assertEqual(sorted(report["skipped"]), sorted([p["sibling"], p["gone"]]))
        for key in ("epic", "issue", "task"):
            self.assertEqual(types(w, p[key]), ["drafted", "approved"], key)
        self.assertEqual(types(w, p["sibling"]), ["drafted", "approved"])
        self.assertEqual(types(w, p["gone"]), ["drafted", "closed"])
        self.check_passes()

    def test_rerun_of_a_tree_finishes_children_under_an_already_approved_root(self) -> None:
        """After a partial failure the root is live and the children are drafts; the rerun must not refuse at the root."""
        w = self.world
        epic = self.draft("Plan", "epic")
        child = self.draft("Child", parent=epic)
        w.approve(epic)
        result = w.run("draft", "approve", epic, "--tree", "--quote", QUOTE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{child} approved", result.stdout)
        self.assertIn(f"{epic} skipped", result.stdout)
        self.assertEqual(types(w, epic), ["drafted", "approved"])

    def test_a_lone_item_that_is_not_a_draft_is_refused_with_nothing_written(self) -> None:
        """Approving live work again must fail loudly and leave no event."""
        w = self.world
        item = self.draft("Work")
        w.approve(item)
        result = w.run("draft", "approve", item, "--quote", QUOTE)
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"nothing to approve: {item} is not a draft", result.stderr)
        self.assertEqual(types(w, item), ["drafted", "approved"])

    def test_a_non_draft_in_a_list_is_skipped_and_the_rest_are_approved(self) -> None:
        """One mistaken name must not abort the batch, and must be visible in text and JSON."""
        w = self.world
        first, second = self.draft("A"), self.draft("B")
        w.approve(second)
        text = w.run("draft", "approve", first, second, "--quote", QUOTE)
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn(f"{first} approved", text.stdout)
        self.assertIn(f"{second} skipped", text.stdout)
        third, fourth = self.draft("C"), self.draft("D")
        w.approve(fourth)
        data = json.loads(w.run("draft", "approve", third, fourth, "--quote", QUOTE, "--json").stdout)
        self.assertEqual(data, {"approved": [third], "skipped": [fourth]})

    def test_a_tree_whose_subtree_is_already_released_is_not_a_silent_success(self) -> None:
        """A no-op that exits 0 with no output looks exactly like a release that worked."""
        w = self.world
        epic = self.draft("Plan", "epic")
        child = self.draft("Child", parent=epic)
        w.approve(epic, child)
        result = w.run("draft", "approve", epic, "--tree", "--quote", QUOTE)
        self.assertEqual(result.returncode, 2)
        self.assertIn("nothing to approve", result.stderr)

    def test_duplicate_references_collapse_to_one_event(self) -> None:
        """Two events from one stale snapshot make an illegal transition and an invalid ledger."""
        w = self.world
        item = self.draft("Work")
        result = w.run("draft", "approve", item, item.upper(), "--quote", QUOTE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(types(w, item), ["drafted", "approved"])
        self.check_passes()

    def test_a_closed_root_is_refused_before_any_write(self) -> None:
        """Approving discarded work would revive it; a bad root must also stop the good ones in the same call."""
        w = self.world
        gone, fine = self.draft("Gone"), self.draft("Fine")
        w.run("close", gone, "--reason", "duplicate")
        result = w.run("draft", "approve", fine, gone, "--quote", QUOTE)
        self.assertEqual(result.returncode, 2)
        self.assertIn("closed", result.stderr)
        self.assertEqual(types(w, fine), ["drafted"])
        self.assertEqual(types(w, gone), ["drafted", "closed"])


class RevertWalk(ReleaseBase):
    def revert(self, *arguments: str, quote: str = REVERT_QUOTE):
        return self.world.run("draft", "revert", *arguments, "--quote", quote)

    def test_golden_revert_records_reason_and_quote_and_withdraws_readiness(self) -> None:
        """Live work must stop being startable the moment its release is withdrawn."""
        w = self.world
        item = self.draft("Work")
        w.approve(item)
        self.assertIn(item, w.run("ready").stdout)
        result = self.revert(item, "--reason", "undecided")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"{item} draft")
        event = w.events(item)[-1]
        self.assertEqual((event["type"], event["reason"], event["quote"]), ("reverted", "undecided", REVERT_QUOTE))
        self.assertEqual(w.run("ready").stdout, "")

    def test_tree_reverts_live_work_and_skips_drafts_and_closed(self) -> None:
        """A half-reverted plan would keep part of the withdrawn work startable."""
        w = self.world
        epic = self.draft("Plan", "epic")
        live = self.draft("Live", parent=epic)
        held = self.draft("Held", parent=epic)
        gone = self.draft("Gone", parent=epic)
        w.approve(epic, live, gone)
        w.run("close", gone, "--reason", "not_planned")
        result = self.revert(epic, "--tree", "--reason", "plan on hold", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["reverted"], [epic, live])
        self.assertEqual(sorted(report["skipped"]), sorted([held, gone]))
        self.assertEqual(types(w, held), ["drafted"])
        self.check_passes()

    def test_blank_or_missing_reason_is_refused_with_nothing_written(self) -> None:
        """An unexplained withdrawal cannot be told apart from an accident later."""
        w = self.world
        item = self.draft("Work")
        w.approve(item)
        blank = self.revert(item, "--reason", "  ")
        self.assertEqual(blank.returncode, 2)
        missing = self.revert(item)
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(types(w, item), ["drafted", "approved"])

    def test_a_lone_draft_is_refused_and_a_mixed_list_skips_it(self) -> None:
        """A rerun must not error on already-reverted work, and a no-op must not look like success."""
        w = self.world
        live, draft = self.draft("Live"), self.draft("Draft")
        w.approve(live)
        lone = self.revert(draft, "--reason", "why not")
        self.assertEqual(lone.returncode, 2)
        self.assertIn(f"nothing to revert: {draft} is a draft", lone.stderr)
        mixed = self.revert(live, draft, "--reason", "why not")
        self.assertEqual(mixed.returncode, 0, mixed.stderr)
        self.assertIn(f"{live} draft", mixed.stdout)
        self.assertIn(f"{draft} skipped", mixed.stdout)

    def test_a_closed_item_is_refused_before_any_write(self) -> None:
        """Reverting closed work would reopen it as a draft by the back door."""
        w = self.world
        live, gone = self.draft("Live"), self.draft("Gone")
        w.approve(live, gone)
        w.run("close", gone, "--reason", "duplicate")
        result = self.revert(live, gone, "--reason", "why not")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(types(w, live), ["drafted", "approved"])

    def test_round_trip_revert_approve_start_and_dependents_stay_blocked_meanwhile(self) -> None:
        """A lossy round trip would lose a quote, and a dependent must not run ahead of a blocker that is a draft."""
        w = self.world
        blocker = self.draft("Blocker")
        dependent = w.create("--kind", "issue", "--title", "Dependent", "--needs", blocker)
        w.approve(blocker, dependent)
        self.assertEqual(self.revert(blocker, "--reason", "rethink").returncode, 0)
        self.assertEqual(w.run("start", dependent).returncode, 2)
        w.approve(blocker)
        self.assertEqual(w.run("start", blocker).returncode, 0)
        self.assertEqual(types(w, blocker), ["drafted", "approved", "reverted", "approved", "claimed"])
        self.assertEqual([e.get("quote") for e in w.events(blocker)], [None, QUOTE, REVERT_QUOTE, QUOTE, None])
        self.check_passes()

    def test_duplicate_references_write_one_reverted_event(self) -> None:
        """A second `reverted` from the same snapshot is an illegal transition."""
        w = self.world
        item = self.draft("Work")
        w.approve(item)
        result = self.revert(item, item.upper(), "--reason", "why not")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(types(w, item), ["drafted", "approved", "reverted"])
        self.check_passes()


class RevertGuardWalk(ReleaseBase):
    def revert(self, *arguments: str):
        return self.world.run("draft", "revert", *arguments, "--reason", "pulling it back", "--quote", REVERT_QUOTE)

    def test_claimed_work_is_refused_naming_release_and_the_way_out_works(self) -> None:
        """Pulling work from under its claimant is the accident there is deliberately no force flag for."""
        w = self.world
        item = self.draft("Work")
        w.approve(item)
        w.run("start", item)
        result = self.revert(item)
        self.assertEqual(result.returncode, 3)
        self.assertIn(f"claimed by tester; release it first with qew release {item}", result.stderr)
        self.assertEqual(types(w, item), ["drafted", "approved", "claimed"])
        self.assertEqual(w.run("release", item, "--reason", "make way for the revert").returncode, 0)
        self.assertEqual(self.revert(item).returncode, 0)

    def test_a_claimed_epic_is_refused_the_same_way(self) -> None:
        """Withdrawing a plan someone is driving must be as hard as withdrawing a task."""
        w = self.world
        epic = self.draft("Plan", "epic")
        w.approve(epic)
        w.run("start", epic)
        self.assertEqual(self.revert(epic).returncode, 3)

    def test_one_claimed_descendant_refuses_the_whole_tree_and_is_named(self) -> None:
        """A half-reverted tree leaves live work under a withdrawn parent."""
        w = self.world
        epic = self.draft("Plan", "epic")
        first = self.draft("First", parent=epic)
        second = self.draft("Second", parent=epic)
        w.approve(epic, first, second)
        w.run("start", second)
        result = self.revert(epic, "--tree")
        self.assertEqual(result.returncode, 3)
        self.assertIn(second, result.stderr)
        for item in (epic, first):
            self.assertEqual(types(w, item), ["drafted", "approved"])

    def test_work_in_review_is_refused_but_a_draft_pull_request_does_not_block(self) -> None:
        """Withdrawing work whose pull request is in review orphans the review; a plan's draft pull request is not review."""
        w = self.world
        item = self.draft("Work")
        w.approve(item)
        mirrors = w.ledger / "mirrors" / item
        mirrors.mkdir(parents=True)
        record = {"forge": "forgejo", "url": "https://git.example/o/r", "kind": "pr", "number": 7}
        (mirrors / "forgejo-git.example-o-r-pr-7.json").write_text(json.dumps({**record, "draft": False}))
        refused = self.revert(item)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("has an open pull request", refused.stderr)
        (mirrors / "forgejo-git.example-o-r-pr-7.json").write_text(json.dumps({**record, "draft": True}))
        self.assertEqual(self.revert(item).returncode, 0)


@unittest.skipIf(os.geteuid() == 0, "a read-only directory does not stop root")
class PartialFailureWalk(ReleaseBase):
    def freeze(self, item: str) -> None:
        """Make the next event write for `item` fail like a disk or permission error."""
        directory = self.world.ledger / "events" / item
        os.chmod(directory, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, directory, stat.S_IRWXU)

    def thaw(self, item: str) -> None:
        os.chmod(self.world.ledger / "events" / item, stat.S_IRWXU)

    def test_a_failed_tree_approve_lists_done_and_left_and_the_rerun_finishes(self) -> None:
        """A half-released plan with no map of what is left cannot be finished safely."""
        w, p = self.world, self.plan()
        self.freeze(p["issue"])
        result = w.run("draft", "approve", p["epic"], "--tree", "--quote", QUOTE)
        self.assertEqual(result.returncode, 3)
        self.assertIn(f"approved: {p['epic']}", result.stderr)
        self.assertIn(f"not approved: {p['issue']} {p['task']}", result.stderr)
        self.assertIn("rerun the same command", result.stderr)
        self.assertIn("qew sync", result.stderr)
        self.assertEqual(types(w, p["epic"]), ["drafted", "approved"])
        self.thaw(p["issue"])
        rerun = w.run("draft", "approve", p["epic"], "--tree", "--quote", QUOTE)
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        self.assertEqual(types(w, p["task"]), ["drafted", "approved"])
        self.check_passes()

    def test_a_failed_tree_revert_lists_reverted_and_not_reverted(self) -> None:
        """A half-withheld plan needs the same map as a half-released one."""
        w = self.world
        epic = self.draft("Plan", "epic")
        child = self.draft("Child", parent=epic)
        w.approve(epic, child)
        self.freeze(child)
        result = w.run("draft", "revert", epic, "--tree", "--reason", "hold", "--quote", REVERT_QUOTE)
        self.assertEqual(result.returncode, 3)
        self.assertIn(f"reverted: {epic}", result.stderr)
        self.assertIn(f"not reverted: {child}", result.stderr)

    def test_an_event_that_does_not_fold_to_the_intended_state_is_not_reported_as_success(self) -> None:
        """A planted later event makes the read-back disagree; claiming success there would hide a stuck item."""
        w = self.world
        item = self.draft("Work")
        planted = {"type": "claimed", "at": "2026-01-01T00:00:00Z", "actor": "x", "owner": "x"}
        (w.ledger / "events" / item / ("z" * 26 + ".json")).write_text(json.dumps(planted))
        result = w.run("draft", "approve", item, "--quote", QUOTE)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertNotIn("approved\n", result.stdout)
        self.assertIn(f"not approved: {item}", result.stderr)


class DraftListWalk(ReleaseBase):
    def test_list_shows_kind_and_parent_and_leaves_out_closed_and_live_items(self) -> None:
        """A review queue must hold only open drafts, with the plan structure readable."""
        w, p = self.world, self.plan()
        data = [json.loads(line) for line in w.run("draft", "list", "--json").stdout.splitlines()]
        self.assertEqual(sorted(row["id"] for row in data), sorted([p["epic"], p["issue"], p["task"]]))
        by_id = {row["id"]: row for row in data}
        self.assertEqual((by_id[p["task"]]["kind"], by_id[p["task"]]["parent"]), ("task", p["issue"]))
        text = w.run("draft", "list").stdout
        self.assertIn(f"{p['task']} draft", text)
        self.assertIn(f"(under {p['issue']})", text)

    def test_parent_filter_lists_only_direct_draft_children_and_rejects_unknown_parents(self) -> None:
        """Releasing one plan must not mean reading every unrelated draft; a typo must not give a plausible empty list."""
        w, p = self.world, self.plan()
        other = self.draft("Other", "epic")
        self.draft("Elsewhere", parent=other)
        listed = [json.loads(line) for line in w.run("draft", "list", "--parent", p["epic"].upper(), "--json").stdout.splitlines()]
        self.assertEqual([row["id"] for row in listed], [p["issue"]])
        unknown = w.run("draft", "list", "--parent", "nothere")
        self.assertEqual(unknown.returncode, 2)


if __name__ == "__main__":
    unittest.main()
