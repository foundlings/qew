"""Walks of one-line titles: every write path refuses a title that spans lines, and check flags one already on disk.

Adversary: a carriage return (or another line separator) in a title passes a newline-only check, then the
item file is read with universal newlines and every later read fails with a malformed header line.
"""

import json
import unittest

from tests.ledger_harness import World


class TitleLineWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def _items(self) -> list:
        return sorted(p.parent.name for p in (self.world.ledger / "items").glob("*/item.md")) if (self.world.ledger / "items").exists() else []

    def test_create_refuses_every_line_separator_and_writes_nothing(self) -> None:
        """A CR, form feed, NEL or paragraph separator in --title is refused before any file is written."""
        w = self.world
        for title in ("a\rb", "a\x0cb", "a\x85b", "a b", "a b"):
            result = w.run("create", "--title", title, "--draft")
            self.assertEqual(result.returncode, 2, (repr(title), result.stdout, result.stderr))
            self.assertIn("one line", result.stderr)
        self.assertEqual(self._items(), [])

    def test_bulk_create_and_set_refuse_a_carriage_return_title(self) -> None:
        """The plan-file path and `set --title` apply the same rule; the item keeps its old title."""
        w = self.world
        bulk = w.run("create", "--from-file", "-", stdin=json.dumps([{"title": "a\rb"}]))
        self.assertEqual(bulk.returncode, 2, bulk.stderr)
        self.assertIn("one line", bulk.stderr)
        item = w.create("--title", "Fine", "--draft")
        result = w.run("set", item, "--title", "x\ry")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("one line", result.stderr)
        self.assertIn("Fine", w.run("show", item).stdout)

    def test_check_names_an_item_whose_file_already_carries_a_carriage_return_title(self) -> None:
        """A file corrupted before the rule (or by hand) is reported by check with the cause, not a malformed header line."""
        w = self.world
        item = w.create("--title", "Fine", "--draft")
        path = w.home("items", f"{item}") / "item.md"
        path.write_bytes(path.read_bytes().replace(b"title: Fine", b"title: a\rb", 1))
        result = w.run("check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("one line", result.stdout + result.stderr)
        self.assertIn(item, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
