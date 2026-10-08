"""Command walks for creating a missing team (area) label through Qew.

A label such as epic:customer must exist before `--area` can apply it, and the
only way to create it was `gh label create`, which Qew must not require.

Adversary: overwriting a label a team already tuned, creating a label Qew
reserves for its own state, and reporting a creation GitHub never persisted.
"""

import json
import unittest

from tests.command_harness import run_qew
from tests.test_init_walk import label_page, pages, response

REPO = ["--repo", "acme/widgets"]
EXISTING = [("epic:customer", "1D76DB", "Customer epic"), ("bug", "D73A4A", "Defect")]


class AreaEnsureWalk(unittest.TestCase):
    def test_a_missing_label_is_created_without_force_and_verified(self) -> None:
        created = [*EXISTING, ("epic:infrastructure", "5319E7", "Infrastructure epic")]
        result, calls = run_qew(
            [pages(label_page(EXISTING)), response(""), pages(label_page(created))],
            [
                "area", "ensure", "epic:infrastructure",
                "--color", "5319E7", "--description", "Infrastructure epic", *REPO,
            ],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "created area epic:infrastructure\n")
        write = calls[1]
        self.assertEqual(write[:3], ["label", "create", "epic:infrastructure"])
        self.assertEqual(write[write.index("--color") + 1], "5319E7")
        self.assertEqual(write[write.index("--description") + 1], "Infrastructure epic")
        self.assertNotIn("--force", write)
        self.assertEqual(len(calls), 3)

    def test_an_existing_label_is_never_overwritten_even_with_other_options(self) -> None:
        # Adversary: a second run with a different colour silently retunes a
        # label the team already uses.
        result, calls = run_qew(
            [pages(label_page(EXISTING))],
            ["area", "ensure", "Epic:Customer", "--color", "FFFFFF", *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"action": "unchanged", "area": "Epic:Customer", "schema_version": 1},
        )
        self.assertEqual(len(calls), 1)

    def test_defaults_give_a_neutral_colour_and_description(self) -> None:
        both = [*EXISTING, ("team:ops", "EDEDED", "Team label")]
        result, calls = run_qew(
            [pages(label_page(EXISTING)), response(""), pages(label_page(both))],
            ["area", "ensure", "team:ops", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1][calls[1].index("--color") + 1], "EDEDED")
        self.assertEqual(calls[1][calls[1].index("--description") + 1], "Team label")

    def test_reserved_and_malformed_names_are_refused_before_any_call(self) -> None:
        for name in (
            "epic", "EPIC", "qew:approved", "author:mallory", "P1", "task", "in-progress",
            "needs:approval", "a,b", "", " lead", "trail ", "x" * 51, "line\nbreak",
        ):
            with self.subTest(name=name):
                result, calls = run_qew([], ["area", "ensure", name, *REPO])

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(calls, [])

    def test_a_malformed_colour_or_description_is_refused_before_any_call(self) -> None:
        for options in (
            ["--color", "zzzzzz"], ["--color", "#123456"], ["--color", ""], ["--description", "a\nb"]
        ):
            with self.subTest(options=options):
                result, calls = run_qew([], ["area", "ensure", "team:ops", *options, *REPO])

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(calls, [])

    def test_a_creation_that_does_not_persist_is_indeterminate(self) -> None:
        result, calls = run_qew(
            [pages(label_page(EXISTING)), response(""), pages(label_page(EXISTING))],
            ["area", "ensure", "team:ops", *REPO],
        )

        self.assertEqual(result.returncode, 4)
        self.assertIn("did not persist", result.stderr)
        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
