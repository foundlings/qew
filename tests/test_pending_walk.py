"""Pending forge work is derived from event ids and mirror files, never queued."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import QUOTE, World

ENV = {"FORGEJO_TOKEN": TOKEN}


class PendingWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        mirrors = (f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
                   'select = "kind:issue"\n')
        self.world = World(mirrors, replication="\n[replication]\nauto_forge_sync = false\n")
        self.addCleanup(self.world.close)

    def pending(self) -> list:
        return [line.split()[0] for line in self.world.run("sync", "--dry-run", env=ENV).stdout.splitlines() if line.split()[0] in ("create", "update")]

    def test_pending_follows_the_newest_event_against_the_mirror(self) -> None:
        """Prevents lost updates and repeat pushes: new item = create, behind = update, current = nothing, selected-out = never."""
        w = self.world
        item = w.create("--kind", "issue", "--title", "A")
        w.create("--kind", "epic", "--title", "Not selected")
        self.assertEqual(self.pending(), ["create"])
        w.run("sync", env=ENV)
        self.assertEqual(self.pending(), [])
        w.run("draft", "approve", item, "--quote", QUOTE)
        self.assertEqual(self.pending(), ["update"])
        w.run("sync", env=ENV)
        self.assertEqual(self.pending(), [])

    def test_a_mirror_claiming_a_future_event_is_a_check_problem(self) -> None:
        """Prevents a hand-edited synced_event hiding pending work forever."""
        w = self.world
        item = w.create("--kind", "issue", "--title", "A")
        w.run("sync", env=ENV)
        path = next((w.ledger / "mirrors" / item).glob("*.json"))
        data = json.loads(path.read_text())
        data["synced_event"] = "zzzzzzzzz"
        path.write_text(json.dumps(data))
        result = w.run("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("synced_event", result.stdout)


if __name__ == "__main__":
    unittest.main()
