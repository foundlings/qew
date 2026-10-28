"""`qew complete`: what a completion records, the explicit no-code path, unresolvable proof and take-over.

Adversary: completion with no audit of what landed, option injection through a branch or change value,
a missing fetch misdiagnosed as unlanded work, completing on guessed data, and freeing a claim for unproven work.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import World, git
from tests.lifecycle_support import approved, event_types, started, tree

BOB = {"QEW_ACTOR": "bob"}


def mirror(world: World, item: str, name: str, **fields) -> None:
    """A recorded mirror file, the way `qew pr open` leaves one."""
    directory = world.home("mirrors", item)
    directory.mkdir(parents=True, exist_ok=True)
    body = {"forge": "github", "url": "https://github.com/o/r", "kind": "pr", **fields}
    (directory / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")


def proven(world: World, item: str) -> str:
    """Record a commit on main as evidence (cited by its short form) and return the full id."""
    sha = world.commit("the work")
    assert world.run("evidence", item, "--change", sha[:10]).returncode == 0
    return sha


class CompletionRecordWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_complete_records_reason_full_commit_branch_and_merged_pull_requests(self) -> None:
        """Prevents completion with no audit of what landed; a short cited id is stored in full."""
        w = self.world
        item = started(w)
        sha = proven(w, item)
        mirror(w, item, "github-github.com-o-r-pr-7", number=7, state="merged")
        mirror(w, item, "github-github.com-o-r-pr-8", number=8, state="open")
        result = w.run("complete", item, "--reason", "parser done")
        self.assertEqual(result.returncode, 0, result.stderr)
        done = w.events(item)[-1]
        self.assertEqual((done["type"], done["reason"], done["commits"], done["branch"], done["prs"]),
                         ("completed", "parser done", [sha], "main", [7]))
        shown = w.run("show", item).stdout
        self.assertIn("parser done", shown)
        self.assertIn(sha, shown)
        self.assertIn("#7", shown)

    def test_landed_on_names_a_stacked_branch_and_the_default_target_still_refuses(self) -> None:
        """Prevents completing stacked work against the wrong branch, or silently against the default."""
        w = self.world
        item = started(w)
        git(w.code, "checkout", "-q", "-b", "stack")
        sha = w.commit("stacked work")
        git(w.code, "checkout", "-q", "main")
        w.run("evidence", item, "--change", sha)
        self.assertEqual(w.run("complete", item).returncode, 2)
        result = w.run("complete", item, "--landed-on", "stack")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(w.events(item)[-1]["branch"], "stack")

    def test_hostile_branch_reason_and_change_values_are_refused_before_any_event(self) -> None:
        """Prevents option injection into the VCS and blank audit text."""
        w = self.world
        item = started(w)
        proven(w, item)
        before = tree(w)
        for arguments in (["--landed-on", "main...evil"], ["--landed-on=-x"], ["--landed-on", ""], ["--reason", "  "]):
            result = w.run("complete", item, *arguments)
            self.assertEqual(result.returncode, 2, (arguments, result.stderr))
        self.assertEqual(tree(w), before)
        hostile = started(w, title="Hostile")
        directory = w.home("events", hostile)
        newest = sorted(path.stem for path in directory.glob("*.json"))[-1]
        (directory / f"{newest}z.json").write_text(json.dumps(
            {"type": "evidence", "at": "2026-01-01T00:00:00Z", "actor": "x", "vcs": "git", "repo": "r",
             "change_ids": ["--output=evil"]}))
        result = w.run("complete", hostile)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("completed", event_types(w, hostile))


class NoCodeCompletionWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_no_commit_with_a_reason_completes_and_check_counts_it(self) -> None:
        """Prevents forcing fake evidence for work whose delivery is not in the repository."""
        w = self.world
        item = started(w)
        result = w.run("complete", item, "--reason", "decision recorded in the wiki", "--no-commit", "decision, no code")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((w.events(item)[-1]["absent"], w.events(item)[-1]["absent_reason"]), ("no-code", "decision, no code"))
        checked = w.run("check")
        self.assertEqual(checked.returncode, 0, checked.stdout)
        self.assertIn("completed without code: 1", checked.stdout)

    def test_no_commit_is_refused_next_to_evidence_or_with_a_blank_explanation(self) -> None:
        """Prevents contradictory flags silently picking one meaning, and a blank escape hatch."""
        w = self.world
        coded = started(w, title="Coded")
        proven(w, coded)
        bare = started(w, title="Bare")
        before = tree(w)
        both = w.run("complete", coded, "--no-commit", "no code needed")
        blank = w.run("complete", bare, "--no-commit", "   ")
        self.assertEqual((both.returncode, blank.returncode), (2, 2), both.stderr + blank.stderr)
        self.assertEqual(tree(w), before)


class UnresolvableProofWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_a_change_this_checkout_has_never_seen_is_unknown_not_unlanded(self) -> None:
        """Prevents misdiagnosing a missing fetch as unlanded work."""
        w = self.world
        item = started(w)
        w.run("evidence", item, "--change", "deadbeefdeadbeef")
        before = tree(w)
        result = w.run("complete", item)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("unknown to this checkout", result.stderr)
        self.assertNotIn("not an ancestor", result.stderr)
        self.assertEqual(tree(w), before)

    def test_an_unreadable_pull_request_record_is_indeterminate_and_nothing_is_written(self) -> None:
        """Prevents completing on guessed data when the pull request list cannot be read."""
        w = self.world
        item = started(w)
        proven(w, item)
        directory = w.home("mirrors", item)
        directory.mkdir(parents=True)
        (directory / "github-github.com-o-r-pr-3.json").write_text("{not json")
        before = tree(w)
        result = w.run("complete", item)
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("indeterminate", result.stderr)
        self.assertEqual(tree(w), before)

    def test_a_missing_target_branch_is_an_environment_failure_naming_it(self) -> None:
        """Prevents a typo in --landed-on reading as unlanded work."""
        w = self.world
        item = started(w)
        proven(w, item)
        result = w.run("complete", item, "--landed-on", "nosuchbranch")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("nosuchbranch", result.stderr)


class CompleteTakeOverWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_take_over_completes_another_actors_claim_and_records_both(self) -> None:
        """Prevents losing whose claim was finished: the evidence is proven first, one event names both."""
        w = self.world
        item = started(w)
        proven(w, item)
        result = w.run("complete", item, "--take-over", "--reason", "tester left the project", env=BOB)
        self.assertEqual(result.returncode, 0, result.stderr)
        done = w.events(item)[-1]
        self.assertEqual((done["type"], done["taken_over_from"], done["actor"]), ("completed", "tester", "bob"))
        self.assertEqual(event_types(w, item).count("completed"), 1)

    def test_take_over_with_nothing_to_take_or_without_a_reason_writes_nothing(self) -> None:
        """Prevents an unexplained takeover and a takeover of your own or unclaimed work."""
        w = self.world
        mine = started(w, title="Mine")
        proven(w, mine)
        idle = approved(w, title="Idle")
        before = tree(w)
        own = w.run("complete", mine, "--take-over", "--reason", "x")
        unclaimed = w.run("complete", idle, "--take-over", "--reason", "x")
        no_reason = w.run("complete", mine, "--take-over", env=BOB)
        self.assertEqual([r.returncode for r in (own, unclaimed, no_reason)], [2, 2, 2])
        self.assertIn("nothing to take over", own.stderr + unclaimed.stderr)
        self.assertEqual(tree(w), before)

    def test_take_over_with_unlanded_evidence_leaves_the_claim_in_place(self) -> None:
        """Prevents freeing a claim for unproven work."""
        w = self.world
        item = started(w)
        git(w.code, "checkout", "-q", "-b", "feature")
        sha = w.commit("unmerged")
        git(w.code, "checkout", "-q", "main")
        w.run("evidence", item, "--change", sha)
        before = tree(w)
        result = w.run("complete", item, "--take-over", "--reason", "x", env=BOB)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(tree(w), before)


if __name__ == "__main__":
    unittest.main()
