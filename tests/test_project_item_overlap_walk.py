"""Command walks for repeated board items in `project status`.

While `project ensure` adds many cards, the paginated item list can return the
same item id on two pages. That is a pagination overlap, not two cards.

Adversary: ensure and status abort for an hour on a harmless overlap, while a
real duplicate (two item ids for one issue) must still be refused and named.
"""

import json
import unittest

from tests.command_harness import run_qew
from tests.test_campaign_project_walk import (
    graphql_pages,
    project_item,
    project_items_page,
    project_list_page,
    repository_issues_page,
)

ARGUMENTS = ["project", "status", "--repo", "acme/widgets", "--json"]


def status(*items):
    return run_qew(
        [
            graphql_pages(project_list_page()),
            graphql_pages(project_items_page(*items)),
            graphql_pages(repository_issues_page()),
        ],
        ARGUMENTS,
        project=31,
    )


def in_repository(item: dict, repository: str, item_id: str = None) -> dict:
    content = {**item["content"], "repository": {"nameWithOwner": repository}}
    return {**item, "content": content, **({} if item_id is None else {"id": item_id})}


class ProjectItemOverlapWalk(unittest.TestCase):
    def test_a_card_for_another_repositorys_issue_is_ignored(self) -> None:
        # Adversary: an organization board also holds other repositories' issues, and
        # their numbers collide with ours (qew#92 beside other#92).
        result, _ = status(
            in_repository(project_item(), "acme/other", "FOREIGN"),
            in_repository(project_item(), "Acme/Widgets"),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output["in_sync"])
        self.assertEqual(output["missing"], [])

    def test_a_foreign_card_never_stands_in_for_a_missing_local_card(self) -> None:
        result, _ = status(in_repository(project_item(), "acme/other", "FOREIGN"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["missing"], [5])

    def test_the_same_item_id_twice_is_a_pagination_overlap(self) -> None:
        result, _ = status(project_item(), project_item())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["in_sync"])

    def test_two_item_ids_for_one_issue_stay_an_error_and_are_named(self) -> None:
        other = {**project_item(), "id": "OTHER-ITEM"}

        result, _ = status(project_item(), other)

        self.assertEqual(result.returncode, 4)
        self.assertIn("duplicate project item for #5", result.stderr)
        self.assertIn("ITEM5", result.stderr)
        self.assertIn("OTHER-ITEM", result.stderr)


if __name__ == "__main__":
    unittest.main()
