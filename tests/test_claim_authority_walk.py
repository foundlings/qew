"""Claims are arbitrated by exactly one authority: the primary forge, or the ledger remote when there is none.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import PROJECT_ROOT, QUOTE, World, git

ENV = {"FORGEJO_TOKEN": TOKEN}
QUIET = "\n[replication]\nauto_forge_sync = false\nauto_push = false\n"


def types(world: World, item: str) -> list:
    return [e["type"] for e in world.events(item)]


class PrimaryForgeWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        self.slug = f"forgejo-{self.fake.repo_url.split('//')[1].replace('/', '-')}"
        mirrors = f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.a = World(mirrors, replication=QUIET + f'\n[authority]\nprimary = "{self.slug}"\n')
        self.addCleanup(self.a.close)
        self.item = self.a.create("--title", "Contested", "--quote", QUOTE)
        self.assertEqual(self.a.run("sync", env=ENV).returncode, 0)
        self.b = self.second_checkout()

    def second_checkout(self) -> World:
        """Another agent: a copy of the ledger with the same config, sharing only the forge."""
        other = World.__new__(World)
        other._owner = False
        other._temporary = self.a._temporary
        other.base = self.a.base
        other.ledger_vcs = None
        other.code = self.a.base / "code-b"
        other.ledger = self.a.base / "ledger-b"
        shutil.copytree(self.a.code, other.code)
        shutil.copytree(self.a.ledger, other.ledger)
        config = other.code / ".qew" / "config.toml"
        config.write_text(config.read_text().replace('"../ledger"', '"../ledger-b"'))
        return other

    def test_the_first_claimant_wins_and_the_second_is_refused_without_writing(self) -> None:
        """Prevents two owners: two checkouts racing one item end with exactly one claim in the ledger."""
        won = self.a.run("start", self.item, env=ENV)
        self.assertEqual(won.returncode, 0, won.stderr)
        self.assertEqual(types(self.a, self.item)[-2:], ["claimed", "claim_confirmed"])
        before = types(self.b, self.item)
        lost = self.b.run("start", self.item, env={**ENV, "QEW_ACTOR": "second"})
        self.assertEqual(lost.returncode, 2)
        self.assertIn("claimed by tester on the primary", lost.stderr)
        self.assertEqual(types(self.b, self.item), before)

    def test_a_primary_that_is_down_refuses_the_claim_and_writes_nothing(self) -> None:
        """Prevents offline claims: with the arbiter unreachable no claimed event exists."""
        self.fake.down = True
        result = self.a.run("start", self.item, env=ENV)
        self.assertEqual(result.returncode, 3)
        self.assertIn("primary", result.stderr)
        self.assertNotIn("claimed", types(self.a, self.item))

    def test_an_item_the_primary_has_never_seen_cannot_be_claimed(self) -> None:
        """Prevents claiming around the arbiter: no mirror on the primary means a sync is needed first."""
        fresh = self.a.create("--title", "Unsynced", "--quote", QUOTE)
        result = self.a.run("start", fresh, env=ENV)
        self.assertEqual(result.returncode, 2)
        self.assertIn("qew sync", result.stderr)

    def test_releasing_frees_the_item_for_the_next_claimant(self) -> None:
        """Prevents a released item staying locked on the forge forever."""
        self.a.run("start", self.item, env=ENV)
        self.assertEqual(self.a.run("release", self.item, env=ENV).returncode, 0)
        again = self.b.run("start", self.item, env={**ENV, "QEW_ACTOR": "second"})
        self.assertEqual(again.returncode, 0, again.stderr)


class LedgerRemoteArbitrationWalk(unittest.TestCase):
    def setUp(self) -> None:
        every = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\n"
        self.a = World(ledger_vcs="git", remote=True, replication=every)
        self.addCleanup(self.a.close)
        self.b = self.a.clone("b", replication=every)
        self.item = self.a.create("--title", "Contested", "--quote", QUOTE)
        self.b.run("list")

    def hold_pushes(self) -> None:
        """Make every push to the ledger remote take a second so concurrent claimants really overlap."""
        hook = self.a.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nsleep 1\nexit 0\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

    def start(self, world: World, actor: str) -> subprocess.Popen:
        environment = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src"), "QEW_ACTOR": actor,
                       "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.invalid",
                       "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.invalid"}
        return subprocess.Popen([sys.executable, "-m", "qew.cli", "start", self.item], cwd=world.code, env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_two_clones_racing_one_claim_end_with_exactly_one_owner_everywhere(self) -> None:
        """Prevents two owners without a primary forge: the ledger remote lets one claim land; the other records claim_lost."""
        self.hold_pushes()
        first, second = self.start(self.a, "alice"), self.start(self.b, "bob")
        codes = sorted([first.wait(timeout=90), second.wait(timeout=90)])
        self.assertEqual(codes, [0, 2], [first.stderr.read(), second.stderr.read()])
        for world in (self.a, self.b, self.a):
            world.run("ready")
        shown_a, shown_b = self.a.run("show", self.item).stdout, self.b.run("show", self.item).stdout
        self.assertEqual(shown_a, shown_b)
        owner = [line for line in shown_a.splitlines() if line.startswith("owner:")]
        self.assertEqual(len(owner), 1)
        all_types = types(self.a, self.item)
        self.assertEqual(all_types.count("claim_lost"), 1)
        self.assertEqual(self.a.run("check").returncode, 0)

    def test_a_ledger_without_a_remote_confirms_at_once(self) -> None:
        """Prevents a single-writer ledger needing a network it does not have."""
        w = World(ledger_vcs="git")
        self.addCleanup(w.close)
        item = w.create("--title", "Solo", "--quote", QUOTE)
        self.assertEqual(w.run("start", item).returncode, 0)
        self.assertEqual(types(w, item)[-2:], ["claimed", "claim_confirmed"])

    def test_a_configured_but_unreachable_remote_refuses_the_claim(self) -> None:
        """Prevents offline claims: with the arbiter away nothing is written."""
        self.a.remote.rename(self.a.remote.with_name("away.git"))
        result = self.a.run("start", self.item)
        self.assertEqual(result.returncode, 3)
        self.assertNotIn("claimed", types(self.a, self.item))


if __name__ == "__main__":
    unittest.main()
