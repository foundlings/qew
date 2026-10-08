"""Walks of label creation during sync: create-only, neutral, confirmed, on every backend.

Adversary: sync recolouring or re-describing a label a team tuned, creating a
label with a random colour, creating the same label on every sync, and
reporting success for a label the forge silently dropped.
"""

import unittest
from dataclasses import replace

from qew import forge_http
from qew.forge import ForgeRejected, IssueSpec
from qew.forge_forgejo import Forgejo
from qew.forge_github import GitHub
from qew.forge_gitlab import GitLab
from qew.forge_http import urllib_transport
from qew.ledger_config import Mirror
from tests.fake_forgejo import TOKEN as FORGEJO_TOKEN, FakeForgejo
from tests.fake_github import TOKEN as GITHUB_TOKEN, FakeGitHub
from tests.fake_gitlab import TOKEN as GITLAB_TOKEN, FakeGitLab

PATH = "group/sub/repo"
SPEC = IssueSpec(title="T", body="b", labels=("area:x",), open=True)


class Adapter:
    """What a walk needs from one backend and its fake, in the same words."""

    def __init__(self, name, forge, fake):
        self.name, self.forge, self.fake = name, forge, fake

    def seed(self, label: str, color: str, description: str) -> None:
        if self.name == "github":
            self.fake.label_store[label] = {"color": color, "description": description}
        elif self.name == "forgejo":
            self.fake._label_id(label)
            self.fake.label_meta[label] = {"color": color, "description": description}
        else:
            self.fake.project(PATH).labels.append({"id": 900, "name": label, "color": color, "description": description})

    def stored(self, label: str):
        if self.name == "github":
            found = self.fake.label_store.get(label)
        elif self.name == "forgejo":
            found = self.fake.label_meta.get(label)
        else:
            found = next((x for x in self.fake.project(PATH).labels if x["name"] == label), None)
        return None if found is None else (found["color"].lstrip("#").lower(), found.get("description", ""))

    def creates(self) -> int:
        if self.name == "github":
            return len(self.fake.calls("POST", "/labels"))
        if self.name == "forgejo":
            return len([1 for method, path, _ in self.fake.requests if method == "POST" and path.endswith("/labels")])
        return len(self.fake.requests("POST", "/labels"))

    def drop_creates(self) -> None:
        self.fake.drop_label_creates = True


def adapters(test: unittest.TestCase):
    github, forgejo, gitlab = FakeGitHub().start(), FakeForgejo().start(), FakeGitLab().start()
    for fake in (github, forgejo, gitlab):
        test.addCleanup(fake.stop)
    return [
        Adapter("github", GitHub(Mirror("github", github.repo_url, None, "", {"api": github.api}), urllib_transport(),
                                 {"GITHUB_TOKEN": GITHUB_TOKEN, "QEW_NO_GH_TOKEN": "1"}), github),
        Adapter("forgejo", Forgejo(Mirror("forgejo", forgejo.repo_url, None, "", {"api": forgejo.api}),
                                   urllib_transport(timeout=5), {"FORGEJO_TOKEN": FORGEJO_TOKEN}), forgejo),
        Adapter("gitlab", GitLab(Mirror("gitlab", f"{gitlab.host}/{PATH}", None, "", {"api": gitlab.api}),
                                 forge_http.urllib_transport(timeout=5), {"GITLAB_TOKEN": GITLAB_TOKEN}), gitlab),
    ]


class LabelCreationWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.backends = adapters(self)

    def test_existing_label_keeps_color_and_description(self) -> None:
        """A label a team tuned must survive a sync that applies it."""
        for b in self.backends:
            b.seed("area:x", "ff0000", "Tuned by the team")
            b.forge.create_issue(SPEC)
            self.assertEqual(b.stored("area:x"), ("ff0000", "Tuned by the team"), b.name)
            self.assertEqual(b.creates(), 0, b.name)

    def test_missing_label_is_created_neutral_exactly_once(self) -> None:
        """A new label gets the neutral colour and fixed description, and a second sync creates nothing."""
        for b in self.backends:
            b.forge.create_issue(SPEC)
            self.assertEqual(b.stored("area:x"), ("ededed", "Managed by qew"), b.name)
            b.forge.create_issue(replace(SPEC, title="U"))
            self.assertEqual(b.creates(), 1, b.name)

    def test_label_creation_that_does_not_persist_fails_the_item(self) -> None:
        """A dropped label create is an indeterminate failure of that item, never a phantom success."""
        for b in self.backends:
            b.drop_creates()
            with self.assertRaises(ForgeRejected, msg=b.name) as raised:
                b.forge.create_issue(SPEC)
            self.assertIn("area:x", raised.exception.detail, b.name)


if __name__ == "__main__":
    unittest.main()
