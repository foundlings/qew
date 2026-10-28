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
    return item_id[-2:].lower()


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
            self.assertTrue(all(len(name) <= 2 for name in top), top)
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
        (w.ledger / "events" / "043zzzzzz").mkdir()
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
