"""Walks of reading a board's cards: foreign repositories, overlap, duplicates, truncation.

Adversary: an organisation board holding several repositories whose issue
numbers collide, a listing that overlaps while cards are added, a real
duplicate, and a listing cut short that would make a card look missing.
"""

import unittest
from unittest import mock

from qew import forge_github
from qew.board_cards import DuplicateCards, local_cards
from qew.forge import COLUMNS, ForgeRejected
from tests.test_forge_github_walk import SPEC, GitHubWalk

LOCAL = "acme/widgets"


class BoardCardsWalk(GitHubWalk):
    def setUp(self) -> None:
        super().setUp()
        self.board = self.backend.ensure_board(COLUMNS)
        self.project = self.fake.project(self.board.id)

    def issue_on_board(self, column: str = "Ready") -> int:
        issue = self.backend.create_issue(SPEC)
        self.backend.place_on_board(self.board, issue.number, column)
        return issue.number

    def test_foreign_repository_card_with_a_colliding_number_is_ignored(self) -> None:
        """acme/other#1 and acme/widgets#1 share a number; only the local card counts."""
        number = self.issue_on_board("Done")
        self.project["items"]["I_foreign"] = {"id": "PVTI_f", "repo": "acme/other", "number": number}
        cards = local_cards(self.backend.read_board_cards(self.board), LOCAL)
        self.assertEqual({n: c.column for n, c in cards.items()}, {number: "Done"})

    def test_foreign_card_never_stands_in_for_a_missing_local_card(self) -> None:
        """With only the foreign card present, the local issue is absent from the result."""
        self.project["items"]["I_foreign"] = {"id": "PVTI_f", "repo": "acme/other", "number": 1}
        self.assertEqual(local_cards(self.backend.read_board_cards(self.board), LOCAL), {})

    def test_same_card_id_on_two_pages_is_one_card(self) -> None:
        """A harmless overlap while cards are being added must not abort the read."""
        numbers = [self.issue_on_board() for _ in range(3)]
        self.fake.cards_page_size = 2
        self.fake.cards_overlap = True
        cards = local_cards(self.backend.read_board_cards(self.board), LOCAL)
        self.assertEqual(sorted(cards), numbers)

    def test_two_card_ids_for_one_issue_is_an_error_naming_both(self) -> None:
        """A real duplicate stays loud."""
        number = self.issue_on_board()
        self.project["items"]["I_dup"] = {"id": "PVTI_dup", "number": number}
        with self.assertRaises(DuplicateCards) as raised:
            local_cards(self.backend.read_board_cards(self.board), LOCAL)
        self.assertIn("PVTI_dup", str(raised.exception))
        self.assertEqual(raised.exception.exit_code, 1)

    def test_a_listing_longer_than_the_page_cap_raises_instead_of_truncating(self) -> None:
        """A cut-short listing would report cards as missing."""
        self.issue_on_board()
        self.fake.cards_endless = True
        with mock.patch.object(forge_github, "PROJECT_ITEM_LIMIT", 250, create=True), mock.patch.object(forge_github, "ARCHIVED_ITEM_LIMIT", 50, create=True):
            with self.assertRaises(ForgeRejected):
                self.backend.read_board_cards(self.board)


if __name__ == "__main__":
    unittest.main()
