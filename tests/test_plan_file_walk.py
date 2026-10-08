"""Walks of whole-plan validation for `create --from-file`: nothing is written unless every entry is valid.

Adversary: a typo silently dropping a dependency, a duplicate ref overwriting an entry, an order-dependent
plan, a cycle or bad file reaching the ledger, and a partly written plan after one bad line.
"""

import json
import os
import unittest

from tests.ledger_harness import World
from tests.plan_support import jsonl, listing, snapshot


def shape_of(world: World) -> list:
    rows = listing(world)
    titles = {row["id"]: row["title"] for row in rows}
    return sorted((row["kind"], row["title"], titles.get(row.get("parent"), "")) for row in rows)


class PlanFileWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def plan_file(self, text, name: str = "plan.jsonl", binary: bool = False) -> str:
        path = self.world.base / name
        if binary:
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        return str(path)

    def refuse(self, text, fragment: str, *extra: str) -> None:
        before = snapshot(self.world)
        result = self.world.run("create", "--from-file", "-", *extra, stdin=text)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(fragment, result.stderr)
        self.assertEqual(snapshot(self.world), before, "a refused plan must write nothing")

    def shape(self) -> list:
        return shape_of(self.world)

    def test_jsonl_and_a_json_array_of_the_same_plan_produce_the_same_items(self) -> None:
        """Format-dependent behaviour would make a reviewed plan mean different things."""
        entries = [
            {"ref": "e", "kind": "epic", "title": "Epic"},
            {"kind": "issue", "title": "One", "parent": "@e"},
            {"kind": "issue", "title": "Two", "parent": "@e"},
        ]
        as_lines = World()
        self.addCleanup(as_lines.close)
        self.assertEqual(as_lines.run("create", "--from-file", "-", stdin=jsonl(*entries)).returncode, 0)
        self.assertEqual(self.world.run("create", "--from-file", "-", stdin=json.dumps(entries)).returncode, 0)
        self.assertEqual(self.shape(), shape_of(as_lines))
        self.assertEqual(len(self.shape()), 3)

    def test_an_unknown_field_is_refused_naming_the_line_and_the_field(self) -> None:
        """A misspelt `need` silently ignored would drop a dependency the author thinks exists."""
        text = jsonl({"title": "A"}, {"title": "B", "need": ["@x"]})
        self.refuse(text, "line 2")
        self.refuse(text, "need")

    def test_duplicate_refs_and_a_ref_equal_to_a_generated_key_are_refused(self) -> None:
        """A duplicate ref overwrote the first entry; `line-N` is the key an anonymous entry gets."""
        self.refuse(jsonl({"ref": "a", "title": "A"}, {"ref": "a", "title": "B"}), "already used")
        self.refuse(jsonl({"title": "Anonymous"}, {"ref": "line-1", "title": "Clash"}), "line-1")
        self.refuse(jsonl({"ref": "line-2", "title": "Clash"}, {"title": "Anonymous"}), "line-2")

    def test_forward_references_resolve_and_dangling_ones_are_refused(self) -> None:
        """A plan must not depend on the order of its lines."""
        ok = jsonl({"ref": "a", "title": "A", "needs": ["@b"]}, {"ref": "b", "title": "B"})
        result = self.world.run("create", "--from-file", "-", stdin=ok)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["title"]: row for row in listing(self.world)}
        self.assertEqual(rows["A"]["needs"], [rows["B"]["id"]])
        self.refuse(jsonl({"title": "A", "needs": ["@nowhere"]}), "@nowhere")

    def test_cycles_among_new_items_and_through_existing_ones_are_refused_before_any_write(self) -> None:
        """A cycle written from a file would be found only by a later `check`."""
        self.refuse(jsonl({"ref": "a", "title": "A", "needs": ["@b"]}, {"ref": "b", "title": "B", "needs": ["@a"]}), "cycle")
        old = self.world.create("--title", "Existing")
        path = self.world.ledger / "items" / f"{old}.md"  # a hand-merged dangling need that the new item will satisfy
        path.write_text(path.read_text().replace("needs: \n", "needs: ZNEW1\n", 1))
        self.refuse(jsonl({"id": "ZNEW1", "title": "New", "needs": [old]}), "cycle")

    def test_bad_files_are_refused_whole(self) -> None:
        """Reading anything but the reviewed regular UTF-8 file, or half of a plan, must be impossible."""
        good = self.plan_file(jsonl({"title": "A"}), "good.jsonl")
        link = self.world.base / "link.jsonl"
        os.symlink(good, link)
        before = snapshot(self.world)
        cases = {
            "symbolic link": str(link),
            "not UTF-8": self.plan_file(b"\xff\xfe{}", "bytes.jsonl", binary=True),
            "line 3": self.plan_file('{"title": "A"}\n{"title": "B"}\n{broken\n', "broken.jsonl"),
            "empty": self.plan_file("\n  \n", "empty.jsonl"),
            "no work items": self.plan_file("[]", "none.json"),
        }
        for fragment, path in cases.items():
            result = self.world.run("create", "--from-file", path)
            self.assertEqual(result.returncode, 2, fragment)
            self.assertIn(fragment, result.stderr, fragment)
        self.assertEqual(snapshot(self.world), before)

    def test_invalid_priority_is_refused_naming_the_field(self) -> None:
        """A priority that only fails `check` later would leave an unreadable header behind."""
        self.refuse(jsonl({"title": "A", "priority": "P1"}), "priority")
        self.refuse(jsonl({"title": "A", "priority": -1}), "priority")

    def test_a_bad_entry_late_in_the_plan_leaves_no_earlier_entry_behind(self) -> None:
        """Validation is of the whole plan: a first valid item must not survive a bad second one."""
        self.refuse(jsonl({"title": "Fine"}, {"title": " "}), "line 2")


if __name__ == "__main__":
    unittest.main()
