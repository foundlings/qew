"""A sync pushes the ledger commits it made, including the ones a checkpoint committed on the way.

Each test is a scenario; its docstring names the failure it prevents (seen live on foundlings: after a rename the first
`qew sync` ended with unpushed=1 and a second sync was needed).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
EVERY = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\nforge_concurrency = 1\n"


class CheckpointPushWalk(unittest.TestCase):
    def test_a_sync_whose_writes_were_all_checkpointed_still_pushes_its_commit(self) -> None:
        """Prevents `unpushed=1` after a clean sync: exactly one checkpoint's worth of mirror writes (25) leaves nothing for
        the session's final commit, and the checkpoint commit itself is never pushed."""
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'
        world = World(mirrors, ledger_vcs="git", remote=True, replication=EVERY)
        self.addCleanup(world.close)
        for n in range(25):
            world.run("create", "--kind", "issue", "--title", f"Item {n}", "--quote", QUOTE, env={"QEW_OFFLINE": "1"})
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        status = world.run("status").stdout
        self.assertIn("unpushed=0", status)
        self.assertIn("uncommitted=0", status)


if __name__ == "__main__":
    unittest.main()
