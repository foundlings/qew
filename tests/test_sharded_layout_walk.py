"""Schema 8: events, items, mirrors and baselines live under a two-character shard of the item id.

Adversary: one directory of tens of thousands of entries rewritten by every commit (the foundlings ledger: 25,000 item
directories in `events/`, a write costing tens of seconds), a migration that loses a file or changes what `qew show` says, an
older flat ledger rewritten without being asked, and two clones whose independent creations conflict in git.
"""

import re
import unittest
from pathlib import Path

from tests.ledger_harness import World, git


def shard(item_id: str) -> str:
    return "_" + item_id[-2:].lower()


def declare(world: World, version: int) -> None:
    toml = world.ledger / "qew-ledger.toml"
    toml.write_text(re.sub(r"schema_version = \d+", f"schema_version = {version}", toml.read_text(encoding="utf-8")), encoding="utf-8")


class ShardedLayoutWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        declare(self.world, 8)

    def test_every_write_lands_in_the_shard_of_its_item_and_the_ledger_still_reads_and_checks(self) -> None:
        """Prevents a flat directory: create, start, evidence and complete put item, events at <xx>/<id>, and show and check work."""
        w = self.world
        item = w.create("--title", "sharded")
        self.assertEqual(w.run("start", item).returncode, 0)
        self.assertEqual(w.run("evidence", item, "--change", w.commit()).returncode, 0)
        self.assertEqual(w.run("complete", item).returncode, 0)
        self.assertTrue((w.ledger / "items" / shard(item) / item / "item.md").is_file())
        self.assertTrue(list((w.ledger / "events" / shard(item) / item).glob("*.json")))
        self.assertFalse((w.ledger / "items" / item).exists())
        self.assertFalse((w.ledger / "events" / item).exists())
        shown = w.run("show", item)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("completed", shown.stdout)
        self.assertEqual(w.run("check").returncode, 0, w.run("check").stderr)
        self.assertIn(item, w.run("list").stdout)

    def test_no_top_level_directory_holds_item_directories_only_shards(self) -> None:
        """Prevents the wide directory: after many items, items/ and events/ hold only two-character shards, each far narrower."""
        w = self.world
        ids = [w.create("--title", f"item {number}") for number in range(40)]
        for kind in ("items", "events"):
            top = sorted(path.name for path in (w.ledger / kind).iterdir())
            self.assertTrue(all(name.startswith("_") and len(name) <= 3 for name in top), top)
            self.assertLessEqual(len(top), len(ids))
            self.assertGreater(len(top), 1)

    def test_migrate_layout_moves_a_flat_ledger_without_changing_what_it_says(self) -> None:
        """Prevents a lossy migration: items, events, mirrors and baselines move to their shards, show and check agree, one commit."""
        w = self.world
        declare(w, 7)
        item = w.create("--title", "flat first")
        other = w.create("--title", "second", "--needs", item)
        w.run("start", item)
        mirror = w.ledger / "mirrors" / item
        mirror.mkdir(parents=True)
        (mirror / "github-example.json").write_text('{"forge": "github", "number": 1, "kind": "issue", "url": "https://example/o/r"}\n')
        baseline = w.ledger / "baselines" / item / "github-example"
        baseline.mkdir(parents=True)
        (baseline / "043aaaaaa.json").write_text('{"state": "open"}\n')
        before = w.run("show", other).stdout
        events_before = {path.name for path in (w.ledger / "events" / item).glob("*.json")}
        self.assertTrue(events_before)
        git(w.ledger, "add", "-A")
        git(w.ledger, "commit", "-q", "-m", "flat state")
        dry = w.run("migrate-layout", "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("8", dry.stdout)
        self.assertTrue((w.ledger / "items" / item / "item.md").is_file(), "a dry run must write nothing")
        commits = len(git(w.ledger, "log", "--format=%h").splitlines())
        done = w.run("migrate-layout")
        self.assertEqual(done.returncode, 0, done.stderr)
        for kind, name in (("items", "item.md"), ("mirrors", "github-example.json")):
            self.assertTrue((w.ledger / kind / shard(item) / item / name).is_file(), kind)
        self.assertTrue((w.ledger / "baselines" / shard(item) / item / "github-example" / "043aaaaaa.json").is_file())
        self.assertEqual({path.name for path in (w.ledger / "events" / shard(item) / item).glob("*.json")}, events_before)
        self.assertFalse((w.ledger / "items" / item).exists())
        self.assertIn("schema_version = 8", (w.ledger / "qew-ledger.toml").read_text())
        self.assertEqual(w.run("show", other).stdout, before)
        self.assertEqual(w.run("check").returncode, 0, w.run("check").stderr)
        self.assertEqual(len(git(w.ledger, "log", "--format=%h").splitlines()) - commits, 1, "the migration is one commit")
        again = w.run("migrate-layout")
        self.assertIn("nothing to migrate", again.stdout)

    def test_a_flat_ledger_stays_flat_until_it_is_migrated(self) -> None:
        """Prevents a surprise rewrite: a schema 7 ledger keeps the flat layout for ordinary writes."""
        w = self.world
        declare(w, 7)
        item = w.create("--title", "still flat")
        self.assertTrue((w.ledger / "items" / item / "item.md").is_file())
        self.assertTrue((w.ledger / "events" / item).is_dir())

    def test_check_names_an_item_directory_left_outside_its_shard(self) -> None:
        """Prevents a flat directory hiding in a sharded ledger: no read would find it, so check names it and the way out."""
        w = self.world
        w.create("--title", "fine")
        (w.ledger / "events" / "043zzzzzz").mkdir()  # an item directory outside any shard
        (w.ledger / "events" / "043zzzzzz" / "043zzzzzy.json").write_text("{}\n")
        checked = w.run("check")
        self.assertEqual(checked.returncode, 1, checked.stdout)
        self.assertIn("events/043zzzzzz: not a shard directory", checked.stdout + checked.stderr)

    def test_pull_names_the_items_a_sharded_commit_brought_in(self) -> None:
        """Prevents the shard standing in for the item: pull lists the item id, not the last two characters of it."""
        w = World(ledger_vcs="git", remote=True)
        self.addCleanup(w.close)
        declare(w, 8)
        git(w.ledger, "add", "-A")
        git(w.ledger, "commit", "-q", "-m", "schema 8")
        git(w.ledger, "push", "-q", "origin", "main")
        other = w.clone("b")
        item = w.create("--title", "from a")
        pulled = other.run("pull")
        self.assertEqual(pulled.returncode, 0, pulled.stdout + pulled.stderr)
        self.assertIn(item, pulled.stdout)

    def tree(self, world: World) -> dict[str, bytes | None]:
        """Every file with its bytes and every directory (None): a directory left behind is part of a tree that was not restored."""
        return {str(path.relative_to(world.ledger)): path.read_bytes() if path.is_file() else None for path in sorted(world.ledger.rglob("*"))
                if ".git" not in path.parts and ".qew" not in path.parts and ".jj" not in path.parts}

    def flat_ledger_with_colliding_ids(self) -> tuple[World, str, str]:
        """Two items whose ids differ only in case from a shard name: on a case-insensitive disk `items/AA` and `items/aa` are one
        directory, the collision that corrupted a rehearsal's rollback."""
        w = self.world
        declare(w, 7)
        first = w.create("--title", "plain")
        epic = w.run("create", "--kind", "epic", "--title", "Letters")  # a letter id: A
        self.assertEqual(epic.returncode, 0, epic.stderr)
        second = w.run("create", "--kind", "epic", "--title", "More letters")  # and B; two-character ids follow
        self.assertEqual(second.returncode, 0, second.stderr)
        git(w.ledger, "add", "-A")
        git(w.ledger, "commit", "-q", "--allow-empty", "-m", "flat state")
        return w, first, epic.stdout.split()[0]

    def test_a_failed_migration_restores_the_exact_tree_even_for_letter_ids(self) -> None:
        """Prevents the mixed tree a rehearsal left behind: when the migration's own check fails, every file is back where it was,
        no shard directory remains, and git sees no change."""
        w, _item, letters = self.flat_ledger_with_colliding_ids()
        (w.ledger / "events" / "GHOST").mkdir()  # events of no item: loading ignores them, only the check after the move names them
        (w.ledger / "events" / "GHOST" / "043zzzzzz.json").write_text('{"type": "commented"}\n')
        git(w.ledger, "add", "-A")
        git(w.ledger, "commit", "-q", "--allow-empty", "-m", "an orphan")
        before = self.tree(w)
        refused = w.run("migrate-layout")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertEqual(self.tree(w), before)
        self.assertEqual(git(w.ledger, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertIn("schema_version = 7", (w.ledger / "qew-ledger.toml").read_text())
        (w.ledger / "events" / "GHOST" / "043zzzzzz.json").unlink()
        (w.ledger / "events" / "GHOST").rmdir()
        self.assertEqual(w.run("check").returncode, 0, w.run("check").stdout)

    def test_an_interrupted_migration_is_finished_by_running_it_again(self) -> None:
        """Prevents a half-moved ledger needing hand repair: some directories already under their shard, the rest flat, and a rerun
        moves what is left and passes the check."""
        w, item, _letters = self.flat_ledger_with_colliding_ids()
        for kind in ("items", "events"):
            target = w.ledger / kind / shard(item)
            target.mkdir(parents=True)
            (w.ledger / kind / item).rename(target / item)
        finished = w.run("migrate-layout")
        self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
        self.assertTrue((w.home("items", item) / "item.md").is_file())
        self.assertEqual(w.run("check").returncode, 0, w.run("check").stderr)
        self.assertIn("nothing to migrate", w.run("migrate-layout").stdout)

    def test_attachments_do_not_fail_the_migrations_own_check(self) -> None:
        """Prevents the false 'not committed' wall of attachment problems: the moved files are not committed yet when the check runs."""
        w = self.world
        declare(w, 7)
        item = w.create("--title", "with a file")
        w.run("start", item)
        attached = w.base / "note.txt"
        attached.write_text("an attachment\n")
        self.assertEqual(w.run("item", "attach", item, str(attached)).returncode, 0)
        git(w.ledger, "add", "-A")
        git(w.ledger, "commit", "-q", "--allow-empty", "-m", "attached")
        done = w.run("migrate-layout")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(list((w.home("items", item) / "attachments").iterdir()))

    def test_no_check_skips_the_verification_pass(self) -> None:
        """Prevents minutes of checking on a rename-only move when the user asks to skip it: --no-check migrates all the same."""
        w, item, _letters = self.flat_ledger_with_colliding_ids()
        done = w.run("migrate-layout", "--no-check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue((w.home("items", item) / "item.md").is_file())

    def test_two_clones_creating_items_merge_without_a_conflict(self) -> None:
        """Prevents shard directories becoming shared mutable files: independent creations in two clones merge cleanly in git."""
        w = World(ledger_vcs="git", remote=True)
        self.addCleanup(w.close)
        declare(w, 8)
        git(w.ledger, "add", "-A")
        git(w.ledger, "commit", "-q", "-m", "schema 8")
        git(w.ledger, "push", "-q", "origin", "main")
        other = w.clone("b")
        first = w.create("--title", "from a")
        second = other.create("--title", "from b")
        git(w.ledger, "pull", "-q", "--no-rebase", "origin", "main")
        git(other.ledger, "pull", "-q", "--no-rebase", "origin", "main")
        for world in (w, other):
            self.assertEqual(world.run("check").returncode, 0, world.run("check").stderr)
            listed = world.run("list").stdout
            self.assertIn(first, listed)
            self.assertIn(second, listed)


if __name__ == "__main__":
    unittest.main()
