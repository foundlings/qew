"""A need removed from the ledger is removed from the forge's blocked-by list on the next sync; links qew did not create stay.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN as FORGEJO_TOKEN, FakeForgejo
from tests.fake_github import TOKEN as GITHUB_TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\ndivergences = \"report\"\nfetch_interval_s = 0\n"


class _Unlink:
    fake: FakeGitHub | FakeForgejo
    world: World
    env: dict[str, str]

    def blockers(self, title: str) -> list[int]:
        raise NotImplementedError

    def number(self, title: str) -> int:
        return next(n for n, i in self.fake.issues.items() if i["title"] == title)

    def setup_items(self) -> tuple[str, str, str]:
        w = self.world
        blocker = w.create("--title", "Blocker", "--quote", QUOTE)
        other = w.create("--title", "Other blocker", "--quote", QUOTE)
        work = w.create("--title", "Work", "--needs", blocker, other, "--quote", QUOTE)
        self.assertEqual(w.run("sync", env=self.env).returncode, 0)
        return blocker, other, work

    def test_a_removed_need_is_unlinked_the_kept_one_stays_and_a_rerun_sends_nothing(self) -> None:
        """Prevents the forge showing 'blocked by' items the ledger no longer needs, which `qew sync` reported as success."""
        blocker, other, work = self.setup_items()
        self.assertEqual(sorted(self.blockers("Work")), sorted([self.number("Blocker"), self.number("Other blocker")]))
        self.assertEqual(self.world.run("set", work, "--needs", blocker).returncode, 0)
        self.assertEqual(self.world.run("sync", env=self.env).returncode, 0)
        self.assertEqual(self.blockers("Work"), [self.number("Blocker")])
        self.assertEqual(self.world.run("set", work, "--needs").returncode, 0)
        self.assertEqual(self.world.run("sync", env=self.env).returncode, 0)
        self.assertEqual(self.blockers("Work"), [])
        calls = len(self.fake.requests)
        self.assertEqual(self.world.run("sync", env=self.env).returncode, 0)
        deletes = [r for r in self.fake.requests[calls:] if r[0] == "DELETE"]
        self.assertEqual(deletes, [])

    def test_a_link_a_human_added_is_not_removed(self) -> None:
        """Prevents sync deleting dependencies qew never created: only the links it recorded are its to remove."""
        blocker, other, work = self.setup_items()
        self.world.run("set", work, "--needs")
        self.add_link("Work", "Third")
        self.assertEqual(self.world.run("sync", env=self.env).returncode, 0)
        self.assertIn(self.number("Third"), self.blockers("Work"))
        self.assertNotIn(self.number("Blocker"), self.blockers("Work"))


class GitHubUnlinkWalk(_Unlink, unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        self.env = {"GITHUB_TOKEN": GITHUB_TOKEN, "QEW_NO_GH_TOKEN": "1"}

    def blockers(self, title: str) -> list[int]:
        return list(self.fake.issues[self.number(title)]["blocked_by"])

    def add_link(self, title: str, blocker: str) -> None:
        self.world.create("--title", blocker, "--quote", QUOTE)
        self.world.run("sync", env=self.env)
        self.fake.issues[self.number(title)]["blocked_by"].append(self.number(blocker))


class ForgejoUnlinkWalk(_Unlink, unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n'
        self.world = World(mirrors, replication=OFF)
        self.addCleanup(self.world.close)
        self.env = {"FORGEJO_TOKEN": FORGEJO_TOKEN}

    def blockers(self, title: str) -> list[int]:
        return list(self.fake.dependencies.get(self.number(title), []))

    def add_link(self, title: str, blocker: str) -> None:
        self.world.create("--title", blocker, "--quote", QUOTE)
        self.world.run("sync", env=self.env)
        self.fake.dependencies.setdefault(self.number(title), []).append(self.number(blocker))


if __name__ == "__main__":
    unittest.main()
