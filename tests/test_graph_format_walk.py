"""Walks of `qew graph --format text|dot|mermaid`.

Adversary: an item title that carries quotes, backslashes or markup breaking
out of the dot or Mermaid syntax (injecting nodes, edges or directives), an order that changes
between runs, and a dangling need crashing the renderer.
"""

import json
import re
import unittest

from tests.ledger_harness import World
from tests.spec_loader import load


def spec(item_id: str, title: str = "", **fields) -> dict:
    return {"id": item_id, "kind": fields.pop("kind", "issue"), "title": title or f"Title {item_id}", **fields}


def diamond(world: World, title_a: str = "") -> None:
    """D needs B and C, both need A; B and C sit under epic E."""
    load(
        world,
        spec("E", kind="epic"),
        spec("A", title_a),
        spec("B", needs=["A"], parent="E"),
        spec("C", needs=["A"], parent="E"),
        spec("D", needs=["B", "C"]),
    )


TEXT = ["B -> A", "C -> A", "D -> B", "D -> C", "B parent E", "C parent E"]

DOT = """digraph qew {
  "A" [label="A: Title A"];
  "B" [label="B: Title B"];
  "C" [label="C: Title C"];
  "D" [label="D: Title D"];
  "E" [label="E: Title E"];
  "B" -> "A";
  "C" -> "A";
  "D" -> "B";
  "D" -> "C";
  "B" -> "E" [style=dashed, label="parent"];
  "C" -> "E" [style=dashed, label="parent"];
}
"""

MERMAID = """graph LR
  n0["A: Title A"]
  n1["B: Title B"]
  n2["C: Title C"]
  n3["D: Title D"]
  n4["E: Title E"]
  n1 --> n0
  n2 --> n0
  n3 --> n1
  n3 --> n2
  n1 -.->|parent| n4
  n2 -.->|parent| n4
"""


class GraphFormatWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def graph(self, *arguments: str) -> str:
        result = self.world.run("graph", *arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_each_format_renders_the_diamond_deterministically(self) -> None:
        """Dependency edges and parent edges appear in text, dot and Mermaid in a stable order."""
        diamond(self.world)
        self.assertEqual(self.graph().splitlines(), TEXT)
        self.assertEqual(self.graph("--format", "text").splitlines(), TEXT)
        self.assertEqual(self.graph("--format", "dot"), DOT)
        self.assertEqual(self.graph("--format", "mermaid"), MERMAID)
        self.assertEqual(self.graph("--format", "dot"), DOT, "a second run is identical")
        edges = [json.loads(line) for line in self.graph("--json").splitlines()]
        self.assertIn({"item": "D", "needs": "B", "schema_version": 1}, edges)
        self.assertIn({"item": "B", "parent": "E", "schema_version": 1}, edges)

    def test_hostile_titles_cannot_break_out_of_dot_or_mermaid(self) -> None:
        """Quotes, backslashes and markup stay inside one quoted label."""
        diamond(self.world, 'say "hi" \\ <b>x</b> ]; evil --> A')
        dot = self.graph("--format", "dot")
        self.assertIn('"A" [label="A: say \\"hi\\" \\\\ <b>x</b> ]; evil --> A"];', dot)
        self.assertEqual(len(dot.splitlines()), len(DOT.splitlines()))
        mermaid = self.graph("--format", "mermaid")
        self.assertIn('n0["A: say #quot;hi#quot; \\ #lt;b#gt;x#lt;/b#gt; ]; evil --#gt; A"]', mermaid)
        self.assertEqual(len(mermaid.splitlines()), len(MERMAID.splitlines()))

    def test_a_title_hiding_characters_is_withheld_from_the_picture(self) -> None:
        """A right-to-left override in a title must not reach a rendered graph, where nobody reads it raw."""
        load(self.world, spec("A", "safe\u202etxt"))
        for form in ("dot", "mermaid"):
            drawn = self.graph("--format", form)
            self.assertIn("title withheld", drawn)
            self.assertNotIn("\u202e", drawn)

    def test_a_dangling_need_is_drawn_as_a_node_not_a_crash(self) -> None:
        """A hand-merged ledger with a missing need still renders, so the break is visible in the picture."""
        w = self.world
        load(w, spec("A"))
        path = w.home("items", "A") / "item.md"
        path.write_text(re.sub(r"(?m)^needs:.*$", "needs: GONE", path.read_text()))
        self.assertEqual(self.graph().splitlines(), ["A -> GONE"])
        self.assertIn('"GONE" [label="GONE"', self.graph("--format", "dot"))
        self.assertIn('["GONE"]', self.graph("--format", "mermaid"))

    def test_json_and_a_format_together_are_refused(self) -> None:
        """Two output shapes were asked for; guessing which wins would hide a scripting mistake."""
        result = self.world.run("graph", "--json", "--format", "dot")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--json and --format", result.stderr)


if __name__ == "__main__":
    unittest.main()
