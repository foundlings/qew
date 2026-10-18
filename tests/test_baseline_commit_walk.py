"""Committed sync baselines: the forge-synced state of an item is a committed fact, so a clone starts from it.

Each test is a scenario; its docstring names the failure it prevents (seen live: a second machine showed pending=2049
and could not tell what the forge looked like until it read all of it).
"""

import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_config
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World, baseline_files, git

REPLICATION = "\n[replication]\nauto_forge_sync = false\nforge_concurrency = 1\nfetch_interval_s = 0\ndivergences = \"report\"\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class BaselineCommitWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        self.mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.a = World(self.mirrors, ledger_vcs="git", remote=True, replication=REPLICATION)
        self.addCleanup(self.a.close)
        self.b = self.a.clone("b", mirrors=self.mirrors, replication=REPLICATION)
        self.slug = ledger_config.mirror_slug(ledger_config.load(self.a.code).mirrors[0])

    def sync(self, world: World):
        result = world.run("sync", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def status_line(self, world: World) -> str:
        world.run("list", env=ENV)  # status never fetches; any other command does
        return next(line for line in world.run("status", env=ENV).stdout.splitlines() if line.startswith("target "))

    def remote_files(self) -> list[str]:
        return git(self.a.base, "--git-dir", str(self.a.remote), "ls-tree", "-r", "--name-only", "main").splitlines()

    def forge_writes(self) -> int:
        return sum(1 for m, path, body in self.fake.requests
                   if (m not in ("GET", "HEAD") and "graphql" not in path) or "mutation" in str(body))

    def test_a_clone_starts_from_the_baseline_another_machine_committed(self) -> None:
        """Prevents a second machine counting every item as unsynced: the forge-synced state is committed per item, so a
        clone sees pending=0, plans no baseline pass, and its first sync writes nothing to the forge."""
        first = self.a.create("--kind", "issue", "--title", "One", "--quote", QUOTE)
        self.a.create("--kind", "issue", "--title", "Two", "--quote", QUOTE)
        self.sync(self.a)
        written = self.forge_writes()
        committed = [p for p in self.remote_files() if p.startswith(f"baselines/{first}/{self.slug}/")]
        self.assertEqual(len(committed), 1, self.remote_files())
        line = self.status_line(self.b)
        self.assertIn("pending=0", line)
        self.assertNotIn("unbaselined", line)
        plan = self.b.run("sync", "--dry-run", env=ENV).stdout
        self.assertNotIn("baseline ", plan)
        self.assertNotIn("update ", plan)
        self.assertIn("updated 0", self.sync(self.b).stdout)
        self.assertEqual(self.forge_writes(), written)

    def test_a_pending_change_stays_on_the_machine_until_it_is_synced(self) -> None:
        """Prevents unsynced work looking synced on another machine: a ledger change not yet pushed to the forge is pending
        against the committed baseline, and the sync that pushes it updates that baseline."""
        item = self.a.create("--kind", "issue", "--title", "Work", "--quote", QUOTE)
        self.sync(self.a)
        self.assertIn("pending=0", self.status_line(self.b))
        self.assertEqual(self.a.run("comment", item, "--body", "a note", env=ENV).returncode, 0)
        self.assertIn("pending=1", self.status_line(self.b))
        self.sync(self.a)
        self.assertIn("pending=0", self.status_line(self.b))

    def test_two_machines_syncing_one_item_add_baselines_without_a_git_conflict(self) -> None:
        """Prevents a git conflict on the baseline: a sync adds a new file and removes the older one, so two machines that
        record the same item add two files and delete the same one, and the ledger still merges and checks."""
        item = self.a.create("--kind", "issue", "--title", "Shared", "--quote", QUOTE)
        self.sync(self.a)
        self.assertEqual(self.a.run("comment", item, "--body", "from a", env=ENV).returncode, 0)
        self.assertEqual(self.b.run("comment", item, "--body", "from b", env=ENV).returncode, 0)
        self.sync(self.a)
        self.sync(self.b)
        for world in (self.a, self.b, self.a):
            self.assertEqual(world.run("check", env=ENV).returncode, 0)
            self.assertEqual(world.run("ready", env=ENV).returncode, 0)
        self.assertIn("pending=0", self.status_line(self.a))
        self.assertIn("pending=0", self.status_line(self.b))

    def test_a_baseline_older_than_the_forge_is_a_change_not_a_divergence(self) -> None:
        """Prevents a stale committed baseline turning a forge edit into a conflict: B starts from A's baseline, the forge
        moved on since, and the edit is taken as the forge's change."""
        item = self.a.create("--kind", "issue", "--title", "Before", "--quote", QUOTE)
        self.sync(self.a)
        issue = next(iter(self.fake.issues.values()))
        issue["title"] = "After"
        self.fake.touch(issue)
        self.sync(self.b)
        shown = self.b.run("show", item, "--json", env=ENV).stdout
        self.assertEqual(json.loads(shown)["title"], "After")
        self.assertEqual(json.loads(self.b.run("status", "--json", env=ENV).stdout)["divergences"], [])

    def test_a_machine_with_only_local_state_commits_a_baseline_for_every_item_on_its_next_sync(self) -> None:
        """Prevents a clone staying unbaselined after the machine that synced it upgraded: unchanged items write nothing,
        so their old local state must be committed as baselines by the sync itself."""
        items = [self.a.create("--kind", "issue", "--title", title, "--quote", QUOTE) for title in ("One", "Two")]
        self.sync(self.a)
        state = self.a.ledger / ".qew" / "state" / self.slug
        state.mkdir(parents=True, exist_ok=True)
        for item in items:  # what qew before 6.31 left: the baseline only as local state
            files = baseline_files(self.a.ledger, item)
            (state / f"{item}.json").write_text(files[-1].read_text())
        shutil.rmtree(self.a.ledger / "baselines")
        self.sync(self.a)
        for item in items:
            self.assertEqual(len(baseline_files(self.a.ledger, item)), 1, item)
        self.assertEqual(len([p for p in self.remote_files() if p.startswith("baselines/")]), 2)
        self.assertIn("pending=0", self.status_line(self.b))


if __name__ == "__main__":
    unittest.main()
