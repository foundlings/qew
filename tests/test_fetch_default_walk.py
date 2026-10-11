"""A read fetches the ledger remote at most every 10 s by default: at 2 s nearly every command paid the fetch round trip (1.3 s)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.ledger_config import Replication, _replication


class FetchDefaultWalk(unittest.TestCase):
    def test_default_interval_is_ten_seconds(self) -> None:
        """Failure prevented: a ledger without [replication] fetches on nearly every read."""
        self.assertEqual(Replication().fetch_interval_s, 10.0)
        self.assertEqual(_replication({}).fetch_interval_s, 10.0)

    def test_configured_interval_still_wins(self) -> None:
        """Failure prevented: the new default overriding a ledger's own setting."""
        self.assertEqual(_replication({"fetch_interval_s": 3}).fetch_interval_s, 3)


if __name__ == "__main__":
    unittest.main()
