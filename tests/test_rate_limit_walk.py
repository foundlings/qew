"""A secondary rate limit makes sync back off and retry instead of failing items, and a sync never starves an interactive verb.

Each test is a scenario; its docstring names the failure it prevents. Backoff is shrunk with QEW_RETRY_BASE_S.
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import filelock
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\ndivergences = \"report\"\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1", "QEW_RETRY_BASE_S": "0.01"}
RATE = {"errors": [{"type": "RATE_LIMITED", "message": "API rate limit already exceeded for user ID 1."}]}


class RateLimitWalk(unittest.TestCase):
    def rig(self) -> tuple[FakeGitHub, World]:
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\nproject_fields = {{ Phase = "field:phase" }}\n'
        world = World(mirrors, replication=OFF)
        self.addCleanup(world.close)
        self.assertEqual(world.run("project", "ensure", env=ENV).returncode, 0)
        return fake, world

    def value(self, fake: FakeGitHub) -> str | None:
        return fake.field_value("1", next(iter(fake.issues.values()))["node_id"], "Phase")

    def test_a_graphql_rate_limit_is_waited_out_and_the_item_still_syncs(self) -> None:
        """Prevents 489 failed updates: the first GraphQL answers 'API rate limit already exceeded', sync waits and goes on."""
        fake, world = self.rig()
        world.create("--title", "Phased", "--field", "phase=12", "--quote", QUOTE)
        fake.fail_next(200, "/graphql", count=3, body=RATE, skip=2)
        result = world.run("sync", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.value(fake), "12")
        self.assertIn("rate limited", result.stdout + result.stderr)

    def test_github_names_the_limit_only_in_the_message_and_it_is_still_waited_out(self) -> None:
        """Prevents the real answer slipping through: GitHub sends 'API rate limit already exceeded' with no RATE_LIMITED type."""
        fake, world = self.rig()
        world.create("--title", "Phased", "--field", "phase=12", "--quote", QUOTE)
        fake.fail_next(200, "/graphql", count=3, skip=2, body={"errors": [{"message": "API rate limit already exceeded for user ID 5199263."}]})
        result = world.run("sync", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.value(fake), "12")

    def test_a_rest_secondary_limit_on_an_update_is_waited_out(self) -> None:
        """Prevents a PATCH answered 403 'secondary rate limit' failing the item: it is not a create, so it is repeated."""
        fake, world = self.rig()
        item = world.create("--title", "Old title", "--quote", QUOTE)
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        world.run("set", item, "--title", "New title")
        fake.fail_next(403, "PATCH /issues", count=2, headers={"Retry-After": "0"},
                       body={"message": "You have exceeded a secondary rate limit. Please wait a few minutes before you try again."})
        result = world.run("sync", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(next(iter(fake.issues.values()))["title"], "New title")

    def test_a_create_refused_by_the_plain_rate_limit_is_waited_out_but_a_secondary_one_is_not_repeated(self) -> None:
        """Prevents a refused create failing the item: 'API rate limit exceeded' was not applied, so it is sent again, once."""
        fake, world = self.rig()
        world.create("--title", "Only", "--quote", QUOTE)
        fake.fail_next(403, "POST /issues", count=2, body={"message": "API rate limit exceeded for user ID 1. Please wait."})
        result = world.run("sync", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([i["title"] for i in fake.issues.values()], ["Only"])

    def test_a_limit_that_never_lifts_stops_the_sync_with_a_retry_time_not_a_failure_per_item(self) -> None:
        """Prevents hammering: when the retries run out the target stops once, naming the wait, and a rerun continues."""
        fake, world = self.rig()
        world.create("--title", "One", "--field", "phase=1", "--quote", QUOTE)
        world.create("--title", "Two", "--field", "phase=2", "--quote", QUOTE)
        fake.fail_next(200, "/graphql", count=1000, body=RATE, skip=2)
        result = world.run("sync", env={**ENV, "QEW_RATE_RETRIES": "2"})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("retry after", result.stdout + result.stderr)
        fake.failures.clear()
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)


class SyncYieldsWalk(unittest.TestCase):
    def test_a_verb_gets_the_lock_while_a_sync_keeps_asking_for_it(self) -> None:
        """Prevents `pr ready` waiting minutes behind the background sync: a sync that wants the lock again yields to a waiter."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock"
            stop = threading.Event()

            def sync() -> None:
                while not stop.is_set():
                    with filelock.hold(path, verb="sync", wait_s=30, polite=True):
                        time.sleep(0.2)

            thread = threading.Thread(target=sync)
            thread.start()
            self.addCleanup(thread.join)
            self.addCleanup(stop.set)
            time.sleep(0.3)
            begun = time.monotonic()
            with filelock.hold(path, verb="pr-ready", wait_s=10):
                waited = time.monotonic() - begun
            self.assertLess(waited, 2.0, f"the verb waited {waited:.1f}s behind a polite sync")


if __name__ == "__main__":
    unittest.main()
