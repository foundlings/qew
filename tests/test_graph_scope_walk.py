"""Scoped views must preserve the graph instead of dropping cross-cutting or shared links."""

import json
import re
import unittest

from tests.ledger_harness import World
from tests.spec_loader import load, spec


class GraphScopeWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.w = World()
        self.addCleanup(self.w.close)
        load(self.w, spec("E", kind="epic"), spec("A"),
             spec("B", parent="E", needs=["A"]), spec("C", parent="E", needs=["A"]),
             spec("D", needs=["B"]), spec("Z"), spec("T"), spec("P"),
             spec("F", discovered_from="B"))
        for item, field, value in (("E", "aliases", "ADU"), ("T", "tracks", "B"), ("P", "pr", "B")):
            path = self.w.home("items", item) / "item.md"
            path.write_text(path.read_text().replace("\n---", f"\n{field}: {value}\n---", 1))

    def document(self, *args: str) -> dict:
        run = self.w.run("graph", *args, "--json")
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_root_alias_excludes_unrelated_nodes_and_preserves_both_diamond_edges(self) -> None:
        """Prevents a rooted view pulling the whole ledger or losing one shared prerequisite."""
        body = self.document("ADU")
        self.assertEqual(body["roots"], ["E"])
        self.assertEqual({node["id"] for node in body["nodes"]}, {"E", "A", "B", "C"})
        self.assertEqual({(e["source"], e["relation"], e["target"]) for e in body["edges"]},
                         {("B", "parent", "E"), ("C", "parent", "E"), ("B", "needs", "A"), ("C", "needs", "A")})

    def test_reverse_and_other_stored_relationships_are_explicit(self) -> None:
        """Prevents reverse traversal changing the stored edge's direction or hiding other item links."""
        for edge, source, relation in (("blocks", "D", "needs"), ("tracked-by", "T", "tracks"),
                                       ("pr-for", "P", "pr"), ("discoveries", "F", "discovered_from")):
            body = self.document("B", "--edges", edge, "--depth", "1")
            self.assertIn({"source": source, "relation": relation, "target": "B"}, body["edges"])
        self.assertEqual({n["id"] for n in self.document("B", "--edges", "parent")["nodes"]}, {"B", "E"})

    def test_depth_and_isolated_roots_are_visible_in_every_drawing(self) -> None:
        """Prevents depth expanding outside scope or an isolated root disappearing."""
        body = self.document("E", "--depth", "1")
        self.assertEqual({n["id"] for n in body["nodes"]}, {"E", "B", "C"})
        for form in ("text", "dot", "mermaid"):
            run = self.w.run("graph", "Z", "--format", form)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("Z", run.stdout)
        run = self.w.run("graph", "ADU", "--tree")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("↩", run.stdout)
        self.assertNotIn("Work Z", run.stdout)

    def test_open_and_unmet_filters_do_not_hide_missing_or_closed_blockers(self) -> None:
        """Completed needs are satisfied; closed or missing needs remain actionable facts."""
        self.w.run("start", "A")
        self.w.run("evidence", "A", "--change", self.w.commit())
        self.assertEqual(self.w.run("complete", "A").returncode, 0)
        self.w.run("close", "Z", "--reason", "not_planned")
        path = self.w.home("items", "B") / "item.md"
        path.write_text(re.sub(r"(?m)^needs:.*$", "needs: A Z GONE", path.read_text()))
        body = self.document("B", "--edges", "needs", "--unmet-only")
        self.assertEqual({n["id"] for n in body["nodes"]}, {"B", "Z", "GONE"})
        self.assertEqual(next(n for n in body["nodes"] if n["id"] == "GONE")["state"], "missing")
        opened = self.document("--open-only", "--edges", "needs", "--unmet-only")
        self.assertNotIn("A", {n["id"] for n in opened["nodes"]})
        self.assertIn("GONE", {n["id"] for n in opened["nodes"]})

    def test_progress_can_follow_needs_and_counts_shared_terminals_once(self) -> None:
        """Prevents progress duplicating a shared leaf or silently following only children."""
        run = self.w.run("progress", "ADU", "--edges", "children", "needs", "--json")
        self.assertEqual(run.returncode, 0, run.stderr)
        body = json.loads(run.stdout)
        self.assertEqual((body["leaves"], body["total"]), (1, 1))
        flat = json.dumps(body)
        self.assertIn('"reference": true', flat)
        tree = self.w.run("progress", "ADU", "--edges", "children", "needs", "--tree", "--open-only")
        self.assertEqual(tree.returncode, 0, tree.stderr)
        self.assertIn("↩", tree.stdout)
        self.assertNotIn("Work Z", tree.stdout)
        reverse = self.w.run("progress", "A", "--edges", "blocks", "--json")
        self.assertEqual(reverse.returncode, 0, reverse.stderr)
        self.assertEqual(json.loads(reverse.stdout)["leaves"], 2)  # D and C

    def test_progress_depth_changes_display_not_unique_totals_and_json_open_filter_applies(self) -> None:
        """Prevents depth making percentages lie or --open-only being ignored for JSON."""
        self.w.run("start", "A")
        self.w.run("evidence", "A", "--change", self.w.commit())
        self.w.run("complete", "A")
        run = self.w.run("progress", "E", "--edges", "children", "needs", "--depth", "1", "--json", "--open-only")
        self.assertEqual(run.returncode, 0, run.stderr)
        body = json.loads(run.stdout)
        self.assertEqual((body["leaves"], body["done"], body["children"]), (1, 1, []))

    def test_unknown_root_and_cycles_refuse_without_partial_output(self) -> None:
        """Prevents a truncated traversal or partial multi-root answer looking complete."""
        run = self.w.run("progress", "E", "UNKNOWN", "--edges", "needs", "--json")
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, "")
        path = self.w.home("items", "A") / "item.md"
        path.write_text(re.sub(r"(?m)^needs:.*$", "needs: B", path.read_text()))
        for command in ("graph", "progress"):
            run = self.w.run(command, "B", "--edges", "needs", "--json")
            self.assertEqual(run.returncode, 1, run.stderr)
            self.assertIn("cycle", run.stderr)
            self.assertEqual(run.stdout, "")
