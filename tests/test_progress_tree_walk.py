"""`qew progress --tree`: a person's dashboard view, strictly opt-in.

Adversary: the tree mode leaking into the default text that agents parse (glyphs, truncation, color, tty sniffing), bars that
do not line up when titles differ in length, a title that wraps a narrow pane, `--open-only` dropping the parent of open
work or keeping finished rows, and color appearing when piped or under NO_COLOR.
"""

import json
import re
import unittest

from qew import cmd_progress
from qew.cmd_progress import Node
from tests.ledger_harness import World

LONG = "A very long title that would wrap a narrow terminal pane long before it ends"


class ProgressTreeWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)
        w = self.world
        self.root = w.create("--kind", "epic", "--title", "Root", "--draft")
        self.finished = w.create("--kind", "epic", "--title", "Finished epic", "--parent", self.root, "--draft")
        self.finished_leaf = self.leaf("Finished leaf", self.finished)
        self.open = w.create("--kind", "epic", "--title", LONG, "--parent", self.root, "--draft")
        self.open_done = self.leaf("Open done", self.open)
        self.open_ready = self.leaf("Open ready", self.open)
        self.empty = w.create("--kind", "epic", "--title", "Empty", "--parent", self.root, "--draft")
        self.empty_leaf = self.leaf("Dropped", self.empty)
        w.approve(self.root, self.finished, self.open, self.empty)
        for item in (self.finished_leaf, self.open_done):
            w.run("start", item)
            w.run("evidence", item, "--change", w.commit())
            self.assertEqual(w.run("complete", item).returncode, 0)
        w.run("close", self.empty_leaf, "--reason", "not_planned")

    def leaf(self, title: str, parent: str) -> str:
        item = self.world.create("--title", title, "--parent", parent, "--draft")
        self.world.approve(item)
        return item

    def tree(self, *arguments: str, columns: str = "100") -> list[str]:
        result = self.world.run("progress", self.root, "--tree", *arguments, env={"COLUMNS": columns, "NO_COLOR": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def test_the_default_text_and_json_stay_exactly_as_agents_parse_them(self) -> None:
        """Without the flag the output has no guides, no truncation and no color, whatever COLUMNS says."""
        plain = self.world.run("progress", self.root, env={"COLUMNS": "20"}).stdout.splitlines()
        self.assertEqual(plain[0], f"{self.root}  Root  [######----] 66%  (2/3)")
        self.assertEqual(plain[1], f"  {self.finished}  Finished epic  [##########] 100%  (1/1)")
        self.assertEqual(plain[2], f"    {self.finished_leaf}  Finished leaf  completed")
        self.assertIn(f"  {self.open}  {LONG}  [#####-----] 50%  (1/2)", plain)
        self.assertIn(f"  {self.empty}  Empty  [----------] n/a  (0/0)", plain)
        self.assertFalse(any(c in line for line in plain for c in "├└│…\x1b"))
        self.assertEqual(json.loads(self.world.run("progress", self.root, "--json").stdout)["percent"], 66)

    def test_numbers_form_a_fixed_left_column_so_bars_line_up_whatever_the_title(self) -> None:
        """Every row starts with bar, percent and done/total; the guides and ids come after, in one column across rows."""
        rows = self.tree("--epics-only")
        self.assertEqual(len(rows), 4)
        self.assertEqual({row.index("[") for row in rows}, {0})
        self.assertEqual({row.index("]") for row in rows}, {11})
        self.assertEqual(len({re.search(r"[├└]─|\S", row[25:]).start() for row in rows[1:]}), 1)
        self.assertTrue(rows[0].startswith("[######----]  66%"), rows[0])
        self.assertIn(self.root, rows[0])

    def test_guides_follow_the_depth_and_the_last_sibling(self) -> None:
        """├─ for a sibling with more below it, └─ for the last, │ carried down under the first."""
        rows = self.tree()
        text = "\n".join(rows)
        self.assertRegex(text, rf"├─ {self.finished}")
        self.assertRegex(text, rf"│\s+└─ {self.finished_leaf}")
        self.assertRegex(text, rf"└─ {self.empty}")

    def test_titles_are_cut_to_the_terminal_width_and_never_wrap(self) -> None:
        """A narrow pane gets an ellipsis and no line past its width; a wide one gets the whole title."""
        narrow = self.tree(columns="60")
        self.assertTrue(all(len(row) <= 60 for row in narrow), narrow)
        self.assertTrue(any(row.endswith("…") for row in narrow))
        self.assertTrue(any(self.open in row for row in narrow))
        self.assertTrue(any(LONG in row for row in self.tree(columns="200")))

    def test_open_only_hides_finished_and_empty_rows_but_keeps_the_parents_of_open_work(self) -> None:
        """A 100% epic, its leaves and a 0/0 epic disappear; the open epic and the root stay, with their open leaf."""
        rows = "\n".join(self.tree("--open-only"))
        self.assertIn(self.open, rows)
        self.assertIn(self.open_ready, rows)
        self.assertIn(self.root, rows)
        for hidden in (self.finished, self.finished_leaf, self.empty, self.empty_leaf, self.open_done):
            self.assertNotIn(hidden, rows)

    def test_no_color_when_piped_or_under_no_color_and_color_when_asked_for(self) -> None:
        """The subprocess is piped so no escape appears; the renderer paints green done, yellow under way, dim blocked."""
        self.assertFalse(any("\x1b" in row for row in self.tree()))
        done = Node("a", "Done", "Done", "task", "completed", dict.fromkeys(cmd_progress.COUNTED, 0) | {"completed": 1}, [])
        under_way = Node("b", "Going", "Going", "epic", "plan", dict.fromkeys(cmd_progress.COUNTED, 0) | {"completed": 1, "ready": 1}, [done])
        blocked = Node("c", "Stuck", "Stuck", "task", "blocked", dict.fromkeys(cmd_progress.COUNTED, 0) | {"blocked": 1}, [])
        painted = cmd_progress.tree_lines(done, None, False, False, 100, True)[0]
        self.assertIn("\x1b[32m", painted)
        self.assertIn("\x1b[33m", cmd_progress.tree_lines(under_way, None, False, False, 100, True)[0])
        self.assertIn("\x1b[2m", cmd_progress.tree_lines(blocked, None, False, False, 100, True)[0])
        self.assertNotIn("\x1b", cmd_progress.tree_lines(done, None, False, False, 100, False)[0])

    def test_open_only_without_tree_filters_the_plain_text_too(self) -> None:
        """The flag means the same in both layouts, and the plain layout keeps its own line shape."""
        rows = self.world.run("progress", self.root, "--open-only").stdout
        self.assertNotIn(self.finished, rows)
        self.assertIn(f"  {self.open}  {LONG}", rows)


if __name__ == "__main__":
    unittest.main()
