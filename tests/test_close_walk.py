"""`qew close` and `qew reopen`: dispositions that name the other item, delivery by someone else, reopen.

Adversary: finished work recorded as abandoned, an unexplained duplicate, contradictory dispositions,
dependents blocked forever behind externally shipped work, and idempotent loops writing duplicate events.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import World, git
from tests.lifecycle_support import approved, event_types, started, tree


class CloseWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_duplicate_and_superseded_store_the_other_item_and_show_mentions_both_ends(self) -> None:
        """Prevents an unexplained duplicate."""
        w = self.world
        original = approved(w, title="Original")
        copy = approved(w, title="Copy")
        old = approved(w, title="Old")
        new = approved(w, title="New")
        self.assertEqual(w.run("close", copy, "--reason", "duplicate", "--of", original, "--note", "same request").returncode, 0)
        self.assertEqual(w.run("close", old, "--reason", "superseded", "--by", new).returncode, 0)
        duplicate, superseded = w.events(copy)[-1], w.events(old)[-1]
        self.assertEqual((duplicate["duplicate_of"], duplicate["note"], superseded["superseded_by"]), (original, "same request", new))
        self.assertIn(original, w.run("show", copy).stdout)
        self.assertIn(copy, w.run("show", original).stdout)
        self.assertIn(old, w.run("show", new).stdout)

    def test_a_close_without_a_clear_disposition_names_the_right_commands_and_writes_nothing(self) -> None:
        """Prevents finished work being recorded as not planned."""
        w = self.world
        item = approved(w)
        before = tree(w)
        for arguments in (["--reason", "because it is done"], ["--reason", "completed"], []):
            result = w.run("close", item, *arguments)
            self.assertEqual(result.returncode, 2, (arguments, result.stderr))
            self.assertIn("qew complete", result.stderr)
        self.assertEqual(tree(w), before)

    def test_contradictory_or_unresolvable_dispositions_are_refused(self) -> None:
        """Prevents loops and contradictory records."""
        w = self.world
        item = approved(w)
        other = approved(w, title="Other")
        before = tree(w)
        for arguments in (
            ["--reason", "superseded", "--by", item], ["--reason", "duplicate", "--of", "nosuchitem"],
            ["--reason", "duplicate", "--of", other, "--by", other], ["--reason", "superseded", "--of", other],
            ["--reason", "duplicate"], ["--reason", "not_planned", "--of", other],
        ):
            result = w.run("close", item, *arguments)
            self.assertEqual(result.returncode, 2, (arguments, result.stderr))
        self.assertEqual(tree(w), before)


class DeliveredCloseWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_work_delivered_by_an_unclaimed_commit_completes_and_unblocks_dependents(self) -> None:
        """Prevents dependents blocked forever behind work that shipped outside qew."""
        w = self.world
        shipped = approved(w, title="Shipped elsewhere")
        later = approved(w, "--needs", shipped, title="Later")
        sha = w.commit("shipped")
        result = w.run("close", shipped, "--delivered-by", sha[:10], "--landed-on", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(event_types(w, shipped)[-2:], ["evidence", "completed"])
        self.assertEqual(w.events(shipped)[-2]["change_ids"], [sha])
        self.assertIn(later, w.run("ready").stdout)

    def test_an_unlanded_commit_or_half_a_proof_is_refused_before_any_write(self) -> None:
        """Prevents claiming delivery that did not land."""
        w = self.world
        item = approved(w)
        git(w.code, "checkout", "-q", "-b", "feature")
        sha = w.commit("unmerged")
        git(w.code, "checkout", "-q", "main")
        before = tree(w)
        unlanded = w.run("close", item, "--delivered-by", sha, "--landed-on", "main")
        half = w.run("close", item, "--delivered-by", sha)
        other_half = w.run("close", item, "--landed-on", "main")
        self.assertEqual([r.returncode for r in (unlanded, half, other_half)], [2, 2, 2])
        self.assertIn(sha, unlanded.stderr)
        self.assertEqual(tree(w), before)


class ReopenWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_reopen_returns_a_closed_claimed_item_to_approved_with_the_reason(self) -> None:
        """Prevents a stale claim surviving a reopen, and reopening with no recorded why."""
        w = self.world
        item = started(w)
        w.run("close", item, "--reason", "not_planned")
        self.assertEqual(w.run("reopen", item).returncode, 2)
        result = w.run("reopen", item, "--reason", "needed after all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(w.events(item)[-1]["reason"], "needed after all")
        self.assertIn("state: approved", w.run("show", item).stdout)
        self.assertNotIn("owner", w.run("show", item).stdout)
        self.assertIn(item, w.run("ready").stdout)

    def test_reopening_open_work_is_a_write_free_no_op(self) -> None:
        """Prevents duplicate events from idempotent loops."""
        w = self.world
        item = started(w)
        before = tree(w)
        result = w.run("reopen", item, "--reason", "again")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"unchanged {item} (already open)", result.stdout)
        self.assertEqual(tree(w), before)

    def test_reopening_a_completed_dependency_blocks_its_dependents_again(self) -> None:
        """Prevents stale readiness."""
        w = self.world
        first = started(w, title="First")
        w.run("evidence", first, "--change", w.commit("done"))
        w.run("complete", first)
        second = approved(w, "--needs", first, title="Second")
        self.assertIn(second, w.run("ready").stdout)
        w.run("reopen", first, "--reason", "regressed")
        self.assertNotIn(second, w.run("ready").stdout)
        self.assertIn(second, w.run("blocked").stdout)


if __name__ == "__main__":
    unittest.main()
