"""The request boundary: what config may name, what a forge may tell the client to follow.

Adversary: a mirror URL that sends the token to another host or reads a local file, and a
pagination Link that leads a client off the forge it was configured for.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import forge_http, ledger_config
from qew.errors import QewError
from qew.forge import ForgeRejected, IssueSpec, PullRequestSpec
from qew.forge_github import GitHub
from qew.forge_forgejo import Forgejo
from qew.forge_gitlab import GitLab
from qew.ledger_config import Mirror
from tests.fake_forgejo import TOKEN as FORGEJO_TOKEN, FakeForgejo
from tests.fake_github import TOKEN as GITHUB_TOKEN, FakeGitHub
from tests.fake_gitlab import TOKEN as GITLAB_TOKEN, FakeGitLab
from tests.ledger_harness import World

BAD_REPOS = (
    "file:///etc/passwd",
    "https://user:pw@host.example/o/r",
    "https://host.example/o/r?x=1",
    "https://host.example/o/r#frag",
    "https://host.example/o/ r",
    "https://host.example/o",
    "https://host.example/o/r/../x",
    "ftp://host.example/o/r",
    "https:///o/r",
    "https://host.example/o/r;rm",
)
BAD_APIS = ("file:///etc/passwd", "https://user:pw@host.example/api", "https://host.example/api?x=1", "javascript:alert(1)")


def mirror_config(repo: str = "https://git.example/o/r", **fields: str) -> str:
    lines = ["", "[[mirror]]", 'forge = "forgejo"', f'repo = "{repo}"']
    lines.extend(f'{key} = "{value}"' for key, value in fields.items())
    return "\n".join(lines) + "\n"


class ConfigBoundaryWalk(unittest.TestCase):
    def load(self, **fields: str):
        world = World(mirror_config(**fields))
        self.addCleanup(world.close)
        return ledger_config.load(world.code)

    def test_a_mirror_repo_that_could_redirect_the_token_or_read_a_file_is_refused_at_load(self) -> None:
        """A hostile URL in config must never reach a request: the token would leave for another host."""
        for repo in BAD_REPOS:
            with self.subTest(repo=repo):
                with self.assertRaises(QewError) as caught:
                    self.load(repo=repo)
                self.assertEqual(caught.exception.exit_code, 2)
                self.assertIn("mirror", str(caught.exception))

    def test_a_mirror_api_override_is_held_to_the_same_rules(self) -> None:
        for api in BAD_APIS:
            with self.subTest(api=api):
                with self.assertRaises(QewError) as caught:
                    self.load(api=api)
                self.assertEqual(caught.exception.exit_code, 2)

    def test_ordinary_hosts_ports_and_nested_gitlab_groups_load(self) -> None:
        self.assertEqual(self.load(repo="http://127.0.0.1:8080/acme/widgets").mirrors[0].repo, "http://127.0.0.1:8080/acme/widgets")
        world = World('\n[[mirror]]\nforge = "gitlab"\nrepo = "https://gitlab.example/group/sub/repo"\napi = "https://gitlab.example/api/v4"\n')
        self.addCleanup(world.close)
        self.assertEqual(ledger_config.load(world.code).mirrors[0].repo, "https://gitlab.example/group/sub/repo")
        world2 = World('\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/a/b/c"\n')
        self.addCleanup(world2.close)
        with self.assertRaises(QewError):
            ledger_config.load(world2.code)  # only GitLab nests namespaces


class ForeignLinkWalk(unittest.TestCase):
    """A Link header naming another host is data: the client keeps paging its own API root and never calls it."""

    def test_forgejo_pages_its_own_root_and_never_calls_the_host_a_link_names(self) -> None:
        fake, other = FakeForgejo().start(), FakeForgejo().start()
        self.addCleanup(fake.stop)
        self.addCleanup(other.stop)
        fake.max_limit = 1
        fake.link_base = other.api
        backend = Forgejo(Mirror("forgejo", fake.repo_url, None, "", {"api": fake.api}), forge_http.urllib_transport(timeout=5), {"FORGEJO_TOKEN": FORGEJO_TOKEN})
        for n in range(3):
            backend.create_issue(IssueSpec(f"T{n}", f"<!-- qew:TST-{n} -->", (), True))
        found = backend.find_issue("<!-- qew:TST-2 -->")
        self.assertIsNotNone(found)
        self.assertEqual(other.requests, [])

    def test_gitlab_pages_by_its_own_header_and_never_calls_the_host_a_link_names(self) -> None:
        fake, other = FakeGitLab(page_size=1).start(), FakeGitLab().start()
        self.addCleanup(fake.stop)
        self.addCleanup(other.stop)
        fake.link_base = other.api
        backend = GitLab(Mirror("gitlab", f"{fake.host}/group/repo", None, "", {"api": fake.api}), forge_http.urllib_transport(timeout=5), {"GITLAB_TOKEN": GITLAB_TOKEN})
        for n in range(3):
            backend.create_issue(IssueSpec(f"T{n}", f"<!-- qew:TST-{n} -->", (), True))
        self.assertIsNotNone(backend.find_issue("<!-- qew:TST-2 -->"))
        self.assertEqual(other.log, [])


class BoundedReadWalk(unittest.TestCase):
    """The reads a merge decision rests on stop at MAX_PAGES with a typed error, never a truncated list."""

    def test_github_check_runs_that_never_end_are_refused_not_summarised(self) -> None:
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        backend = GitHub(Mirror("github", fake.repo_url, None, "", {"api": fake.api}), forge_http.urllib_transport(timeout=5),
                         {"GITHUB_TOKEN": GITHUB_TOKEN, "QEW_NO_GH_TOKEN": "1"})
        pull = backend.open_pull_request(PullRequestSpec("topic", "main", "T", "b", False))
        fake.endless_check_runs = True
        before = len(fake.calls("GET", "/check-runs"))
        with self.assertRaises(ForgeRejected):
            backend.read_pull_request(pull.number)
        self.assertLessEqual(len(fake.calls("GET", "/check-runs")) - before, forge_http.MAX_PAGES)

    def test_gitlab_lists_that_never_end_are_refused(self) -> None:
        fake = FakeGitLab().start()
        self.addCleanup(fake.stop)
        backend = GitLab(Mirror("gitlab", f"{fake.host}/group/repo", None, "", {"api": fake.api}), forge_http.urllib_transport(timeout=5), {"GITLAB_TOKEN": GITLAB_TOKEN})
        backend.create_issue(IssueSpec("T", "<!-- qew:TST-1 -->", (), True))
        fake.endless_pages = True
        with self.assertRaises(ForgeRejected):
            backend.find_issue("<!-- qew:TST-404 -->")


if __name__ == "__main__":
    unittest.main()
