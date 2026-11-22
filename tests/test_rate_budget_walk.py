"""The forge call budget: an exhausted primary limit is waited out only when it lifts soon, and background sync keeps a reserve.

Adversary: a shared token drained to 0 by background pulls and retries into a 403 wall, so every merge fails; and a 403 that says
`x-ratelimit-remaining: 0` and a reset an hour away being retried six times into the same wall.
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\ndivergences = \"report\"\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1", "QEW_RETRY_BASE_S": "0.01"}
EXHAUSTED = {"message": "API rate limit exceeded for user ID 1. Please wait."}


class RateBudgetWalk(unittest.TestCase):
    def rig(self) -> tuple[FakeGitHub, World]:
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'
        world = World(mirrors, replication=OFF)
        self.addCleanup(world.close)
        return fake, world

    def listings(self, fake: FakeGitHub) -> int:
        return len([r for r in fake.requests if r[0] == "GET" and "/issues?" in r[1]])

    def test_an_exhausted_limit_that_lifts_in_an_hour_is_reported_once_not_retried(self) -> None:
        """Prevents six more requests into a wall the forge said stays up for an hour: the first 403 stops the pull and names the reset."""
        fake, world = self.rig()
        world.create("--title", "Only", "--quote", QUOTE)
        reset = str(int(time.time()) + 3000)
        fake.fail_next(403, "GET /issues", count=1000, body=EXHAUSTED, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset})
        result = world.run("sync", "--full", env=ENV)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLessEqual(self.listings(fake), 2, self.listings(fake))
        self.assertRegex(result.stdout + result.stderr, r"retry after|resets")

    def test_an_exhausted_limit_that_lifts_in_seconds_is_waited_until_its_reset(self) -> None:
        """Prevents retrying on a 30 s guess when the forge named the second the quota returns."""
        fake, world = self.rig()
        world.create("--title", "Only", "--quote", QUOTE)
        reset = str(int(time.time()) + 2)
        fake.fail_next(403, "GET /issues", count=1, body=EXHAUSTED, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset})
        started = time.time()
        result = world.run("sync", "--full", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreaterEqual(time.time() - started, 1.0)

    def test_the_background_sync_stops_at_the_reserve_and_leaves_the_budget_for_merges(self) -> None:
        """Prevents a background pass spending the token to 0: with fewer calls left than the reserve it makes no more requests."""
        fake, world = self.rig()
        world.create("--title", "Only", "--quote", QUOTE)
        fake.quota = {"x-ratelimit-remaining": "100", "x-ratelimit-reset": str(int(time.time()) + 3000)}
        before = len(fake.requests)
        world.run("sync", env={**ENV, "QEW_BACKGROUND": "1"})
        self.assertLessEqual(len(fake.requests) - before, 2, fake.requests[before:])
        log = (world.ledger / ".qew" / "sync.log").read_text() if (world.ledger / ".qew" / "sync.log").exists() else ""
        self.assertIn("reserve", log)
        interactive = len(fake.requests)
        self.assertEqual(world.run("sync", "--all", env=ENV).returncode, 0)  # a person's verb is never held back by the reserve
        self.assertGreater(len(fake.requests) - interactive, 2)


if __name__ == "__main__":
    unittest.main()
