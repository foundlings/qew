"""Branch methods of the forge interface over the real urllib transport against each local fake.

Adversary: deleting or reporting merged a branch the forge says holds unmerged commits, a truncated
listing treated as complete, a delete of a branch that is already gone failing the caller, and a
backend without branch support pretending to have it.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import forge_http
from qew.forge import Forge, ForgeRejected, PullRequestSpec, RemoteBranch, Unsupported
from qew.forge_forgejo import Forgejo
from qew.forge_github import GitHub
from qew.forge_gitlab import GitLab
from qew.ledger_config import Mirror
from tests import fake_forgejo, fake_github
from tests.fake_forgejo import FakeForgejo
from tests.fake_github import FakeGitHub
from tests.fake_gitlab import FakeGitLab


class BranchContract:
    """The behaviour every supporting backend shares; subclasses supply `self.fake` and `self.backend`."""

    def populate(self) -> None:
        self.fake.add_branch("main")
        self.fake.add_branch("feature/done", contained_in=("main",))
        self.fake.add_branch("wip")
        self.fake.protection["main"] = {"required_status_checks": None, "enable_status_check": False}

    def test_branches_are_listed_with_default_and_protected_flags(self) -> None:
        """Prevents treating the default or a protected branch as an ordinary prune candidate."""
        self.populate()
        found = {branch.name: branch for branch in self.backend.list_branches()}
        self.assertEqual(sorted(found), ["feature/done", "main", "wip"])
        self.assertEqual(found["main"], RemoteBranch("main", True, True))
        self.assertEqual(found["wip"], RemoteBranch("wip", False, False))

    def test_a_branch_is_merged_only_when_the_base_already_holds_its_tip(self) -> None:
        """Prevents deleting commits that exist nowhere else (slashes in the name included)."""
        self.populate()
        self.assertTrue(self.backend.branch_is_merged("feature/done", "main"))
        self.assertFalse(self.backend.branch_is_merged("wip", "main"))

    def test_delete_removes_the_branch_and_a_second_delete_is_success(self) -> None:
        """Prevents a retried cleanup failing on a branch that is already gone."""
        self.populate()
        self.backend.delete_branch("feature/done")
        self.assertNotIn("feature/done", self.fake.branches)
        self.backend.delete_branch("feature/done")
        self.assertIn("wip", self.fake.branches)

    def test_a_refused_delete_is_an_error_not_a_silent_success(self) -> None:
        """Prevents reporting a protected branch as deleted."""
        self.populate()
        with self.assertRaises(ForgeRejected):
            self.backend.delete_branch("main")
        self.assertIn("main", self.fake.branches)

    def test_open_pull_heads_name_every_branch_an_open_pull_request_uses(self) -> None:
        """Prevents pruning a branch under review; merged and closed pull requests do not count."""
        self.populate()
        self.open_pulls()
        self.assertEqual(sorted(self.backend.list_open_pull_heads()), ["wip"])


class GitHubBranchWalk(BranchContract, unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub(page_size=2).start()
        self.addCleanup(self.fake.stop)
        mirror = Mirror("github", self.fake.repo_url, None, "", {"api": self.fake.api})
        self.backend = GitHub(mirror, forge_http.urllib_transport(timeout=5), {"GITHUB_TOKEN": fake_github.TOKEN})

    def open_pulls(self) -> None:
        for head, state in (("wip", "open"), ("feature/done", "closed")):
            number = self.fake.new_pull({"title": head, "head": head, "base": "main"})["number"]
            self.fake.pulls[number]["state"] = state


class ForgejoBranchWalk(BranchContract, unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        mirror = Mirror("forgejo", self.fake.repo_url, None, "", {"api": self.fake.api})
        self.backend = Forgejo(mirror, forge_http.urllib_transport(timeout=5), {"FORGEJO_TOKEN": fake_forgejo.TOKEN})

    def open_pulls(self) -> None:
        for head, state in (("wip", "open"), ("feature/done", "closed")):
            created = self.backend.open_pull_request(PullRequestSpec(head, "main", head, "", False))
            self.fake.pulls[created.number]["state"] = state


class GitLabBranchWalk(unittest.TestCase):
    def test_gitlab_says_unsupported_for_every_branch_call(self) -> None:
        """Prevents a fabricated empty listing on a forge qew cannot prune."""
        fake = FakeGitLab().start()
        self.addCleanup(fake.stop)
        mirror = Mirror("gitlab", f"{fake.host}/group/sub/repo", None, "", {"api": fake.api})
        backend = GitLab(mirror, forge_http.urllib_transport(timeout=5), {"GITLAB_TOKEN": "t"})
        for call in (backend.list_branches, backend.list_open_pull_heads, lambda: backend.delete_branch("x"), lambda: backend.branch_is_merged("x", "main")):
            with self.assertRaises(Unsupported):
                call()
        self.assertTrue(issubclass(GitLab, Forge))


if __name__ == "__main__":
    unittest.main()
