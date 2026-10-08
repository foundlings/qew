"""Command walks: creating work records the human who asked for it.

Behavior: create, plan create, campaign create, and apply refuse to write
unless the caller quotes a human requester (`Name: 'their words'`). The quote is
rendered into the created text under a fixed `## Requested by` heading, and new
work packages wait behind `needs:approval` unless `--approved` (apply:
`"approved": true`) says the quote also authorizes starting them.

Adversary: an unattended agent minting approved work with no trace of who
wanted it, or satisfying the gate with an empty, too-short, or placeholder
quote.
"""

import json
from pathlib import Path
import tempfile
import unittest

from tests.command_harness import author_responses, run_qew
from tests.test_campaign_project_walk import milestone, response, rest_pages

QUOTE = "Daniel: 'open an item only when a human asked for it'"
REQUESTED_BY = f"## Requested by\n\n> {QUOTE}"
CREATED = {"stdout": "https://example.test/acme/widgets/issues/23\n"}


def _create(*extra):
    return [
        "create",
        "--repo",
        "acme/widgets",
        "--type",
        "task",
        "--title",
        "Build adapter",
        "--goal",
        "Provide one boundary.",
        *extra,
        "--json",
    ]


def _create_call(calls):
    return next(call for call in calls if call[:2] == ["issue", "create"])


def _body(call):
    return call[call.index("--body") + 1]


class RequesterQuoteWalk(unittest.TestCase):
    def test_create_without_quote_is_refused_before_any_write(self) -> None:
        result, calls = run_qew([CREATED], _create())

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--quote", result.stderr)
        self.assertEqual(calls, [])

    def test_create_refuses_empty_short_unattributed_and_placeholder_quotes(self) -> None:
        for quote in (
            "",
            "Daniel: 'ok go'",
            "'open an item only when a human asked for it'",
            "Daniel: open an item only when a human asked for it",
            "Daniel: '<their words>'",
            "Daniel: '...........................'",
            "Daniel: 'TODO: paste the real quote here'",
            "Daniel: 'open an item\nonly when a human asked'",
        ):
            with self.subTest(quote=quote):
                result, calls = run_qew([CREATED], _create("--quote", quote))

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("quote", result.stderr)
                self.assertEqual(calls, [])

    def test_create_renders_quote_and_waits_for_approval_by_default(self) -> None:
        result, calls = run_qew(author_responses() + [CREATED], _create("--quote", QUOTE))

        self.assertEqual(result.returncode, 0, result.stderr)
        body = _body(_create_call(calls))
        self.assertIn("### Goal\n\nProvide one boundary.", body)
        self.assertTrue(body.endswith(REQUESTED_BY), body)
        self.assertIn("needs:approval", _create_call(calls))
        self.assertIn("qew:approved", _create_call(calls))

    def test_create_approved_skips_the_approval_gate(self) -> None:
        result, calls = run_qew(author_responses() + [CREATED], _create("--quote", QUOTE, "--approved"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(REQUESTED_BY, _body(_create_call(calls)))
        self.assertNotIn("needs:approval", _create_call(calls))

    def test_plan_create_requires_and_renders_quote(self) -> None:
        arguments = ["plan", "create", "--repo", "acme/widgets", "--title", "Plan: P", "--goal", "G."]
        result, calls = run_qew([CREATED], [*arguments, "--json"])

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(calls, [])

        result, calls = run_qew(
            author_responses() + [CREATED], [*arguments, "--quote", QUOTE, "--json"]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(_body(_create_call(calls)).endswith(REQUESTED_BY))
        # A plan is never started, so it carries the quote but no approval gate.
        self.assertNotIn("needs:approval", _create_call(calls))

    def test_campaign_create_requires_quote_before_any_read_or_write(self) -> None:
        result, calls = run_qew(
            [rest_pages([])],
            ["campaign", "create", "--title", "Qew v3", "--repo", "acme/widgets"],
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--quote", result.stderr)
        self.assertEqual(calls, [])

    def test_campaign_create_records_quote_in_new_milestone_description(self) -> None:
        result, calls = run_qew(
            [rest_pages([]), response(milestone(3, "Qew v3"))],
            [
                "campaign",
                "create",
                "--title",
                "Qew v3",
                "--description",
                "Next campaign",
                "--quote",
                QUOTE,
                "--repo",
                "acme/widgets",
                "--json",
            ],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1][:3], ["api", "--method", "POST"])
        self.assertIn(f"description=Next campaign\n\n{REQUESTED_BY}", calls[1])

    def test_apply_refuses_an_item_without_its_own_quote_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = Path(temporary_directory) / "plan.jsonl"
            plan.write_text(
                json.dumps({"id": "a", "title": "A", "type": "task", "quote": QUOTE})
                + "\n"
                + json.dumps({"id": "b", "title": "B", "type": "task"})
                + "\n",
                encoding="utf-8",
            )
            result, calls = run_qew([CREATED, CREATED], ["apply", str(plan), "--repo", "acme/widgets"])

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("quote", result.stderr)
        self.assertIn("line 2", result.stderr)
        self.assertEqual(calls, [])

    def test_apply_renders_each_item_quote_and_gates_unapproved_work(self) -> None:
        other = "Jan: 'the settlement screen needs a printable summary'"
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = Path(temporary_directory) / "plan.jsonl"
            plan.write_text(
                json.dumps({"id": "a", "title": "A", "type": "task", "quote": QUOTE})
                + "\n"
                + json.dumps(
                    {"id": "b", "title": "B", "type": "task", "quote": other, "approved": True}
                )
                + "\n",
                encoding="utf-8",
            )
            result, calls = run_qew(
                [
                    {"stdout": "[]"},
                    *author_responses(),
                    CREATED,
                    {"stdout": "[]"},
                    {"stdout": "https://example.test/acme/widgets/issues/24\n"},
                ],
                ["apply", str(plan), "--repo", "acme/widgets", "--throttle", "0", "--json"],
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        creates = [call for call in calls if call[:2] == ["issue", "create"]]
        self.assertEqual(len(creates), 2)
        self.assertIn(REQUESTED_BY, _body(creates[0]))
        self.assertIn("needs:approval", creates[0])
        self.assertIn(f"## Requested by\n\n> {other}", _body(creates[1]))
        self.assertNotIn("needs:approval", creates[1])


if __name__ == "__main__":
    unittest.main()
