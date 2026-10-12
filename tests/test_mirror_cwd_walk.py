"""The mirror targets of a command come from the ledger's own code repo, never from the directory it was run in.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import QUOTE, World

ENV = {"FORGEJO_TOKEN": TOKEN}


class MirrorCwdWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        self.mirrors = f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.quiet = "\n[replication]\nfetch_interval_s = 0\nauto_forge_sync = false\n"
        self.owner = World("", ledger_vcs="git", remote=True, replication=self.quiet)  # the ledger's own repo: no mirror here
        self.addCleanup(self.owner.close)
        self.other = self.owner.clone("b", mirrors=self.mirrors, replication=self.quiet)  # an unrelated code repo that mirrors elsewhere

    def test_a_sync_run_from_another_repo_never_files_the_ledgers_items_there(self) -> None:
        """Prevents QEW_LEDGER=A run from a checkout of repo B creating A's items as issues in B's forge repository."""
        self.owner.create("--title", "Belongs elsewhere", "--quote", QUOTE)
        ledger = {**ENV, "QEW_LEDGER": str(self.owner.ledger)}
        result = self.other.run("sync", env=ledger)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("QEW_LEDGER", result.stderr)
        self.assertEqual(self.fake.issues, {})
        self.assertEqual(self.other.run("mirror", "list", env=ledger).returncode, 2)

    def test_a_repo_that_names_the_same_ledger_still_syncs(self) -> None:
        """Prevents the refusal catching the ordinary case: QEW_LEDGER pointing at the ledger the repo's own config names."""
        self.other.create("--title", "Mine", "--quote", QUOTE)
        result = self.other.run("sync", env={**ENV, "QEW_LEDGER": str(self.other.ledger)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.fake.issues), 1)


if __name__ == "__main__":
    unittest.main()
