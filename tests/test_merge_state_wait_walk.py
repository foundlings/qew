"""Walks of the wait for a forge that is still computing `mergeable`.

Adversary: GitHub answers `mergeable: null` for a few seconds after a push or a base move; a merge that refuses at once makes
every caller sleep and retry by hand, while a merge that waits forever hangs, and a wait that guesses merges unchecked work.
"""

import os
import sys
import unittest
from dataclasses import replace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.test_pr_merge_walk import MergeBase


class Computing:
    """A forge that reads `mergeable` as None for the first `unknown` reads of a pull request, then as it really is."""

    def __init__(self, forge, unknown: int) -> None:
        self.forge, self.left, self.reads = forge, unknown, 0
        self.real = forge.read_pull_request

    def __call__(self, number: int):
        self.reads += 1
        pull = self.real(number)
        if self.left > 0:
            self.left -= 1
            return replace(pull, mergeable=None)
        return pull


class MergeStateWaitWalk(MergeBase):
    def computing(self, unknown: int) -> Computing:
        computing = Computing(self.w.forge, unknown)
        patch = mock.patch.object(self.w.forge, "read_pull_request", computing)
        patch.start()
        self.addCleanup(patch.stop)
        return computing

    def test_a_merge_state_that_settles_after_a_few_reads_merges_with_backoff_waits(self) -> None:
        """Prevents the hand-made sleep-and-retry: three unknown reads then True must merge, waiting 1, 2 and 4 seconds."""
        self.computing(3)
        waits: list[float] = []
        run = self.merge(sleep=waits.append)
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.w.forge.merged, [(1, self.sha)])
        self.assertEqual(waits[:3], [1.0, 2.0, 4.0])

    def test_a_merge_state_that_never_settles_refuses_after_the_budget_and_says_how_long_it_waited(self) -> None:
        """Prevents a hang or a guess: all-unknown refuses with exit 2 once the 60 second budget is spent, and merges nothing."""
        self.computing(10**6)
        waits: list[float] = []
        run = self.merge(sleep=waits.append)
        self.assertEqual(run.code, 2, run)
        self.assertIn("still unknown after waiting 60s", run.err)
        self.assertEqual(sum(waits), 60.0)
        self.nothing_merged()

    def test_check_waits_too_and_a_settled_conflict_is_still_refused(self) -> None:
        """Prevents --check reporting 'unknown' for what the merge would wait out, and a wait hiding a real conflict."""
        computing = self.computing(2)
        self.w.forge.edit_pull(1, mergeable=False)
        waits: list[float] = []
        run = self.merge("--check", sleep=waits.append)
        self.assertEqual(run.code, 2, run)
        self.assertIn("has conflicts", run.err)
        self.assertEqual(waits, [1.0, 2.0])
        self.assertGreaterEqual(computing.reads, 3)

    def test_a_known_merge_state_never_waits(self) -> None:
        """Prevents slowing the common merge: a bool on the first read sleeps zero times."""
        waits: list[float] = []
        self.assertEqual(self.merge(sleep=waits.append).code, 0)
        self.assertEqual(waits, [])


if __name__ == "__main__":
    unittest.main()
