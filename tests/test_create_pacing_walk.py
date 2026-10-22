"""Creates are paced per forge backend, and a secondary-limit 403 backs off for minutes and says when to retry.

Each test is a scenario; its docstring names the failure it prevents. Pacing limits belong to a forge, not to qew:
GitHub.com has content-creation limits, other backends default to none, and a mirror can override either way.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_config
from qew.forge_forgejo import Forgejo
from qew.forge_github import GitHub
from qew.forge_http import urllib_transport
from qew.ledger_config import Mirror
from qew.ledger_store import Ledger
from qew.pacing import Pacer
from qew.sync import Target, sync_target
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
SECONDARY = {"message": "You have exceeded a secondary rate limit and have been temporarily blocked from content creation."}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class PacerWalk(unittest.TestCase):
    def test_the_pacer_spaces_creates_under_a_per_minute_and_a_per_hour_limit(self) -> None:
        """Prevents a burst past the limit: the third create in a minute waits for the first to leave the window."""
        clock = Clock()
        pacer = Pacer(2, 0, clock, clock.sleep)
        for _ in range(3):
            pacer.acquire()
        self.assertEqual(clock.slept, [60.0])
        hourly = Pacer(0, 2, clock, clock.sleep)
        clock.slept.clear()
        for _ in range(3):
            hourly.acquire()
        self.assertEqual(clock.slept, [3600.0])

    def test_no_limits_means_no_waiting(self) -> None:
        """Prevents pacing a backend that has no limit."""
        clock = Clock()
        pacer = Pacer(0, 0, clock, clock.sleep)
        for _ in range(50):
            pacer.acquire()
        self.assertEqual(clock.slept, [])

    def test_a_wait_longer_than_the_allowance_is_refused_without_reserving_a_slot(self) -> None:
        """Prevents a time-limited sync sleeping past its budget: it stops and says when the next create may go."""
        clock = Clock()
        pacer = Pacer(1, 0, clock, clock.sleep)
        pacer.acquire()
        self.assertEqual(pacer.acquire(allow=5.0), 60.0)
        self.assertEqual(clock.slept, [])


class BackendLimits(unittest.TestCase):
    def test_only_github_dot_com_has_a_default_creation_limit(self) -> None:
        """Prevents GitHub's limits being applied to GitHub Enterprise Server, Forgejo and Gitea, which differ."""
        dotcom = GitHub(Mirror("github", "https://github.com/o/r", None, "", {}), urllib_transport(), ENV)
        server = GitHub(Mirror("github", "https://ghe.example/o/r", None, "", {}), urllib_transport(), ENV)
        forgejo = Forgejo(Mirror("forgejo", "https://git.example/o/r", None, "", {}), urllib_transport(), ENV)
        self.assertEqual(dotcom.create_pace(), (70, 450))
        self.assertEqual(server.create_pace(), (0, 0))
        self.assertEqual(forgejo.create_pace(), (0, 0))


class PacedSyncWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)

    def rig(self, options: str = ""):
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n{options}'
        world = World(mirrors, replication=OFF)
        self.addCleanup(world.close)
        return world

    def test_a_mirror_limit_stops_a_time_boxed_sync_and_a_rerun_continues(self) -> None:
        """Prevents blowing through a configured creation limit: the sync stops as paced and nothing is lost."""
        import time
        world = self.rig("create_per_minute = 2\n")
        for n in range(5):
            world.create("--kind", "issue", "--title", f"Item {n}", "--quote", QUOTE)
        config = ledger_config.load(world.code)
        ledger = Ledger(world.ledger, "TST")
        mirror = config.mirrors[0]
        from qew.forge_github import GitHub as Backend
        target = Target(mirror, ledger_config.mirror_slug(mirror), Backend(mirror, urllib_transport(), ENV))
        report = sync_target(ledger, target, config, deadline=time.monotonic() + 2)
        self.assertEqual(report.created, 2, report)
        self.assertIn("paced", report.stopped or "")
        self.assertGreater(report.retry_after, 0)
        self.assertEqual(len(self.fake.issues), 2)

    def test_a_secondary_limit_403_backs_off_for_minutes_and_prints_when_to_retry(self) -> None:
        """Prevents 'retry after 60s' for a block that lasts minutes: the wait is 5 minutes and the clock time is shown."""
        world = self.rig()
        world.create("--kind", "issue", "--title", "Item", "--quote", QUOTE)
        self.fake.fail_next(403, "POST widgets/issues", count=5, body=SECONDARY)  # a create: never repeated, the pacer learns from it
        result = world.run("sync", "--all", env=ENV)
        text = result.stdout + result.stderr
        self.assertIn("retry after 300s", text)
        self.assertRegex(text, r"retry after 300s \(about \d\d:\d\d")


if __name__ == "__main__":
    unittest.main()
