"""Forge contract for finding an open pull request by head and closing one, on both REST backends.

Adversary: a head filter that matches a prefix or another branch, a closed or merged pull request listed as open,
and a close that is not idempotent or leaves the pull request open.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import forge_http
from qew.forge import PullRequestSpec
from qew.forge_forgejo import Forgejo
from qew.forge_github import GitHub
from qew.ledger_config import Mirror
from tests import fake_forgejo, fake_github
from tests.fake_forgejo import FakeForgejo
from tests.fake_github import FakeGitHub


class AdoptContract:
    def populate(self) -> None:
        for head in ("wip", "wip-two", "gone"):
            self.backend.open_pull_request(PullRequestSpec(head, "main", head, f"body {head}", True))
        self.backend.close_pull_request(3)

    def test_find_returns_only_open_pull_requests_of_exactly_that_head(self) -> None:
        """Prevents adopting a prefix match or a closed pull request."""
        self.populate()
        found = self.backend.find_open_pull_requests("wip")
        self.assertEqual([(p.number, p.head_branch, p.body.strip()) for p in found], [(1, "wip", "body wip")])
        self.assertEqual(self.backend.find_open_pull_requests("gone"), [])
        self.assertEqual(self.backend.find_open_pull_requests("nothing"), [])

    def test_close_reads_back_closed_and_a_second_close_is_success(self) -> None:
        """Prevents a retried close failing, or reporting closed while the forge says open."""
        self.populate()
        self.assertEqual(self.backend.close_pull_request(1).state, "closed")
        self.assertEqual(self.backend.close_pull_request(1).state, "closed")
        self.assertEqual(self.backend.read_pull_request(2).state, "open")


class GitHubAdoptWalk(AdoptContract, unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub(page_size=2).start()
        self.addCleanup(self.fake.stop)
        mirror = Mirror("github", self.fake.repo_url, None, "", {"api": self.fake.api})
        self.backend = GitHub(mirror, forge_http.urllib_transport(timeout=5), {"GITHUB_TOKEN": fake_github.TOKEN})


class ForgejoAdoptWalk(AdoptContract, unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        mirror = Mirror("forgejo", self.fake.repo_url, None, "", {"api": self.fake.api})
        self.backend = Forgejo(mirror, forge_http.urllib_transport(timeout=5), {"FORGEJO_TOKEN": fake_forgejo.TOKEN})


if __name__ == "__main__":
    unittest.main()
