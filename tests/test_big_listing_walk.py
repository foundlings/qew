"""A forge with thousands of issues: a pull stays cheap when nothing changed and never refuses the listing for its length.

Adversary: the conditional pull asks one request per issue page on every sync (80+ pages at foundlings' size, minutes of
work) and then fails with `more than 50 issue pages`, throwing the sync's work away on every run; the daily full listing
needs the same pages and fails the same way.
"""

from datetime import timedelta
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import forge_pull
from qew.forge_github import GitHub
from qew.forge_http import urllib_transport
from qew.ledger_config import Mirror
from tests.fake_github import TOKEN, FakeGitHub
from tests.test_incremental_pull_walk import Rig


class BigListingWalk(Rig):
    def build(self, page_size: int) -> None:
        self.github = FakeGitHub(page_size=page_size).start()
        self.addCleanup(self.github.stop)
        backend = GitHub(Mirror("github", self.github.repo_url, None, "", {"api": self.github.api}), urllib_transport(),
                         {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"})
        self.start(backend, f'\n[[mirror]]\nforge = "github"\nrepo = "{self.github.repo_url}"\napi = "{self.github.api}"\n')

    def list_requests(self) -> list[tuple]:
        return [r for r in self.github.requests if r[0] == "GET" and "/issues?" in r[1]]

    def test_a_long_listing_is_not_asked_page_by_page_once_the_cursor_is_fresh(self) -> None:
        """Prevents a no-change sync costing one request per issue page for ever: past CONDITIONAL_MAX_PAGES it asks `since`."""
        self.build(page_size=2)
        for number in range(1, 6):
            self.add_item(f"Item {number}")  # numbers 1-5: three pages of two
        with mock.patch.object(forge_pull, "CONDITIONAL_MAX_PAGES", 2):
            self.assertEqual(self.pull().mode, "conditional")  # no cursor yet: the complete set
            self.clock.advance(minutes=10)
            before = len(self.list_requests())
            report = self.pull()
            self.assertEqual((report.mode, len(self.list_requests()) - before), ("incremental", 1))
            self.assertEqual((report.applied, report.failures), (0, []))
            self.github.issues[3]["title"] = "Retitled by a human"
            self.github.touch(self.github.issues[3])
            self.clock.advance(minutes=10)
            self.assertEqual(self.pull().applied, 1)
            self.clock.advance(hours=25)
            self.assertEqual(self.pull().mode, "conditional")  # the daily full listing still sees deletions

    def test_a_listing_past_fifty_pages_is_read_whole_not_refused(self) -> None:
        """Prevents `more than 50 issue pages; refusing a truncated listing` on a repository that really has that many."""
        self.build(page_size=1)
        for number in range(55):
            self.github.new_issue({"title": f"Filed by a human {number}"})
        report = self.pull()
        self.assertEqual((report.failures, report.mode, report.new_items), ([], "conditional", 55))


if __name__ == "__main__":
    unittest.main()
