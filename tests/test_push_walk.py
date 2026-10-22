"""`qew push` sends pending changes to the forge, and a bare `qew sync` is refused so an agent notices it needs a direction.

Each test is a scenario; its docstring names the failure it prevents (seen live: an agent that only wanted to send one closed
issue to the forge ran a bare `qew sync`, which read the whole board and printed a page of output).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

REPLICATION = "\n[replication]\nauto_forge_sync = false\nforge_concurrency = 1\nfetch_interval_s = 0\ndivergences = \"report\"\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class PushWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, ledger_vcs="git", replication=REPLICATION)
        self.addCleanup(self.world.close)

    def test_push_writes_the_pending_item_and_says_so_in_one_line(self) -> None:
        """Prevents an agent paying for a whole-board sync to send one change: push writes only what is pending, names the count,
        and a second push has nothing to do."""
        self.world.create("--kind", "issue", "--title", "Pending", "--quote", QUOTE)
        self.assertEqual(len(self.fake.issues), 0)
        result = self.world.run("push", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.fake.issues), 1)
        self.assertIn("pushed 1 item", result.stdout)
        again = self.world.run("push", env=ENV)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("nothing pending", again.stdout)

    def test_a_bare_sync_is_refused_and_names_the_ways_forward(self) -> None:
        """Prevents an agent running the expensive whole-board sync by habit: a bare `qew sync` exits 2, touches no forge, and says
        to push, pull or pick an option."""
        self.world.create("--kind", "issue", "--title", "Pending", "--quote", QUOTE)
        seen = len(self.fake.requests)
        result = self.world.run("sync", env=ENV)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        message = result.stdout + result.stderr
        for word in ("qew push", "qew pull", "--full"):
            self.assertIn(word, message)
        self.assertEqual(len(self.fake.requests), seen)
        self.assertEqual(len(self.fake.issues), 0)

    def test_a_sync_with_an_option_and_the_background_worker_still_sync(self) -> None:
        """Prevents the refusal breaking its own users: `qew sync --full` and `--dry-run` work as before, and the detached worker,
        which runs a bare sync, is not refused."""
        self.world.create("--kind", "issue", "--title", "Pending", "--quote", QUOTE)
        planned = self.world.run("sync", "--dry-run", env=ENV)
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        worker = self.world.run("sync", env={**ENV, "QEW_BACKGROUND": "1"})
        self.assertEqual(worker.returncode, 0, worker.stdout + worker.stderr)
        self.assertEqual(len(self.fake.issues), 1)
        full = self.world.run("sync", "--full", env=ENV)
        self.assertEqual(full.returncode, 0, full.stdout + full.stderr)


if __name__ == "__main__":
    unittest.main()
