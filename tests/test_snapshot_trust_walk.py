"""An event the snapshot cache has already validated is not validated again on every read, and what is not validated stays refused.

Each test is a scenario; its docstring names the failure it prevents. Parsing 142,000 events is most of what a warm read of
foundlings' ledger costs; the text was validated when it entered the cache, and `qew check` validates everything again.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_event, ledger_store, snapshot_cache
from qew.ledger_store import Ledger
from tests.ledger_harness import QUOTE, World


class SnapshotTrustWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        for n in range(3):
            self.world.create("--title", f"Item {n}", "--quote", QUOTE)
        self.ledger = Ledger(self.world.ledger, "TST")
        snapshot_cache._open.pop(str(self.world.ledger), None)  # a fresh process-level cache object over the world's database

    def parses(self, action) -> int:
        calls = [0]
        original = ledger_event.parse_event

        def counting(*arguments, **keywords):
            calls[0] += 1
            return original(*arguments, **keywords)

        with mock.patch.object(ledger_store, "parse_event", counting), mock.patch.object(snapshot_cache, "parse_event", counting, create=True):
            action()
        return calls[0]

    def test_a_warm_snapshot_parses_no_event_again(self) -> None:
        """Prevents re-validating 142,000 unchanged events on every command: the second and third reads parse none."""
        import time

        self.ledger.snapshot()
        time.sleep(2.2)  # past the racy window, so the entries are trusted
        self.ledger.snapshot()
        snapshot_cache._open.pop(str(self.world.ledger), None)  # a new process: the database is all there is
        self.assertGreater(self.parses(self.ledger.snapshot), -1)
        self.assertEqual(self.parses(self.ledger.snapshot), 0)

    def test_an_event_that_fails_validation_is_never_trusted(self) -> None:
        """Prevents a cache hiding a bad file: a malformed event is refused on every read, cached or not."""
        item = self.ledger.item_ids()[0]
        bad = self.world.ledger / "events" / item / "9999999zz.json"
        bad.write_text('{"type": "no-such-event", "at": "2026-01-01T00:00:00Z", "actor": "x"}\n')
        for _ in range(3):
            with self.assertRaises(Exception) as caught:
                self.ledger.snapshot()
            self.assertIn("unknown event type", str(caught.exception))
        bad.unlink()
        self.assertEqual(len(self.ledger.snapshot().items), 3)

    def test_check_validates_every_event_again(self) -> None:
        """Prevents trust becoming blindness: `qew check` re-reads and re-parses everything and reports a bad file."""
        item = self.ledger.item_ids()[0]
        self.assertEqual(self.world.run("check").returncode, 0)
        bad = self.world.ledger / "events" / item / "9999999zz.json"
        bad.write_text('{"type": "no-such-event", "at": "2026-01-01T00:00:00Z", "actor": "x"}\n')
        result = self.world.run("check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unknown event type", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
