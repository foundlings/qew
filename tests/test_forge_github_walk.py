"""The GitHub backend driven through the real urllib transport against tests/fake_github.py."""

import sys
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.fake_github import OWNER, REPO, TOKEN, FakeGitHub

from qew import forge, instants, ledger_ids
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
SPEC = IssueSpec(title="Title", body="<!-- qew:TST-1 -->\nBody", labels=("qew:ready",), open=True)


def mirror_for(fake: FakeGitHub, project: str | None = None, **options: object) -> Mirror:
    return Mirror("github", fake.repo_url, project, "", {"api": fake.api, **options})


class GitHubWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGitHub().start()
        self.addCleanup(self.fake.stop)
        self.backend = self.build()

    def build(self, project: str | None = None, env: dict[str, str] | None = None, **options: object) -> GitHub:
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

    def test_find_in_a_repository_longer_than_the_page_cap_looks_only_at_issues_changed_since_the_item_existed(self) -> None:
        """Prevents `qew push` from failing 'more than 50 pages' on a big repository: a new item's lookup needs no full listing."""
        fake = FakeGitHub(page_size=2).start()
        self.addCleanup(fake.stop)
        backend = GitHub(mirror_for(fake), urllib_transport(), ENV)
        fake.clock = lambda: instants.now() - timedelta(days=30)
        for n in range(6):
            backend.create_issue(replace(SPEC, body=f"<!-- qew:OLD-{n} -->"))
        fake.clock = instants.now
        item_id = ledger_ids.event_id()
        orphan = backend.create_issue(replace(SPEC, body=f"<!-- qew:{item_id} -->"))
        with mock.patch("qew.forge_github.MAX_PAGES", 2):
            self.assertEqual(backend.find_issue(f"<!-- qew:{item_id} -->"), orphan)
            self.assertIsNone(backend.find_issue(f"<!-- qew:{ledger_ids.event_id(item_id)} -->"))
            with self.assertRaises(ForgeRejected):
                backend.find_issue("<!-- qew:OLD-0 -->")  # an id that says nothing about its age still refuses a truncated listing

    def test_a_refused_listing_names_the_repository_the_listing_and_the_cause(self) -> None:
        """Prevents an agent facing 'more than 50 pages' with nothing to act on: the refusal says which repository and listing
        overflowed, the cap, and that an id with no mint time cannot be narrowed to a since-window."""
        fake = FakeGitHub(page_size=2).start()
        self.addCleanup(fake.stop)
        backend = GitHub(mirror_for(fake), urllib_transport(), ENV)
        for n in range(6):
            backend.create_issue(replace(SPEC, body=f"<!-- qew:OLD-{n} -->"))
        with mock.patch("qew.forge_github.MAX_PAGES", 2):
            with self.assertRaises(ForgeRejected) as caught:
                backend.find_issue("<!-- qew:OLD-0 -->")
        message = str(caught.exception)
        self.assertIn(f"{OWNER}/{REPO}", message)
        self.assertIn("issues", message)
        self.assertIn("2 pages", message)
        self.assertIn("mint time", message)

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
        # GitHub refuses to change the reason of a closed issue: the backend reopens and closes again with the wanted one
        done = self.backend.update_issue(created.number, replace(SPEC, open=False, close_reason="completed", title="Edited while closed"))
        self.assertEqual(self.fake.issues[created.number]["state_reason"], "completed")
        self.assertEqual(self.fake.issues[created.number]["title"], "Edited while closed")
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
        self.assertTrue(self.backend.mark_pull_request_draft(opened.number).draft, "ready converts back to draft")
        self.assertTrue(self.backend.mark_pull_request_draft(opened.number).draft, "draft is idempotent")
        self.assertFalse(self.backend.mark_pull_request_ready(opened.number).draft)
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


class PullRequestEditWalk(GitHubWalk):
    def open_with(self, body: str = "Body", assignee: str = "") -> forge.RemotePullRequest:
        return self.backend.open_pull_request(PullRequestSpec("topic", "main", "Add", body, False, assignee))

    def test_body_author_and_assignee_round_trip_and_an_edit_changes_only_the_body(self) -> None:
        """A backend that truncates, escapes or drops the opener would let a checklist or ownership drift."""
        opened = self.open_with("- [ ] one\r\n- [ ] two\r\n", "alice")
        self.assertEqual((opened.body, opened.author, opened.assignee), ("- [ ] one\r\n- [ ] two\r\n", "opener", "alice"))
        edited = self.backend.update_pull_request_body(opened.number, "- [x] one\r\n- [ ] two\r\n")
        self.assertEqual(edited, replace(opened, body="- [x] one\r\n- [ ] two\r\n"))
        self.assertEqual(self.backend.read_pull_request(opened.number), edited)
        self.assertEqual(self.fake.calls("PATCH", f"/pulls/{opened.number}"), [{"body": "- [x] one\r\n- [ ] two\r\n"}])

    def test_a_login_the_forge_will_not_assign_leaves_the_pull_request_open_and_unassigned(self) -> None:
        """Prevents an orphaned pull request: it exists on the forge, so the caller must be able to record it."""
        self.fake.fail_next(422, "/issues/")
        opened = self.open_with(assignee="not-a-login")
        self.assertEqual((opened.state, opened.assignee), ("open", ""))

    def test_a_null_pull_body_reads_empty_and_a_mistyped_one_is_rejected(self) -> None:
        """A fabricated empty body would hide an open checklist from the merge gate."""
        opened = self.open_with()
        self.fake.pulls[opened.number]["body"] = None
        self.assertEqual(self.backend.read_pull_request(opened.number).body, "")
        self.fake.pulls[opened.number]["body"] = 7
        with self.assertRaises(ForgeRejected):
            self.backend.read_pull_request(opened.number)

    def test_status_check_rules_come_from_rulesets_and_classic_protection_and_never_default_to_false(self) -> None:
        """An unreadable rule set must not be read as 'no checks required'."""
        self.assertFalse(self.backend.requires_status_checks("main"))
        self.fake.rules["main"] = [{"type": "pull_request"}]
        self.assertFalse(self.backend.requires_status_checks("main"))
        self.fake.rules["main"] = [{"type": "required_status_checks"}]
        self.assertTrue(self.backend.requires_status_checks("main"))
        self.fake.rules.clear()
        self.fake.protection["main"] = {"required_status_checks": {"contexts": ["ci"]}}
        self.assertTrue(self.backend.requires_status_checks("main"))
        self.fake.protection["main"] = {"required_status_checks": None}
        self.assertFalse(self.backend.requires_status_checks("main"))
        self.fake.fail_next(403, "/rules/branches/")
        with self.assertRaises(ForgeAuth):
            self.backend.requires_status_checks("main")

    def test_auto_merge_is_enabled_through_the_forge_and_read_back(self) -> None:
        """The caller must see whether the forge took the request, not assume it."""
        opened = self.open_with()
        self.assertFalse(opened.auto_merge)
        self.assertTrue(self.backend.enable_auto_merge(opened.number).auto_merge)
        self.fake.pulls[opened.number]["auto_merge"] = False
        self.fake.auto_merge_ignored = True
        self.assertFalse(self.backend.enable_auto_merge(opened.number).auto_merge)

    def test_a_bypass_merge_sends_the_same_pinned_request_and_names_the_merge_commit(self) -> None:
        opened = self.open_with()
        merged = self.backend.merge_pull_request(opened.number, opened.head_sha, bypass_checks=True)
        self.assertEqual((merged.state, merged.merge_sha), ("merged", "f" * 40))
        self.assertEqual(self.fake.calls("PUT", "/merge"), [{"merge_method": "merge", "sha": opened.head_sha}])

    def test_a_failing_check_run_on_the_second_page_makes_the_pull_request_failing(self) -> None:
        """Merging a red PR because page one was green is the failure this prevents."""
        fake = FakeGitHub(page_size=2).start()
        self.addCleanup(fake.stop)
        backend = GitHub(mirror_for(fake), urllib_transport(), ENV)
        pull = backend.open_pull_request(PullRequestSpec("topic", "main", "T", "b", False))
        for conclusion in ("success", "success", "failure"):
            fake.set_check_run(pull.head_sha, "completed", conclusion)
        before = len(fake.calls("GET", "/check-runs"))
        self.assertEqual(backend.read_pull_request(pull.number).checks, "failing")
        self.assertEqual(len(fake.calls("GET", "/check-runs")) - before, 2)


class BoardWalk(GitHubWalk):
    def status_field(self, board: forge.Board) -> dict:
        project = self.fake.project(board.id)
        return next(f for f in project["fields"] if f["name"] == "Status")

    def test_ensure_creates_project_with_exact_status_options(self) -> None:
        board = self.backend.ensure_board(COLUMNS)
        self.assertEqual(board.columns, COLUMNS)
        self.assertEqual(self.fake.project(board.id)["title"], REPO)
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
        self.assertEqual(self.fake.project(board.id)["title"], "Roadmap")
        again = self.build(project="1").ensure_board(COLUMNS)
        self.assertEqual(again.id, board.id)

    def test_board_id_round_trips_as_the_mirror_project(self) -> None:
        first = self.backend.ensure_board(COLUMNS)
        self.assertEqual(first.id, "1")
        second = self.build(project=first.id).ensure_board(COLUMNS)
        self.assertEqual(second, first)
        self.assertEqual(len(self.fake.projects), 1)
        issue = self.backend.create_issue(SPEC)
        self.build(project=first.id).place_on_board(second, issue.number, "Done")
        self.assertEqual(self.fake.item_status(first.id, issue.node_id), "Done")

    def test_existing_title_is_never_duplicated(self) -> None:
        self.backend.ensure_board(COLUMNS)
        self.build().ensure_board(COLUMNS)
        self.build(project_title=REPO).ensure_board(COLUMNS)
        self.assertEqual(len(self.fake.projects), 1)

    def test_unknown_project_number_is_rejected(self) -> None:
        with self.assertRaises(ForgeRejected):
            self.build(project="7").ensure_board(COLUMNS)

    def test_missing_status_field_is_created(self) -> None:
        board = self.backend.ensure_board(COLUMNS)
        self.fake.project(board.id)["fields"] = []
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
        self.assertEqual(len(self.fake.project(board.id)["items"]), 1)

    def test_unknown_column_is_rejected_before_any_write(self) -> None:
        board = self.backend.ensure_board(COLUMNS)
        issue = self.backend.create_issue(SPEC)
        with self.assertRaises(ForgeRejected):
            self.backend.place_on_board(board, issue.number, "Nowhere")
        self.assertEqual(self.fake.project(board.id)["items"], {})

    def test_graphql_errors_are_typed(self) -> None:
        self.fake.fail_next(200, "/api/graphql", body={"errors": [{"type": "RATE_LIMITED", "message": "slow"}]})
        with self.assertRaises(ForgeUnavailable):
            self.build(env={**ENV, "QEW_RATE_RETRIES": "0"}).ensure_board(COLUMNS)  # no waiting: the retries are tested in test_rate_limit_walk
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
        self.sent: list[HttpRequest] = []
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


class ReadSideWalk(GitHubWalk):
    def test_listing_pages_oldest_first_with_closed_issues_and_without_pull_requests(self) -> None:
        """An import must see every issue, closed ones included, and never mistake a pull request for one."""
        fake = FakeGitHub(page_size=2).start()
        self.addCleanup(fake.stop)
        backend = GitHub(mirror_for(fake), urllib_transport(), ENV)
        first = backend.create_issue(replace(SPEC, title="one", labels=("a",), assignee="octo"))
        backend.open_pull_request(PullRequestSpec("topic", "main", "T", "pr body", False))
        backend.create_issue(replace(SPEC, title="two", open=False, close_reason="completed"))
        for n in range(3, 6):
            backend.create_issue(replace(SPEC, title=f"n{n}"))
        listed = backend.list_issues()
        self.assertEqual([i.title for i in listed], ["one", "two", "n3", "n4", "n5"])
        self.assertEqual([i.open for i in listed], [True, False, True, True, True])
        self.assertEqual((listed[0].labels, listed[0].assignee, listed[0].body), (("a",), "octo", SPEC.body))
        self.assertEqual(listed[0], backend.read_issue(first.number))
        self.assertGreaterEqual(len(fake.calls("GET", "state=all")), 3)

    def test_comments_come_back_in_creation_order_and_create_round_trips(self) -> None:
        """Pull must replay a thread in order, and a posted comment must read back as the same comment."""
        issue = self.backend.create_issue(SPEC)
        self.fake.add_comment(issue.number, "alice", "first")
        posted = self.backend.create_comment(issue.number, "second")
        self.fake.add_comment(issue.number, "bob", "third")
        comments = self.backend.list_comments(issue.number)
        self.assertEqual([c.body for c in comments], ["first", "second", "third"])
        self.assertEqual([c.author for c in comments], ["alice", "qew-bot", "bob"])
        self.assertEqual(comments[1], posted)
        self.assertTrue(all(c.id and c.created for c in comments))

    def test_comment_listing_pages(self) -> None:
        """A long thread must not be cut at the first page."""
        fake = FakeGitHub(page_size=2).start()
        self.addCleanup(fake.stop)
        backend = GitHub(mirror_for(fake), urllib_transport(), ENV)
        issue = backend.create_issue(SPEC)
        for n in range(5):
            fake.add_comment(issue.number, "alice", f"c{n}")
        self.assertEqual([c.body for c in backend.list_comments(issue.number)], [f"c{n}" for n in range(5)])

    def test_comment_count_follows_create_comment_on_every_read_path(self) -> None:
        """Incremental pull relies on the count moving whenever a comment is posted."""
        issue = self.backend.create_issue(SPEC)
        self.assertEqual(issue.comments, 0)
        self.backend.create_comment(issue.number, "hello")
        self.assertEqual(self.backend.read_issue(issue.number).comments, 1)
        self.assertEqual(self.backend.list_issues()[0].comments, 1)
        self.assertEqual(self.backend.update_issue(issue.number, SPEC).comments, 1)

    def test_null_bodies_read_as_empty(self) -> None:
        """A forge sends null for an empty body; that is empty text, not a rejected listing."""
        issue = self.backend.create_issue(SPEC)
        self.fake.issues[issue.number]["body"] = None
        self.fake.add_comment(issue.number, "alice", None)
        self.assertEqual(self.backend.list_issues()[0].body, "")
        self.assertEqual(self.backend.list_comments(issue.number)[0].body, "")

    def test_listing_longer_than_the_page_bound_is_rejected_not_truncated(self) -> None:
        """A silently truncated import would drop issues without anyone noticing."""
        link = f'<{self.fake.api}/repos/{OWNER}/{REPO}/issues?page=2>; rel="next"'
        backend = GitHub(mirror_for(self.fake), RecordingTransport(link), ENV)
        with self.assertRaises(ForgeRejected):
            backend.list_issues()
        comment_link = f'<{self.fake.api}/repos/{OWNER}/{REPO}/issues/1/comments?page=2>; rel="next"'
        self.backend.create_issue(SPEC)
        endless = GitHub(mirror_for(self.fake), RecordingTransport(comment_link), ENV)
        with self.assertRaises(ForgeRejected):
            endless.list_comments(1)

    def test_malformed_json_is_rejected(self) -> None:
        """Garbage from the forge must stop the import, never become an empty listing."""
        issue = self.backend.create_issue(SPEC)
        self.fake.fail_next(200, "/issues?", body=b"<html>not json")
        with self.assertRaises(ForgeRejected):
            self.backend.list_issues()
        self.fake.fail_next(200, "/comments", body=b"{broken")
        with self.assertRaises(ForgeRejected):
            self.backend.list_comments(issue.number)
        self.fake.fail_next(200, "/comments", body={"id": "x"})
        with self.assertRaises(ForgeRejected):
            self.backend.create_comment(issue.number, "hi")

    def test_server_errors_are_unavailable(self) -> None:
        """An outage must read as retry-later, not as an empty forge."""
        issue = self.backend.create_issue(SPEC)
        for call in (
            lambda: self.backend.list_issues(),
            lambda: self.backend.list_comments(issue.number),
            lambda: self.backend.create_comment(issue.number, "x"),
        ):
            self.fake.fail_next(503, "/issues")
            with self.assertRaises(ForgeUnavailable):
                call()


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
