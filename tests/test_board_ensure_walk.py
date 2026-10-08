"""Walks of `project ensure` building the full board on GitHub Projects (fake wire) and refusing elsewhere.

Adversary: a duplicate project or field on re-run, a view edited by hand, a project
renamed by a human, and a forge that has no boards at all.
"""

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.fake_github import FakeGitHub, TOKEN
from tests.ledger_harness import World
from tests.memory_forge import MemoryForge

from qew import cmd_project, ledger_config
from qew.forge import COLUMNS, PRIORITY_OPTIONS, STATUS_OPTIONS
from qew.forge_github import GitHub
from qew.forge_http import urllib_transport
from qew.ledger_config import Mirror

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
VIEWS = [
    ("Kanban", "BOARD_LAYOUT", ""),
    ("Table", "TABLE_LAYOUT", ""),
    ("Ready", "TABLE_LAYOUT", "status:Ready"),
    ("Blocked", "TABLE_LAYOUT", "is:blocked"),
    ("Active", "TABLE_LAYOUT", 'status:"In progress"'),
    ("Review", "TABLE_LAYOUT", 'status:"In review"'),
    ("Drafts", "TABLE_LAYOUT", "status:Draft"),
    ("Roadmap", "ROADMAP_LAYOUT", ""),
]


class BoardEnsureWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)

    def backend(self, project=None, **options) -> GitHub:
        mirror = Mirror("github", self.fake.repo_url, project, "", {"api": self.fake.api, **options})
        return GitHub(mirror, urllib_transport(), ENV)

    def field(self, board, name):
        return next(f for f in self.fake.project(board.id)["fields"] if f["name"] == name)

    def views(self, board):
        return self.views_of(self.fake, board)

    def views_of(self, fake, board):
        return [(v["name"], v["layout"], v["filter"] or "") for v in fake.project(board.id)["views"]]

    def mutations(self, word=""):
        return [b for _m, _p, b in self.fake.requests if b and "mutation" in b.get("query", "") and word in b["query"]]

    def test_golden_full_board_is_built_once_and_a_rerun_changes_nothing(self) -> None:
        """Prevents duplicate fields or views, and a board missing its colors, priorities or views."""
        board = self.backend().ensure_board(COLUMNS)
        status = self.field(board, "Status")["options"]
        self.assertEqual([(o["name"], o["color"], o["description"]) for o in status], list(STATUS_OPTIONS))
        priority = self.field(board, "Priority")["options"]
        self.assertEqual([(o["name"], o["color"], o["description"]) for o in priority], list(PRIORITY_OPTIONS))
        self.assertEqual(self.views(board), VIEWS)
        self.assertEqual(board.notes, ())
        before = len(self.mutations())
        again = self.backend(project=board.id).ensure_board(COLUMNS)
        self.assertEqual(again, board)
        self.assertEqual(len(self.mutations()), before, "a second ensure writes nothing")
        self.assertEqual(len(self.fake.projects), 1)

    def test_a_view_edited_on_the_forge_is_corrected_and_reported(self) -> None:
        """Prevents a hand-edited filter silently hiding ready work from the Ready view."""
        board = self.backend().ensure_board(COLUMNS)
        ready = next(v for v in self.fake.project(board.id)["views"] if v["name"] == "Ready")
        ready["filter"] = "status:Done"
        extra = {"id": "PVTV_human", "name": "My view", "layout": "TABLE_LAYOUT", "filter": "assignee:me"}
        self.fake.project(board.id)["views"].append(extra)
        fixed = self.backend(project=board.id).ensure_board(COLUMNS)
        self.assertEqual(ready["filter"], "status:Ready")
        self.assertEqual(len(fixed.notes), 1)
        self.assertIn("Ready", fixed.notes[0])
        self.assertEqual(extra["filter"], "assignee:me", "a human view is left alone")

    def test_overlay_mirror_prefixes_every_view_filter_and_repository_mode_stays_bare(self) -> None:
        """Prevents human-owned issues appearing on qew's board in an overlay mirror."""
        overlay = self.backend().ensure_board(COLUMNS, managed_filter="label:qew:managed")
        for name, _layout, text in self.views(overlay):
            self.assertTrue(text.startswith("label:qew:managed"), name)
        self.assertEqual(dict((n, f) for n, _l, f in self.views(overlay))["Ready"], "label:qew:managed status:Ready")
        other = FakeGitHub().start()
        self.addCleanup(other.stop)
        bare = GitHub(Mirror("github", other.repo_url, None, "", {"api": other.api}), urllib_transport(), ENV)
        board = bare.ensure_board(COLUMNS)
        self.assertEqual([f for _n, _l, f in self.views_of(other, board)], [f for _n, _l, f in VIEWS])

    def test_a_forge_without_boards_reports_unsupported_and_succeeds(self) -> None:
        """Prevents `project ensure` failing a whole run because one forge has no boards."""
        world = World('[[mirror]]\nforge = "forgejo"\nrepo = "https://git.example/o/r"\n')
        self.addCleanup(world.close)
        config = ledger_config.load(world.code)
        out = cmd_project.ensure(config, None, build=lambda mirror: MemoryForge(boards=False))
        self.assertIn("boards unsupported", out)

    def test_a_project_renamed_by_a_human_is_found_by_its_recorded_id(self) -> None:
        """Prevents a second project appearing because someone retitled the first."""
        world = World(f'[[mirror]]\nforge = "github"\nrepo = "{self.fake.repo_url}"\napi = "{self.fake.api}"\n')
        self.addCleanup(world.close)
        config = ledger_config.load(world.code)

        def build(mirror):
            return GitHub(mirror, urllib_transport(), ENV)

        cmd_project.ensure(config, None, build=build)
        project = next(iter(self.fake.projects.values()))
        project["title"] = "Our roadmap"
        cmd_project.ensure(config, None, build=build)
        self.assertEqual(len(self.fake.projects), 1)
        record = json.loads(next((world.ledger / "boards").glob("*.json")).read_text())
        self.assertEqual(record["id"], "1")


if __name__ == "__main__":
    unittest.main()
