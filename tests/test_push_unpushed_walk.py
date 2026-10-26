"""`qew push --item A --item B ...` leaves the ledger caught up with its remote in one run.

Adversary (seen by wolf on 6.36.2): after a multi-item push `qew status` still showed unpushed above 0 and only a second
push, which said "nothing pending", caught it up. Each test names the shape it guards; none reproduced the report, so they
stand as the regression walk the item asked for.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World, jj_available

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
OFFLINE = {"QEW_OFFLINE": "1"}
EVERY = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\nforge_concurrency = 1\n"


class PushUnpushedWalk(unittest.TestCase):
    vcs = "git"

    def setUp(self) -> None:
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'
        self.world = World(mirrors, ledger_vcs=self.vcs, remote=True, replication=EVERY)
        self.addCleanup(self.world.close)
        self.ids = [self.world.run("create", "--kind", "issue", "--title", f"Item {n}", "--quote", QUOTE, env=OFFLINE).stdout.split()[0]
                    for n in range(4)]

    def status(self) -> str:
        return self.world.run("status").stdout

    def push(self, *items: str) -> str:
        run = self.world.run("push", *[a for item in items for a in ("--item", item)], env=ENV)
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def test_new_items_pushed_together_leave_nothing_unpushed(self) -> None:
        """Prevents the mirror-record commit landing after the ledger push: one push of every item ends at unpushed=0."""
        self.assertIn("unpushed=4", self.status())
        self.push(*self.ids)
        self.assertIn("unpushed=0", self.status())

    def test_a_subset_push_also_sends_the_commits_of_items_it_did_not_name(self) -> None:
        """Prevents a targeted push leaving the unnamed items' ledger commits behind: the ledger goes as a whole."""
        self.push(*self.ids[:2])
        self.assertIn("unpushed=0", self.status())

    def test_changed_mirrored_items_pushed_together_leave_nothing_unpushed(self) -> None:
        """Prevents updates to already-mirrored items needing a second push."""
        self.assertEqual(self.world.run("sync", "--all", env=ENV).returncode, 0)
        for item in self.ids:
            self.assertEqual(self.world.run("set", item, "--priority", "3", env=OFFLINE).returncode, 0)
        self.assertIn("unpushed=4", self.status())
        self.push(*self.ids)
        self.assertIn("unpushed=0", self.status())

    def test_a_push_with_no_forge_write_pending_still_catches_the_ledger_up(self) -> None:
        """Prevents 'nothing pending' leaving ledger commits unpushed: the first push says so and has already sent them."""
        self.assertEqual(self.world.run("sync", "--all", env=ENV).returncode, 0)
        self.assertEqual(self.world.run("set", self.ids[0], "--priority", "3", env=OFFLINE).returncode, 0)
        self.assertEqual(self.world.run("sync", "--all", env=ENV).returncode, 0)
        self.assertEqual(self.world.run("set", self.ids[1], "--priority", "3", env=OFFLINE).returncode, 0)
        self.assertIn("unpushed=1", self.status())
        self.assertIn("nothing pending", self.push(self.ids[2]))
        self.assertIn("unpushed=0", self.status())


@unittest.skipUnless(jj_available(), "jj is not installed")
class PushUnpushedJujutsuWalk(PushUnpushedWalk):
    vcs = "jj"


if __name__ == "__main__":
    unittest.main()
