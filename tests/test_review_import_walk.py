"""Pull request reviews become comments on the item the pull request covers.

Each test is a scenario; its docstring names the failure it prevents. Reviews and inline review comments are read from the
forge, stored once (idempotent by remote reference) with their author and time, and a finished pull request is read to the
end once and then left alone. Forge text is untrusted data and only ever stored as text.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_config
from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"
ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}


class ReviewImportWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        self.fake.add_branch("main")
        self.fake.add_branch("topic")
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        self.slug = ledger_config.mirror_slug(ledger_config.load(self.world.code).mirrors[0])
        self.item = self.world.create("--kind", "issue", "--title", "Reviewed work", "--quote", QUOTE)
        self.sync()
        self.pull = self.fake.new_pull({"title": "Topic", "body": "b", "head": "topic", "base": "main"})
        self.record_path = self.world.ledger / "mirrors" / self.item / f"{self.slug}-pr-{self.pull['number']}.json"
        self.record_path.write_text(json.dumps({
            "forge": "github", "url": self.fake.repo_url, "kind": "pr", "number": self.pull["number"], "items": [self.item], "state": "open",
        }))

    def sync(self) -> None:
        result = self.world.run("sync", env=ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def review_calls(self) -> int:
        return sum(1 for _m, path, _b in self.fake.requests if "/pulls/" in path and ("/reviews" in path or "/comments" in path))

    def test_reviews_and_inline_comments_arrive_once_with_author_state_and_place(self) -> None:
        """Prevents review discussion staying invisible to the ledger, or arriving again on every sync."""
        self.fake.add_review(self.pull["number"], "dana", "CHANGES_REQUESTED", "please split this",
                             [{"path": "src/a.py", "line": 12, "body": "naming nit"}])
        self.fake.add_review(self.pull["number"], "erik", "APPROVED", "")
        self.sync()
        shown = self.world.run("show", self.item).stdout
        self.assertIn("@dana", shown)
        self.assertIn("CHANGES_REQUESTED", shown)
        self.assertIn("please split this", shown)
        self.assertIn("src/a.py:12", shown)
        self.assertIn("naming nit", shown)
        self.assertIn("@erik", shown)
        self.assertIn("APPROVED", shown)
        before = len([e for e in self.world.events(self.item) if e["type"] == "commented"])
        self.sync()
        self.assertEqual(len([e for e in self.world.events(self.item) if e["type"] == "commented"]), before)
        self.assertEqual(self.world.run("check").returncode, 0)

    def test_a_closed_pull_request_is_read_to_the_end_once_and_then_left_alone(self) -> None:
        """Prevents reading every old pull request on every sync: after the last read a merged one costs no request."""
        self.fake.add_review(self.pull["number"], "dana", "APPROVED", "ship it")
        data = json.loads(self.record_path.read_text())
        self.record_path.write_text(json.dumps({**data, "state": "merged"}))
        self.sync()
        self.assertIn("ship it", self.world.run("show", self.item).stdout)
        calls = self.review_calls()
        self.assertGreater(calls, 0)
        self.fake.add_review(self.pull["number"], "late", "COMMENTED", "after the merge")
        self.sync()
        self.assertEqual(self.review_calls(), calls)
        self.assertNotIn("after the merge", self.world.run("show", self.item).stdout)

    def test_a_tracked_pull_request_the_forge_no_longer_has_is_skipped_and_not_asked_again(self) -> None:
        """Prevents one vanished pull request failing every pull: a 404 on its reviews means it is gone, read once and left alone."""
        ghost = self.world.ledger / "mirrors" / self.item / f"{self.slug}-pr-999.json"
        ghost.write_text(json.dumps({"forge": "github", "url": self.fake.repo_url, "kind": "pr", "number": 999, "items": [self.item], "state": "open"}))
        first = self.world.run("sync", env=ENV)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertNotIn("pull failed", first.stdout + first.stderr)
        calls = self.review_calls()
        self.assertEqual(self.world.run("sync", env=ENV).returncode, 0)
        asked_for_ghost = [path for _m, path, _b in self.fake.requests if "/pulls/999/" in path]
        self.assertEqual(len(asked_for_ghost), 1, asked_for_ghost)  # the one reviews read that answered 404, never again
        self.assertGreaterEqual(self.review_calls(), calls)


if __name__ == "__main__":
    unittest.main()
