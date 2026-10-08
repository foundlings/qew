"""Command walks for merging a ready pull request.

Adversary: merging a draft, a pull request with open checklist items, red or
pending checks, a conflicting or foreign pull request, or one that closes a
draft issue; `--auto` on a base branch that requires no checks (GitHub then
merges at once, as PR 307 did with CI still running); a merge of a head that
moved; and a merge that lands nothing or whose landing failure is hidden.
"""

import json
import unittest

from tests.command_harness import PROJECT_HINT, run_qew
from tests.test_land_walk import CLEAN, STALE, pull_request as land_response
from tests.test_lifecycle_walk import FULL_SHA, compare_response, issue, pulls_response, response

REPO = ["--repo", "acme/widgets"]
HEAD = "a" * 40
READY_BODY = "## Progress\n\n- [x] done\n\nFixes #15"


def pull(
    *,
    state="OPEN",
    draft=False,
    author="alexander",
    body=READY_BODY,
    checks="SUCCESS",
    mergeable="MERGEABLE",
    protected=False,
    auto=False,
    closing=((15, ("task", "qew:approved")),),
    base="main",
    labels=(),
) -> dict:
    return {
        "stdout": json.dumps(
            {
                "data": {
                    "viewer": {"login": "alexander"},
                    "repository": {
                        "pullRequest": {
                            "number": 42,
                            "state": state,
                            "isDraft": draft,
                            "author": {"login": author},
                            "body": body,
                            "labels": {"nodes": [{"name": name} for name in labels], "pageInfo": {"hasNextPage": False}},
                            "headRefOid": HEAD,
                            "baseRefName": base,
                            "mergeable": mergeable,
                            "autoMergeRequest": {"enabledAt": "2026-10-07T10:00:00Z"} if auto else None,
                            "commits": {
                                "nodes": [
                                    {"commit": {"statusCheckRollup": None if checks is None else {"state": checks}}}
                                ]
                            },
                            "closingIssuesReferences": {
                                "nodes": [
                                    {
                                        "number": entry[0],
                                        "labels": {
                                            "nodes": [{"name": name} for name in entry[1]],
                                            "pageInfo": {"hasNextPage": False},
                                        },
                                        "assignees": {
                                            "totalCount": len(entry[2]) if len(entry) > 2 else 0,
                                            "nodes": [{"login": login} for login in (entry[2] if len(entry) > 2 else ())],
                                        },
                                    }
                                    for entry in closing
                                ],
                                "pageInfo": {"hasNextPage": False},
                            },
                            "baseRef": {
                                "branchProtectionRule": {"requiresStatusChecks": True} if protected else None
                            },
                        },
                    },
                }
            }
        )
    }


MERGED = {"stdout": "merged\n"}


def rules(value: list) -> dict:
    return {"stdout": json.dumps(value)}


def merges(calls):
    return [call for call in calls if call[:2] == ["pr", "merge"]]


def landing() -> list:
    return [
        land_response(),
        response(issue(**STALE)),
        response(issue(**STALE)),
        compare_response("ahead", FULL_SHA),
        pulls_response([(42, "2026-10-01T10:00:00Z")]),
        {"stdout": "edited\n"},
        {"stdout": "commented\n"},
        response(issue(**CLEAN)),
    ]


class PullRequestMergeWalk(unittest.TestCase):
    def test_golden_walk_merges_the_exact_head_then_lands(self) -> None:
        result, calls = run_qew(
            [pull(), MERGED, pull(state="MERGED"), *landing()], ["pr", "merge", "42", *REPO]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("merged PR #42", result.stdout)
        self.assertIn("landed #15 from PR #42", result.stdout)
        self.assertEqual(
            merges(calls),
            [["pr", "merge", "42", "--repo", "acme/widgets", "--merge", "--match-head-commit", HEAD]],
        )

    def test_json_reports_the_merge_and_what_landed(self) -> None:
        result, _ = run_qew(
            [pull(), MERGED, pull(state="MERGED"), *landing()], ["pr", "merge", "42", *REPO, "--json"]
        )

        output = json.loads(result.stdout)
        self.assertTrue(output["merged"])
        self.assertEqual(output["landed"], [15])
        self.assertEqual(output["pull_request"], 42)

    def test_refusals_write_nothing(self) -> None:
        cases = {
            "draft": (pull(draft=True), 2, "is a draft; run qew pr ready"),
            "open item": (pull(body="- [ ] todo\n\nFixes #15"), 2, "checklist"),
            "closes a draft": (pull(closing=((15, ("task", "qew:draft")),)), 2, "closes #15"),
            "conflict": (pull(mergeable="CONFLICTING"), 2, "conflicts"),
            "closed": (pull(state="CLOSED"), 2, "closed"),
            "merged": (pull(state="MERGED"), 2, "merged"),
            "foreign": (pull(author="mallory"), 3, "mallory"),
            "failing checks": (pull(checks="FAILURE"), 3, "are failing"),
            "pending checks": (pull(checks="PENDING"), 3, "are pending"),
        }
        for name, (response_value, code, text) in cases.items():
            with self.subTest(name):
                result, calls = run_qew([response_value], ["pr", "merge", "42", *REPO])

                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn(text, result.stderr)
                self.assertEqual(merges(calls), [])

    def test_a_pull_request_without_any_checks_may_merge(self) -> None:
        result, calls = run_qew(
            [pull(checks=None), MERGED, pull(state="MERGED"), *landing()], ["pr", "merge", "42", *REPO]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(merges(calls)), 1)

    def test_auto_is_refused_when_the_base_branch_requires_no_checks(self) -> None:
        # Adversary: GitHub merges at once, so auto-merge would skip a pending CI.
        result, calls = run_qew(
            [pull(checks="PENDING"), rules([])], ["pr", "merge", "42", "--auto", *REPO]
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("immediately", result.stderr)
        self.assertEqual(merges(calls), [])

    def test_auto_is_accepted_when_a_ruleset_requires_checks(self) -> None:
        # Adversary: A consumer protects main with a ruleset, not a branch protection
        # rule, so auto-merge was refused although GitHub would wait for the checks.
        result, calls = run_qew(
            [
                pull(checks="PENDING"),
                rules([{"type": "required_status_checks", "parameters": {}}]),
                MERGED,
                pull(checks="PENDING", auto=True),
            ],
            ["pr", "merge", "42", "--auto", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--auto", merges(calls)[0])
        self.assertEqual(calls[1][:2], ["api", "repos/acme/widgets/rules/branches/main"])

    def test_a_ruleset_without_required_checks_does_not_enable_auto(self) -> None:
        result, calls = run_qew(
            [pull(checks="PENDING"), rules([{"type": "pull_request", "parameters": {}}])],
            ["pr", "merge", "42", "--auto", *REPO],
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(merges(calls), [])

    def test_rules_that_cannot_be_read_are_indeterminate_not_assumed(self) -> None:
        result, calls = run_qew(
            [pull(checks="PENDING"), {"stderr": "HTTP 403", "exit_code": 1}],
            ["pr", "merge", "42", "--auto", *REPO],
        )

        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(merges(calls), [])

    def test_auto_enables_auto_merge_and_names_the_landing_command(self) -> None:
        result, calls = run_qew(
            [pull(checks="PENDING", protected=True), MERGED, pull(checks="PENDING", protected=True, auto=True)],
            ["pr", "merge", "42", "--auto", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--auto", merges(calls)[0])
        self.assertIn("qew land 42 --repo acme/widgets", result.stdout)

    def test_auto_that_did_not_take_effect_is_reported(self) -> None:
        result, _ = run_qew(
            [pull(checks="PENDING", protected=True), MERGED, pull(checks="PENDING", protected=True)],
            ["pr", "merge", "42", "--auto", *REPO],
        )

        self.assertEqual(result.returncode, 3, result.stderr)

    def test_admin_needs_a_reason_before_any_call(self) -> None:
        result, calls = run_qew([], ["pr", "merge", "42", "--admin", *REPO])

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--reason", result.stderr)
        self.assertEqual(calls, [])

    def test_admin_skips_the_check_rule_and_prints_the_reason(self) -> None:
        result, calls = run_qew(
            [pull(checks="PENDING"), MERGED, pull(state="MERGED"), *landing()],
            ["pr", "merge", "42", "--admin", "--reason", "docs only", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--admin", merges(calls)[0])
        self.assertIn("admin merge: docs only", result.stdout)

    def test_admin_still_refuses_open_items_and_drafts(self) -> None:
        result, calls = run_qew(
            [pull(draft=True)], ["pr", "merge", "42", "--admin", "--reason", "docs only", *REPO]
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(merges(calls), [])

    def test_admin_still_refuses_every_readiness_rule(self) -> None:
        cases = {
            "open item": (pull(body="- [ ] todo\n\nFixes #15"), "open checklist"),
            "conflict": (pull(mergeable="CONFLICTING"), "conflicts"),
            "closing draft": (pull(closing=((15, ("task", "qew:draft")),)), "closes #15"),
            "draft": (pull(draft=True), "is a draft; run qew pr ready"),
        }
        for name, (response_value, text) in cases.items():
            with self.subTest(name):
                result, calls = run_qew(
                    [response_value], ["pr", "merge", "42", "--admin", "--reason", "docs only", *REPO]
                )

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(text, result.stderr)
                self.assertEqual(merges(calls), [])

    def test_failing_and_pending_checks_are_told_apart(self) -> None:
        for checks, text in (("FAILURE", "are failing"), ("PENDING", "are pending")):
            with self.subTest(checks):
                result, _ = run_qew([pull(checks=checks)], ["pr", "merge", "42", *REPO])

                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertIn(text, result.stderr)

    def test_contradictory_options_are_refused_before_any_call(self) -> None:
        for extra, text in (
            (["--auto", "--admin", "--reason", "x"], "--auto"),
            (["--reason", "x"], "--reason is only for"),
        ):
            with self.subTest(extra=extra):
                result, calls = run_qew([], ["pr", "merge", "42", *extra, *REPO])

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(text, result.stderr)
                self.assertEqual(calls, [])

    def test_a_pull_request_labelled_qew_draft_is_refused_until_its_issues_are_approved(self) -> None:
        # Adversary: a plan's pull request is merged while its plan is still a draft.
        result, calls = run_qew(
            [pull(labels=("qew:draft",))], ["pr", "merge", "42", *REPO]
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("labelled qew:draft", result.stderr)
        self.assertIn("qew draft approve", result.stderr)
        self.assertEqual(merges(calls), [])

    def test_a_teammates_pull_request_merges_when_the_caller_holds_every_claim(self) -> None:
        # The lane was claimed deliberately (qew start --take-over): that is the
        # authority to merge, the same way it is the authority to complete.
        result, calls = run_qew(
            [pull(author="peter", closing=((15, ("task", "qew:approved"), ("alexander",)),)), MERGED, pull(state="MERGED", author="peter"), *landing()],
            ["pr", "merge", "42", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(merges(calls)), 1)

    def test_a_teammates_pull_request_is_refused_without_the_claim_and_names_both_ways(self) -> None:
        cases = {
            "unclaimed": ((15, ("task", "qew:approved"), ()),),
            "claimed by someone else": ((15, ("task", "qew:approved"), ("mallory",)),),
            "one of two claimed": ((15, ("task", "qew:approved"), ("alexander",)), (16, ("task", "qew:approved"), ())),
            "closes nothing": (),
        }
        for name, closing in cases.items():
            with self.subTest(name):
                result, calls = run_qew(
                    [pull(author="peter", closing=closing)], ["pr", "merge", "42", *REPO]
                )

                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertIn("opened by peter", result.stderr)
                self.assertIn("--take-over", result.stderr)
                self.assertIn("--other-author", result.stderr)
                self.assertEqual(merges(calls), [])

    def test_other_author_with_a_reason_merges_and_prints_it(self) -> None:
        result, calls = run_qew(
            [pull(author="peter"), MERGED, pull(state="MERGED", author="peter"), *landing()],
            ["pr", "merge", "42", "--other-author", "--reason", "reviewed and agreed with the author", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("merging peter's pull request: reviewed and agreed with the author", result.stdout)
        self.assertEqual(len(merges(calls)), 1)

    def test_other_author_needs_a_reason_and_never_overrides_readiness(self) -> None:
        missing, calls = run_qew([], ["pr", "merge", "42", "--other-author", *REPO])
        draft, draft_calls = run_qew(
            [pull(author="peter", draft=True)],
            ["pr", "merge", "42", "--other-author", "--reason", "agreed", *REPO],
        )

        self.assertEqual(missing.returncode, 2, missing.stderr)
        self.assertIn("--other-author requires --reason", missing.stderr)
        self.assertEqual(calls, [])
        self.assertEqual(draft.returncode, 2, draft.stderr)
        self.assertEqual(merges(draft_calls), [])

    def test_a_pull_request_labelled_qew_draft_is_refused_until_its_issues_are_approved(self) -> None:
        # Adversary: a plan's pull request is merged while its plan is still a draft.
        result, calls = run_qew([pull(labels=("qew:draft",))], ["pr", "merge", "42", *REPO])

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("labelled qew:draft", result.stderr)
        self.assertIn("qew draft approve", result.stderr)
        self.assertEqual(merges(calls), [])

    def test_a_merge_not_yet_visible_is_read_once_more_before_giving_up(self) -> None:
        # Adversary: GitHub still reports OPEN right after a successful merge, so
        # the issues were never landed.
        result, _ = run_qew(
            [pull(), MERGED, pull(), pull(state="MERGED"), *landing()], ["pr", "merge", "42", *REPO]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("landed #15 from PR #42", result.stdout)

    def test_a_merge_that_did_not_happen_is_reported(self) -> None:
        result, _ = run_qew([pull(), MERGED, pull(), pull()], ["pr", "merge", "42", *REPO])

        self.assertEqual(result.returncode, 3, result.stderr)

    def test_a_landing_failure_is_reported_with_the_command_to_rerun(self) -> None:
        result, _ = run_qew(
            [pull(), MERGED, pull(state="MERGED"), {"stderr": "boom", "exit_code": 1}],
            ["pr", "merge", "42", *REPO],
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("merged", result.stdout)
        self.assertIn("qew land 42 --repo acme/widgets", result.stderr)

    def test_incomplete_closing_references_are_indeterminate(self) -> None:
        value = json.loads(pull()["stdout"])
        value["data"]["repository"]["pullRequest"]["closingIssuesReferences"]["pageInfo"]["hasNextPage"] = True
        result, calls = run_qew([{"stdout": json.dumps(value)}], ["pr", "merge", "42", *REPO])

        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(merges(calls), [])


if __name__ == "__main__":
    unittest.main()
