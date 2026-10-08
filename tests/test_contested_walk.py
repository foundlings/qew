"""Contradictions found in a merged ledger surface as divergences; the authority, never a clock, resolves them.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World

FIRST, SECOND = "zzzzzzzza", "zzzzzzzzb"


def claim(world: World, item: str, name: str, owner: str, at: str) -> None:
    body = {"type": "claimed", "at": at, "actor": owner, "owner": owner}
    (world.ledger / "events" / item / f"{name}.json").write_text(json.dumps(body))


def snapshot_files(world: World) -> dict:
    return {str(p): p.read_bytes() for p in sorted(world.ledger.rglob("*")) if p.is_file() and ".qew" not in p.parts}


class ContestedClaimWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        self.item = self.world.create("--title", "Contested", "--quote", QUOTE)
        # A hand merge of two offline claims: the later event id carries the EARLIER clock reading.
        claim(self.world, self.item, FIRST, "alice", "2026-12-01T00:00:00Z")
        claim(self.world, self.item, SECOND, "bob", "2026-01-01T00:00:00Z")

    def test_a_hand_merged_double_claim_shows_one_divergence_and_keeps_the_ledger_readable(self) -> None:
        """Prevents a double claim bricking every read or going unseen: one open divergence, reads still work."""
        status = self.world.run("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("divergences=1", status.stdout)
        self.assertIn(f"{SECOND} {self.item} owner", status.stdout)
        self.assertEqual(self.world.run("list").returncode, 0)
        checked = self.world.run("check")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn("contested", checked.stdout)

    def test_status_and_reads_never_resolve_it_by_timestamp(self) -> None:
        """Prevents last-writer-wins: the later clock reading does not decide, nothing is written, the contest stays open."""
        before = snapshot_files(self.world)
        for command in (("status",), ("show", self.item), ("list",), ("status",)):
            self.assertEqual(self.world.run(*command).returncode, 0)
        self.assertEqual(snapshot_files(self.world), before)
        self.assertIn("owner: alice", self.world.run("show", self.item).stdout)
        self.assertIn("divergences=1", self.world.run("status").stdout)

    def test_accept_hands_the_item_to_the_contender_and_reject_keeps_the_holder(self) -> None:
        """Prevents an unresolvable contest: accept and reject each settle it explicitly and the divergence closes."""
        accepted = self.world.run("accept", SECOND)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("owner: bob", self.world.run("show", self.item).stdout)
        self.assertIn("divergences=0", self.world.run("status").stdout)
        self.assertEqual(self.world.run("check").returncode, 0)
        other = World()
        self.addCleanup(other.close)
        item = other.create("--title", "Contested again", "--quote", QUOTE)
        claim(other, item, FIRST, "alice", "2026-12-01T00:00:00Z")
        claim(other, item, SECOND, "bob", "2026-01-01T00:00:00Z")
        self.assertEqual(other.run("reject", SECOND).returncode, 0)
        self.assertIn("owner: alice", other.run("show", item).stdout)
        self.assertIn("divergences=0", other.run("status").stdout)


if __name__ == "__main__":
    unittest.main()
