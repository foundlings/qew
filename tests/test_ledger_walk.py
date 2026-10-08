"""Golden walk of the ledger: create, show, list, approve, ready, blocked, graph.

Adversary: state or dependencies read from prose, a shared index file that two
writers would conflict on, a draft offered as ready work, and a dependency that
does not gate readiness.
"""

import json
import unittest

from tests.ledger_harness import QUOTE, World


class LedgerWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def test_golden_walk_from_draft_to_ready_to_blocked(self) -> None:
        w = self.world
        first = w.create("--kind", "issue", "--title", "Build parser", "--priority", "1")
        second = w.create("--kind", "issue", "--title", "Use parser", "--needs", first)
        self.assertTrue(first.startswith("TST-"), first)
        self.assertEqual(len(first), len("TST-") + 26)

        self.assertEqual(w.run("ready").stdout.strip(), "", "drafts are never ready")
        w.approve(first, second)

        ready = w.run("ready").stdout.splitlines()
        self.assertEqual(len(ready), 1, ready)
        self.assertIn("Build parser", ready[0])
        self.assertIn("approved", ready[0])

        blocked = w.run("blocked").stdout.splitlines()
        self.assertEqual(len(blocked), 1, blocked)
        self.assertIn("Use parser", blocked[0])
        self.assertTrue(first.startswith(blocked[0].split("<- ")[1]), blocked[0])

        graph = w.run("graph").stdout.splitlines()
        left, right = graph[0].split(" -> ")
        self.assertEqual(len(graph), 1)
        self.assertTrue(second.startswith(left) and first.startswith(right), graph)

    def test_item_file_is_a_fixed_header_ending_at_a_rule(self) -> None:
        w = self.world
        item = w.create("--kind", "task", "--title", "Write it", "--label", "area:erp", "--body", "Body text")
        text = (w.ledger / "items" / f"{item}.md").read_text(encoding="utf-8")
        header, _, body = text.partition("\n---\n")
        keys = [line.split(":", 1)[0] for line in header.splitlines()]
        self.assertEqual(keys[:3], ["id", "kind", "title"])
        self.assertIn("labels: area:erp", header)
        self.assertEqual(body.strip(), "Body text")
        self.assertFalse((w.ledger / "index.json").exists(), "no shared index is committed")

    def test_show_resolves_a_short_alias_and_reports_derived_edges(self) -> None:
        w = self.world
        first = w.create("--kind", "issue", "--title", "A")
        second = w.create("--kind", "issue", "--title", "B", "--needs", first)
        shown = w.run("show", first[4:20]).stdout
        self.assertIn(first, shown)
        self.assertIn("state: draft", shown)
        self.assertIn(f"blocks: {second}", shown)

    def test_list_filters_and_json_output(self) -> None:
        w = self.world
        w.create("--kind", "epic", "--title", "Big")
        issue = w.create("--kind", "issue", "--title", "Small")
        rows = json.loads(w.run("list", "--kind", "issue", "--json").stdout)
        self.assertEqual([row["id"] for row in rows], [issue])
        self.assertEqual(rows[0]["state"], "draft")

    def test_create_with_a_human_quote_is_approved_without_a_second_step(self) -> None:
        w = self.world
        item = w.create("--kind", "issue", "--title", "Direct", "--quote", QUOTE)
        self.assertEqual([e["type"] for e in w.events(item)], ["approved"])
        self.assertEqual(w.events(item)[0]["quote"], QUOTE)

    def test_create_from_file_resolves_refs_between_new_items(self) -> None:
        w = self.world
        payload = json.dumps(
            [
                {"ref": "a", "kind": "issue", "title": "First"},
                {"ref": "b", "kind": "issue", "title": "Second", "needs": ["@a"]},
            ]
        )
        result = w.run("create", "--from-file", "-", stdin=payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        first, second = [line.split()[0] for line in result.stdout.splitlines()]
        self.assertIn(f"needs: {first}", (w.ledger / "items" / f"{second}.md").read_text())

    def test_set_changes_the_header_and_records_an_audit_event(self) -> None:
        w = self.world
        item = w.create("--kind", "issue", "--title", "Old")
        result = w.run("set", item, "--title", "New", "--priority", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("title: New", (w.ledger / "items" / f"{item}.md").read_text())
        fields = [e for e in w.events(item) if e["type"] == "field"]
        self.assertEqual({(e["key"], e["old"], e["new"]) for e in fields}, {("title", "Old", "New"), ("priority", "2", "0")})


if __name__ == "__main__":
    unittest.main()
