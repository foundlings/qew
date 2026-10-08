"""Command walks for removing one repository's cards from a board.

An organization board can end up holding another repository's cards (a run that
named the wrong repository). `project prune --from OWNER/REPO` is the deliberate
way to remove exactly those cards. A card is never an issue: deleting it
changes no issue.

Adversary: deleting the board's own cards, deleting by a loose match, and
reporting success while cards remain.
"""

import json
import unittest

from tests.command_harness import run_qew
from tests.test_campaign_project_walk import (
    graphql_pages,
    project_item,
    project_items_page,
    project_list_page,
    response,
)

PRUNE = ["project", "prune", "--from", "acme/other", "--repo", "acme/widgets", "--json"]
DELETED = response({"data": {"deleteProjectV2Item": {"deletedItemId": "X"}}})


def card(number: int, repository: str) -> dict:
    item = project_item("Ready", number)
    item["content"] = {**item["content"], "repository": {"nameWithOwner": repository}}
    return item


def prune(items, *writes, verified=None, arguments=PRUNE):
    return run_qew(
        [
            graphql_pages(project_list_page()),
            graphql_pages(project_items_page(*items)),
            *writes,
            *([] if verified is None else [graphql_pages(project_items_page(*verified))]),
        ],
        arguments,
        project=31,
    )


class ProjectPruneWalk(unittest.TestCase):
    def test_deletes_exactly_the_named_repositorys_cards_and_verifies(self) -> None:
        own = [card(5, "acme/widgets"), card(6, "Acme/Widgets")]
        result, calls = prune(
            [card(1, "acme/other"), card(2, "ACME/OTHER"), *own, card(3, "acme/other-two")],
            DELETED,
            DELETED,
            verified=[*own, card(3, "acme/other-two")],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual((output["removed"], output["from"]), (2, "acme/other"))
        deleted = [" ".join(call) for call in calls if "deleteProjectV2Item" in " ".join(call)]
        self.assertEqual(len(deleted), 2)
        self.assertTrue(any("ITEM1" in text for text in deleted))
        self.assertTrue(any("ITEM2" in text for text in deleted))
        self.assertFalse(any("ITEM5" in text or "ITEM3" in text for text in deleted))

    def test_the_boards_own_repository_is_refused_before_any_call(self) -> None:
        result, calls = run_qew(
            [], ["project", "prune", "--from", "Acme/Widgets", "--repo", "acme/widgets"], project=31
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("own repository", result.stderr)
        self.assertEqual(calls, [])

    def test_a_malformed_source_is_refused_before_any_call(self) -> None:
        for source in ("other", "a/b/c", "", "a b/c"):
            with self.subTest(source=source):
                result, calls = run_qew(
                    [], ["project", "prune", "--from", source, "--repo", "acme/widgets"], project=31
                )

                self.assertEqual(result.returncode, 2)
                self.assertEqual(calls, [])

    def test_nothing_to_remove_deletes_nothing(self) -> None:
        result, calls = prune([card(5, "acme/widgets")])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["removed"], 0)
        self.assertFalse(any("deleteProjectV2Item" in " ".join(call) for call in calls))

    def test_cards_that_remain_after_the_deletes_are_indeterminate(self) -> None:
        result, _ = prune(
            [card(1, "acme/other")], DELETED, verified=[card(1, "acme/other")]
        )

        self.assertEqual(result.returncode, 4)
        self.assertIn("remain", result.stderr)


if __name__ == "__main__":
    unittest.main()
