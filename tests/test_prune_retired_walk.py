"""`sync --prune-labels` deletes only labels qew itself retired: a label prefix converted to a field, and qew's own managed
labels it no longer emits. A label that merely nothing carries is never deleted: teams make labels in preparation.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\ndivergences = \"report\"\n"
RETIRED = {"phase:12", "wp:alpha", "author:old", "status:old"}
KEPT = {"bug", "documentation", "duplicate", "enhancement", "good first issue", "santa", "elf3", "owner:elf3", "held-for-2262",
        "nice-label-made-in-preparation", "gated", "ready", "in-progress", "needs:approval", "P3", "epic", "task", "phase-notes"}


class PruneRetiredWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        self.world.create("--title", "Phased", "--field", "phase=12", "--field", "wp=alpha", "--quote", QUOTE)
        self.assertEqual(self.world.run("sync", env=ENV).returncode, 0)
        for name in (*RETIRED, *KEPT):
            self.fake.label_store.setdefault(name, {"color": "ededed", "description": ""})

    def prune(self, *arguments: str):
        result = self.world.run("sync", "--prune-labels", *arguments, env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_only_retired_labels_are_listed_and_deleted(self) -> None:
        """Prevents deleting GitHub's defaults and a team's labels: the dry run names exactly what qew retired, nothing else."""
        before = set(self.fake.label_store)
        preview = self.prune("--dry-run")
        listed = {line.rsplit(" ", 1)[-1] for line in preview.stdout.splitlines() if "would delete label" in line}
        self.assertEqual(listed, RETIRED, preview.stdout)
        self.assertEqual(set(self.fake.label_store), before)
        self.prune()
        self.assertEqual(before - set(self.fake.label_store), RETIRED)
        self.assertTrue(KEPT <= set(self.fake.label_store))
        self.assertIn("no retired labels", self.prune().stdout)

    def test_a_retired_label_something_still_carries_is_not_deleted(self) -> None:
        """Prevents stripping a label from live issues or pull requests: carried means kept, retired or not."""
        issue = self.fake.new_issue({"title": "Foreign", "body": "", "labels": ["phase:12"]})
        issue["pull_request"] = {}
        self.prune()
        self.assertIn("phase:12", self.fake.label_store)
        self.assertEqual(issue["labels"], ["phase:12"])

    def test_the_old_everything_flag_is_gone(self) -> None:
        """Prevents a widening switch coming back: --all-labels is not an option any more."""
        result = self.world.run("sync", "--prune-labels", "--all-labels", env=ENV)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
