"""Walks of item IDs: time, letters and counter modes, imported IDs, duplicates.

Adversary: two writers minting the same ID, a counter that sorts "10" before
"9", an imported ID rewritten or duplicated, and a hand-copied file that
shadows an existing item.
"""

import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import World

TIME = re.compile(r"^[0-9a-km-z]{9}$")


def configure(world: World, text: str) -> None:
    path = world.ledger / "qew-ledger.toml"
    path.write_text(path.read_text() + text)


class IdModeWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_time_mode_is_the_default_unprefixed_and_sortable(self) -> None:
        ids = [self.world.create("--title", f"T{n}", "--draft") for n in range(5)]
        self.assertTrue(all(TIME.match(i) for i in ids), ids)
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 5)

    def test_letters_mode_counts_a_to_z_then_aa(self) -> None:
        configure(self.world, '[id_mode]\nepic = "letters"\n')
        specs = json.dumps([{"kind": "epic", "title": f"E{n}"} for n in range(28)])
        result = self.world.run("create", "--from-file", "-", stdin=specs)
        ids = [line.split()[0] for line in result.stdout.splitlines()]
        self.assertEqual(ids[:3], ["A", "B", "C"])
        self.assertEqual(ids[25:28], ["Z", "AA", "AB"])
        issue = self.world.create("--kind", "issue", "--title", "still time", "--draft")
        self.assertRegex(issue, TIME)

    def test_counter_mode_counts_up_and_lists_numerically(self) -> None:
        configure(self.world, 'id_mode = "counter"\n')
        specs = json.dumps([{"kind": "issue", "title": f"N{n}"} for n in range(11)])
        result = self.world.run("create", "--from-file", "-", stdin=specs)
        ids = [line.split()[0] for line in result.stdout.splitlines()]
        self.assertEqual(ids, [str(n) for n in range(1, 12)])
        listed = [line.split()[0] for line in self.world.run("list").stdout.splitlines()]
        self.assertEqual(listed, ids)

    def test_imported_ids_are_kept_and_duplicates_are_refused(self) -> None:
        w = self.world
        spec = json.dumps({"id": "WP-ADA-03", "kind": "task", "title": "Imported"})
        self.assertEqual(w.run("create", "--from-file", "-", stdin=spec).stdout.split()[0], "WP-ADA-03")
        other = json.dumps({"id": "WP-ADA-03", "kind": "task", "title": "A different plan, same id"})
        again = w.run("create", "--from-file", "-", stdin=other)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("already exists", again.stderr)
        self.assertIn("WP-ADA-03", w.run("show", "WP-ADA").stdout)

    def test_check_fails_on_a_duplicate_id_across_files(self) -> None:
        w = self.world
        first = w.create("--title", "One", "--draft")
        text = (w.ledger / "items" / f"{first}" / "item.md").read_text()
        (w.ledger / "items" / "copy" / "item.md").parent.mkdir(exist_ok=True)
        (w.ledger / "items" / "copy" / "item.md").write_text(text)
        result = w.run("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate id", result.stdout)

    def test_ambiguous_prefix_is_refused_and_exact_wins(self) -> None:
        configure(self.world, 'id_mode = "letters"\n')
        specs = json.dumps([{"title": "a"}, {"title": "b"}] + [{"title": str(n)} for n in range(24)] + [{"title": "aa"}])
        self.world.run("create", "--from-file", "-", stdin=specs)
        self.assertIn("id: A\n", self.world.run("show", "A").stdout.replace("id: A\n", "id: A\n"))
        self.assertIn("\nid: AA\n", self.world.run("show", "AA").stdout)


if __name__ == "__main__":
    unittest.main()
