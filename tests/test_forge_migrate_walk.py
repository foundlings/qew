"""`qew forge migrate`: move the primary to another forge without losing, duplicating or half-switching anything.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import cmd_forge, ledger_config
from qew.errors import QewError
from tests import fake_github
from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.fake_github import FakeGitHub
from tests.ledger_harness import QUOTE, World
from tests.memory_forge import MemoryForge

ENV = {"FORGEJO_TOKEN": TOKEN, "GITHUB_TOKEN": fake_github.TOKEN}
QUIET = "\n[replication]\nauto_forge_sync = false\nauto_push = false\nforge_concurrency = 1\n"


def forgejo_block(fake: FakeForgejo) -> str:
    return f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\n'


def github_block(fake: FakeGitHub) -> str:
    return f'\n[[mirror]]\nforge = "github"\nrepo = "{fake.repo_url}"\napi = "{fake.api}"\ngraphql = "{fake.base}{fake_github.GRAPHQL_PATH}"\n'


def slug_of(fake: FakeForgejo) -> str:
    return f"forgejo-{fake.repo_url.split('//')[1].replace('/', '-')}"


class MigrateWalk(unittest.TestCase):
    def test_migrating_from_forgejo_to_github_switches_the_primary_and_demotes_the_old_one(self) -> None:
        """Prevents a half move: all issues exist on the target, the primary names it, the old forge is a read-only mirror, claims survive."""
        old, new = FakeForgejo().start(), FakeGitHub().start()
        self.addCleanup(old.stop)
        self.addCleanup(new.stop)
        world = World(forgejo_block(old) + github_block(new), replication=QUIET + f'\n[authority]\nprimary = "{slug_of(old)}"\n')
        self.addCleanup(world.close)
        first = world.create("--title", "First", "--quote", QUOTE)
        second = world.create("--title", "Second", "--quote", QUOTE)
        self.assertEqual(world.run("sync", "--target", "forgejo", env=ENV).returncode, 0)
        self.assertEqual(world.run("start", first, env=ENV).returncode, 0)
        result = world.run("forge", "migrate", "--to", "127.0.0.1", env=ENV)
        self.assertNotEqual(result.returncode, 0, "an ambiguous target must be refused")
        result = world.run("forge", "migrate", "--to", "github", env=ENV)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(new.issues), 2)
        config = (world.code / ".qew" / "config.toml").read_text()
        self.assertIn("github-", config.split("[authority]")[1])
        self.assertIn("read_only = true", config)
        listing = world.run("mirror", "list").stdout
        self.assertRegex(listing, r"github-\S+ primary")
        self.assertRegex(listing, r"forgejo-\S+ read-only")
        self.assertIn("owner: tester", world.run("show", first).stdout)
        self.assertEqual(len(old.issues), 2)

    def test_a_failed_migration_changes_nothing_and_the_rerun_finishes_without_duplicates(self) -> None:
        """Prevents duplicates or a half switch: a down target leaves the config alone, a partial restore resumes, a third run is a no-op."""
        old, new = FakeForgejo().start(), FakeForgejo().start()
        self.addCleanup(old.stop)
        self.addCleanup(new.stop)
        world = World(forgejo_block(old) + forgejo_block(new), replication=QUIET + f'\n[authority]\nprimary = "{slug_of(old)}"\n')
        self.addCleanup(world.close)
        world.create("--title", "First", "--quote", QUOTE)
        second = world.create("--title", "Second", "--quote", QUOTE)
        self.assertEqual(world.run("sync", "--target", slug_of(old), env=ENV).returncode, 0)
        config = world.code / ".qew" / "config.toml"
        before = config.read_text()
        new.down = True
        down = world.run("forge", "migrate", "--to", slug_of(new), env=ENV)
        self.assertNotEqual(down.returncode, 0)
        self.assertEqual(config.read_text(), before)
        new.down = False
        self.assertEqual(world.run("sync", "--target", slug_of(new), "--item", second, env=ENV).returncode, 0)
        self.assertEqual(len(new.issues), 1)
        done = world.run("forge", "migrate", "--to", slug_of(new), env=ENV)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(new.issues), 2)
        posts = [r for r in new.requests if r[0] == "POST" and r[1].endswith("/issues")]
        again = world.run("forge", "migrate", "--to", slug_of(new), env=ENV)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("already", again.stdout)
        self.assertEqual([r for r in new.requests if r[0] == "POST" and r[1].endswith("/issues")], posts)
        self.assertIn(f'primary = "{slug_of(new)}"', config.read_text())

    def test_a_verification_mismatch_leaves_the_primary_unchanged(self) -> None:
        """Prevents switching to a forge that lacks items: one rejected issue refuses the migration and the config stays."""
        world = World('\n[[mirror]]\nforge = "forgejo"\nrepo = "https://a.example/o/r"\n\n[[mirror]]\nforge = "forgejo"\nrepo = "https://b.example/o/r"\n',
                      replication=QUIET + '\n[authority]\nprimary = "forgejo-a.example-o-r"\n')
        self.addCleanup(world.close)
        world.create("--title", "Fine", "--quote", QUOTE)
        world.create("--title", "poison", "--quote", QUOTE)
        forges = {"https://a.example/o/r": MemoryForge("a.example"), "https://b.example/o/r": MemoryForge("b.example")}
        forges["https://b.example/o/r"].reject_title = "poison"
        config_path = world.code / ".qew" / "config.toml"
        before = config_path.read_text()
        config = ledger_config.load(world.code)
        with self.assertRaises(QewError) as caught:
            cmd_forge.migrate(config, "b.example", False, build=lambda mirror: forges[mirror.repo])
        self.assertEqual(caught.exception.exit_code, 1)
        self.assertEqual(config_path.read_text(), before)

    def test_remove_old_drops_the_old_primary_and_an_unknown_target_is_refused(self) -> None:
        """Prevents leftovers and typos: --remove-old deletes the old block, an unknown target changes nothing."""
        old, new = FakeForgejo().start(), FakeForgejo().start()
        self.addCleanup(old.stop)
        self.addCleanup(new.stop)
        world = World(forgejo_block(old) + forgejo_block(new), replication=QUIET + f'\n[authority]\nprimary = "{slug_of(old)}"\n')
        self.addCleanup(world.close)
        world.create("--title", "First", "--quote", QUOTE)
        config = world.code / ".qew" / "config.toml"
        before = config.read_text()
        self.assertEqual(world.run("forge", "migrate", "--to", "nowhere", env=ENV).returncode, 2)
        self.assertEqual(config.read_text(), before)
        done = world.run("forge", "migrate", "--to", slug_of(new), "--remove-old", env=ENV)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn(old.repo_url, config.read_text())
        self.assertEqual(len(world.run("mirror", "list").stdout.strip().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
