"""Three agents on three checkouts of one ledger converge: the scenario the replication design exists for."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import QUOTE, World

EVERY = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\n"
OFFLINE = {"QEW_OFFLINE": "1"}


def rows(world: World) -> list:
    return sorted((r["id"], r["state"], r["title"]) for r in map(json.loads, world.run("list", "--json").stdout.splitlines()))


class MultiAgentWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.env = {"FORGEJO_TOKEN": TOKEN}
        self.a = World(mirrors, ledger_vcs="git", remote=True, replication=EVERY)
        self.addCleanup(self.a.close)
        self.b = self.a.clone("b", mirrors=mirrors, replication=EVERY)
        self.c = self.a.clone("c", mirrors=mirrors, replication=EVERY)

    def settle(self) -> None:
        for world in (self.a, self.b, self.c, self.a, self.b):
            self.assertEqual(world.run("ready").returncode, 0)

    def test_create_claim_complete_and_offline_work_converge_on_every_checkout(self) -> None:
        """Prevents divergent ledgers: A files work, B finishes it, C works offline, and after reconnecting all three agree."""
        item = self.a.create("--title", "Shared work", "--quote", QUOTE)
        self.b.run("list")
        self.assertEqual(self.b.run("start", item).returncode, 0)
        sha = self.b.commit("the work")
        self.b.run("evidence", item, "--change", sha)
        self.assertEqual(self.b.run("complete", item).returncode, 0)
        offline = self.c.run("create", "--title", "Written while offline", "--quote", QUOTE, env=OFFLINE).stdout.split()[0]
        self.settle()
        self.assertEqual(rows(self.a), rows(self.b))
        self.assertEqual(rows(self.b), rows(self.c))
        states = {row[0]: row[1] for row in rows(self.a)}
        self.assertEqual((states[item], states[offline]), ("completed", "approved"))
        for world in (self.a, self.b, self.c):
            self.assertEqual(world.run("check").returncode, 0)
            self.assertEqual(world.ledger_dirty(), [])

    def test_concurrent_creates_get_distinct_ids_and_a_forge_close_diverges_everywhere(self) -> None:
        """Prevents id collisions and silent forge overrides: simultaneous creates stay distinct, a forge-side close is a divergence on all."""
        first = self.a.run("create", "--title", "From A", "--quote", QUOTE, env=OFFLINE).stdout.split()[0]
        second = self.b.run("create", "--title", "From B", "--quote", QUOTE, env=OFFLINE).stdout.split()[0]
        self.assertNotEqual(first, second)
        self.settle()
        self.assertEqual(self.a.run("sync", env=self.env).returncode, 0)
        number = next(iter(self.fake.issues))
        self.fake.issues[number]["state"] = "closed"
        self.a.run("sync", env=self.env)
        self.settle()
        for world in (self.a, self.b, self.c):
            status = json.loads(world.run("status", "--json").stdout)
            self.assertEqual([d["field"] for d in status["divergences"]], ["state"])
            self.assertEqual(rows(world), rows(self.a))


if __name__ == "__main__":
    unittest.main()
