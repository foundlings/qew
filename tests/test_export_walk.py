"""Walks of `qew export`: Markdown files with fixed headers, and the JSON that `create --from-file` accepts.

Adversary: an export that depends on run order or time, writes outside its directory or over
existing files, or emits JSON that `create --from-file` cannot read back (a child listed
before its parent, a changed field).
"""

import json
import os
import re
import unittest

from tests.ledger_harness import World
from tests.spec_loader import load, spec


def populate(world: World) -> None:
    """The parent Z sorts after its children so file order alone would not be dependency order."""
    load(
        world,
        spec("Z", "Epic Z", kind="epic"),
        spec("A", "Child A", parent="Z", labels=["area:x"], priority=1, body="Line one\n\nLine `two`\n"),
        spec("B", "Task B", kind="task", parent="Z", needs=["A"], body="Body"),
    )


def read(path) -> str:
    return path.read_text(encoding="utf-8")


class ExportWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        populate(self.world)
        self.out = self.world.base / "out"

    def test_export_writes_the_item_files_and_a_deterministic_events_summary(self) -> None:
        """Item files are the ledger's own files; the summary orders by id and carries no wall clock of its own."""
        w = self.world
        result = w.run("export", str(self.out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"exported 3 items to {self.out}")
        for item_id in ("A", "B", "Z"):
            self.assertEqual(read(self.out / "items" / f"{item_id}.md"), read(w.ledger / "items" / f"{item_id}.md"))
        summary = json.loads(read(self.out / "events.json"))
        self.assertEqual(list(summary), ["A", "B", "Z"])
        self.assertEqual([event["type"] for event in summary["A"]], ["approved"])
        self.assertEqual(summary["A"][0]["actor"], "tester")
        again = w.base / "again"
        self.assertEqual(w.run("export", str(again)).returncode, 0)
        self.assertEqual(read(again / "events.json"), read(self.out / "events.json"))

    def test_export_refuses_a_used_directory_a_symlink_and_a_hostile_id(self) -> None:
        """Nothing is overwritten, followed through a link, or written outside the target directory."""
        w = self.world
        self.out.mkdir()
        (self.out / "keep.txt").write_text("mine")
        used = w.run("export", str(self.out))
        self.assertEqual(used.returncode, 2, used.stderr)
        self.assertIn("not empty", used.stderr)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["keep.txt"])

        elsewhere = w.base / "elsewhere"
        elsewhere.mkdir()
        link = w.base / "link"
        os.symlink(elsewhere, link)
        linked = w.run("export", str(link))
        self.assertEqual(linked.returncode, 2, linked.stderr)
        self.assertIn("symbolic link", linked.stderr)
        self.assertEqual(list(elsewhere.iterdir()), [])

        (w.ledger / "items" / "evil.md").write_text(
            "id: ../../escape\nkind: issue\ntitle: x\ncreated: 2026-01-01T00:00:00Z\n---\n"
        )
        fresh = w.base / "fresh" / "deep"
        hostile = w.run("export", str(fresh))
        self.assertNotEqual(hostile.returncode, 0)
        self.assertIn("not a valid id", hostile.stderr)
        self.assertFalse((w.base / "fresh").exists(), "nothing is created before the ledger reads whole")
        self.assertFalse((w.base / "escape.md").exists())

    def test_export_refuses_a_graph_it_cannot_describe_whole(self) -> None:
        """A dangling need or a cycle would export a file set that imports into a broken ledger."""
        w = self.world
        path = w.ledger / "items" / "A.md"
        original = read(path)
        path.write_text(re.sub(r"(?m)^needs:.*$", "needs: GONE", original))
        for arguments in ([str(self.out)], ["--json"]):
            dangling = w.run("export", *arguments)
            self.assertEqual(dangling.returncode, 1, dangling.stdout)
            self.assertIn("dangling need GONE", dangling.stderr)
        path.write_text(re.sub(r"(?m)^needs:.*$", "needs: B", original))
        cycle = w.run("export", "--json")
        self.assertEqual(cycle.returncode, 1, cycle.stdout)
        self.assertIn("cycle", cycle.stderr)
        self.assertFalse(self.out.exists())

    def test_export_needs_exactly_one_destination(self) -> None:
        """A directory and --json together, or neither, is a usage mistake and not a guess."""
        both = self.world.run("export", str(self.out), "--json")
        self.assertEqual(both.returncode, 2)
        self.assertIn("a directory or --json", both.stderr)
        neither = self.world.run("export")
        self.assertEqual(neither.returncode, 2)
        self.assertIn("a directory or --json", neither.stderr)


class ExportJsonWalk(unittest.TestCase):
    def test_export_json_round_trips_through_create_from_file(self) -> None:
        """What export prints, create --from-file accepts, parents first, and the item files come out equal."""
        source, target = World(), World()
        self.addCleanup(source.close)
        self.addCleanup(target.close)
        populate(source)
        exported = source.run("export", "--json")
        self.assertEqual(exported.returncode, 0, exported.stderr)
        items = json.loads(exported.stdout)
        self.assertEqual([item["id"] for item in items], ["Z", "A", "B"])
        self.assertEqual(
            set(items[0]), {"id", "kind", "title", "parent", "needs", "labels", "priority", "created", "body"}
        )
        created = target.run("create", "--from-file", "-", stdin=exported.stdout)
        self.assertEqual(created.returncode, 0, created.stderr)
        for item_id in ("A", "B", "Z"):
            self.assertEqual(read(target.ledger / "items" / f"{item_id}.md"), read(source.ledger / "items" / f"{item_id}.md"))
        self.assertEqual(target.run("export", "--json").stdout, exported.stdout)


if __name__ == "__main__":
    unittest.main()
