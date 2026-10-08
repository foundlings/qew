"""Command walks for filing draft work and draft plans.

A draft is filed and discussed but not worked until approved. Adversary: a draft
that carries qew:approved or needs:approval (so reads or the gate treat it as
live work), and contradictory flags that silently pick one meaning.
"""

import unittest

from tests.command_harness import REQUESTER_QUOTE, author_responses, run_qew

REPO = ["--repo", "acme/widgets"]
CREATED = {"stdout": "https://example.test/acme/widgets/issues/42\n"}


def labels(call: list) -> list:
    return [call[index + 1] for index, argument in enumerate(call) if argument == "--label"]


class CreateDraftWalk(unittest.TestCase):
    def test_create_draft_files_work_marked_draft_and_never_approved(self) -> None:
        result, calls = run_qew(
            author_responses() + [CREATED],
            ["create", "--draft", "--quote", REQUESTER_QUOTE, *REPO, "--type", "task", "--title", "Idea"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        issued = labels(calls[-1])
        self.assertIn("qew:draft", issued)
        self.assertNotIn("qew:approved", issued)
        self.assertNotIn("needs:approval", issued)

    def test_create_draft_in_overlay_mode_is_still_managed(self) -> None:
        result, calls = run_qew(
            author_responses() + [CREATED],
            ["create", "--draft", "--quote", REQUESTER_QUOTE, *REPO, "--type", "task", "--title", "Idea"],
            repository_mode="overlay",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("qew:managed", labels(calls[-1]))
        self.assertIn("qew:draft", labels(calls[-1]))

    def test_draft_with_approved_or_needs_approval_is_refused_before_any_call(self) -> None:
        for flag in ("--approved", "--needs-approval"):
            with self.subTest(flag=flag):
                result, calls = run_qew(
                    [],
                    ["create", "--draft", flag, "--quote", REQUESTER_QUOTE, *REPO, "--type", "task", "--title", "x"],
                )

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("--draft", result.stderr)
                self.assertEqual(calls, [])

    def test_plan_create_draft_files_a_draft_epic(self) -> None:
        result, calls = run_qew(
            author_responses() + [CREATED],
            ["plan", "create", "--draft", "--quote", REQUESTER_QUOTE, *REPO, "--title", "Big idea", "--goal", "g"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        issued = labels(calls[-1])
        self.assertIn("epic", issued)
        self.assertIn("qew:draft", issued)
        self.assertNotIn("qew:approved", issued)

    def test_plan_create_draft_refuses_children_before_any_call(self) -> None:
        # Adversary: adopting approved children under a draft epic would mix states.
        result, calls = run_qew(
            [],
            ["plan", "create", "--draft", "--children", "5", "--quote", REQUESTER_QUOTE, *REPO, "--title", "t"],
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("create --draft --parent", result.stderr)
        self.assertEqual(calls, [])

    def test_a_missing_draft_label_names_qew_init(self) -> None:
        # Adversary: gh refuses an unknown label with an opaque message.
        result, _ = run_qew(
            author_responses() + [{"stderr": "could not add label: 'qew:draft' not found", "exit_code": 1}],
            ["create", "--draft", "--quote", REQUESTER_QUOTE, *REPO, "--type", "task", "--title", "Idea"],
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("qew init", result.stderr)


if __name__ == "__main__":
    unittest.main()
