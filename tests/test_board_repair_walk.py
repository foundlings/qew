"""Walks of repairing a board's Status field in place.

Adversary: an id-dropping rewrite that empties a column of its cards, a column
silently deleted, and a malformed option quietly dropped.
"""

import unittest

from tests.test_board_ensure_walk import BoardFixture, SPEC

from qew.forge import COLUMNS, ForgeRejected


class BoardRepairWalk(BoardFixture, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.board = self.backend().ensure_board(COLUMNS)
        self.status = self.field(self.board, "Status")

    def ids(self):
        return {o["name"]: o["id"] for o in self.status["options"]}

    def test_a_misordered_board_is_reordered_keeping_option_ids(self) -> None:
        """A board whose Ready sits last is fixed by one update that carries the existing ids, so no column empties."""
        before = self.ids()
        options = self.status["options"]
        options.append(options.pop(options.index(next(o for o in options if o["name"] == "Ready"))))
        issue = self.backend().create_issue(SPEC)
        self.backend().place_on_board(self.board, issue.number, "Ready")
        updates = len(self.mutations("updateProjectV2Field"))
        self.backend(project=self.board.id).ensure_board(COLUMNS)
        self.assertEqual([o["name"] for o in self.status["options"]], list(COLUMNS))
        self.assertEqual(len(self.mutations("updateProjectV2Field")) - updates, 1)
        self.assertEqual(self.ids()["Ready"], before["Ready"])
        self.assertEqual(self.fake.item_status(self.board.id, issue.node_id), "Ready")

    def test_a_board_missing_a_column_gains_it_in_place(self) -> None:
        """An older board lacking Draft gets it back; every other option keeps its id."""
        before = self.ids()
        self.status["options"] = [o for o in self.status["options"] if o["name"] != "Draft"]
        self.backend(project=self.board.id).ensure_board(COLUMNS)
        after = self.ids()
        self.assertIn("Draft", after)
        self.assertTrue(all(after[name] == before[name] for name in before if name != "Draft"))

    def test_a_malformed_foreign_option_fails_loudly(self) -> None:
        """An option without an id is garbage; ensure must raise, never delete it silently."""
        self.status["options"].append({"name": "Broken", "color": "GRAY", "description": ""})
        with self.assertRaises(ForgeRejected):
            self.backend(project=self.board.id).ensure_board(COLUMNS)


if __name__ == "__main__":
    unittest.main()


class BoardViewWalk(BoardFixture, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.board = self.backend(select="label:area:erp").ensure_board(COLUMNS, "label:area:erp")

    def test_views_not_owned_are_untouched(self) -> None:
        """A custom view a human made survives every ensure unchanged."""
        project = self.fake.project(self.board.id)
        project["views"].append({"id": "V_mine", "name": "Mine", "layout": "TABLE_LAYOUT", "filter": "assignee:@me"})
        self.backend(project=self.board.id).ensure_board(COLUMNS, "label:area:erp")
        mine = next(v for v in project["views"] if v["name"] == "Mine")
        self.assertEqual((mine["layout"], mine["filter"]), ("TABLE_LAYOUT", "assignee:@me"))

    def test_scoped_views_are_not_rewritten_by_a_rerun(self) -> None:
        """Views already carrying the scope prefix get no update call, so hand scoping is not reverted."""
        filters = {v["name"]: v["filter"] for v in self.fake.project(self.board.id)["views"]}
        self.assertEqual(filters["Ready"], "label:area:erp status:Ready")
        before = len(self.mutations("updateProjectV2View"))
        self.backend(project=self.board.id).ensure_board(COLUMNS, "label:area:erp")
        self.assertEqual(len(self.mutations("updateProjectV2View")), before)
