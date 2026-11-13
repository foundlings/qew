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


class SharedBudgetWalk(unittest.TestCase):
    """Many pull requests share ONE wait budget: wall time must not scale with how many are still computing."""

    def pulls(self, numbers):
        from qew.forge import RemotePullRequest

        return {n: RemotePullRequest(n, f"u{n}", "open", False, f"sha{n}", f"b{n}", "main", None, "passing", "", "me", "") for n in numbers}

    def reader(self, resolves_on: dict[int, int]):
        """Pull `n` reads as unknown until its `resolves_on[n]`th re-read (None: never); records who was re-read each round."""
        seen: dict[int, int] = {}
        rounds: list[list[int]] = []

        def reread(number: int):
            seen[number] = seen.get(number, 0) + 1
            wanted = resolves_on[number]
            return replace(self.pulls([number])[number], mergeable=True if wanted and seen[number] >= wanted else None)

        return reread, seen, rounds

    def test_three_pulls_resolving_on_different_rounds_finish_inside_one_budget(self) -> None:
        """Prevents N x 60 s: pulls resolving on rounds 1, 2 and 3 cost 1+2+4 seconds together, and each round re-reads only the unknown."""
        from qew import cmd_pr

        reread, seen, _ = self.reader({1: 1, 2: 2, 3: 3})
        clock = [0.0]

        def sleep(seconds: float) -> None:
            clock[0] += seconds

        settled = cmd_pr.settle(self.pulls([1, 2, 3]), reread, sleep)
        self.assertTrue(all(p.mergeable is True for p in settled.values()))
        self.assertEqual(clock[0], 7.0)
        self.assertEqual(seen, {1: 1, 2: 2, 3: 3})  # a resolved pull is never read again

    def test_wall_time_does_not_scale_with_the_number_of_unknown_pulls(self) -> None:
        """Prevents a per-pull backoff: 1 and 12 pulls that never settle both spend exactly the 60 second budget."""
        from qew import cmd_pr

        for count in (1, 12):
            with self.subTest(count=count):
                reread, _, _ = self.reader({n: 0 for n in range(1, count + 1)})
                clock = [0.0]
                cmd_pr.settle(self.pulls(range(1, count + 1)), reread, lambda s: clock.__setitem__(0, clock[0] + s))
                self.assertEqual(clock[0], 60.0)

    def test_a_partial_timeout_leaves_exactly_the_unresolved_pulls_unknown(self) -> None:
        """Prevents blaming the wrong pull: after the budget, the resolved ones are bools and only the stuck ones are None."""
        from qew import cmd_pr

        reread, _, _ = self.reader({1: 1, 2: 0, 3: 2, 4: 0})
        settled = cmd_pr.settle(self.pulls([1, 2, 3, 4]), reread, lambda s: None)
        self.assertEqual([n for n, p in settled.items() if p.mergeable is None], [2, 4])
