"""A refused sub-issue call: a relation the forge already holds is recorded once, never retried on every sync.

Each test is a scenario; its docstring names the failure it prevents (seen live: 98 refused sub_issues calls on a first
sync from a second machine used the hourly create budget and left the sync asleep for an hour).
"""

import json
import os
import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew.pacing import Pacer
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World, baseline_path

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\ndivergences = \"report\"\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class SubIssueRefusalWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        self.parent = self.world.create("--kind", "epic", "--title", "Parent", "--quote", QUOTE)
        self.child = self.world.create("--kind", "task", "--title", "Child", "--parent", self.parent, "--quote", QUOTE)
        self.assertEqual(self.world.run("sync", env=ENV).returncode, 0)
        self.numbers = {i["title"]: n for n, i in self.fake.issues.items()}

    def forget_parent(self) -> None:
        """What a second machine has: the relation exists on the forge, this machine never recorded setting it."""
        path = baseline_path(self.world.ledger, self.child)
        data = json.loads(path.read_text())
        data.pop("parent_number")
        path.write_text(json.dumps(data))

    def posts(self) -> int:
        return len([1 for method, path, _ in self.fake.requests if method == "POST" and path.endswith("/sub_issues")])

    def test_a_refusal_for_a_relation_the_forge_holds_is_recorded_and_not_retried(self) -> None:
        """Prevents the same child being posted again on every sync: the refusal is read back, the relation is there, and
        the parent is recorded, so the sync succeeds and the next one makes no sub-issue call."""
        self.forget_parent()
        self.fake.fail_next(422, "/sub_issues", count=1, body={"message": "Validation Failed"})
        result = self.world.run("sync", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(baseline_path(self.world.ledger, self.child).read_text())["parent_number"], self.numbers["Parent"])
        before = self.posts()
        self.assertEqual(self.world.run("sync", env=ENV).returncode, 0)
        self.assertEqual(self.posts(), before)

    def test_a_refusal_for_a_relation_the_forge_does_not_hold_still_fails(self) -> None:
        """Prevents hiding a real refusal: when the forge does not show the parent, the 422 is a failure the sync reports."""
        self.forget_parent()
        self.fake.issues[self.numbers["Child"]]["parent"] = None
        self.fake.fail_next(422, "/sub_issues", count=1, body={"message": "Validation Failed"})
        result = self.world.run("sync", env=ENV)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("422", result.stdout + result.stderr)

    def test_a_long_pace_wait_says_so_on_stderr(self) -> None:
        """Prevents a sync that looks hung: a wait of more than a minute for the create window is announced with its length."""
        now = [0.0]
        waited: list[float] = []
        pacer = Pacer(0, 1, clock=lambda: now[0], sleep=waited.append)
        pacer.acquire()
        captured = StringIO()
        with redirect_stderr(captured):
            pacer.acquire()
        self.assertEqual(waited, [3600.0])
        self.assertIn("3600", captured.getvalue())
        self.assertIn("create limit", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
