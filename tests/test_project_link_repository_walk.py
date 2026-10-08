"""Command walks: a checkout's board link belongs to the checkout's own repository.

`.qew/config.json` links one Project, for the repository this checkout is of.
Using it for any other `--repo` wrote that repository's issues onto this
repository's board (1916 foreign cards on the Qew board).

Adversary: a `--repo` naming another repository silently reuses the link.
"""

import json
import unittest

from tests.command_harness import PROJECT_HINT, run_qew
from tests.test_campaign_project_walk import (
    graphql_pages,
    project_item,
    project_items_page,
    project_list_page,
    repository_issues_page,
)
from tests.test_lifecycle_walk import issue, response

ORIGIN = "https://example.test/acme/widgets.git"
STATUS = ["project", "status", "--json"]


def status(*arguments, remote=ORIGIN, title="Qew Factory"):
    return run_qew(
        [
            graphql_pages(project_list_page(title)),
            graphql_pages(project_items_page(project_item())),
            graphql_pages(repository_issues_page()),
        ],
        [*arguments],
        project=31,
        remote=remote,
    )


class ProjectLinkRepositoryWalk(unittest.TestCase):
    def test_another_repository_does_not_use_the_checkouts_link(self) -> None:
        result, calls = run_qew(
            [], [*STATUS, "--repo", "acme/other"], project=31, remote=ORIGIN
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("acme/widgets", result.stderr)
        self.assertIn("--title", result.stderr)
        self.assertEqual(calls, [])

    def test_another_repository_selects_its_board_by_title(self) -> None:
        result, _ = status(*STATUS, "--repo", "acme/other", "--title", "Other Board", title="Other Board")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["title"], "Other Board")

    def test_the_checkouts_own_repository_still_uses_the_link(self) -> None:
        for remote in (
            ORIGIN,
            "git@example.test:Acme/Widgets.git",
            "https://example.test/acme/widgets",
        ):
            with self.subTest(remote=remote):
                result, _ = status(*STATUS, "--repo", "acme/widgets", remote=remote)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["title"], "Qew Factory")

    def test_a_mutation_for_another_repository_syncs_no_card_on_this_board(self) -> None:
        result, calls = run_qew(
            [
                response(issue()),
                {"stdout": "claimed\n"},
                response(issue(assignees=["alexander"], labels=["qew:approved", "task", "P2", "in-progress"])),
            ],
            ["start", "15", "--repo", "acme/other"],
            project=31,
            remote=ORIGIN,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 3)
        self.assertEqual(result.stderr, PROJECT_HINT)

    def test_a_checkout_without_a_readable_origin_keeps_the_link(self) -> None:
        result, _ = status(*STATUS, "--repo", "acme/other", remote=None)

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
