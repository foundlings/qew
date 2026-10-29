"""A whole-ledger command lists the shard tree once, not once per item.

Adversary: the foundlings ledger after the schema 8 migration (25,000 items in about 1,200 shards). Every per-item lookup of an
item's other ids asked for the alias map, and the map re-stamped the whole shard tree (a scan plus one stat per shard) each
time, so `check`, `sync` and the pull request commands took tens of minutes of pure CPU.
"""

import subprocess
import sys
import unittest
from pathlib import Path

from tests.ledger_harness import QUOTE, World

SRC = Path(__file__).resolve().parents[1] / "src"
COUNT = """
import os, sys
sys.path.insert(0, {src!r})
from qew import cli, ledger_layout
calls = [0]
real = ledger_layout.stamp
def counted(*arguments, **keywords):
    calls[0] += 1
    return real(*arguments, **keywords)
ledger_layout.stamp = counted
try:
    cli.main({command!r})
except SystemExit:
    pass
print("STAMPS", calls[0])
"""


class AliasScanCostWalk(unittest.TestCase):
    def stamps(self, world: World, command: list[str]) -> int:
        result = subprocess.run([sys.executable, "-c", COUNT.format(src=str(SRC), command=command)], cwd=world.code,
                                capture_output=True, text=True, timeout=120, check=False)
        line = next(line for line in result.stdout.splitlines() if line.startswith("STAMPS"))
        return int(line.split()[1])

    def test_check_and_sync_stamp_the_shard_tree_a_constant_number_of_times(self) -> None:
        """Prevents items x shards stats: 30 items must cost the same few stamps as 3 items would."""
        world = World(schema="8")
        self.addCleanup(world.close)
        for number in range(30):
            world.create("--title", f"Item {number}", "--quote", QUOTE)
        for command in (["check"], ["sync", "--all"]):
            self.assertLess(self.stamps(world, command), 8, command)


if __name__ == "__main__":
    unittest.main()
