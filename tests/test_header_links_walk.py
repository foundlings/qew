"""Item headers carry ledger-level links only; forge numbers and URLs live in mirrors/.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import QUOTE, World

ENV = {"FORGEJO_TOKEN": TOKEN}
QUIET = "\n[replication]\nauto_forge_sync = false\nauto_push = false\n"


def mirror_block(fake: FakeForgejo) -> str:
    return f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'


class HeaderLinksWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        self.world = World(mirror_block(self.fake), replication=QUIET)
        self.addCleanup(self.world.close)
        self.issue = self.world.create("--title", "The tracking issue", "--quote", QUOTE)
        self.work = self.world.create("--title", "The work", "--quote", QUOTE)

    def item_file(self, item: str) -> str:
        return (self.world.ledger / "items" / f"{item}.md").read_text()

    def test_show_resolves_a_link_to_the_forge_url_from_the_mirror_index(self) -> None:
        """Prevents forge URLs being stored in items: the header holds the ledger id and show finds the URL in mirrors/."""
        result = self.world.run("set", self.work, "--tracks", self.issue, "--pr", self.issue)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.world.run("sync", env=ENV).returncode, 0)
        shown = self.world.run("show", self.work).stdout
        url = f"{self.fake.repo_url}/issues/1"
        self.assertIn(f"tracks: {self.issue} {url}", shown)
        self.assertNotIn("http", self.item_file(self.work))
        self.assertIn(f"tracks: {self.issue}", self.item_file(self.work))

    def test_a_header_holding_a_url_or_a_forge_number_fails_check(self) -> None:
        """Prevents the forge mapping leaking into item files: a URL, a #number or a dangling id is a problem."""
        self.assertEqual(self.world.run("set", self.work, "--tracks", self.issue).returncode, 0)
        self.assertEqual(self.world.run("check").returncode, 0)
        self.world.run("set", self.work, "--tracks", "")
        for value in ("https://github.com/o/r/issues/278", "#278", "nothere"):
            path = self.world.ledger / "items" / f"{self.work}.md"
            clean = self.item_file(self.work)
            path.write_text(clean.replace("owner:", f"tracks: {value}\nowner:", 1))
            result = self.world.run("check")
            self.assertEqual(result.returncode, 1, value)
            self.assertIn("tracks", result.stdout + result.stderr)
            path.write_text(clean)
        refused = self.world.run("set", self.work, "--tracks", "https://github.com/o/r/issues/278")
        self.assertEqual(refused.returncode, 2)
        self.assertNotIn("tracks", self.item_file(self.work))

    def test_adding_a_forge_target_rewrites_no_item_file(self) -> None:
        """Prevents a forge change touching the work: a new target adds mirrors/ files only, items/ stay byte for byte."""
        before = {i: self.item_file(i) for i in (self.issue, self.work)}
        second = FakeForgejo().start()
        self.addCleanup(second.stop)
        added = self.world.run("mirror", "add", "forgejo", second.repo_url, "--api", second.api)
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(self.world.run("sync", env=ENV).returncode, 0)
        self.assertEqual({i: self.item_file(i) for i in before}, before)
        self.assertEqual(len(list((self.world.ledger / "mirrors" / self.work).glob("*.json"))), 2)


if __name__ == "__main__":
    unittest.main()
