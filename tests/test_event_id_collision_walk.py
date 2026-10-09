"""Two machines minting an event after the same predecessor in the same second must almost never pick the same id.

Same ids on two machines are the one thing that can make an event file conflict in git (the base requirement is
conflict-free merges). Each trial resets the process state to emulate a second machine.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_ids


def mint(after: str, now: float) -> str:
    ledger_ids._last = (0, 0)  # a fresh process
    with mock.patch.object(ledger_ids.time, "time", return_value=now):
        return ledger_ids.event_id(after)


class EventIdCollisionWalk(unittest.TestCase):
    def test_two_machines_after_the_same_event_in_the_same_second_rarely_collide(self) -> None:
        """Prevents the +1 bump: both machines saw the same predecessor from this very second and chose its successor,
        giving identical event ids (about half the time) and a git add/add conflict on merge."""
        now = ledger_ids._EPOCH + 1_000_000.0
        after = ledger_ids._encode(1_000_000, ledger_ids._TIME_WIDTH) + ledger_ids._encode(900, 2)  # a late pair of that second
        collisions = sum(1 for _ in range(300) if mint(after, now) == mint(after, now))
        self.assertLessEqual(collisions, 6, f"{collisions} of 300 pairs of machines minted the same id")

    def test_ids_still_sort_after_their_predecessor_and_after_each_other(self) -> None:
        """Prevents the fix breaking order: every id is greater than the one it follows, within one process."""
        now = ledger_ids._EPOCH + 2_000_000.0
        ledger_ids._last = (0, 0)
        previous = ""
        with mock.patch.object(ledger_ids.time, "time", return_value=now):
            for _ in range(50):
                found = ledger_ids.event_id(previous)
                self.assertGreater(found, previous)
                previous = found


if __name__ == "__main__":
    unittest.main()
