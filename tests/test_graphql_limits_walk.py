"""Walks of GitHub's GraphQL size limits: a call that asks for too many nodes, a project past its item cap.

Adversary: a board big enough that a page of 100 cards makes GitHub refuse the call (500,000-node cap), and a
listing that never ends. Neither may end as a silent truncation or an opaque rejection.
"""

import re
import unittest
from unittest import mock

from qew import forge_github
from qew.forge import COLUMNS, ForgeRejected
from tests.test_forge_github_walk import SPEC, GitHubWalk

NODE_LIMIT = 500_000
QUERY_MARGIN = 100_000  # no query of ours may need more than this at the default page size


def estimated_nodes(query: str, page: int) -> int:
    """GitHub's rule: every `first: N` multiplies the nodes requested below it; the call's total is the sum over connections."""
    total, pending = 1, 1
    multipliers = [1]
    for count, brace in re.findall(r"first:\s*(\$?\w+)|([{}])", query):
        if count:
            pending = page if count.startswith("$") else int(count)
            total += multipliers[-1] * pending
        elif brace == "{":
            multipliers.append(multipliers[-1] * pending)
            pending = 1
        else:
            multipliers.pop()
    return total


class GraphqlLimitsWalk(GitHubWalk):
    def setUp(self) -> None:
        super().setUp()
        self.board = self.backend.ensure_board(COLUMNS)

    def fill(self, count: int) -> list[int]:
        numbers = []
        for _ in range(count):
            issue = self.backend.create_issue(SPEC)
            self.backend.place_on_board(self.board, issue.number, "Ready")
            numbers.append(issue.number)
        return numbers

    def test_a_node_limit_answer_halves_the_page_until_the_board_is_read(self) -> None:
        """Prevents a board read dying on GitHub's node cap: it retries smaller pages and returns every card."""
        numbers = self.fill(12)
        self.fake.node_limit_page = 5
        cards = self.backend.read_board_cards(self.board)
        self.assertEqual(sorted(card.number for card in cards), sorted(numbers))

    def test_board_placements_halve_the_page_too(self) -> None:
        """Prevents the sync-wide board listing from stopping on the node cap while the card listing copes."""
        numbers = self.fill(12)
        self.fake.node_limit_page = 5
        self.assertEqual(sorted(self.backend.read_board_placements(self.board)), sorted(numbers))

    def test_a_page_that_is_too_large_even_at_the_floor_fails_naming_the_limit(self) -> None:
        """Prevents an opaque rejection: the failure names the node cap, the listing and the smallest page tried."""
        self.fill(1)
        self.fake.node_limit_page = 0
        with self.assertRaises(ForgeRejected) as raised:
            self.backend.read_board_cards(self.board)
        text = str(raised.exception)
        self.assertIn("500,000", text)
        self.assertIn("cards", text)
        self.assertIn("5 per page", text)

    def test_a_board_past_the_project_item_cap_is_refused_naming_the_cap(self) -> None:
        """Prevents a truncated listing from reading as 'the other cards are gone': the refusal names GitHub's cap."""
        self.fill(1)
        self.fake.cards_endless = True
        with mock.patch.object(forge_github, "PROJECT_ITEM_LIMIT", 250, create=True), mock.patch.object(forge_github, "ARCHIVED_ITEM_LIMIT", 50, create=True):
            with self.assertRaises(ForgeRejected) as raised:
                self.backend.read_board_cards(self.board)
        self.assertIn("250", str(raised.exception))
        self.assertIn("cap", str(raised.exception))


class QueryBudgetWalk(unittest.TestCase):
    def test_every_query_stays_far_below_the_node_cap(self) -> None:
        """Prevents a future edit (a bigger first:, a new nested connection) from reaching GitHub's 500,000-node cap."""
        queries = {name: text for name, text in vars(forge_github).items() if name.startswith("_") and isinstance(text, str) and "query " in text}
        self.assertGreaterEqual(len(queries), 5)
        for name, text in queries.items():
            self.assertLess(estimated_nodes(text, getattr(forge_github, "PAGE", 100)), QUERY_MARGIN, name)

    def test_the_estimator_counts_nested_connections(self) -> None:
        """Prevents a blind estimator: a 100 x 30 nesting must be counted as thousands of nodes, not one."""
        self.assertEqual(estimated_nodes("query A { items(first: 100) { nodes { values(first: 30) { nodes { id } } } } }", 100), 1 + 100 + 3000)
        self.assertGreater(estimated_nodes("query A { a(first: 100) { nodes { b(first: 100) { nodes { c(first: 100) { nodes { id } } } } } } }", 100), NODE_LIMIT)


if __name__ == "__main__":
    unittest.main()
