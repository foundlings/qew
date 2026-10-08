"""Command walks for the person inbox: replies, waiting approvals, assigned work."""

from datetime import datetime, timedelta, timezone
import json
import unittest

from tests.command_harness import run_qew

SINCE = "2026-10-07T09:00:00Z"
CLAIM_MARKER = '<!-- qew:claim {"kind": "compute", "lane": "a", "workspace": null} -->'
RELEASE_MARKER = '<!-- qew:release {"by": "alice", "lane": "a", "reason": "done"} -->'


class InboxWalk(unittest.TestCase):
    def inbox(self, responses: list, *extra: str):
        return run_qew(
            responses,
            ["inbox", "--repo", "acme/widgets", "--login", "alice", "--since", SINCE, *extra],
        )

    def test_golden_walk_shows_replies_waiting_and_assigned(self) -> None:
        long_line = "x" * 300
        replies = search_page(
            reply_issue(12, "Needs an answer", [
                comment("bob", "2026-10-08T10:00:00Z", f"{long_line}\nsecond line"),
                comment("carol", "2026-10-08T11:00:00Z", "Looks fine"),
            ]),
        )
        responses = [
            response(replies),
            response(search_page(plain_issue(40, "Approve this"))),
            response(search_page(plain_issue(7, "Mine"), plain_issue(8, "Also mine"))),
        ]

        result, calls = self.inbox(responses, "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json_lines(result.stdout)
        self.assertEqual(rows[0]["section"], "reply")
        self.assertEqual(
            {key: value for key, value in rows[0].items() if key != "replies"},
            {
                "number": 12,
                "schema_version": 1,
                "section": "reply",
                "title": "Needs an answer",
                "url": "https://example.test/acme/widgets/issues/12",
            },
        )
        self.assertEqual([reply["author"] for reply in rows[0]["replies"]], ["bob", "carol"])
        self.assertEqual(rows[0]["replies"][0]["created_at"], "2026-10-08T10:00:00Z")
        excerpt = rows[0]["replies"][0]["excerpt"]
        self.assertTrue(excerpt.startswith("xxx"))
        self.assertLessEqual(len(excerpt), 100)
        self.assertNotIn("second line", excerpt)
        self.assertEqual(
            [(row["section"], row["number"]) for row in rows[1:]],
            [("waiting", 40), ("assigned", 7), ("assigned", 8)],
        )
        self.assertEqual(rows[1]["schema_version"], 1)
        queries = [search_query(call) for call in calls]
        self.assertIn("repo:acme/widgets", queries[0])
        self.assertIn("is:open", queries[0])
        self.assertIn("involves:alice", queries[0])
        self.assertIn(f"updated:>={SINCE}", queries[0])
        self.assertIn('label:"needs:approval"', queries[1])
        self.assertIn('label:"author:alice"', queries[1])
        self.assertIn("assignee:alice", queries[2])

    def test_text_output_names_each_section(self) -> None:
        responses = [
            response(search_page(reply_issue(12, "Needs an answer", [comment("bob", "2026-10-08T10:00:00Z", "Hi\nmore")]))),
            response(search_page(plain_issue(40, "Approve this"))),
            response(search_page(plain_issue(7, "Mine"))),
        ]

        result, _ = self.inbox(responses)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                f"Replies since {SINCE} (1)",
                "#12 Needs an answer",
                "  bob 2026-10-08T10:00:00Z  Hi",
                "Waiting for a human (1)",
                "#40 Approve this",
                "Assigned (1)",
                "#7 Mine",
            ],
        )

    def test_count_is_one_line_for_a_session_hook(self) -> None:
        responses = [
            response(search_page(
                reply_issue(12, "A", [comment("alice", "2026-10-08T10:00:00Z", "mine"), comment("bob", "2026-10-08T10:00:00Z", "q")]),
                reply_issue(40, "B", [comment("carol", "2026-10-09T10:00:00Z", "q")]),
            )),
            response(search_page(plain_issue(1, "W1"), plain_issue(2, "W2"))),
            response(search_page(*(plain_issue(n, f"A{n}") for n in range(10, 15)))),
        ]

        result, _ = self.inbox(responses, "--count")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            f"qew inbox (alice): 2 issue(s) with replies since {SINCE}: #12 (bob), #40 (carol); "
            "2 waiting for a human; 5 assigned.\n",
        )

    def test_count_with_no_replies_omits_the_list(self) -> None:
        responses = [response(search_page()), response(search_page()), response(search_page())]

        result, _ = self.inbox(responses, "--count")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            f"qew inbox (alice): 0 issue(s) with replies since {SINCE}; 0 waiting for a human; 0 assigned.\n",
        )

    def test_self_bot_marker_and_old_comments_are_not_replies(self) -> None:
        """Adversary: Qew's own claim comments and bots flood the inbox with noise."""
        issue = reply_issue(12, "Noisy", [
            comment("alice", "2026-10-08T10:00:00Z", "my own comment"),
            comment("dependabot", "2026-10-08T10:01:00Z", "bump", typename="Bot"),
            comment("ci-helper[bot]", "2026-10-08T10:02:00Z", "built", typename="User"),
            comment("bob", "2026-10-08T10:03:00Z", f"Claimed by lane a.\n\n{CLAIM_MARKER}"),
            comment("bob", "2026-10-08T10:04:00Z", f"released.\n\n{RELEASE_MARKER}"),
            comment("bob", "2026-10-07T08:59:59Z", "just before the window"),
        ])
        other = reply_issue(13, "Real", [comment("bob", SINCE, "exactly at the window")])
        responses = [response(search_page(issue, other)), response(search_page()), response(search_page())]

        result, _ = self.inbox(responses, "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json_lines(result.stdout)
        self.assertEqual([row["number"] for row in rows], [13])
        self.assertEqual(rows[0]["replies"][0]["author"], "bob")

    def test_search_is_paged_to_the_end(self) -> None:
        """Adversary: label edits bump updated, so the real reply sits on page two."""
        noise = [reply_issue(n, f"Label churn {n}", [comment("alice", "2026-10-08T10:00:00Z", "x")]) for n in range(1, 4)]
        real = reply_issue(50, "Buried reply", [comment("bob", "2026-10-09T10:00:00Z", "found me")])
        responses = [
            response(search_page(*noise, has_next=True, cursor="cursor-1", count=4)),
            response(search_page(real, count=4)),
            response(search_page()),
            response(search_page()),
        ]

        result, calls = self.inbox(responses, "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["number"] for row in json_lines(result.stdout)], [50])
        self.assertIn("endCursor=cursor-1", calls[1])
        self.assertEqual(search_query(calls[0]), search_query(calls[1]))

    def test_older_comment_pages_are_fetched_until_the_window_is_covered(self) -> None:
        """Adversary: a long thread hides the in-window reply behind the last-N cut."""
        newest = reply_issue(
            12,
            "Long thread",
            [comment("alice", "2026-10-09T10:00:00Z", "mine")],
            has_previous=True,
            cursor="older-1",
        )
        older = comment_page(
            [comment("bob", "2026-10-01T10:00:00Z", "ancient"), comment("bob", "2026-10-08T10:00:00Z", "in window")],
            has_previous=True,
            cursor="older-2",
        )
        responses = [
            response(search_page(newest)),
            response(older),
            response(search_page()),
            response(search_page()),
        ]

        result, calls = self.inbox(responses, "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json_lines(result.stdout)
        self.assertEqual([reply["excerpt"] for reply in rows[0]["replies"]], ["in window"])
        self.assertIn("before=older-1", calls[1])
        self.assertEqual(len(calls), 4, "must stop once a fetched comment predates the window")

    def test_unreachable_older_comments_fail_loudly(self) -> None:
        stuck = reply_issue(12, "Broken cursor", [comment("bob", "2026-10-09T10:00:00Z", "x")], has_previous=True, cursor=None)

        result, _ = self.inbox([response(search_page(stuck))])

        self.assertEqual(result.returncode, 4)
        self.assertEqual(result.stdout, "")
        self.assertIn("indeterminate", result.stderr)

    def test_truncated_or_malformed_api_data_fails_loudly(self) -> None:
        good = comment("bob", "2026-10-08T10:00:00Z", "q")
        broken = {
            "search without next cursor": search_page(plain_issue(1, "x"), has_next=True, cursor=None, count=2),
            "search beyond GitHub's cap": search_page(plain_issue(1, "x"), count=1001),
            "issue without comments": search_page({"number": 1, "title": "x", "url": "u"}),
            "unparseable comment time": search_page(reply_issue(1, "x", [comment("bob", "yesterday", "q")])),
            "comment without body": search_page(reply_issue(1, "x", [{**good, "body": None}])),
            "issue without title": search_page({**reply_issue(1, "x", [good]), "title": None}),
        }
        for name, page in broken.items():
            with self.subTest(name):
                result, _ = self.inbox([response(page), response(search_page()), response(search_page())], "--json")

                self.assertEqual(result.returncode, 4, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("indeterminate", result.stderr)

    def test_malformed_since_is_a_usage_error(self) -> None:
        for value in ("yesterday", "2026-13-45", "2026-10-07 09:00", "2026-10-07T09:00:00+02:00", ""):
            with self.subTest(value=value):
                result, calls = run_qew(
                    [], ["inbox", "--repo", "acme/widgets", "--login", "alice", "--since", value]
                )

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(calls, [])

    def test_date_since_means_midnight_utc(self) -> None:
        responses = [response(search_page()), response(search_page()), response(search_page())]

        result, calls = run_qew(
            responses, ["inbox", "--repo", "acme/widgets", "--login", "alice", "--since", "2026-10-07", "--count"]
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("updated:>=2026-10-07T00:00:00Z", search_query(calls[0]))
        self.assertIn("since 2026-10-07T00:00:00Z", result.stdout)

    def test_defaults_to_the_authenticated_user_and_seven_days(self) -> None:
        responses = [
            response({"data": {"viewer": {"login": "alice"}}}),
            response(search_page()),
            response(search_page()),
            response(search_page()),
        ]
        before = datetime.now(timezone.utc) - timedelta(days=7)

        result, calls = run_qew(responses, ["inbox", "--repo", "acme/widgets", "--count"])

        after = datetime.now(timezone.utc) - timedelta(days=7)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("qew inbox (alice): 0 issue(s)"))
        query = search_query(calls[1])
        self.assertIn("involves:alice", query)
        stamp = query.split("updated:>=")[1].split()[0]
        parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertTrue(before.replace(microsecond=0) <= parsed <= after, stamp)

    def test_login_cannot_inject_search_qualifiers(self) -> None:
        result, calls = run_qew([], ["inbox", "--repo", "acme/widgets", "--login", "alice label:secret"])

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(calls, [])


def response(payload: dict) -> dict:
    return {"stdout": json.dumps(payload)}


def json_lines(output: str) -> list:
    return [json.loads(line) for line in output.splitlines()]


def search_query(call: list) -> str:
    return next(argument.split("=", 1)[1] for argument in call if argument.startswith("searchQuery="))


def search_page(*issues: dict, has_next: bool = False, cursor=None, count=None) -> dict:
    return {
        "data": {
            "search": {
                "issueCount": len(issues) if count is None else count,
                "nodes": list(issues),
                "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
            }
        }
    }


def plain_issue(number: int, title: str) -> dict:
    return {"number": number, "title": title, "url": f"https://example.test/acme/widgets/issues/{number}"}


def reply_issue(number: int, title: str, comments: list, has_previous: bool = False, cursor="start") -> dict:
    return {
        **plain_issue(number, title),
        "comments": {
            "nodes": comments,
            "pageInfo": {"hasPreviousPage": has_previous, "startCursor": cursor},
        },
    }


def comment(login: str, created_at: str, body: str, typename: str = "User") -> dict:
    return {
        "author": {"__typename": typename, "login": login},
        "body": body,
        "createdAt": created_at,
    }


def comment_page(comments: list, has_previous: bool, cursor) -> dict:
    return {
        "data": {
            "repository": {
                "issue": {
                    "comments": {
                        "nodes": comments,
                        "pageInfo": {"hasPreviousPage": has_previous, "startCursor": cursor},
                    }
                }
            }
        }
    }


if __name__ == "__main__":
    unittest.main()
