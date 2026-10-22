"""Secondary mirrors are one-way, and `qew mirror` edits the config without disturbing the rest of it.

Each test is a scenario; its docstring names the failure it prevents.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from qew import ledger_config
from qew.forge_pull import pull_target
from qew.ledger_store import Ledger
from qew.sync import Target, sync_target
from tests.fake_forgejo import TOKEN, FakeForgejo
from tests.ledger_harness import QUOTE, World
from tests.memory_forge import MemoryForge

ENV = {"FORGEJO_TOKEN": TOKEN}
QUIET = "\n[replication]\nauto_forge_sync = false\nauto_push = false\n"
HAND_WRITTEN = '# my ledger\n[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n'


def slug_of(fake: FakeForgejo) -> str:
    return f"forgejo-{fake.repo_url.split('//')[1].replace('/', '-')}"


class SecondaryEditsWalk(unittest.TestCase):
    def test_an_edit_on_a_read_only_mirror_is_not_imported_but_a_close_is_flagged(self) -> None:
        """Prevents a one-way mirror leaking forge edits into the ledger: content is ignored, a state change is only flagged."""
        world = World(HAND_WRITTEN + "read_only = true\n", replication=QUIET)
        self.addCleanup(world.close)
        forge = MemoryForge("git.example")
        config = ledger_config.load(world.code)
        ledger = Ledger(world.ledger, "TST")
        target = Target(config.mirrors[0], ledger_config.mirror_slug(config.mirrors[0]), forge, read_only=True)
        item = world.create("--title", "Original", "--quote", QUOTE)
        sync_target(ledger, target, config)
        forge.edit(1, title="Hijacked", labels=("injected",))
        forge.comment_as_human(1, "drive-by")
        report = pull_target(ledger, target)
        self.assertEqual((report.applied, report.comments, report.new_items), (0, 0, 0))
        self.assertEqual([e["type"] for e in world.events(item)], ["approved"])
        self.assertIn("title: Original", world.run("show", item).stdout)
        forge.edit(1, open=False)
        self.assertEqual(pull_target(ledger, target).divergences, 1)
        self.assertEqual(world.events(item)[-1]["field"], "state")

    def test_only_the_primary_forge_is_read_back_when_a_primary_is_set(self) -> None:
        """Prevents two-way sync on a secondary: with a primary set, every other target is one-way, the primary keeps its pull."""
        first, second = FakeForgejo().start(), FakeForgejo().start()
        self.addCleanup(first.stop)
        self.addCleanup(second.stop)
        mirrors = "".join(
            f'\n[[mirror]]\nforge = "forgejo"\nrepo = "{f.repo_url}"\napi = "{f.api}"\n' for f in (first, second)
        )
        world = World(mirrors, replication=QUIET + f'\n[authority]\nprimary = "{slug_of(first)}"\n')
        self.addCleanup(world.close)
        item = world.create("--title", "Original", "--quote", QUOTE)
        self.assertEqual(world.run("sync", "--all", env=ENV).returncode, 0)
        second.issues[1]["title"] = "Secondary edit"
        result = world.run("sync", "--all", env=ENV)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("title: Original", world.run("show", item).stdout)
        self.assertEqual([e["type"] for e in world.events(item)], ["approved"])
        first.issues[1]["title"] = "Primary edit"
        self.assertEqual(world.run("sync", "--all", env=ENV).returncode, 0)
        self.assertIn("title: Primary edit", world.run("show", item).stdout)


class MirrorCommandWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(HAND_WRITTEN, replication=QUIET)
        self.addCleanup(self.world.close)
        self.config = self.world.code / ".qew" / "config.toml"
        self.original = self.config.read_text()

    def test_add_list_and_remove_round_trip_leaves_the_rest_of_the_config_untouched(self) -> None:
        """Prevents a config edit rewriting the file: comments, order and other tables survive add then remove byte for byte."""
        added = self.world.run("mirror", "add", "github", "https://github.com/acme/widgets", "--read-only")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertIn("github-github.com-acme-widgets", added.stdout)
        text = self.config.read_text()
        self.assertTrue(text.startswith(self.original))
        self.assertIn("read_only = true", text)
        listed = self.world.run("mirror", "list").stdout
        self.assertIn("github-github.com-acme-widgets", listed)
        self.assertIn("read-only", listed)
        self.assertIn("forgejo-git.example-o-r", listed)
        removed = self.world.run("mirror", "remove", "acme-widgets")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(self.config.read_text(), self.original)

    def test_removing_the_primary_is_refused_and_changes_nothing(self) -> None:
        """Prevents orphaning claim arbitration: the primary cannot be removed until another forge takes over."""
        self.config.write_text(self.original + '\n[authority]\nprimary = "git.example"\n')
        before = self.config.read_text()
        result = self.world.run("mirror", "remove", "git.example")
        self.assertEqual(result.returncode, 2)
        self.assertIn("primary", result.stderr)
        self.assertEqual(self.config.read_text(), before)

    def test_an_invalid_or_duplicate_target_is_refused_before_anything_is_written(self) -> None:
        """Prevents a broken config: a bad URL or a repeated target leaves the file exactly as it was."""
        for arguments in (("forgejo", "not a url"), ("forgejo", "https://git.example/o/r"), ("nowhere", "https://x.example/o/r")):
            result = self.world.run("mirror", "add", *arguments)
            self.assertEqual(result.returncode, 2, arguments)
        self.assertEqual(self.config.read_text(), self.original)
        self.assertEqual(self.world.run("mirror", "remove", "missing").returncode, 2)


if __name__ == "__main__":
    unittest.main()
