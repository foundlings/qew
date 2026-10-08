"""`qew status`: replication state in a few fixed lines."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import World, git

OFFLINE = {"QEW_OFFLINE": "1"}


class StatusWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.w = World(mirrors, ledger_vcs="git", remote=True,
                       replication="\n[replication]\nbackground_sync = false\nauto_forge_sync = false\n")
        self.addCleanup(self.w.close)

    def status(self, *extra, env=None):
        result = self.w.run("status", *extra, env={"FORGEJO_TOKEN": TOKEN, **(env or {})})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_a_clean_synced_ledger_reports_nothing_pending(self) -> None:
        """Prevents status being noisy when all is well: every counter is zero and the mode is online."""
        self.w.create("--kind", "issue", "--title", "A")
        self.w.run("sync", env={"FORGEJO_TOKEN": TOKEN})
        data = json.loads(self.status("--json"))
        self.assertEqual(data["ledger"], {"uncommitted": 0, "unpushed": 0, "mode": "online", "remote": "ok"})
        self.assertEqual(data["targets"][0]["pending"], 0)
        self.assertEqual(data["divergences"], [])
        self.assertTrue(data["targets"][0]["last_sync"])

    def test_every_problem_class_appears_with_its_count(self) -> None:
        """Prevents hidden backlog: offline commits, stray files and unsynced items each show up."""
        item = self.w.run("create", "--kind", "issue", "--title", "Offline", env=OFFLINE).stdout.split()[0]
        (self.w.ledger / "events" / item / "ZZZZZZZZZ.json").write_text(
            '{"type": "field", "key": "title", "old": "a", "new": "b", "at": "2026-01-01T00:00:00Z", "actor": "x"}\n')
        text = self.status()
        self.assertIn("unpushed=1", text)
        self.assertIn("uncommitted=1", text)
        self.assertIn("pending=1", text)
        self.assertIn("synced=never", text)

    def test_offline_mode_is_reported_without_touching_the_network(self) -> None:
        """Prevents status lying about connectivity: QEW_OFFLINE shows mode=offline."""
        self.assertIn("mode=offline", self.status(env=OFFLINE))

    def test_an_unreachable_remote_shows_as_offline_after_the_attempt(self) -> None:
        """Prevents an outage looking like a healthy ledger: the failed push is remembered and shown."""
        self.w.remote.rename(self.w.remote.with_name("away.git"))
        self.w.run("create", "--kind", "issue", "--title", "While away")
        text = self.status()
        self.assertIn("remote=offline", text)
        self.assertIn("unpushed=1", text)

    def test_the_default_output_is_short(self) -> None:
        """Prevents status costing agent tokens: three lines for one target."""
        self.assertLessEqual(len(self.status().strip().splitlines()), 4)


if __name__ == "__main__":
    unittest.main()
