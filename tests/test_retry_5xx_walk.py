"""A 5xx from the forge is retried with backoff on calls that are safe to repeat; an item that still fails is recorded and the
sync goes on; only a run of failed items stops it.

Each test is a scenario; its docstring names the failure it prevents. Backoff is shrunk with QEW_RETRY_BASE_S.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1", "QEW_RETRY_BASE_S": "0.01"}


class Retry5xxWalk(unittest.TestCase):
    def rig(self, options: str = "", replication: str = OFF):
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n{options}'
        world = World(mirrors, replication=replication)
        self.addCleanup(world.close)
        return fake, world

    def number(self, fake: FakeGitHub, title: str) -> int:
        return next(n for n, i in fake.issues.items() if i["title"] == title)

    def test_a_repeatable_call_is_retried_through_two_500s_and_the_sync_succeeds(self) -> None:
        """Prevents one GitHub hiccup stopping a whole round: the sub-issue call is repeated and the link is made."""
        fake, world = self.rig()
        world.create("--kind", "epic", "--title", "Phase", "--quote", QUOTE)
        epic = world.run("list").stdout.split()[0]
        world.create("--kind", "issue", "--title", "Work", "--parent", epic, "--quote", QUOTE)
        fake.fail_next(500, "/sub_issues", count=2)
        result = world.run("sync", "--all", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(fake.issues[self.number(fake, "Work")].get("parent"), self.number(fake, "Phase"))
        self.assertIn("retried", result.stdout + result.stderr)

    def test_a_create_is_never_retried_so_a_500_cannot_duplicate_it(self) -> None:
        """Prevents duplicates: an issue create answered 500 is attempted once, then recorded as failed."""
        fake, world = self.rig()
        world.create("--kind", "issue", "--title", "Only", "--quote", QUOTE)
        fake.fail_next(500, "POST /issues", count=1)
        result = world.run("sync", "--all", env=ENV)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        posts = [1 for method, path, _b in fake.requests if method == "POST" and path.endswith("/issues")]
        self.assertEqual(len(posts), 1)
        self.assertEqual(world.run("sync", "--all", env=ENV).returncode, 0)
        self.assertEqual([i["title"] for i in fake.issues.values()], ["Only"])

    def test_an_item_that_keeps_failing_is_recorded_and_the_others_are_still_synced(self) -> None:
        """Prevents one bad call halting the target: the failing item is named, the rest are created, the rerun finishes it."""
        fake, world = self.rig('forge_retries = 2\n')
        first = world.create("--kind", "epic", "--title", "Phase", "--quote", QUOTE)
        child = world.create("--kind", "issue", "--title", "Work", "--parent", first, "--quote", QUOTE)
        world.create("--kind", "issue", "--title", "Free one", "--quote", QUOTE)
        world.create("--kind", "issue", "--title", "Free two", "--quote", QUOTE)
        fake.fail_next(500, "/sub_issues", count=3)
        result = world.run("sync", "--all", env=ENV)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(child[:8], result.stdout + result.stderr)
        self.assertEqual(sorted(i["title"] for i in fake.issues.values()), ["Free one", "Free two", "Phase", "Work"])
        self.assertIsNone(fake.issues[self.number(fake, "Work")].get("parent"))
        self.assertEqual(world.run("sync", "--all", env=ENV).returncode, 0)
        self.assertEqual(fake.issues[self.number(fake, "Work")].get("parent"), self.number(fake, "Phase"))

    def test_a_run_of_failed_items_stops_the_target_and_a_rerun_continues(self) -> None:
        """Prevents hammering a forge that is down: after forge_failure_limit consecutive failed items the sync stops."""
        fake, world = self.rig(replication=OFF + "forge_failure_limit = 2\n")
        for name in ("One", "Two", "Three", "Four"):
            world.create("--kind", "issue", "--title", name, "--quote", QUOTE)
        fake.fail_next(500, "POST /issues", count=99)
        result = world.run("sync", "--all", env=ENV)
        self.assertNotEqual(result.returncode, 0)
        posts = [1 for method, path, _b in fake.requests if method == "POST" and path.endswith("/issues")]
        self.assertEqual(len(posts), 2)
        fake.failures.clear()
        self.assertEqual(world.run("sync", "--all", env=ENV).returncode, 0)
        self.assertEqual(sorted(i["title"] for i in fake.issues.values()), ["Four", "One", "Three", "Two"])

    def test_zero_retries_keeps_the_old_behaviour(self) -> None:
        """Prevents a surprise for anyone who wants the stop: forge_retries = 0 makes the first 500 final."""
        fake, world = self.rig('forge_retries = 0\n')
        epic = world.create("--kind", "epic", "--title", "Phase", "--quote", QUOTE)
        world.create("--kind", "issue", "--title", "Work", "--parent", epic, "--quote", QUOTE)
        fake.fail_next(500, "/sub_issues", count=1)
        result = world.run("sync", "--all", env=ENV)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(len([1 for _m, path, _b in fake.requests if "/sub_issues" in path]), 1)

    def test_only_calls_that_are_safe_to_repeat_are_classified_repeatable(self) -> None:
        """Prevents a retry that duplicates: creates and unknown mutations are not repeatable, queries and updates are."""
        import json
        from qew.forge import HttpRequest
        from qew.forge_retry import repeatable

        def graphql(text: str) -> HttpRequest:
            return HttpRequest("POST", "https://api.github.com/graphql", {}, json.dumps({"query": text}).encode())

        self.assertTrue(repeatable(HttpRequest("GET", "https://x/issues", {})))
        self.assertTrue(repeatable(HttpRequest("PATCH", "https://x/issues/1", {})))
        self.assertTrue(repeatable(HttpRequest("POST", "https://x/issues/1/sub_issues", {})))
        self.assertTrue(repeatable(graphql("query ItemPlacement($id: ID!) { node(id: $id) { id } }")))
        self.assertTrue(repeatable(graphql("mutation SetCardField($a: ID!) { x }")))
        self.assertFalse(repeatable(HttpRequest("POST", "https://x/issues", {})))
        self.assertFalse(repeatable(HttpRequest("POST", "https://x/issues/1/comments", {})))
        self.assertFalse(repeatable(graphql("mutation CreateProject($a: ID!) { x }")))
        self.assertFalse(repeatable(HttpRequest("POST", "https://x/graphql", {}, b"not json")))


if __name__ == "__main__":
    unittest.main()
