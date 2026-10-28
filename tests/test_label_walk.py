"""Walks of label validation: reserved namespaces and malformed names are refused before any write.

Adversary: a label such as `qew:approved` on a draft that sync would mirror as
if the forge-side state said approved, and a batch import that half-writes.
"""

import json
import unittest

from tests.ledger_harness import World

RESERVED = ["qew:approved", "kind:epic", "priority:0", "author:alice", "QEW:Done", "Kind:Issue", "PRIORITY:9", "Author:x"]
MALFORMED = ["x" * 51, " edge", "edge ", "line\nbreak", "tab\tbreak", "a,b", "", "bell\x07"]


def snapshot_of(world: World) -> dict:
    return {str(p.relative_to(world.ledger)): p.read_bytes() for p in world.ledger.rglob("*")
            if p.is_file() and ".git" not in p.parts and ".qew" not in p.parts}


class LabelWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_create_refuses_each_reserved_label_before_any_write(self) -> None:
        """A reserved label would spoof forge state; the ledger must stay untouched."""
        before = snapshot_of(self.world)
        for label in RESERVED:
            result = self.world.run("create", "--title", "T", "--label", label, "--draft")
            self.assertEqual(result.returncode, 2, label + result.stderr)
            self.assertIn("reserved", result.stderr)
        self.assertEqual(snapshot_of(self.world), before)

    def test_set_refuses_reserved_labels_before_any_write(self) -> None:
        """`qew set --labels` must not smuggle a reserved name in or append an event."""
        item = self.world.create("--title", "T", "--label", "ok", "--draft")
        before = snapshot_of(self.world)
        for label in RESERVED:
            result = self.world.run("set", item, "--labels", "ok", label)
            self.assertEqual(result.returncode, 2, label + result.stderr)
            self.assertIn("reserved", result.stderr)
        self.assertEqual(snapshot_of(self.world), before)

    def test_import_refuses_a_reserved_label_for_the_whole_file(self) -> None:
        """One bad item in a batch aborts everything: no partial writes, for both --from-file and --import."""
        before = snapshot_of(self.world)
        events = [{"type": "drafted", "at": "2026-01-01T00:00:00Z", "actor": "x"}]
        plain = [{"title": "good", "labels": ["fine"]}, {"title": "bad", "labels": ["qew:approved"]}]
        imported = [{**spec, "events": events} for spec in plain]
        for batch, extra in ((plain, []), (imported, ["--import"])):
            result = self.world.run("create", "--from-file", "-", *extra, stdin=json.dumps(batch))
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("reserved", result.stderr)
        self.assertEqual(snapshot_of(self.world), before)

    def test_malformed_label_names_are_refused(self) -> None:
        """Long, space-edged, control-character, comma and empty labels never reach a file."""
        before = snapshot_of(self.world)
        for label in MALFORMED:
            result = self.world.run("create", "--title", "T", "--label", label, "--draft")
            self.assertEqual(result.returncode, 2, repr(label) + result.stderr)
        self.assertEqual(snapshot_of(self.world), before)

    def test_check_flags_a_reserved_label_found_in_a_file(self) -> None:
        """A merge or hand edit can put a reserved label in a file; check must flag it."""
        item = self.world.create("--title", "T", "--label", "ok", "--draft")
        path = self.world.home("items", f"{item}") / "item.md"
        path.write_text(path.read_text().replace("labels: ok", "labels: ok qew:approved"))
        result = self.world.run("check")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("reserved", result.stdout + result.stderr)
        self.assertEqual(self.world.run("list").returncode, 0, "reads stay available so the label can be repaired")
        self.assertEqual(self.world.run("set", item, "--labels", "ok").returncode, 0)
        self.assertEqual(self.world.run("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
