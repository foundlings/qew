"""An `author:` label shows the human the forge knows, not the agent that filed the item: authors are users, the elfs are agents.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.fake_github import TOKEN, FakeGitHub
from tests.ledger_harness import QUOTE, World

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
OFF = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\ndivergences = \"report\"\n"


class AuthorLabelWalk(unittest.TestCase):
    def world(self, forge_login: str = "") -> tuple[World, FakeGitHub]:
        fake = FakeGitHub().start()
        self.addCleanup(fake.stop)
        mirrors = f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n{forge_login}'
        world = World(mirrors, replication=OFF)
        self.addCleanup(world.close)
        return world, fake

    def labels(self, fake: FakeGitHub) -> list[str]:
        return next(iter(fake.issues.values()))["labels"]

    def test_an_agent_authored_item_shows_the_human_login(self) -> None:
        """Prevents author:elf1 and author:elf2 on the board: with a forge login configured the label names that login, the ledger keeps elf1."""
        world, fake = self.world()
        (world.code / ".qew" / "config.toml").write_text((world.code / ".qew" / "config.toml").read_text() + '\n[forge]\nlogin = "887"\n')
        created = world.run("create", "--title", "Filed by an agent", "--quote", QUOTE, env={"QEW_ACTOR": "elf1"})
        self.assertEqual(created.returncode, 0)
        self.assertEqual(world.run("sync", "--all", env=ENV).returncode, 0)
        self.assertIn("author:887", self.labels(fake))
        self.assertNotIn("author:elf1", self.labels(fake))
        self.assertIn("author: elf1", world.run("show", created.stdout.split()[0]).stdout)

    def test_without_a_forge_login_the_author_keeps_its_own_name_and_the_old_label_is_pruned_once_mapped(self) -> None:
        """Prevents a guessed human: no login configured leaves author:elf1; configuring one relabels and prune deletes the retired label."""
        world, fake = self.world()
        self.assertEqual(world.run("create", "--title", "Filed by an agent", "--quote", QUOTE, env={"QEW_ACTOR": "elf1"}).returncode, 0)
        self.assertEqual(world.run("sync", "--all", env=ENV).returncode, 0)
        self.assertIn("author:elf1", self.labels(fake))
        config = world.code / ".qew" / "config.toml"
        config.write_text(config.read_text() + '\n[forge]\nlogin = "887"\n')
        self.assertEqual(world.run("sync", "--all", env=ENV).returncode, 0)
        self.assertIn("author:887", self.labels(fake))
        self.assertEqual(world.run("sync", "--prune-labels", env=ENV).returncode, 0)
        self.assertNotIn("author:elf1", fake.label_store)


if __name__ == "__main__":
    unittest.main()
