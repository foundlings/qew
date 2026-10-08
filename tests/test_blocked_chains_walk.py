"""Walks of `qew blocked --chains`: why an item is blocked, down to the root blocker.

Adversary: a long chain summarised only by its first unmet need (hiding the real root), a
dangling or closed need silently omitted so blocked work looks stuck for no reason, and a
needs cycle looping the walk forever.
"""

import json
import re
import unittest

from tests.ledger_harness import World
from tests.spec_loader import load


def item(item_id: str, needs=()) -> dict:
    return {"id": item_id, "kind": "issue", "title": f"Work {item_id}", "needs": list(needs)}


def chains(output: str) -> list:
    return [line.strip() for line in output.splitlines() if line.startswith("  chain:")]


class BlockedChainsWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_a_chain_of_three_is_shown_down_to_its_root_blocker(self) -> None:
        """D waits on C waits on B waits on claimed A; each blocked item names the whole path to A."""
        w = self.world
        load(w, item("A"), item("B", ["A"]), item("C", ["B"]), item("D", ["C"]))
        self.assertEqual(w.run("start", "A").returncode, 0)
        plain = w.run("blocked")
        self.assertEqual(chains(plain.stdout), [], "chains only appear when asked for")
        result = w.run("blocked", "--chains")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            chains(result.stdout),
            [
                "chain: B -> A [root claimed]",
                "chain: C -> B -> A [root claimed]",
                "chain: D -> C -> B -> A [root claimed]",
            ],
        )
        rows = [line for line in result.stdout.splitlines() if not line.startswith("  ")]
        self.assertEqual(rows, plain.stdout.splitlines(), "the plain rows are unchanged")
        data = {row["id"]: row for row in json.loads(w.run("blocked", "--chains", "--json").stdout)}
        self.assertEqual(data["D"]["chain"], ["D", "C", "B", "A"])
        self.assertEqual(data["D"]["root"], {"id": "A", "state": "claimed"})

    def test_the_longest_chain_wins_and_dangling_and_closed_needs_are_named(self) -> None:
        """A missing or closed need can never be met; the report must say so instead of omitting it."""
        w = self.world
        load(
            w,
            item("X"),
            item("Q", ["X"]),
            item("A"),
            item("B", ["A"]),
            item("L", ["Q", "B"]),
            item("M", ["A", "Q"]),
            item("P"),
        )
        self.assertEqual(w.run("close", "X", "--reason", "not_planned").returncode, 0)
        path = w.ledger / "items" / "P.md"
        path.write_text(re.sub(r"(?m)^needs:.*$", "needs: GONE", path.read_text()))
        result = w.run("blocked", "--chains")
        self.assertEqual(result.returncode, 0, result.stderr)
        found = chains(result.stdout)
        self.assertIn("chain: P -> GONE [root dangling]", found)
        self.assertIn("chain: Q -> X [root closed]", found)
        self.assertIn("chain: B -> A [root approved]", found)
        self.assertIn("chain: L -> B -> A [root approved]", found, "equal length: the lower id wins the tie")
        self.assertIn("chain: M -> Q -> X [root closed]", found, "the longer chain wins over the shorter one")

    def test_a_needs_cycle_is_refused_not_walked_forever(self) -> None:
        """Two items needing each other have no root blocker; say so rather than loop or guess one."""
        w = self.world
        load(w, item("P"), item("Q", ["P"]))
        path = w.ledger / "items" / "P.md"
        path.write_text(re.sub(r"(?m)^needs:.*$", "needs: Q", path.read_text()))
        result = w.run("blocked", "--chains")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("cycle", result.stderr)


if __name__ == "__main__":
    unittest.main()
