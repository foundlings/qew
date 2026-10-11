"""GitHub's pull request labels over real HTTP against the fake: read with the pull request, set as the whole set, created when missing.

Adversary: a set that drops labels it was not told about, a label that is never created, and a readback that hides the labels.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import forge_http
from qew.forge import PullRequestSpec
from qew.forge_github import GitHub
from qew.ledger_config import Mirror
from tests import fake_github
from tests.fake_github import FakeGitHub


class GitHubPullLabelWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        self.fake.add_branch("main")
        self.fake.add_branch("work")
        mirror = Mirror("github", self.fake.repo_url, None, "", {"api": self.fake.api})
        self.backend = GitHub(mirror, forge_http.urllib_transport(timeout=5), {"GITHUB_TOKEN": fake_github.TOKEN})
        self.number = self.backend.open_pull_request(PullRequestSpec("work", "main", "Work", "body", True)).number

    def test_the_whole_set_is_written_read_back_and_the_labels_exist(self) -> None:
        """Prevents a label that is applied but never defined, and a readback that does not show the set."""
        self.assertEqual(self.backend.read_pull_request(self.number).labels, ())
        pull = self.backend.set_pull_request_labels(self.number, ("elf2", "owner:elf2"))
        self.assertEqual(sorted(pull.labels), ["elf2", "owner:elf2"])
        self.assertTrue({"elf2", "owner:elf2"} <= set(self.fake.label_store))
        pull = self.backend.set_pull_request_labels(self.number, ("elf2",))
        self.assertEqual(pull.labels, ("elf2",))


if __name__ == "__main__":
    unittest.main()
