"""Walks of `qew import DIR`: an exported tree of Markdown files read back mechanically.

Adversary: a malformed or hostile file half-importing a tree, a duplicate id overwriting or
mixing with an existing item, a file read through a symbolic link, and a header the importer
guesses at instead of refusing.
"""

import os
import shutil
import unittest

from tests.ledger_harness import QUOTE, World
from tests.spec_loader import load, spec

HEADER = "id: {id}\nkind: issue\ntitle: Bad\n{extra}created: 2026-01-01T00:00:00Z\n---\nbody\n"


def read(path) -> str:
    return path.read_text(encoding="utf-8")


def written(world: World) -> list:
    items = world.ledger / "items"
    return sorted(p.name for p in items.iterdir()) if items.is_dir() else []


class MarkdownImportWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.source = World()
        self.target = World()
        self.addCleanup(self.source.close)
        self.addCleanup(self.target.close)
        load(
            self.source,
            spec("Z", "Epic Z", kind="epic"),
            spec("A", "Child A", parent="Z", labels=["area:x"], priority=1, body="Line one\n\nLine `two`\n"),
            spec("B", "Task B", kind="task", parent="Z", needs=["A"], body="Body"),
        )
        self.tree = self.source.base / "tree"
        self.assertEqual(self.source.run("export", str(self.tree)).returncode, 0)

    def test_an_exported_tree_imports_into_an_empty_ledger_with_ids_kept_as_drafts(self) -> None:
        """Export then import reproduces every item file; nothing is released without a human's words."""
        w = self.target
        result = w.run("import", str(self.tree))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["A draft Child A", "B draft Task B", "Z draft Epic Z"])
        for item_id in ("A", "B", "Z"):
            self.assertEqual(read(w.ledger / "items" / f"{item_id}.md"), read(self.source.ledger / "items" / f"{item_id}.md"))
            self.assertEqual([e["type"] for e in w.events(item_id)], ["drafted"])
        self.assertEqual(w.run("ready").stdout, "")
        self.assertEqual(w.run("check").returncode, 0)

    def test_a_human_quote_releases_the_imported_items(self) -> None:
        """--quote works as on create: the words are verified and recorded on each item."""
        w = self.target
        self.assertEqual(w.run("import", str(self.tree), "--quote", "nobody said this").returncode, 2)
        self.assertEqual(written(w), [])
        self.assertEqual(w.run("import", str(self.tree), "--quote", QUOTE).returncode, 0)
        self.assertEqual([e["type"] for e in w.events("A")], ["approved"])
        self.assertIn("A", w.run("ready").stdout)

    def test_a_duplicate_id_refuses_the_whole_import_before_any_write(self) -> None:
        """B already exists here: A and Z must not be written either, and B must stay untouched."""
        w = self.target
        load(w, spec("B", "Mine"))
        before = read(w.ledger / "items" / "B.md")
        result = w.run("import", str(self.tree))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("B already exists", result.stderr)
        self.assertEqual(written(w), ["B.md"])
        self.assertEqual(read(w.ledger / "items" / "B.md"), before)

    def test_a_malformed_tree_is_refused_whole_and_names_the_file(self) -> None:
        """Each broken shape stops the import before any item is written, and the message cites the culprit."""
        cases = {
            "unknown key": ("Bad.md", HEADER.format(id="Bad", extra="status: done\n")),
            "no terminator": ("Bad.md", "id: Bad\nkind: issue\ntitle: Bad\ncreated: 2026-01-01T00:00:00Z\n"),
            "id differs from the file name": ("Bad.md", HEADER.format(id="Other", extra="")),
            "owner set": ("Bad.md", HEADER.format(id="Bad", extra="owner: someone\n")),
            "dangling parent": ("Bad.md", HEADER.format(id="Bad", extra="parent: Nowhere\n")),
            "not markdown": ("Bad.txt", HEADER.format(id="Bad", extra="")),
        }
        for name, (file_name, text) in cases.items():
            with self.subTest(name):
                broken = self.source.base / "broken"
                shutil.rmtree(broken, ignore_errors=True)
                shutil.copytree(self.tree, broken)
                (broken / "items" / file_name).write_text(text)
                result = self.target.run("import", str(broken))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(file_name, result.stderr)
                self.assertEqual(written(self.target), [])

    def test_a_dependency_cycle_in_the_tree_is_refused(self) -> None:
        """Importing P and Q that need each other would create a ledger `check` fails forever."""
        tree = self.source.base / "cycle" / "items"
        tree.mkdir(parents=True)
        (tree / "P.md").write_text(HEADER.format(id="P", extra="needs: Q\n"))
        (tree / "Q.md").write_text(HEADER.format(id="Q", extra="needs: P\n"))
        result = self.target.run("import", str(tree.parent))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("needs cycle", result.stderr)
        self.assertEqual(written(self.target), [])

    def test_a_symbolic_link_and_a_missing_items_directory_are_refused(self) -> None:
        """A link could pull in a file from anywhere; a directory without items/ is not an export."""
        outside = self.source.base / "outside.md"
        outside.write_text(HEADER.format(id="Link", extra=""))
        os.symlink(outside, self.tree / "items" / "Link.md")
        linked = self.target.run("import", str(self.tree))
        self.assertEqual(linked.returncode, 2, linked.stderr)
        self.assertIn("symbolic link", linked.stderr)
        self.assertEqual(written(self.target), [])
        empty = self.source.base / "empty"
        empty.mkdir()
        missing = self.target.run("import", str(empty))
        self.assertEqual(missing.returncode, 2, missing.stderr)
        self.assertIn("no items directory", missing.stderr)


if __name__ == "__main__":
    unittest.main()
