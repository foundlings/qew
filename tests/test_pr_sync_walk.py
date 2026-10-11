"""`qew pr sync N`: an open pull request takes the current title of its item, recorded in the ledger. Nothing else is rewritten.

Each test is a scenario; its docstring names the failure it prevents. (An item retitled after its pull request was opened could only
be followed by closing the pull request and opening another.)
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import forge_http
from qew.forge import PullRequestSpec
from qew.forge_github import GitHub
from qew.ledger_config import Mirror
from tests.fake_github import TOKEN, FakeGitHub
from tests.pr_harness import PrWorld


class PrSyncWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = PrWorld()
        self.addCleanup(self.w.close)
        self.item = self.w.item("Old title")
        self.assertEqual(self.w.open(self.item).code, 0)

    def retitle(self, title: str) -> None:
        self.assertEqual(self.w.qew("set", self.item, "--title", title).code, 0)

    def test_the_title_follows_the_item_and_the_body_is_left_alone(self) -> None:
        """Prevents throwing a pull request away to retitle it: sync sets the title, records it, and touches no body or checklist."""
        body = self.w.forge.pulls[1].body
        self.retitle("Collect every repair into one integration PR")
        run = self.w.qew("pr", "sync", "1")
        self.assertEqual(run.code, 0, run)
        self.assertEqual(self.w.forge.pulls[1].title, "Collect every repair into one integration PR")
        self.assertEqual(self.w.forge.pulls[1].body, body)
        self.assertEqual(self.w.forge.body_writes, [])
        self.assertIn("retitled", json.dumps(self.w.events(self.item)))
        self.assertEqual(self.w.forge.pulls[1].state, "open")

    def test_a_pull_request_that_is_already_current_is_not_written_again(self) -> None:
        """Prevents noise: a second sync, and one before any retitle, change nothing and say so."""
        again = self.w.qew("pr", "sync", "1")
        self.assertEqual(again.code, 0, again)
        self.assertIn("unchanged", again.out)
        self.retitle("New title")
        self.assertEqual(self.w.qew("pr", "sync", "1").code, 0)
        before = len(self.w.forge.title_writes)
        self.assertIn("unchanged", self.w.qew("pr", "sync", "1").out)
        self.assertEqual(len(self.w.forge.title_writes), before)

    def test_a_finished_or_unknown_pull_request_is_refused(self) -> None:
        """Prevents rewriting history: a merged or closed pull request keeps the title it ended with; an unrecorded number is refused."""
        self.retitle("New title")
        self.w.forge.pulls[1] = self.w.forge._with(1, state="merged", merge_sha="m" * 40)
        merged = self.w.qew("pr", "sync", "1")
        self.assertEqual(merged.code, 2, merged)
        self.assertEqual(self.w.forge.pulls[1].title, "Old title")
        self.assertEqual(self.w.qew("pr", "sync", "99").code, 2)


class GitHubTitleWalk(unittest.TestCase):
    def test_the_github_backend_sets_and_reads_back_a_title(self) -> None:
        """Prevents a title the forge never took: the REST PATCH goes to the pull request and the title reads back."""
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        backend = GitHub(Mirror("github", fake.repo_url, None, "", {"api": fake.api}), forge_http.urllib_transport(),
                         {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"})
        fake.add_branch("main")
        fake.add_branch("topic")
        opened = backend.open_pull_request(PullRequestSpec("topic", "main", "First", "body", True))
        self.assertEqual(backend.read_pull_request(opened.number).title, "First")
        self.assertEqual(backend.update_pull_request_title(opened.number, "Second").title, "Second")
        self.assertEqual(backend.read_pull_request(opened.number).title, "Second")
        self.assertEqual(backend.read_pull_request(opened.number).body, "body")


if __name__ == "__main__":
    unittest.main()
