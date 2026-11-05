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
        self.assertEqual((body["units"], body["total"]), (3, 3))
        flat = json.dumps(body)
        self.assertIn('"reference": true', flat)
        tree = self.w.run("progress", "ADU", "--edges", "children", "needs", "--tree", "--open-only")
        self.assertEqual(tree.returncode, 0, tree.stderr)
        self.assertIn("↩", tree.stdout)
        self.assertNotIn("Work Z", tree.stdout)
        reverse = self.w.run("progress", "A", "--edges", "blocks", "--json")
        self.assertEqual(reverse.returncode, 0, reverse.stderr)
        self.assertEqual(json.loads(reverse.stdout)["units"], 4)  # A, B, C and D, each once

    def test_progress_depth_changes_display_not_unique_totals_and_json_open_filter_applies(self) -> None:
        """Prevents depth making percentages lie or --open-only being ignored for JSON."""
        self.w.run("start", "A")
        self.w.run("evidence", "A", "--change", self.w.commit())
        self.w.run("complete", "A")
        run = self.w.run("progress", "E", "--edges", "children", "needs", "--depth", "1", "--json", "--open-only")
        self.assertEqual(run.returncode, 0, run.stderr)
        body = json.loads(run.stdout)
        self.assertEqual((body["units"], body["done"], body["percent"]), (3, 1, 33))
        self.assertEqual(len(body["children"]), 2)
        self.assertTrue(all(not child["children"] for child in body["children"]))

    def test_open_progress_json_hides_finished_rows_without_changing_totals(self) -> None:
        """Prevents the JSON view ignoring --open-only or calling unfinished dependents complete."""
        self.w.run("start", "A")
        self.w.run("evidence", "A", "--change", self.w.commit())
        self.w.run("complete", "A")
        run = self.w.run("progress", "ADU", "--edges", "children", "needs", "--open-only", "--json")
        self.assertEqual(run.returncode, 0, run.stderr)
        body = json.loads(run.stdout)
        self.assertEqual((body["done"], body["total"], body["percent"]), (1, 3, 33))
        self.assertEqual([child["percent"] for child in body["children"]], [50, 50])
        self.assertTrue(all(not child["children"] for child in body["children"]))

    def test_a_mixed_direction_cycle_is_a_reference_with_unique_progress_units(self) -> None:
        """Prevents a display traversal discarding valid reverse links or double-counting their work units."""
        run = self.w.run("graph", "E", "--edges", "children", "parent", "--tree")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("↩", run.stdout)
        run = self.w.run("progress", "E", "--edges", "children", "parent", "--json")
        self.assertEqual(run.returncode, 0, run.stderr)
        body = json.loads(run.stdout)
        self.assertEqual(body["units"], 2)  # B and C, not their container E
        self.assertIn('"reference": true', json.dumps(body))

    def test_multiple_roots_depth_zero_and_invalid_options_do_not_guess(self) -> None:
        """Prevents silently ignored filters or root-only scope drawing neighbors."""
        body = self.document("E", "Z", "--depth", "0")
        self.assertEqual(body["roots"], ["E", "Z"])
        self.assertEqual({n["id"] for n in body["nodes"]}, {"E", "Z"})
        self.assertEqual(body["edges"], [])
        bounded = self.w.run("graph", "E", "--edges", "children", "parent", "--tree", "--depth", "1")
        self.assertEqual(bounded.returncode, 0, bounded.stderr)
        self.assertEqual(len(bounded.stdout.splitlines()), 3)
        self.assertNotIn("↩", bounded.stdout)
        for args in (("graph", "E", "--tree", "--json"), ("graph", "E", "--depth", "-1"),
                     ("progress", "E", "--unmet-only"), ("graph", "E", "--edges", "children", "--unmet-only")):
            run = self.w.run(*args)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertEqual(run.stdout, "")

    def test_scoped_drawings_escape_titles_and_json_withholds_hidden_characters(self) -> None:
        """Prevents scoped rendering bypassing the existing untrusted-label boundary."""
        self.w.run("set", "A", "--title", 'say "hi" <b> ]; evil --> A')
        dot = self.w.run("graph", "E", "--format", "dot")
        self.assertEqual(dot.returncode, 0, dot.stderr)
        self.assertIn('say \\"hi\\"', dot.stdout)
        mermaid = self.w.run("graph", "E", "--format", "mermaid")
        self.assertIn("#quot;hi#quot;", mermaid.stdout)
        self.w.run("set", "A", "--title", "hidden\u202etitle")
        node = next(n for n in self.document("E")["nodes"] if n["id"] == "A")
        self.assertIsNone(node["title"])

    def test_layered_diamonds_render_references_without_enumerating_paths(self) -> None:
        """Prevents graph progress expanding exponentially when many chains share the same work."""
        specs = [spec("X0")]
        previous = ["X0"]
        for layer in range(1, 13):
            current = [f"L{layer}", f"R{layer}"]
            specs += [spec(item, needs=previous) for item in current]
            previous = current
        specs.append(spec("G0", needs=previous))
        load(self.w, *specs)
        run = self.w.run("progress", "G0", "--edges", "needs", "--tree")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertLess(len(run.stdout.splitlines()), 60)
        self.assertIn("↩", run.stdout)
        body = json.loads(self.w.run("progress", "G0", "--edges", "needs", "--json").stdout)
        self.assertEqual(body["units"], 26)

    def test_a_deep_chain_with_bounded_display_keeps_complete_totals(self) -> None:
        """Prevents recursive setup/width scans overflowing before a requested shallow view is drawn."""
        load(self.w, *(spec(f"J{n}", needs=[f"J{n-1}"] if n else []) for n in range(1100)))
        run = self.w.run("progress", "J1099", "--edges", "needs", "--depth", "1", "--tree")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(len(run.stdout.splitlines()), 2)
        self.assertIn("0/1100", run.stdout)
        body = self.document("J1099", "--edges", "needs", "--depth", "1")
        self.assertEqual(len(body["nodes"]), 2)
        drawn = self.w.run("progress", "J1099", "--edges", "needs", "--tree")
        self.assertEqual(drawn.returncode, 0, drawn.stderr)
        self.assertEqual(len(drawn.stdout.splitlines()), 1100)
        nested = self.w.run("progress", "E", "J1099", "--edges", "needs", "--json")
        self.assertEqual(nested.returncode, 2)
        self.assertIn("--depth 200", nested.stderr)
        self.assertEqual(nested.stdout, "")

    def test_epic_without_selected_work_is_context_not_a_fabricated_work_unit(self) -> None:
        """Prevents an epic-only dependency selection claiming a count or percentage without any selected work."""
        run = self.w.run("progress", "ADU", "--edges", "needs", "--json")
        self.assertEqual(run.returncode, 0, run.stderr)
        body = json.loads(run.stdout)
        self.assertEqual((body["units"], body["total"], body["percent"]), (0, 0, None))
        self.assertEqual(body["children"], [])

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
