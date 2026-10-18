"""A read that meets a writer half way through sees a transient state and looks again before it refuses.

Each test is a scenario; its docstring names the failure it prevents (seen live: while one agent filed items in a series, other
agents' reads failed with 'the ledger is not readable: events/X: belongs to no item', naming the newest id, though the files
were all there afterwards).
"""

import json
import os
import shutil
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


class ReadRacesWriterWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        self.first = self.world.create("--title", "First", "--quote", QUOTE)

    def file_item_slowly(self, pause: float) -> str:
        """Write a second item the way a writer does it, with the events directory visible `pause` seconds before the item file."""
        ledger = self.world.ledger
        second = "042zzzzzz"
        source = next((ledger / "events" / self.first).glob("*.json"))
        (ledger / "events" / second).mkdir()
        shutil.copy(source, ledger / "events" / second / source.name)
        text = (ledger / "items" / self.first / "item.md").read_text().replace(self.first, second)

        def finish() -> None:
            time.sleep(pause)
            (ledger / "items" / second).mkdir()
            (ledger / "items" / second / "item.md").write_text(text)

        thread = threading.Thread(target=finish)
        thread.start()
        self.addCleanup(thread.join)
        return second

    def test_a_read_during_a_write_waits_for_the_item_instead_of_refusing(self) -> None:
        """Prevents 'the ledger is not readable' for an item that is a few hundred milliseconds from being complete: the read
        looks again and lists both items."""
        second = self.file_item_slowly(0.8)
        result = self.world.run("list", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(second, result.stdout)

    def test_a_real_orphan_still_refuses_the_read(self) -> None:
        """Prevents hiding corruption: an events directory that never gets its item is still reported and refused."""
        ledger = self.world.ledger
        source = next((ledger / "events" / self.first).glob("*.json"))
        (ledger / "events" / "042yyyyyy").mkdir()
        shutil.copy(source, ledger / "events" / "042yyyyyy" / source.name)
        result = self.world.run("list")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("belongs to no item", result.stderr)


if __name__ == "__main__":
    unittest.main()
