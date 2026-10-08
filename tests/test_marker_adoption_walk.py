"""Walks of marker-based adoption on every backend.

Adversary: an untrusted issue body that merely quotes `<!-- qew:ID -->` (or an
unrelated issue on a shared repository) being adopted and then overwritten by
the next sync; a pull request being adopted as an issue.
"""

import unittest
from dataclasses import replace

from qew import forge_http
from qew.forge import IssueSpec, marker_for
from qew.forge_forgejo import Forgejo
from qew.forge_github import GitHub
from qew.forge_gitlab import GitLab
from qew.forge_http import urllib_transport
from qew.ledger_config import Mirror
from tests.fake_forgejo import TOKEN as FORGEJO_TOKEN, FakeForgejo
from tests.fake_github import TOKEN as GITHUB_TOKEN, FakeGitHub
from tests.fake_gitlab import TOKEN as GITLAB_TOKEN, FakeGitLab

GITLAB_PATH = "group/sub/repo"
MARKER = marker_for("TST-1")
BASE = IssueSpec(title="T", body="", labels=(), open=True)


def backends(test: unittest.TestCase):
    github = FakeGitHub().start()
    forgejo = FakeForgejo().start()
    gitlab = FakeGitLab().start()
    for fake in (github, forgejo, gitlab):
        test.addCleanup(fake.stop)
    return {
        "github": GitHub(Mirror("github", github.repo_url, None, "", {"api": github.api}), urllib_transport(),
                         {"GITHUB_TOKEN": GITHUB_TOKEN, "QEW_NO_GH_TOKEN": "1"}),
        "forgejo": Forgejo(Mirror("forgejo", forgejo.repo_url, None, "", {"api": forgejo.api}), urllib_transport(timeout=5),
                           {"FORGEJO_TOKEN": FORGEJO_TOKEN}),
        "gitlab": GitLab(Mirror("gitlab", f"{gitlab.host}/{GITLAB_PATH}", None, "", {"api": gitlab.api}),
                         forge_http.urllib_transport(timeout=5), {"GITLAB_TOKEN": GITLAB_TOKEN}),
    }


class MarkerAdoptionWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.forges = backends(self)

    def test_quoted_marker_inside_a_sentence_is_not_adopted(self) -> None:
        """A body that merely mentions the marker must not be hijacked and later overwritten."""
        for name, forge in self.forges.items():
            forge.create_issue(replace(BASE, body=f"see {MARKER} for details"))
            forge.create_issue(replace(BASE, body=f"> {MARKER}"))
            self.assertIsNone(forge.find_issue(MARKER), name)

    def test_malformed_or_recased_markers_do_not_match(self) -> None:
        """Only the exact marker for this exact id counts."""
        bad = ["<!-- qew:tst-1 -->", "<!--qew:TST-1-->", "<!-- qew:TST-10 -->", "<!-- qew:TST-1 -->x"]
        for name, forge in self.forges.items():
            for body in bad:
                forge.create_issue(replace(BASE, body=body))
            self.assertIsNone(forge.find_issue(MARKER), name)

    def test_marker_alone_on_a_line_is_adopted_once(self) -> None:
        """The marker as first line, or alone on a later line, identifies the issue; the lowest number wins a race."""
        for name, forge in self.forges.items():
            first = forge.create_issue(replace(BASE, body=f"{MARKER}\n\nbody"))
            forge.create_issue(replace(BASE, body=f"intro\n{MARKER}\nmore"))
            found = forge.find_issue(MARKER)
            self.assertIsNotNone(found, name)
            self.assertEqual(found.number, first.number, name)


if __name__ == "__main__":
    unittest.main()
