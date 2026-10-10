"""`qew complete --manifest`: many approved items completed in one commit, on a human's quoted words, without claims.

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

WORDS = "Alexander: 'done per measure-board.py: phase not open on main (#570)'"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class CompleteManifestWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        self.epic = self.world.create("--kind", "epic", "--title", "Old phase", "--quote", QUOTE)
        self.one = self.world.create("--kind", "task", "--title", "One", "--parent", self.epic, "--quote", QUOTE)
        self.two = self.world.create("--kind", "task", "--title", "Two", "--parent", self.epic, "--quote", QUOTE)
        self.path = self.world.base / "manifest.jsonl"

    def manifest(self, *rows: object) -> None:
        self.path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def state(self, item: str) -> str:
        return json.loads(self.world.run("show", item, "--json").stdout)["state"]

    def commits(self) -> int:
        return int(subprocess.run(["git", "-C", str(self.world.ledger), "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout)

    def run_bulk(self, *extra: str):
        return self.world.run("complete", "--manifest", str(self.path), "--quote", WORDS, *extra)

    def test_a_phase_and_its_tasks_complete_in_one_commit_with_the_quote_and_reasons(self) -> None:
        """Prevents 4,000 single calls: children, then the epic, all done in one ledger commit carrying the human's words."""
        self.manifest({"item": self.epic, "reason": "phase closed"}, {"item": self.one}, {"item": self.two, "reason": "shipped earlier"})
        before = self.commits()
        result = self.run_bulk()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("completed 3", result.stdout)
        self.assertEqual(self.commits(), before + 1)
        for item in (self.epic, self.one, self.two):
            self.assertEqual(self.state(item), "completed")
        event = [e for e in self.world.events(self.two) if e["type"] == "completed"][0]
        self.assertEqual(event["reason"], "shipped earlier")
        self.assertIn("measure-board.py", event["quote"])
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_dry_run_writes_nothing_and_a_rerun_is_unchanged(self) -> None:
        """Prevents a surprise or a duplicate: --dry-run changes nothing, and completing twice adds no event or commit."""
        self.manifest({"item": self.one}, {"item": self.two})
        before = self.commits()
        preview = self.run_bulk("--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn("would complete 2", preview.stdout)
        self.assertEqual((self.commits(), self.state(self.one)), (before, "approved"))
        self.assertEqual(self.run_bulk().returncode, 0)
        done = self.commits()
        again = self.run_bulk()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("unchanged 2", again.stdout)
        self.assertEqual(self.commits(), done)
        self.assertEqual(len([e for e in self.world.events(self.one) if e["type"] == "completed"]), 1)

    def test_one_bad_row_refuses_the_whole_manifest_and_names_it(self) -> None:
        """Prevents a half-done bulk: an unknown item, a duplicate row or bad JSON refuses everything before any write."""
        before = self.commits()
        for rows, needle in (([{"item": self.one}, {"item": "nope"}], "line 2"),
                             ([{"item": self.one}, {"item": self.one}], "line 2"),
                             ([{"item": self.one, "surprise": 1}], "surprise")):
            with self.subTest(needle=needle):
                self.manifest(*rows)
                refused = self.run_bulk()
                self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
                self.assertIn(needle, refused.stderr)
                self.assertEqual((self.commits(), self.state(self.one)), (before, "approved"))
        self.path.write_text('{"item": "' + self.one + '"}\nnot json\n', encoding="utf-8")
        self.assertEqual(self.run_bulk().returncode, 2)
        self.assertEqual(self.state(self.one), "approved")

    def test_drafts_closed_items_and_foreign_claims_are_refused(self) -> None:
        """Prevents overriding what is not ours: a draft, a closed item and another login's claim stop the manifest."""
        draft = self.world.create("--kind", "task", "--title", "Draft", "--draft")
        closed = self.world.create("--kind", "task", "--title", "Dropped", "--quote", QUOTE)
        self.assertEqual(self.world.run("close", closed, "--reason", "not_planned").returncode, 0)
        held = self.world.create("--kind", "task", "--title", "Held", "--quote", QUOTE)
        self.assertEqual(self.world.run("start", held, env={"QEW_ACTOR": "someone-else"}).returncode, 0)
        for item, code in ((draft, 2), (closed, 2), (held, 3)):
            with self.subTest(item=item):
                self.manifest({"item": item})
                refused = self.run_bulk()
                self.assertEqual(refused.returncode, code, refused.stdout + refused.stderr)
                self.assertIn(item, refused.stderr)

    def test_an_open_child_or_unmet_need_outside_the_manifest_refuses_it(self) -> None:
        """Prevents a completed phase with live children: the epic needs every child completed or in the same manifest."""
        self.manifest({"item": self.epic}, {"item": self.one})
        refused = self.run_bulk()
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn(self.two, refused.stderr)
        later = self.world.create("--kind", "task", "--title", "After", "--needs", self.one, "--quote", QUOTE)
        self.manifest({"item": later})
        blocked = self.run_bulk()
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertIn(self.one, blocked.stderr)

    def test_the_quote_is_required_and_the_single_item_form_is_unchanged(self) -> None:
        """Prevents anonymous bulk completion: no quote, no manifest; `complete ITEM` still needs its claim."""
        self.manifest({"item": self.one})
        missing = self.world.run("complete", "--manifest", str(self.path))
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)
        self.assertIn("quote", missing.stderr)
        both = self.world.run("complete", self.one, "--manifest", str(self.path), "--quote", WORDS)
        self.assertEqual(both.returncode, 2)
        self.assertEqual(self.world.run("complete", self.one).returncode, 2)
        self.assertEqual(self.state(self.one), "approved")

    def test_a_sync_closes_the_mirrored_issue_as_completed_not_as_not_planned(self) -> None:
        """Prevents bulk-completed work showing as dropped on GitHub: the mirrored issue closes with reason completed."""
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'
        world = World(mirrors, replication="\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n")
        self.addCleanup(world.close)
        item = world.create("--kind", "task", "--title", "Mirrored", "--quote", QUOTE)
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        path = world.base / "m.jsonl"
        path.write_text(json.dumps({"item": item}) + "\n", encoding="utf-8")
        self.assertEqual(world.run("complete", "--manifest", str(path), "--quote", WORDS).returncode, 0)
        self.assertEqual(world.run("sync", env=ENV).returncode, 0)
        issue = next(iter(fake.issues.values()))
        self.assertEqual((issue["state"], issue["state_reason"]), ("closed", "completed"))


if __name__ == "__main__":
    unittest.main()
