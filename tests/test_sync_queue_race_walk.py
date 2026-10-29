"""The sync queue never loses an id a write enqueued while the worker takes the queue.

Adversary: a writer that opened `sync.queue` for append just before the worker renamed it away and read it, so its line lands in
the renamed file after the read and is deleted with it. The id is then synced only by the next full pass, which may be never:
seen live as "5 of 6 items synced" in a burst of creates. The walk hammers enqueue from several threads while one takes the
queue in a loop and requires every id to come out exactly in the union of what was taken and what is left.
"""

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from qew import filelock, syncworker


class SyncQueueRaceWalk(unittest.TestCase):
    def test_every_enqueued_id_is_taken_or_still_queued(self) -> None:
        """Prevents an id vanishing between the append and the worker's take: all ids of all threads are accounted for."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = SimpleNamespace(ledger_path=Path(directory.name))
        taken: set[str] = set()
        done = threading.Event()

        def writer(prefix: str) -> None:
            for number in range(400):
                syncworker.enqueue(config, [f"{prefix}{number:04d}"])  # type: ignore[arg-type]

        def worker() -> None:
            while not done.is_set():
                taken.update(syncworker.take_queue(config))  # type: ignore[arg-type]

        threads = [threading.Thread(target=writer, args=(letter,)) for letter in "ABCDEF"]
        taker = threading.Thread(target=worker)
        taker.start()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        done.set()
        taker.join()
        taken.update(syncworker.take_queue(config))  # type: ignore[arg-type]
        expected = {f"{letter}{number:04d}" for letter in "ABCDEF" for number in range(400)}
        self.assertEqual(sorted(expected - taken), [], f"{len(expected - taken)} of {len(expected)} enqueued ids were lost")


    def test_the_short_lock_times_out_with_the_holder_named_instead_of_waiting_forever(self) -> None:
        """Prevents an append waiting without bound on a stopped holder: a contended short lock gives up after its wait."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "queue.lock"
        with filelock.short(path, 1.0, "the queue"):
            with self.assertRaises(filelock.LockTimeout):
                with filelock.short(path, 0.05, "the queue"):
                    pass
        with filelock.short(path, 1.0, "the queue"):  # and it is free again once released
            pass


if __name__ == "__main__":
    unittest.main()
