"""Pacing learns the account's real budget: a secondary-limit block records how many creates came before it.

Each test is a scenario; its docstring names the failure it prevents. GitHub's documented limits are a guess for any one
account, so after a block the next run paces under what the block showed, and relaxes while no block happens.
"""

import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_config
from qew.forge_github import GitHub
from qew.forge_http import urllib_transport
from qew.ledger_store import Ledger
from qew.pacing import Pacer
from qew.sync import Target, sync_target
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class SeedWalk(unittest.TestCase):
    def test_creates_from_an_earlier_run_count_against_the_window(self) -> None:
        """Prevents each run starting with an empty window: two creates 10s ago leave 50s of a per-minute limit of two."""
        clock = Clock()
        pacer = Pacer(2, 0, clock, clock.sleep)
        pacer.seed([10.0, 10.0])
        pacer.acquire()
        self.assertEqual(clock.slept, [50.0])

    def test_creates_older_than_the_window_do_not_count(self) -> None:
        """Prevents an old run holding back a new one."""
        clock = Clock()
        pacer = Pacer(2, 0, clock, clock.sleep)
        pacer.seed([300.0, 400.0])
        pacer.acquire()
        self.assertEqual(clock.slept, [])


class AdaptiveWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        self.config = ledger_config.load(self.world.code)
        self.ledger = Ledger(self.world.ledger, "TST")
        mirror = self.config.mirrors[0]
        self.slug = ledger_config.mirror_slug(mirror)
        self.target = Target(mirror, self.slug, GitHub(mirror, urllib_transport(), ENV))
        for n in range(10):
            self.world.create("--kind", "issue", "--title", f"Item {n}", "--quote", QUOTE)

    def state(self) -> dict:
        path = self.world.ledger / ".qew" / "state" / self.slug / "pace.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    def test_a_block_records_the_budget_and_the_next_run_paces_under_it(self) -> None:
        """Prevents re-hitting the wall every round: the block after 6 creates makes the next run stop at 4 per window."""
        self.fake.secondary_after = 6
        first = sync_target(self.ledger, self.target, self.config)
        self.assertEqual(first.created, 6, first)
        self.assertEqual(self.state().get("budget"), 6)
        again = Target(self.target.mirror, self.slug, GitHub(self.target.mirror, urllib_transport(), ENV))
        second = sync_target(self.ledger, again, self.config, deadline=time.monotonic() + 2)
        self.assertEqual(second.created, 0, second)
        self.assertIn("paced", second.stopped or "")
        self.assertGreater(second.retry_after, 0)

    def test_a_clean_run_that_reached_the_learned_pace_raises_the_budget(self) -> None:
        """Prevents a one-off block capping the account's pace forever."""
        path = self.world.ledger / ".qew" / "state" / self.slug
        path.mkdir(parents=True, exist_ok=True)
        (path / "pace.json").write_text(json.dumps({"budget": 4, "span": 1800}))
        sync_target(self.ledger, self.target, self.config, deadline=time.monotonic() + 2)
        self.assertGreater(self.state()["budget"], 4)


if __name__ == "__main__":
    unittest.main()
