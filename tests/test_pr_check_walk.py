"""Command walks for ticking a pull request's checklist.

`qew pr` opens a draft with a checklist, and ticking it needed `gh pr edit`,
whose --body-file rebuild once dropped the closing trailers. Qew edits the live
body in place.

Adversary: ticking an item nobody named, ticking inside a code block, losing a
concurrent human edit, rewriting the rest of the body, touching another login's
pull request, and reporting success when the write did not stick.
"""

import json
import unittest

from tests.command_harness import run_qew

REPO = ["--repo", "acme/widgets"]
MARKED = (
    "## Progress\n\n"
    "- [ ] RED test <!-- pr-progress:red -->\n"
    "- [ ] Implementation <!-- pr-progress:impl -->\n"
    "- [x] Docs <!-- pr-progress:docs -->\n\n"
    "Fixes #7\nPart of #3"
)
PLAIN = "## Progress\n\n- [ ] first\n- [ ] second\n\nFixes #7"


def pull_request(body: str, *, state="OPEN", author="alexander", claims=None) -> dict:
    return {
        "stdout": json.dumps(
            {
                "data": {
                    "viewer": {"login": "alexander"},
                    "repository": {
                        "pullRequest": {
                            "number": 42,
                            "state": state,
                            "isDraft": True,
                            "author": {"login": author},
                            "body": body,
                            "closingIssuesReferences": {
                                "nodes": [
                                    {
                                        "number": number,
                                        "assignees": {
                                            "totalCount": len(logins),
                                            "nodes": [{"login": login} for login in logins],
                                        },
                                    }
                                    for number, logins in (claims or [])
                                ],
                                "pageInfo": {"hasNextPage": False},
                            },
                        }
                    },
                }
            }
        )
    }


def edits(calls):
    return [call for call in calls if call[:2] == ["pr", "edit"]]


def tick(body: str, *needles: str) -> str:
    for needle in needles:
        body = body.replace(f"- [ ] {needle}", f"- [x] {needle}")
    return body


class PullRequestCheckWalk(unittest.TestCase):
    def test_ticks_a_marked_item_in_place_and_verifies(self) -> None:
        after = tick(MARKED, "RED test")
        result, calls = run_qew(
            [pull_request(MARKED), pull_request(MARKED), {"stdout": "edited\n"}, pull_request(after)],
            ["pr", "check", "42", "red", *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"changed": ["red"], "open": ["impl"], "pull_request": 42, "schema_version": 1},
        )
        (edit,) = edits(calls)
        self.assertEqual(edit[edit.index("--body") + 1], after)
        self.assertIn("Fixes #7\nPart of #3", edit[edit.index("--body") + 1])

    def test_an_item_without_a_marker_is_addressed_by_its_position(self) -> None:
        after = tick(PLAIN, "second")
        result, calls = run_qew(
            [pull_request(PLAIN), pull_request(PLAIN), {"stdout": "edited\n"}, pull_request(after)],
            ["pr", "check", "42", "2", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(edits(calls)[0][edits(calls)[0].index("--body") + 1], after)

    def test_all_ticks_every_open_item(self) -> None:
        after = tick(MARKED, "RED test", "Implementation")
        result, _ = run_qew(
            [pull_request(MARKED), pull_request(MARKED), {"stdout": "edited\n"}, pull_request(after)],
            ["pr", "check", "42", "--all", *REPO, "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["open"], [])

    def test_an_already_ticked_item_writes_nothing(self) -> None:
        result, calls = run_qew([pull_request(MARKED)], ["pr", "check", "42", "docs", *REPO, "--json"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed"], [])
        self.assertEqual(edits(calls), [])

    def test_an_unknown_item_is_refused_before_any_write(self) -> None:
        result, calls = run_qew([pull_request(MARKED)], ["pr", "check", "42", "red", "nope", *REPO])

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("nope", result.stderr)
        self.assertEqual(edits(calls), [])

    def test_items_inside_a_code_block_do_not_count(self) -> None:
        body = "```\n- [ ] fake <!-- pr-progress:fake -->\n```\n\n- [ ] real <!-- pr-progress:real -->"
        result, calls = run_qew([pull_request(body)], ["pr", "check", "42", "fake", *REPO])

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(edits(calls), [])

    def test_fences_follow_the_markdown_rules(self) -> None:
        # Adversary: a one-line fence or a shorter closing fence flips the fence
        # state, hiding later open boxes so pr merge merges past them.
        bodies = {
            "one-line fence": "```x```\n- [ ] real <!-- pr-progress:real -->",
            "tilde inside a backtick fence": "```\n~~~\n- [ ] fake <!-- pr-progress:fake -->\n```\n- [ ] real <!-- pr-progress:real -->",
            "short fence inside a long one": "````\n```\n- [ ] fake <!-- pr-progress:fake -->\n````\n- [ ] real <!-- pr-progress:real -->",
        }
        for name, body in bodies.items():
            with self.subTest(name):
                result, _ = run_qew([pull_request(body)], ["pr", "check", "42", "--list", *REPO, "--json"])

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([item["id"] for item in json.loads(result.stdout)["items"]], ["real"])

    def test_a_crlf_body_is_ticked_without_changing_its_line_endings(self) -> None:
        body = "- [ ] a <!-- pr-progress:a -->\r\n- [ ] b <!-- pr-progress:b -->\r\nFixes #7"
        after = body.replace("- [ ] a", "- [x] a")
        result, calls = run_qew(
            [pull_request(body), pull_request(body), {"stdout": "edited\n"}, pull_request(after)],
            ["pr", "check", "42", "a", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(edits(calls)[0][edits(calls)[0].index("--body") + 1], after)

    def test_a_marker_id_that_equals_a_position_does_not_make_the_body_unreadable(self) -> None:
        # Adversary: marker "2" on item 1 and a markerless item 2 both claimed id 2.
        body = "- [ ] a <!-- pr-progress:2 -->\n- [ ] b"
        result, _ = run_qew([pull_request(body)], ["pr", "check", "42", "--list", *REPO, "--json"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item["id"] for item in json.loads(result.stdout)["items"]], ["2", "@2"])

    def test_a_duplicate_item_id_is_refused(self) -> None:
        body = "- [ ] a <!-- pr-progress:x -->\n- [ ] b <!-- pr-progress:x -->"
        result, calls = run_qew([pull_request(body)], ["pr", "check", "42", "x", *REPO])

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("duplicate", result.stderr)
        self.assertEqual(edits(calls), [])

    def test_a_body_edited_meanwhile_is_not_overwritten(self) -> None:
        changed = MARKED + "\n\nA reviewer's note."
        result, calls = run_qew(
            [pull_request(MARKED), pull_request(changed)], ["pr", "check", "42", "red", *REPO]
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(edits(calls), [])

    def test_a_teammates_pull_request_is_ticked_by_the_login_that_holds_every_claim(self) -> None:
        # The factory lane holds the integration issue; the planner opened the PR.
        claims = [(7, ["alexander"])]
        after = tick(MARKED, "RED test")
        result, calls = run_qew(
            [
                pull_request(MARKED, author="peter", claims=claims),
                pull_request(MARKED, author="peter", claims=claims),
                {"stdout": "edited\n"},
                pull_request(after, author="peter", claims=claims),
            ],
            ["pr", "check", "42", "red", *REPO],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(edits(calls)), 1)

    def test_a_teammates_pull_request_is_refused_when_a_claim_is_missing_and_names_the_take_over(self) -> None:
        for claims in ([], [(7, [])], [(7, ["mallory"])], [(7, ["alexander"]), (8, [])]):
            with self.subTest(claims=claims):
                result, calls = run_qew(
                    [pull_request(MARKED, author="peter", claims=claims)], ["pr", "check", "42", "red", *REPO]
                )

                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertIn("--take-over", result.stderr)
                self.assertEqual(edits(calls), [])

    def test_another_logins_pull_request_is_refused(self) -> None:
        result, calls = run_qew(
            [pull_request(MARKED, author="mallory")], ["pr", "check", "42", "red", *REPO]
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(edits(calls), [])

    def test_a_merged_pull_request_is_refused(self) -> None:
        result, calls = run_qew(
            [pull_request(MARKED, state="MERGED")], ["pr", "check", "42", "red", *REPO]
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(edits(calls), [])

    def test_a_write_that_did_not_stick_is_reported(self) -> None:
        result, _ = run_qew(
            [pull_request(MARKED), pull_request(MARKED), {"stdout": "edited\n"}, pull_request(MARKED)],
            ["pr", "check", "42", "red", *REPO],
        )

        self.assertEqual(result.returncode, 3, result.stderr)

    def test_list_shows_each_item_and_whether_it_is_ticked(self) -> None:
        result, calls = run_qew([pull_request(MARKED)], ["pr", "check", "42", "--list", *REPO, "--json"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["items"],
            [
                {"checked": False, "id": "red", "text": "RED test"},
                {"checked": False, "id": "impl", "text": "Implementation"},
                {"checked": True, "id": "docs", "text": "Docs"},
            ],
        )
        self.assertEqual(edits(calls), [])

    def test_a_body_without_a_checklist_says_so(self) -> None:
        result, _ = run_qew([pull_request("No boxes.")], ["pr", "check", "42", "--list", *REPO])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no checklist", result.stdout)

    def test_naming_items_and_all_or_nothing_is_refused_before_any_call(self) -> None:
        for extra in ([], ["--all", "red"], ["--list", "red"]):
            with self.subTest(extra=extra):
                result, calls = run_qew([], ["pr", "check", "42", *extra, *REPO])

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
