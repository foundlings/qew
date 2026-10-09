"""Ten agents on a git ledger with chosen writers killed mid-session converge (the jj twin is test_ten_agents_jj_walk)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fleet_harness import run_fleet


class TenAgentsWalk(unittest.TestCase):
    def test_ten_agents_on_a_git_ledger_with_killed_writers_converge(self) -> None:
        """Prevents stale locks, duplicate items, tracebacks, dirty checkouts and a diverged remote under ten parallel agents."""
        run_fleet(self, "git")


if __name__ == "__main__":
    unittest.main()
