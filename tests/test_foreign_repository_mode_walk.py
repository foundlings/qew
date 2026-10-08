"""Command walks: a checkout's mode and claim cap belong to its own repository.

`.qew/config.json` says a repository is Qew-owned (`repository` mode: every
issue is in scope) or only partly (`overlay`: only `qew:managed` issues). Using
that for another `--repo` treated every issue of the other repository as ours
(a backfill preview wanted 1154 issues instead of 310).

Adversary: a foreign `--repo` reads the checkout's mode and acts on issues Qew
does not own.
"""

import json
import unittest

from tests.command_harness import run_qew
from tests.test_init_walk import response
from tests.test_lifecycle_walk import issue
from tests.test_lifecycle_walk import response as issue_response

ORIGIN = "https://example.test/acme/widgets.git"
PAGE = {"hasNextPage": False, "endCursor": None}
EMPTY = response(
    json.dumps([{"data": {"repository": {"issues": {"nodes": [], "pageInfo": PAGE}}}}])
)
MANAGED_ONLY = 'labels: ["qew:managed"]'


def queries(calls) -> str:
    return " ".join(" ".join(call) for call in calls)


class ForeignRepositoryModeWalk(unittest.TestCase):
    def test_a_foreign_repository_is_scoped_to_managed_issues(self) -> None:
        result, calls = run_qew(
            [EMPTY],
            ["author", "backfill", "--dry-run", "--repo", "acme/other"],
            repository_mode="repository",
            remote=ORIGIN,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(MANAGED_ONLY, queries(calls))

    def test_the_checkouts_own_repository_keeps_its_mode(self) -> None:
        result, calls = run_qew(
            [EMPTY],
            ["author", "backfill", "--dry-run", "--repo", "Acme/Widgets"],
            repository_mode="repository",
            remote=ORIGIN,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(MANAGED_ONLY, queries(calls))

    def test_a_checkout_without_a_readable_origin_keeps_its_mode(self) -> None:
        result, calls = run_qew(
            [EMPTY],
            ["author", "backfill", "--dry-run", "--repo", "acme/other"],
            repository_mode="repository",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(MANAGED_ONLY, queries(calls))

    def test_the_checkouts_claim_cap_never_applies_to_another_repository(self) -> None:
        config = {"mode": "repository", "schema_version": 1, "max_active_claims": 1}
        result, calls = run_qew(
            [
                issue_response(issue()),
                {"stdout": "claimed\n"},
                issue_response(
                    issue(assignees=["alexander"], labels=["qew:approved", "task", "P2", "in-progress"])
                ),
            ],
            ["start", "15", "--repo", "acme/other"],
            config=config,
            remote=ORIGIN,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
