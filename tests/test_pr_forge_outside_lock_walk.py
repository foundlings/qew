"""The pr verbs talk to the forge without holding the ledger write lock.

Adversary: `pr open`, `pr ready`, `pr close` and `pr merge` holding `.qew/lock` across every forge request, so one slow forge
keeps every other writer waiting (seen live: a `pr-open` held the lock 136 s with eleven writers queued), and the opposite
mistake, a forge result that never reaches the ledger because the short write section was skipped.
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import filelock
from tests.pr_harness import PrWorld


class ForgeOutsideLockWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.lock = self.w.world.ledger / ".qew" / "lock"
        self.during: dict[str, list[bool]] = {}

    def watch(self, *names: str) -> None:
        """Record, at every call of these forge methods, whether the ledger write lock is held."""
        for name in names:
            original = getattr(self.w.forge, name)

            def recording(*args: object, _name: str = name, _original: object = original, **kwargs: object) -> object:
                self.during.setdefault(_name, []).append(filelock.is_held(self.lock))
                return _original(*args, **kwargs)  # type: ignore[operator]

            patcher = mock.patch.object(self.w.forge, name, recording)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_open_ready_and_close_call_the_forge_with_the_lock_free_and_still_record_the_result(self) -> None:
        """Prevents the lock spanning forge requests, and a recorded pull request the short write section never wrote."""
        self.watch("open_pull_request", "read_pull_request", "mark_pull_request_ready", "close_pull_request")
        item = self.w.item("Parse headers")
        self.assertEqual(self.w.open(item).code, 0)
        self.assertEqual(len(self.w.mirror_files(item)), 1)
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)
        self.assertEqual(self.w.qew("pr", "close", "1").code, 0)
        self.assertEqual(self.w.forge.count("close_pr"), 1)
        self.assertTrue({"open_pull_request", "read_pull_request", "mark_pull_request_ready", "close_pull_request"} <= set(self.during))
        for name, held in self.during.items():
            self.assertTrue(held and not any(held), f"{name} ran while the ledger write lock was held: {held}")

    def test_merge_calls_the_forge_with_the_lock_free_and_records_the_merge(self) -> None:
        """Prevents the merge's forge calls (merge, read-back, branch cleanup) holding the lock, and an unrecorded merge."""
        item = self.w.item("Merge me")
        self.assertEqual(self.w.open(item).code, 0)
        sha = self.w.commit("work")
        self.w.forge.edit_pull(1, head_sha=sha)
        self.assertEqual(self.w.qew("evidence", item, "--change", sha).code, 0)
        self.assertEqual(self.w.qew("pr", "ready", "1").code, 0)
        self.watch("read_pull_request", "merge_pull_request")
        merged = self.w.qew("pr", "merge", "1")
        self.assertEqual(merged.code, 0, merged.err)
        self.assertEqual(self.w.forge.merged, [(1, sha)])
        self.assertIn("merge_pull_request", self.during)
        for name, held in self.during.items():
            self.assertTrue(held and not any(held), f"{name} ran while the ledger write lock was held: {held}")


if __name__ == "__main__":
    unittest.main()
