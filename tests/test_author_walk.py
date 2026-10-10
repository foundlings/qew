"""Authorship: the importer is a tool, not the author. A reviewed bulk correction event names the human, keeps the tool as `via`,
and sync turns author:qew-import into author:887.

Each test is a scenario; its docstring names the failure it prevents.
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

NOW = "2024-05-01T10:00:00Z"
OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\ndivergences = \"report\"\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
REVIEW = "Alexander: 'everything was authored by me, the importer was only the tool used'"


def imported(item_id: str, actor: str = "qew-import") -> dict:
    return {"id": item_id, "kind": "issue", "title": f"Old work {item_id}",
            "events": [{"type": "approved", "at": NOW, "actor": actor, "quote": QUOTE}]}


class AuthorWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        load = self.world.run("create", "--from-file", "-", "--import", stdin=json.dumps([imported("IA"), imported("IB"), imported("IC", "alice")]))
        self.assertEqual(load.returncode, 0, load.stdout + load.stderr)

    def show(self, item: str) -> dict:
        return json.loads(self.world.run("show", item, "--json").stdout)

    def commits(self) -> int:
        return int(subprocess.run(["git", "-C", str(self.world.ledger), "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout)

    def test_the_correction_names_the_human_keeps_the_tool_and_never_rewrites_history(self) -> None:
        """Prevents 22,487 items credited to a tool: one reviewed event each moves the author, the first event stays as it was."""
        self.assertEqual(self.show("IA")["author"], "qew-import")
        first = {p.name: p.read_bytes() for p in (self.world.ledger / "events" / "IA").glob("*.json")}
        before = self.commits()
        preview = self.world.run("correct-author", "--from", "qew-import", "--to", "887", "--quote", REVIEW, "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn("would correct 2", preview.stdout)
        self.assertEqual(self.commits(), before)
        done = self.world.run("correct-author", "--from", "qew-import", "--to", "887", "--quote", REVIEW)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("corrected 2", done.stdout)
        self.assertEqual(self.commits(), before + 1)
        for item in ("IA", "IB"):
            data = self.show(item)
            self.assertEqual((data["author"], data.get("via")), ("887", "qew-import"))
        self.assertEqual(self.show("IC")["author"], "alice")
        for name, content in first.items():
            self.assertEqual((self.world.ledger / "events" / "IA" / name).read_bytes(), content)
        self.assertIn("IA", self.world.run("list", "--author", "887").stdout)
        self.assertEqual(self.world.run("list", "--author", "qew-import").stdout.strip(), "")
        again = self.world.run("correct-author", "--from", "qew-import", "--to", "887", "--quote", REVIEW)
        self.assertIn("corrected 0", again.stdout)
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_the_correction_needs_a_humans_words_and_a_real_change(self) -> None:
        """Prevents a quiet bulk rewrite of credit: no quote, a bad actor or a no-op change refuses before any write."""
        before = self.commits()
        for arguments in (("--from", "qew-import", "--to", "887"),
                          ("--from", "qew-import", "--to", "not an actor", "--quote", REVIEW),
                          ("--from", "qew-import", "--to", "qew-import", "--quote", REVIEW)):
            with self.subTest(arguments=arguments):
                out = self.world.run("correct-author", *arguments)
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(self.commits(), before)

    def test_a_new_import_can_record_the_human_as_author_and_the_tool_as_via(self) -> None:
        """Prevents the next import repeating the mistake: --as makes the human the actor of the entry's events, the tool its via."""
        out = self.world.run("create", "--from-file", "-", "--import", "--as", "887", stdin=json.dumps([imported("ID")]))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        data = self.show("ID")
        self.assertEqual((data["author"], data.get("via")), ("887", "qew-import"))


class AuthorSyncWalk(unittest.TestCase):
    def test_sync_relabels_the_issue_and_the_prune_drops_the_old_label(self) -> None:
        """Prevents author:qew-import lingering on 1,909 issues: after the correction sync labels author:887 and prune deletes the old one."""
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'
        world = World(mirrors, replication=OFF)
        self.addCleanup(world.close)
        self.assertEqual(world.run("create", "--from-file", "-", "--import", stdin=json.dumps([imported("IA")])).returncode, 0)
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        labels = lambda: next(iter(fake.issues.values()))["labels"]  # noqa: E731
        self.assertIn("author:qew-import", labels())
        self.assertEqual(world.run("correct-author", "--from", "qew-import", "--to", "887", "--quote", REVIEW).returncode, 0)
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        self.assertIn("author:887", labels())
        self.assertNotIn("author:qew-import", labels())
        self.assertEqual(world.run("sync", "--prune-labels", env=ENV).returncode, 0)
        self.assertNotIn("author:qew-import", fake.label_store)


if __name__ == "__main__":
    unittest.main()
