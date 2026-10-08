"""Walks of named board files (`views/NAME.json` in the ledger): create, list, set, render, check, merge.

Adversary: a duplicate name, a hand-edited file with columns the model lacks, and two
agents adding different boards while apart.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from tests.ledger_harness import QUOTE, World


class BoardConfigWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()
        self.addCleanup(self.world.close)

    def run_ok(self, *arguments: str) -> str:
        result = self.world.run(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_a_named_board_keeps_its_columns_sort_group_and_filter_and_flags_override(self) -> None:
        """Prevents a saved board view being ignored or impossible to override for one run."""
        w = self.world
        w.create("--kind", "issue", "--title", "b-ready", "--priority", "1", "--quote", QUOTE)
        w.create("--kind", "issue", "--title", "a-ready", "--priority", "3", "--quote", QUOTE)
        w.create("--kind", "task", "--title", "a-task", "--quote", QUOTE)
        w.create("--kind", "issue", "--title", "drafty")
        self.run_ok("board", "create", "review", "--columns", "Ready,Draft", "--sort", "title", "--filter", "kind:issue")
        self.assertIn("review", self.run_ok("board", "list"))
        data = json.loads(self.run_ok("board", "--name", "review", "--json"))
        self.assertEqual(data["board"], "review")
        self.assertEqual([g["name"] for g in data["groups"]], ["Ready", "Draft"])
        self.assertEqual([c["title"] for c in data["groups"][0]["cards"]], ["a-ready", "b-ready"])
        over = json.loads(self.run_ok("board", "--name", "review", "--json", "--sort", "priority"))
        self.assertEqual([c["title"] for c in over["groups"][0]["cards"]], ["b-ready", "a-ready"])

    def test_set_changes_a_boards_defaults_and_the_board_named_default_is_the_bare_view(self) -> None:
        """Prevents `qew board` ignoring the team's default view."""
        w = self.world
        w.create("--kind", "issue", "--title", "x", "--quote", QUOTE)
        self.run_ok("board", "create", "default", "--columns", "Ready")
        self.run_ok("board", "set", "default", "--columns", "Ready,Done", "--group-by", "owner")
        data = json.loads(self.run_ok("board", "--json"))
        self.assertEqual(data["board"], "default")
        self.assertEqual(data["groups"][0]["name"], "(none)")
        listing = self.run_ok("board", "list")
        self.assertIn("columns=Ready,Done", listing)

    def test_a_duplicate_name_or_unknown_column_is_refused_and_writes_nothing(self) -> None:
        """Prevents a second board silently replacing the first, or a column the model lacks."""
        self.run_ok("board", "create", "one")
        again = self.world.run("board", "create", "one")
        self.assertEqual(again.returncode, 2)
        self.assertIn("already exists", again.stderr)
        for arguments in (["board", "create", "two", "--columns", "Ready,Nowhere"], ["board", "create", "Bad Name"],
                          ["board", "create", "dup", "--columns", "Ready,Ready"], ["board", "set", "missing", "--sort", "title"]):
            result = self.world.run(*arguments)
            self.assertEqual(result.returncode, 2, arguments)
        self.assertEqual(sorted(p.name for p in (self.world.ledger / "views").glob("*.json")), ["one.json"])

    def test_check_fails_for_a_hand_edited_board_file_with_unknown_or_duplicate_columns(self) -> None:
        """Prevents a mis-rendering board file passing the ledger check."""
        self.run_ok("board", "create", "ok")
        path = self.world.ledger / "views" / "ok.json"
        value = json.loads(path.read_text())
        self.assertEqual(self.world.run("check").returncode, 0)
        for columns, name in ((["Ready", "Nope"], "ok"), (["Ready", "Ready"], "ok"), (["Ready"], "other")):
            path.write_text(json.dumps({**value, "columns": columns, "name": name}))
            result = self.world.run("check")
            self.assertEqual(result.returncode, 1, columns)
            self.assertIn("views/ok.json", result.stdout)

    def test_two_agents_adding_different_boards_merge_without_conflict(self) -> None:
        """Prevents lost boards when agents work apart: one file per board merges cleanly."""
        every_time = "\n[replication]\nfetch_interval_s = 0\n"
        a = World(ledger_vcs="git", remote=True, replication=every_time)
        self.addCleanup(a.close)
        b = a.clone("b", replication=every_time)
        offline = {"QEW_OFFLINE": "1"}
        self.assertEqual(a.run("board", "create", "alpha", env=offline).returncode, 0)
        self.assertEqual(b.run("board", "create", "beta", env=offline).returncode, 0)
        for agent in (a, b, a):
            self.assertEqual(agent.run("ready").returncode, 0)
        for agent in (a, b):
            listing = agent.run("board", "list").stdout
            self.assertIn("alpha", listing)
            self.assertIn("beta", listing)
        self.assertEqual(a.run("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
