"""Walks of GitHub rate limits: waits are typed, a pass stops at the first one, the worker backs off.

Adversary: a 403 that is a rate limit mistaken for bad credentials (or the reverse), a worker
that keeps hammering a limited forge, a resumed pass that duplicates what the first one made,
and a sync that spends API calls per card when nothing changed.
"""

import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

from qew import cmd_project, cmd_sync, ledger_config, syncworker
from qew.forge import ForgeAuth, ForgeUnavailable
from qew.forge_github import GitHub
from qew.forge_http import urllib_transport
from qew.ledger_config import Mirror
from qew.ledger_store import Ledger
from qew.sync import Target, sync_target

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class RateLimitWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        self.mirrors = f'[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'

    def backend(self) -> GitHub:
        return GitHub(Mirror("github", self.fake.repo_url, None, "", {"api": self.fake.api}), urllib_transport(), ENV)

    def world(self) -> World:
        w = World(self.mirrors)
        self.addCleanup(w.close)
        return w

    def sync(self, w: World, forge=None):
        config = ledger_config.load(w.code)
        mirror = cmd_sync.mirrors_with_boards(Ledger(w.ledger, "TST"), config)[0]
        target = Target(mirror, ledger_config.mirror_slug(mirror), forge or GitHub(mirror, urllib_transport(), ENV))
        return sync_target(Ledger(w.ledger, "TST"), target, config)

    def test_rate_limit_answers_carry_their_wait_and_a_plain_403_stays_an_auth_failure(self) -> None:
        """Prevents retrying bad credentials forever, or hammering a forge that said how long to wait."""
        self.fake.fail_next(403, "/api/v3", headers={"Retry-After": "90"})
        with self.assertRaises(ForgeUnavailable) as caught:
            self.backend().read_issue(1)
        self.assertEqual(caught.exception.retry_after, 90)
        reset = str(int(time.time()) + 300)
        self.fake.fail_next(403, "/api/v3", headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset})
        with self.assertRaises(ForgeUnavailable) as caught:
            self.backend().read_issue(1)
        self.assertTrue(250 <= caught.exception.retry_after <= 301, caught.exception.retry_after)
        self.fake.fail_next(429, "/api/v3")
        with self.assertRaises(ForgeUnavailable) as caught:
            self.backend().read_issue(1)
        self.assertGreater(caught.exception.retry_after, 0)
        self.fake.fail_next(403, "/api/v3", body={"message": "Bad credentials"})
        with self.assertRaises(ForgeAuth):
            self.backend().read_issue(1)

    def test_a_limited_pass_stops_at_once_records_the_wait_and_a_later_pass_resumes_without_duplicates(self) -> None:
        """Prevents a sync hammering a limited forge, and a resume creating an issue twice."""
        w = self.world()
        for n in range(3):
            w.create("--kind", "issue", "--title", f"Item {n}", "--quote", QUOTE)
        self.fake.fail_next(403, "/issues", count=1000, skip=3, headers={"Retry-After": "120"})
        report = self.sync(w)
        self.assertEqual(report.created, 1)
        self.assertEqual(report.retry_after, 120)
        self.assertIsNotNone(report.stopped)
        touched = [r for r in self.fake.requests if "/issues" in r[1]]
        self.assertEqual(len(touched), 4, "lookup, create and re-check of the first item, then one refused lookup; nothing after")
        self.fake.failures.clear()
        again = self.sync(w)
        self.assertEqual((again.created, again.failures), (2, []))
        self.assertEqual(len(self.fake.issues), 3)

    def test_the_worker_records_the_earliest_retry_and_neither_it_nor_status_hammers(self) -> None:
        """Prevents a write or a read command restarting the worker while the forge asked for a pause."""
        w = self.world()
        w.create("--kind", "issue", "--title", "Limited", "--quote", QUOTE)
        self.fake.fail_next(403, "/issues", count=1000, headers={"Retry-After": "600"})
        w.run("sync", env={**ENV, "QEW_BACKGROUND": "1"})
        pending = (w.ledger / ".qew" / "sync.pending")
        self.assertTrue(pending.exists())
        config = ledger_config.load(w.code)
        wait = syncworker.retry_at(config) - time.time()
        self.assertTrue(500 < wait <= 601, wait)
        self.assertRegex(w.run("status").stdout, r"retry_after=\d+")
        with mock.patch.object(syncworker, "queue") as queue:
            syncworker.retry_if_pending(config)
            queue.assert_not_called()
            pending.write_text(f"{time.time()}\nretry_at={time.time() - 1}\n", encoding="utf-8")
            syncworker.retry_if_pending(config)
            queue.assert_called_once()

    def test_an_unchanged_board_costs_no_per_card_calls_and_the_project_is_read_once(self) -> None:
        """Prevents the rate limit being spent on re-reading the project and every card each sync."""
        w = self.world()
        for n in range(5):
            w.create("--kind", "issue", "--title", f"Card {n}", "--quote", QUOTE)
        config = ledger_config.load(w.code)
        cmd_project.ensure(config, None, build=lambda m: GitHub(m, urllib_transport(), ENV))
        first = self.sync(w)
        self.assertEqual(first.failures, [])
        self.assertLessEqual(self.fake.graphql_operations().count("OwnerProjects"), 3)
        self.fake.requests.clear()
        second = self.sync(w)
        self.assertEqual((second.created, second.updated, second.failures), (0, 0, []))
        rest = [r for r in self.fake.requests if not r[1].endswith("/api/graphql")]
        self.assertEqual(rest, [], "no per-card REST reads")
        self.assertLessEqual(len(self.fake.graphql_operations()), 3)
        self.assertNotIn("ItemPlacement", self.fake.graphql_operations())


if __name__ == "__main__":
    unittest.main()
