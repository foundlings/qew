"""The GitHub backend driven through the real urllib transport against tests/fake_github.py."""

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.fake_github import OWNER, REPO, TOKEN, FakeGitHub

from qew import forge
from qew.forge import (
    COLUMNS,
    ForgeAuth,
    ForgeRejected,
    ForgeUnavailable,
    HttpRequest,
    HttpResponse,
    IssueSpec,
    PullRequestSpec,
)
from qew.forge_github import GitHub, api_roots
from qew.forge_http import urllib_transport
from qew.ledger_config import Mirror

ENV = {"GITHUB_TOKEN": TOKEN, "QEW_NO_GH_TOKEN": "1"}
SPEC = IssueSpec(title="Title", body="Body <!-- qew:TST-1 -->", labels=("qew:ready",), open=True)


def mirror_for(fake: FakeGitHub, project: Optional[str] = None, **options: object) -> Mirror:
    return Mirror("github", fake.repo_url, project, "", {"api": fake.api, **options})


class GitHubWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        self.backend = self.build()

    def build(self, project: Optional[str] = None, env: Optional[Dict[str, str]] = None, **options: object) -> GitHub:
        return GitHub(mirror_for(self.fake, project, **options), urllib_transport(), ENV if env is None else env)


class IssueWalk(GitHubWalk):
    def test_create_find_read_update_round_trip(self) -> None:
        created = self.backend.create_issue(SPEC)
        self.assertEqual((created.title, created.open, created.labels), ("Title", True, ("qew:ready",)))
        self.assertTrue(created.url.endswith(f"/{OWNER}/{REPO}/issues/{created.number}"))
        self.assertTrue(created.node_id)
        found = self.backend.find_issue("<!-- qew:TST-1 -->")
        self.assertEqual(found, created)
        self.assertEqual(self.backend.read_issue(created.number), created)
        updated = self.backend.update_issue(created.number, replace(SPEC, title="New", labels=("a", "b")))
        self.assertEqual((updated.title, updated.labels), ("New", ("a", "b")))
        self.assertIsNone(self.backend.find_issue("<!-- qew:TST-404 -->"))

    def test_find_pages_through_the_list_and_skips_pull_requests(self) -> None:
        fake = FakeGitHub(page_size=2).start()
        self.addCleanup(fake.stop)
        backend = GitHub(mirror_for(fake), urllib_transport(), ENV)
        backend.open_pull_request(PullRequestSpec("topic", "main", "T", "<!-- qew:TST-9 -->", False))
        for n in range(5):
            backend.create_issue(replace(SPEC, body=f"<!-- qew:TST-{n} -->"))
        found = backend.find_issue("<!-- qew:TST-4 -->")
        assert found is not None
        self.assertEqual(found.body, "<!-- qew:TST-4 -->")
        self.assertIsNone(backend.find_issue("<!-- qew:TST-9 -->"))
        self.assertGreaterEqual(len(fake.calls("GET", "/issues?")), 3)

    def test_null_body_reads_as_empty_and_other_nulls_are_malformed(self) -> None:
        created = self.backend.create_issue(replace(SPEC, body=""))
        self.fake.issues[created.number]["body"] = None
        self.assertEqual(self.backend.read_issue(created.number).body, "")
        self.fake.issues[created.number]["title"] = None
        with self.assertRaises(ForgeRejected):
            self.backend.read_issue(created.number)

    def test_close_with_reason_and_reopen(self) -> None:
        created = self.backend.create_issue(SPEC)
        closed = self.backend.update_issue(created.number, replace(SPEC, open=False, close_reason="not_planned"))
        self.assertFalse(closed.open)
        self.assertEqual(self.fake.issues[created.number]["state_reason"], "not_planned")
        done = self.backend.update_issue(created.number, replace(SPEC, open=False, close_reason="completed"))
        self.assertEqual(self.fake.issues[created.number]["state_reason"], "completed")
        self.assertFalse(done.open)
        reopened = self.backend.update_issue(created.number, SPEC)
        self.assertTrue(reopened.open)

    def test_create_closed_issue_closes_after_creating(self) -> None:
        created = self.backend.create_issue(replace(SPEC, open=False, close_reason="completed"))
        self.assertFalse(created.open)
        self.assertEqual(self.fake.issues[created.number]["state_reason"], "completed")

    def test_labels_replace_and_assignee_set_then_cleared(self) -> None:
        created = self.backend.create_issue(replace(SPEC, labels=("a", "b"), assignee="octo"))
        self.assertEqual(created.assignee, "octo")
        swapped = self.backend.update_issue(created.number, replace(SPEC, labels=("c",), assignee=""))
        self.assertEqual((swapped.labels, swapped.assignee), (("c",), ""))

    def test_dependency_link_is_idempotent(self) -> None:
        first = self.backend.create_issue(SPEC)
        second = self.backend.create_issue(replace(SPEC, body="two"))
        self.backend.link_dependency(second.number, first.number)
        self.backend.link_dependency(second.number, first.number)
        self.assertEqual(self.fake.issues[second.number]["blocked_by"], [first.number])
        posted = self.fake.calls("POST", "/dependencies/blocked_by")
        self.assertEqual(posted, [{"issue_id": self.fake.issues[first.number]["id"]}])

    def test_missing_issue_is_rejected_with_status(self) -> None:
        with self.assertRaises(ForgeRejected) as caught:
            self.backend.read_issue(99)
        self.assertEqual(caught.exception.status, 404)


class PullRequestWalk(GitHubWalk):
    def open(self, draft: bool = True) -> forge.RemotePullRequest:
        return self.backend.open_pull_request(PullRequestSpec("topic", "main", "Add", "Body", draft))

    def test_open_draft_ready_merge(self) -> None:
        opened = self.open()
        self.assertTrue(opened.draft)
        self.assertEqual((opened.state, opened.head_branch, opened.base_branch), ("open", "topic", "main"))
        ready = self.backend.mark_pull_request_ready(opened.number)
        self.assertFalse(ready.draft)
        self.backend.mark_pull_request_ready(opened.number)
        self.assertEqual(len(self.fake.calls("POST", "/api/graphql")), 1)
        merged = self.backend.merge_pull_request(opened.number, opened.head_sha)
        self.assertEqual(merged.state, "merged")
        self.assertEqual(self.fake.calls("PUT", "/merge")[0], {"merge_method": "merge", "sha": opened.head_sha})

    def test_open_non_draft(self) -> None:
        self.assertFalse(self.open(draft=False).draft)

    def test_merge_refuses_when_head_moved(self) -> None:
        opened = self.open(draft=False)
        self.fake.push_to_pull(opened.number, "b" * 40)
        with self.assertRaises(ForgeRejected):
            self.backend.merge_pull_request(opened.number, opened.head_sha)
        self.assertEqual(self.fake.calls("PUT", "/merge"), [])
        self.assertTrue(self.fake.pulls[opened.number]["state"] == "open")

    def test_merge_of_a_draft_is_rejected_by_the_forge(self) -> None:
        opened = self.open()
        with self.assertRaises(ForgeRejected) as caught:
            self.backend.merge_pull_request(opened.number, opened.head_sha)
        self.assertEqual(caught.exception.status, 405)

    def test_mergeable_null_stays_none_while_computing(self) -> None:
        opened = self.open(draft=False)
        self.fake.pulls[opened.number]["mergeable"] = None
        self.assertIsNone(self.backend.read_pull_request(opened.number).mergeable)
        self.fake.pulls[opened.number]["mergeable"] = False
        self.assertIs(self.backend.read_pull_request(opened.number).mergeable, False)

    def test_checks_combine_statuses_and_check_runs(self) -> None:
        pull = self.open(draft=False)
        sha = pull.head_sha
        self.assertEqual(self.backend.read_pull_request(pull.number).checks, "none")
        self.fake.set_check_run(sha, "completed", "success")
        self.fake.set_check_run(sha, "completed", "skipped")
        self.assertEqual(self.backend.read_pull_request(pull.number).checks, "passing")
        self.fake.set_status(sha, "success")
        self.assertEqual(self.backend.read_pull_request(pull.number).checks, "passing")
        self.fake.set_check_run(sha, "in_progress", None)
        self.assertEqual(self.backend.read_pull_request(pull.number).checks, "pending")
        self.fake.set_check_run(sha, "completed", "failure")
        self.assertEqual(self.backend.read_pull_request(pull.number).checks, "failing")

    def test_failing_commit_status_alone_is_failing(self) -> None:
        pull = self.open(draft=False)
        self.fake.set_status(pull.head_sha, "failure")
        self.assertEqual(self.backend.read_pull_request(pull.number).checks, "failing")

    def test_closed_unmerged_pull_request(self) -> None:
        pull = self.open(draft=False)
        self.fake.pulls[pull.number]["state"] = "closed"
        self.assertEqual(self.backend.read_pull_request(pull.number).state, "closed")


class BoardWalk(GitHubWalk):
    def status_field(self, board: forge.Board) -> dict:
        project = self.fake.projects[board.id]
        return next(f for f in project["fields"] if f["name"] == "Status")

    def test_ensure_creates_project_with_exact_status_options(self) -> None:
        board = self.backend.ensure_board(COLUMNS)
        self.assertEqual(board.columns, COLUMNS)
        self.assertEqual(self.fake.projects[board.id]["title"], REPO)
        self.assertEqual([o["name"] for o in self.status_field(board)["options"]], list(COLUMNS))

    def test_ensure_is_idempotent_and_does_not_rewrite_matching_options(self) -> None:
        first = self.backend.ensure_board(COLUMNS)
        before = [o["id"] for o in self.status_field(first)["options"]]
        second = self.backend.ensure_board(COLUMNS)
        self.assertEqual(first, second)
        self.assertEqual(len(self.fake.projects), 1)
        self.assertEqual([o["id"] for o in self.status_field(second)["options"]], before)

    def test_ensure_titles_by_option_and_finds_by_number(self) -> None:
        board = self.build(project_title="Roadmap").ensure_board(COLUMNS)
        self.assertEqual(self.fake.projects[board.id]["title"], "Roadmap")
        again = self.build(project="1").ensure_board(COLUMNS)
        self.assertEqual(again.id, board.id)

    def test_unknown_project_number_is_rejected(self) -> None:
        with self.assertRaises(ForgeRejected):
            self.build(project="7").ensure_board(COLUMNS)

    def test_missing_status_field_is_created(self) -> None:
        board = self.backend.ensure_board(COLUMNS)
        self.fake.projects[board.id]["fields"] = []
        self.backend.ensure_board(COLUMNS)
        self.assertEqual([o["name"] for o in self.status_field(board)["options"]], list(COLUMNS))

    def test_place_adds_sets_status_and_moves_idempotently(self) -> None:
        board = self.backend.ensure_board(COLUMNS)
        issue = self.backend.create_issue(SPEC)
        self.backend.place_on_board(board, issue.number, "Ready")
        self.assertEqual(self.fake.item_status(board.id, issue.node_id), "Ready")
        self.backend.place_on_board(board, issue.number, "Ready")
        self.backend.place_on_board(board, issue.number, "In review")
        self.assertEqual(self.fake.item_status(board.id, issue.node_id), "In review")
        self.assertEqual(len(self.fake.projects[board.id]["items"]), 1)

    def test_unknown_column_is_rejected_before_any_write(self) -> None:
        board = self.backend.ensure_board(COLUMNS)
        issue = self.backend.create_issue(SPEC)
        with self.assertRaises(ForgeRejected):
            self.backend.place_on_board(board, issue.number, "Nowhere")
        self.assertEqual(self.fake.projects[board.id]["items"], {})

    def test_graphql_errors_are_typed(self) -> None:
        self.fake.fail_next(200, "/api/graphql", body={"errors": [{"type": "RATE_LIMITED", "message": "slow"}]})
        with self.assertRaises(ForgeUnavailable):
            self.backend.ensure_board(COLUMNS)
        self.fake.fail_next(200, "/api/graphql", body={"errors": [{"type": "INSUFFICIENT_SCOPES", "message": "scope"}]})
        with self.assertRaises(ForgeAuth):
            self.backend.ensure_board(COLUMNS)
        self.fake.fail_next(200, "/api/graphql", body={"errors": [{"type": "OTHER", "message": "nope"}]})
        with self.assertRaises(ForgeRejected):
            self.backend.ensure_board(COLUMNS)
        self.fake.fail_next(200, "/api/graphql", body={"data": None})
        with self.assertRaises(ForgeRejected):
            self.backend.ensure_board(COLUMNS)


class FailureWalk(GitHubWalk):
    def test_missing_token_is_auth_and_sends_nothing(self) -> None:
        backend = self.build(env={"QEW_NO_GH_TOKEN": "1"})
        with self.assertRaises(ForgeAuth):
            backend.read_issue(1)
        self.assertEqual(self.fake.requests, [])

    def test_wrong_token_is_auth(self) -> None:
        with self.assertRaises(ForgeAuth):
            self.build(env={**ENV, "GITHUB_TOKEN": "wrong"}).read_issue(1)

    def test_server_error_is_unavailable(self) -> None:
        self.fake.fail_next(503, "/issues")
        with self.assertRaises(ForgeUnavailable):
            self.backend.create_issue(SPEC)

    def test_rate_limit_status_is_unavailable_and_forbidden_is_auth(self) -> None:
        self.fake.fail_next(429, "/issues")
        with self.assertRaises(ForgeUnavailable):
            self.backend.create_issue(SPEC)
        self.fake.fail_next(403, "/issues")
        with self.assertRaises(ForgeAuth):
            self.backend.create_issue(SPEC)

    def test_unreachable_server_is_unavailable(self) -> None:
        self.fake.stop()
        self.addCleanup(self.fake.thread.join)
        with self.assertRaises(ForgeUnavailable):
            self.backend.read_issue(1)

    def test_malformed_shapes_are_rejected_not_defaulted(self) -> None:
        for body in ({"nope": 1}, [], {"number": "x"}):
            self.fake.fail_next(200, "/issues/1", body=body)
            with self.assertRaises(ForgeRejected):
                self.backend.read_issue(1)
        self.fake.fail_next(200, "/issues?", body={"not": "a list"})
        with self.assertRaises(ForgeRejected):
            self.backend.find_issue("x")


class RecordingTransport:
    def __init__(self, tamper_link: str = "") -> None:
        self.inner = urllib_transport()
        self.sent: List[HttpRequest] = []
        self.tamper_link = tamper_link

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.sent.append(request)
        response = self.inner(request)
        if self.tamper_link:
            return HttpResponse(response.status, {**response.headers, "Link": self.tamper_link}, response.body)
        return response


class WireWalk(GitHubWalk):
    def test_every_request_carries_bearer_and_api_version(self) -> None:
        transport = RecordingTransport()
        GitHub(mirror_for(self.fake), transport, ENV).create_issue(SPEC)
        headers = transport.sent[0].headers
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(headers["X-GitHub-Api-Version"], "2022-11-28")

    def test_next_link_to_another_host_is_refused_without_following(self) -> None:
        transport = RecordingTransport('<https://evil.example/issues?page=2>; rel="next"')
        backend = GitHub(mirror_for(self.fake), transport, ENV)
        with self.assertRaises(ForgeRejected):
            backend.find_issue("x")
        self.assertTrue(all(r.url.startswith(self.fake.base) for r in transport.sent))

    def test_endless_pagination_is_bounded(self) -> None:
        transport = RecordingTransport(f'<{self.fake.api}/repos/{OWNER}/{REPO}/issues?page=2>; rel="next"')
        backend = GitHub(mirror_for(self.fake), transport, ENV)
        with self.assertRaises(ForgeRejected):
            backend.find_issue("x")
        self.assertLessEqual(len(transport.sent), 60)


class ApiRootWalk(unittest.TestCase):
    def roots(self, repo: str, **options: object) -> tuple:
        return api_roots(Mirror("github", repo, None, "", dict(options)))

    def test_dotcom_and_enterprise_roots(self) -> None:
        self.assertEqual(self.roots("https://github.com/a/b"), ("https://api.github.com", "https://api.github.com/graphql"))
        self.assertEqual(
            self.roots("https://ghe.example/a/b"), ("https://ghe.example/api/v3", "https://ghe.example/api/graphql")
        )

    def test_overrides(self) -> None:
        self.assertEqual(
            self.roots("https://github.com/a/b", api="http://h/api/v3/"), ("http://h/api/v3", "http://h/api/graphql")
        )
        self.assertEqual(
            self.roots("https://github.com/a/b", api="http://h/x", graphql="http://g/q"), ("http://h/x", "http://g/q")
        )


if __name__ == "__main__":
    unittest.main()
