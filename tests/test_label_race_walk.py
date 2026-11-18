"""Creating a label another writer created a moment earlier is not a failure: the label is there, which is what was wanted.

Two sync workers (or two machines) that both find a label missing both create it; the loser's 409 used to fail its item and leave it
unsynced. Each test is a scenario; its docstring names the failure it prevents.
"""

import unittest
from unittest import mock

from qew import forge_http
from qew.forge_forgejo import Forgejo
from qew.forge_gitlab import GitLab
from qew.ledger_config import Mirror
from tests.fake_forgejo import TOKEN as FORGEJO_TOKEN, FakeForgejo
from tests.fake_gitlab import TOKEN as GITLAB_TOKEN, FakeGitLab

PATH = "group/sub/repo"


class LabelRaceWalk(unittest.TestCase):
    def test_forgejo_takes_the_id_of_a_label_created_between_the_listing_and_the_create(self) -> None:
        """Prevents the second creator failing with `409 label exists` and its item staying unsynced."""
        fake = FakeForgejo().start()
        self.addCleanup(fake.stop)
        forge = Forgejo(Mirror("forgejo", fake.repo_url, None, "", {"api": fake.api}), forge_http.urllib_transport(timeout=5), {"FORGEJO_TOKEN": FORGEJO_TOKEN})
        first = forge._label_ids(("area:x",))  # the other writer got there first
        real = forge._list
        calls = {"n": 0}

        def stale(path: str, query=None):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return [] if calls["n"] == 1 and path == "/labels" else real(path, query) if query is not None else real(path)

        with mock.patch.object(forge, "_list", stale):
            again = forge._label_ids(("area:x",))
        self.assertEqual(again, first)

    def test_gitlab_takes_the_id_of_a_label_created_in_the_meantime(self) -> None:
        """Prevents the same race failing on GitLab, which answers 409 for an existing label."""
        fake = FakeGitLab().start()
        self.addCleanup(fake.stop)
        forge = GitLab(Mirror("gitlab", f"{fake.host}/{PATH}", None, "", {"api": fake.api}), forge_http.urllib_transport(timeout=5), {"GITLAB_TOKEN": GITLAB_TOKEN})
        first = forge._create_label("area:x")
        again = forge._create_label("area:x")
        self.assertEqual(again, first)


if __name__ == "__main__":
    unittest.main()
