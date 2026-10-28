"""The snapshot cache: every command reads the ledger's files through a local SQLite cache of their text, validated by stat, and
gets exactly what an uncached read would give.

Each test is a scenario; its docstring names the failure it prevents. The cache lives in `.qew/snapshot.db` (never committed);
`QEW_NO_CACHE=1` bypasses it.
"""

import json
import os
import sqlite3
import stat
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World

NO_CACHE = {"QEW_NO_CACHE": "1"}


class SnapshotCacheWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(ledger_vcs="git")
        self.addCleanup(self.world.close)
        self.db = self.world.ledger / ".qew" / "snapshot.db"

    def out(self, *argv: str, env: dict | None = None) -> str:
        result = self.world.run(*argv, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def same_as_uncached(self, *commands: tuple[str, ...]) -> None:
        for argv in commands:
            self.assertEqual(self.out(*argv), self.out(*argv, env=NO_CACHE), f"qew {' '.join(argv)} differs with the cache")

    def make(self, title: str = "Work", *arguments: str) -> str:
        return self.world.create("--title", title, *arguments, "--quote", QUOTE)

    def test_a_warm_cache_serves_the_files_without_reading_them(self) -> None:
        """Prevents a cache that still reads 165,000 files: with every item and event file unreadable the warm read still works."""
        first = self.world.create("--kind", "epic", "--title", "First", "--quote", QUOTE)
        self.make("Second", "--parent", first)
        self.assertEqual(self.world.run("start", first).returncode, 0)
        time.sleep(2.2)  # the entries are old enough to be trusted
        warm = self.out("list", "--json")
        self.assertTrue(self.db.is_file())
        locked = []
        for path in (self.world.ledger / "items").rglob("item.md"), (self.world.ledger / "events").rglob("*.json"):
            for file in path:
                os.chmod(file, 0)
                locked.append(file)
        try:
            self.assertEqual(self.out("list", "--json"), warm)
        finally:
            for file in locked:
                os.chmod(file, stat.S_IRUSR | stat.S_IWUSR)

    def test_every_write_is_seen_by_the_next_read(self) -> None:
        """Prevents a stale answer after qew's own writes: create, start, evidence, complete and set each show at once."""
        item = self.make("One")
        self.same_as_uncached(("list", "--json"), ("ready",), ("show", item, "--json"))
        self.assertEqual(self.world.run("start", item).returncode, 0)
        self.same_as_uncached(("list", "--json"), ("ready",), ("show", item, "--json"))
        sha = self.world.commit("the work")
        self.world.run("evidence", item, "--change", sha)
        self.assertEqual(self.world.run("complete", item).returncode, 0)
        self.assertEqual(self.world.run("set", item, "--closed", "--title", "One renamed", "--field", "phase=2").returncode, 0)
        self.same_as_uncached(("list", "--json"), ("show", item, "--json"), ("check",))
        self.assertIn("One renamed", self.out("list"))

    def test_an_edit_made_outside_qew_is_seen_even_in_the_same_second(self) -> None:
        """Prevents a stale title after a hand edit, a git merge or another tool: in place or not, new files or removed ones."""
        item = self.make("Old title")
        other = self.make("Other")
        self.out("list")
        path = self.world.home("items", item) / "item.md"
        text = path.read_text()
        with open(path, "r+") as handle:  # in place, same length, no rename
            handle.write(text.replace("Old title", "New title"))
        self.assertIn("New title", self.out("list"))
        event = next((self.world.home("events", other)).glob("*.json"))
        later = event.with_name("9999999999z.json")
        later.write_text(json.dumps({"type": "commented", "at": "2026-10-11T00:00:00Z", "actor": "hand", "text": "typed by hand"}))
        self.assertIn("typed by hand", self.out("show", other))
        later.unlink()
        self.assertNotIn("typed by hand", self.out("show", other))
        for kind in ("items", "events"):
            for entry in (self.world.ledger / kind / other).iterdir():
                entry.unlink()
            (self.world.ledger / kind / other).rmdir()
        self.assertNotIn("Other", self.out("list"))

    def test_a_broken_or_foreign_cache_is_rebuilt_not_trusted(self) -> None:
        """Prevents a damaged cache failing a command: garbage, another format version and a missing file all rebuild silently."""
        self.make("Work")
        expected = self.out("list", "--json")
        self.db.write_bytes(b"this is not a database")
        self.assertEqual(self.out("list", "--json"), expected)
        connection = sqlite3.connect(self.db)
        connection.execute("update meta set v = 'from-the-future' where k = 'format'")
        connection.commit()
        connection.close()
        self.assertEqual(self.out("list", "--json"), expected)
        self.db.unlink()
        self.assertEqual(self.out("list", "--json"), expected)
        self.assertTrue(self.db.is_file())

    def test_no_cache_means_no_database(self) -> None:
        """Prevents the escape hatch writing anyway: QEW_NO_CACHE=1 reads the files and leaves no database behind."""
        self.make("Work")
        for suffix in ("", "-wal", "-shm", "-journal"):
            self.db.with_name(self.db.name + suffix).unlink(missing_ok=True)
        self.out("list", env=NO_CACHE)
        self.assertFalse(self.db.exists())

    def test_check_compares_with_the_files_and_heals_a_stale_cache(self) -> None:
        """Prevents a poisoned cache living on: `qew check` reads the files itself, says what the cache got wrong and fixes it."""
        item = self.make("Real title")
        self.out("list")
        time.sleep(2.2)
        self.out("list")
        connection = sqlite3.connect(self.db)
        connection.execute("update items set body = replace(body, 'Real title', 'Fake title') where id = ?", (item,))
        connection.commit()
        connection.close()
        self.assertIn("Fake title", self.out("list"), "the warm cache is trusted until check looks")
        checked = self.out("check")
        self.assertIn("snapshot cache", checked)
        self.assertIn("Real title", self.out("list"))

    def test_the_cache_is_not_part_of_the_repository(self) -> None:
        """Prevents committing a 50 MB cache: the database is ignored by git and leaves the ledger clean."""
        self.make("Work")
        self.out("list")
        self.assertTrue(self.db.is_file())
        self.assertEqual(self.world.ledger_dirty(), [])


if __name__ == "__main__":
    unittest.main()
