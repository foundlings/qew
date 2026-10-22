"""Command-level walks for the commands and flags the coverage inventory found unexercised."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN as FORGEJO_TOKEN, FakeForgejo
from tests.fake_github import TOKEN as GITHUB_TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

QUIET = "\n[replication]\nauto_forge_sync = false\n"


class BoardCommandWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirror = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.w = World(mirror, replication=QUIET)
        self.addCleanup(self.w.close)
        self.env = {"GITHUB_TOKEN": GITHUB_TOKEN, "QEW_NO_GH_TOKEN": "1"}

    def test_status_reports_drift_then_agrees_after_a_sync_and_prune_removes_only_foreign_cards(self) -> None:
        """Prevents a silent board: drift is named with the fix, a synced board reports in sync, prune never touches our cards."""
        w = self.w
        w.create("--title", "Card", "--quote", QUOTE)
        self.assertEqual(w.run("project", "ensure", env=self.env).returncode, 0)
        drifted = w.run("project", "status", "--json", env=self.env)
        self.assertEqual(drifted.returncode, 1)
        self.assertFalse(json.loads(drifted.stdout)["in_sync"])
        self.assertEqual(w.run("sync", "--all", env=self.env).returncode, 0)
        synced = w.run("project", "status", env=self.env)
        self.assertEqual(synced.returncode, 0, synced.stdout + synced.stderr)
        pruned = w.run("project", "prune", "--from", "acme/other", env=self.env)
        self.assertEqual(pruned.returncode, 0, pruned.stderr)
        self.assertIn("removed 0", pruned.stdout)
        self.assertEqual(w.run("project", "status", env=self.env).returncode, 0)
        orphans = w.run("project", "prune", "--orphans", env=self.env)
        self.assertEqual(orphans.returncode, 0, orphans.stderr)
        self.assertIn("removed 0", orphans.stdout)
        self.assertEqual(w.run("project", "prune", "--orphans", "--from", "acme/other", env=self.env).returncode, 2)


class AdHocForgeWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        self.env = {"FORGEJO_TOKEN": FORGEJO_TOKEN}
        self.args = ("--forge", "forgejo", "--repo", self.fake.repo_url, "--api", self.fake.api)

    def test_restore_then_import_round_trips_through_an_ad_hoc_target(self) -> None:
        """Prevents restore/import needing a configured mirror: an ad hoc forge+repo rebuilds the forge, then an empty ledger from it."""
        source = World(replication=QUIET)
        self.addCleanup(source.close)
        first = source.create("--title", "First", "--quote", QUOTE)
        source.create("--title", "Second", "--needs", first, "--quote", QUOTE)
        restored = source.run("restore", *self.args, env=self.env)
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        self.assertEqual(len(self.fake.issues), 2)
        empty = World(replication=QUIET)
        self.addCleanup(empty.close)
        imported = empty.run("import", *self.args, "--history", env=self.env)
        self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
        titles = {json.loads(line)["title"] for line in empty.run("list", "--json").stdout.splitlines()}
        self.assertEqual(titles, {"First", "Second"})

    def test_an_ad_hoc_target_needs_both_the_forge_and_the_repository(self) -> None:
        """Prevents half a target being guessed: --forge without --repo is refused."""
        w = World(replication=QUIET)
        self.addCleanup(w.close)
        result = w.run("restore", "--forge", "forgejo", env=self.env)
        self.assertEqual(result.returncode, 2)


class SmallFlagWalk(unittest.TestCase):
    def test_evidence_vcs_override_and_accept_completed_refusal(self) -> None:
        """Prevents the escape hatches misbehaving: --vcs git records git evidence; accept --completed never completes unclaimed work."""
        w = World(replication=QUIET)
        self.addCleanup(w.close)
        item = w.create("--title", "Work", "--quote", QUOTE)
        w.run("start", item)
        self.assertEqual(w.run("evidence", item, "--vcs", "git", "--change", w.commit("c")).returncode, 0)
        self.assertEqual(w.events(item)[-1]["vcs"], "git")
        refused = w.run("accept", item, "--completed")
        self.assertNotEqual(refused.returncode, 0)


if __name__ == "__main__":
    unittest.main()
