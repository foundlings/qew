"""Walks of choosing the target board on GitHub: by recorded number, never by guess.

Adversary: a renamed board silently replaced by a new one, a missing number
quietly creating a board, and a title option reconciling the wrong board.
"""

import unittest

from tests.test_forge_github_walk import GitHubWalk

from qew.forge import COLUMNS, ForgeRejected


class BoardTargetWalk(GitHubWalk):
    def test_linked_board_found_by_number_after_a_rename(self) -> None:
        """A board renamed on the forge keeps its number; ensure finds it and reports the new title."""
        first = self.backend.ensure_board(COLUMNS)
        self.fake.project(first.id)["title"] = "Renamed"
        again = self.build(project=first.id).ensure_board(COLUMNS)
        self.assertEqual((again.id, again.title), (first.id, "Renamed"))
        self.assertEqual(len(self.fake.projects), 1)

    def test_missing_board_number_is_refused_without_creating(self) -> None:
        """Number 99 absent: refuse; creating a new board would silently drift the checkout."""
        with self.assertRaises(ForgeRejected):
            self.build(project="99").ensure_board(COLUMNS)
        self.assertEqual(len(self.fake.projects), 0)

    def test_title_option_conflicting_with_linked_number_is_refused(self) -> None:
        """The refusal names the number and the actual title; a different board is never reconciled."""
        first = self.backend.ensure_board(COLUMNS)
        with self.assertRaises(ForgeRejected) as raised:
            self.build(project=first.id, project_title="Elsewhere").ensure_board(COLUMNS)
        self.assertIn(first.id, raised.exception.detail)
        self.assertIn("Elsewhere", raised.exception.detail)
        self.assertIn(self.fake.project(first.id)["title"], raised.exception.detail)
        self.assertEqual(len(self.fake.projects), 1)


if __name__ == "__main__":
    unittest.main()
