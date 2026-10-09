"""A first sync of many items must not list the whole repository once or twice per create.

Adversary: `_find_or_create` pages through every issue before and after each create, so a 2,000 item first sync
costs about 2 full listings per item and slows as it grows.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.fake_github import FakeGitHub
from tests.test_forge_github_walk import ENV, mirror_for

from qew import sync
from qew.forge import IssueSpec, marker_for
from qew.forge_github import GitHub
from qew.forge_http import urllib_transport


def spec(name: str) -> IssueSpec:
    return IssueSpec(title=name, body=f"{marker_for(name)}\nBody", labels=(), open=True)


class FirstSyncListingWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        self.backend = GitHub(mirror_for(self.fake), urllib_transport(), ENV)

    def listings(self) -> int:
        return sum(1 for method, path, _ in self.fake.requests if method == "GET" and "/issues?" in path)

    def test_creating_many_items_in_a_big_repo_lists_it_a_bounded_number_of_times(self) -> None:
        """Prevents 2 full listings per create: 30 creates in a 250 issue repo may list at most 3 pages once, plus one newest page each."""
        for n in range(250):
            self.backend.create_issue(spec(f"old-{n}"))
        before = self.listings()
        for n in range(30):
            issue, adopted, _ = sync._find_or_create(self.backend, f"new-{n}", spec(f"new-{n}"))
            self.assertFalse(adopted)
        self.assertLessEqual(self.listings() - before, 3 + 30)

    def test_an_issue_created_earlier_is_still_adopted(self) -> None:
        """Prevents the cached index hiding an existing issue: it is adopted, not created twice."""
        self.backend.create_issue(spec("a"))
        self.backend.create_issue(spec("b"))
        issue, adopted, _ = sync._find_or_create(self.backend, "b", spec("b"))
        self.assertTrue(adopted)
        self.assertEqual(len(self.fake.calls("POST", "/issues")), 2)


if __name__ == "__main__":
    unittest.main()
