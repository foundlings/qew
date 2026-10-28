"""The sync worker's lock order: the write lock first, the push lock only to commit and push, the network push outside the write lock.

Adversary: a worker that holds `push.lock` while it queues behind the writers for `lock` (every writer that needs `push.lock`
then waits for the worker, and the worker waits for them), and a push over the network made while the write lock is held.
Seen live: detached syncs held `push.lock` for 30 minutes while 11 writers queued.
"""

from contextlib import contextmanager
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from qew import replication


class SyncLockOrderWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.log: list[str] = []
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        replicate = SimpleNamespace(timeout_s=1.0)
        self.config = SimpleNamespace(replication=replicate, ledger_path=Path(temporary.name))
        self.repo = SimpleNamespace(
            exclude_cache=lambda: None, fetch_merge=lambda _t: "", unpushed=lambda: 1, vcs="jj",
            push=lambda _t: self.log.append("push"),
        )

    @contextmanager
    def fake_lock(self, _config: object, name: str = "lock", verb: str = "", wait_s: float | None = None) -> Iterator[None]:
        self.log.append(f"+{name}")
        try:
            yield
        finally:
            self.log.append(f"-{name}")

    def patched(self) -> object:
        stack = mock.patch.multiple(
            replication, _lock=self.fake_lock, _recover=lambda *_a, **_k: None, _commit=lambda *_a, **_k: self.log.append("commit"),
        )
        return stack

    def test_the_finishing_step_takes_lock_before_push_lock_and_pushes_outside_the_write_lock(self) -> None:
        """Prevents the worker sitting on push.lock while it waits for lock, and a network push inside the write lock."""
        with self.patched(), mock.patch.object(replication.ledger_store, "journal_take", return_value=[("f", "events/x")]), \
                mock.patch.object(replication.ledger_store, "journal_start"), mock.patch.object(replication.op_gc, "maybe_collect"):
            session = replication._sync_session(self.config, self.repo, True, True, False)  # type: ignore[arg-type]
            next(session)
            with self.assertRaises(StopIteration):
                next(session)
        self.assertEqual(self.log[:2] + self.log[-1:], ["+lock", "-lock", "-push.lock"])  # the first section holds lock, the fetch
        finishing = self.log[2:]
        self.assertEqual(finishing, ["+lock", "+push.lock", "commit", "-push.lock", "-lock", "+push.lock", "push", "-push.lock"])

    def test_a_checkpoint_takes_lock_before_push_lock(self) -> None:
        """Prevents the same wait amplification at each checkpoint commit."""
        entries = [("f", f"events/{n}") for n in range(replication.CHECKPOINT_EVERY)]
        replication._ACTIVE = (self.config, self.repo, True, True)  # type: ignore[assignment]
        self.addCleanup(setattr, replication, "_ACTIVE", None)
        with self.patched(), mock.patch.object(replication.ledger_store, "journal_take", return_value=entries), \
                mock.patch.object(replication.ledger_store, "journal_start"):
            replication.checkpoint()
        self.assertEqual(self.log, ["+lock", "+push.lock", "commit", "-push.lock", "-lock"])


if __name__ == "__main__":
    unittest.main()
