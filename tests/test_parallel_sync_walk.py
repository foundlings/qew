"""Forge writes run side by side: same result as one at a time, no duplicates after a lost response, one pause for all on a rate limit.

Each test is a scenario; its docstring names the failure it prevents. Driven through `qew sync` against the GitHub fake,
which can delay every request so that concurrent requests overlap.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
EPIC_CHILDREN = 14


class Rig:
    """One ledger of an epic, its children and a needs chain, mirrored to one fake GitHub."""

    def __init__(self, test: unittest.TestCase, concurrency: int) -> None:
        self.fake = FakeGitHub().start()
        test.addCleanup(self.fake.stop)
        self.fake.delay = 0.03
        replication = f"\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = {concurrency}\n"
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=replication)
        test.addCleanup(self.world.close)
        epic = self.world.create("--kind", "epic", "--title", "Epic", "--quote", QUOTE)
        previous = ""
        self.titles: list[str] = ["Epic"]
        for n in range(EPIC_CHILDREN):
            extra = ["--needs", previous] if previous and n % 3 else []
            previous = self.world.create("--kind", "issue", "--title", f"Child {n}", "--parent", epic, *extra, "--quote", QUOTE)
            self.titles.append(f"Child {n}")

    def sync(self, *extra: str, env: dict[str, str] | None = None):
        return self.world.run("sync", *extra, env=ENV if env is None else env)

    def shape(self) -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
        """What the forge holds, independent of the numbers it handed out."""
        by_number: dict[int, str] = {n: i["title"] for n, i in self.fake.issues.items()}
        return sorted((i["title"], i["state"], tuple(sorted(i["labels"])), tuple(sorted(by_number[b] for b in i["blocked_by"])))
                      for i in self.fake.issues.values())


class ParallelSyncWalk(unittest.TestCase):
    def test_parallel_sync_gives_the_same_forge_as_one_at_a_time_and_overlaps_requests(self) -> None:
        """Prevents parallelism changing what lands on the forge (links, labels, states) or not overlapping at all."""
        parallel, serial = Rig(self, 4), Rig(self, 1)
        for rig in (parallel, serial):
            result = rig.sync()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(parallel.fake.issues), EPIC_CHILDREN + 1)
        self.assertEqual(parallel.shape(), serial.shape())
        self.assertGreater(parallel.fake.max_inflight, 1)
        self.assertEqual(serial.fake.max_inflight, 1)

    def test_a_create_whose_response_was_lost_is_adopted_not_duplicated_on_the_rerun(self) -> None:
        """Prevents a killed or failed parallel run leaving duplicates: the applied create is found by its marker."""
        rig = Rig(self, 4)
        rig.fake.fail_next(500, "widgets/issues", count=1, skip=6, after=True)
        rig.sync()
        again = rig.sync()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        titles = [i["title"] for i in rig.fake.issues.values()]
        self.assertEqual(sorted(titles), sorted(rig.titles))

    def test_a_rate_limit_stops_every_worker_and_the_rerun_finishes(self) -> None:
        """Prevents workers hammering a rate-limited forge: after the first 429 no worker starts another item."""
        rig = Rig(self, 4)
        rig.fake.fail_next(429, "", count=1000, skip=12, headers={"Retry-After": "60"})
        result = rig.sync(env={**ENV, "QEW_RATE_RETRIES": "0"})  # waiting is tested in test_rate_limit_walk; here the limit never lifts
        total = len(rig.fake.requests)
        self.assertIn("retry after 60s", result.stdout + result.stderr)
        self.assertLess(total, 12 + 4 * 6, "workers kept sending requests after the limit")
        rig.fake.failures.clear()
        self.assertEqual(rig.sync().returncode, 0)
        self.assertEqual(sorted(i["title"] for i in rig.fake.issues.values()), sorted(rig.titles))


if __name__ == "__main__":
    unittest.main()
