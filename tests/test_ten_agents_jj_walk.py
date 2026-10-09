"""The ten-agent walk on a colocated jj ledger (its own module so the gate runs it beside the git one)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fleet_harness import run_fleet
from tests.ledger_harness import jj_available


@unittest.skipUnless(jj_available(), "jj is not installed")
class TenAgentsJjWalk(unittest.TestCase):
    def test_ten_agents_on_a_jj_ledger_with_killed_writers_converge(self) -> None:
        """Prevents the same failures on a colocated jj ledger."""
        run_fleet(self, "jj")


if __name__ == "__main__":
    unittest.main()
