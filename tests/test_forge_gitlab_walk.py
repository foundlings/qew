"""Walks of the GitLab backend through the real urllib transport against the offline fake."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


import socket
import unittest
from unittest import mock

from qew import forge_http
from dataclasses import replace

from qew.forge import (
    COLUMNS,
    ForgeAuth,
    ForgeRejected,
    ForgeUnavailable,
    IssueSpec,
    PullRequestSpec,
    Unsupported,
    marker_for,
)
from qew.forge_gitlab import GitLab
from qew.ledger_config import Mirror
from tests.fake_gitlab import TOKEN, FakeGitLab

PATH = "group/sub/repo"


def spec(title="Title", body="", labels=(), open=True, assignee="", close_reason=""):
    return IssueSpec(title, body, tuple(labels), open, close_reason, assignee)


class GitLabBase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitLab().start()
        self.addCleanup(self.fake.stop)
        self.forge = self.backend()

    def backend(self, env=None, project=None, api=True):
        options = {"api": self.fake.api} if api else {}
        mirror = Mirror("gitlab", f"{self.fake.host}/{PATH}", project, "", options)
        return GitLab(mirror, forge_http.urllib_transport(timeout=5), {"GITLAB_TOKEN": TOKEN} if env is None else env)

    def project(self):
        return self.fake.project(PATH)

    def issue_with(self, **kwargs):
        return self.forge.create_issue(spec(body=marker_for("A1"), **kwargs))



class GitLabWalk(GitLabBase):
    # ----- issues -----

    def test_create_find_read_round_trip(self):
        created = self.forge.create_issue(spec("First", marker_for("A1") + "\nbody", ("kind:task",)))
        self.assertEqual((created.number, created.open, created.title), (1, True, "First"))
        self.assertEqual(created.labels, ("kind:task",))
        self.assertEqual(created.assignee, "")
        self.assertEqual(created.node_id, str(self.project().issues[0]["id"]))
        self.assertTrue(created.url.endswith(f"/{PATH}/-/issues/1"))
        self.assertEqual(self.forge.find_issue(marker_for("A1")), created)
        self.assertEqual(self.forge.read_issue(1), created)
        self.assertIsNone(self.forge.find_issue(marker_for("ZZ")))
        sent = self.fake.requests("GET", "/issues")[0]
        self.assertEqual(sent["query"]["in"], "description")
        self.assertIn("group%2Fsub%2Frepo", sent["raw_path"])
        self.assertEqual(sent["headers"]["private-token"], TOKEN)

    def test_find_verifies_the_marker_in_what_the_forge_returns(self):
        self.forge.create_issue(spec("Other", "no marker here"))
        self.fake.search_returns_all = True
        self.assertIsNone(self.forge.find_issue(marker_for("A1")))

    def test_find_pages_through_results_and_bounds_the_walk(self):
        self.fake.page_size = 2
        self.fake.search_returns_all = True
        for index in range(5):
            self.forge.create_issue(spec(f"n{index}", marker_for(f"X{index}")))
        self.assertEqual(self.forge.find_issue(marker_for("X0")).number, 1)
        self.fake.endless_pages = True
        with mock.patch.object(forge_http, "MAX_PAGES", 3):
            with self.assertRaises(ForgeRejected):
                self.forge.find_issue(marker_for("missing"))
        self.assertEqual(len(self.fake.requests("GET", "/issues")), 3 + 3)

    def test_update_closes_and_reopens(self):
        issue = self.issue_with()
        closed = self.forge.update_issue(issue.number, spec("T2", "b2", open=False, close_reason="not_planned"))
        self.assertEqual((closed.open, closed.title, closed.body), (False, "T2", "b2"))
        self.assertEqual(self.project().issues[0]["state"], "closed")
        reopened = self.forge.update_issue(issue.number, spec("T2", "b2"))
        self.assertTrue(reopened.open)

    def test_labels_are_replaced_but_board_labels_survive(self):
        issue = self.issue_with(labels=("old", "keep"))
        self.project().issues[0]["labels"].append("board::Ready")
        updated = self.forge.update_issue(issue.number, spec(labels=("new", "keep")))
        self.assertEqual(sorted(updated.labels), ["board::Ready", "keep", "new"])
        cleared = self.forge.update_issue(issue.number, spec(labels=()))
        self.assertEqual(cleared.labels, ("board::Ready",))

    def test_assignee_resolved_set_and_cleared(self):
        issue = self.issue_with(assignee="alice")
        self.assertEqual(issue.assignee, "alice")
        self.assertEqual(self.project().issues[0]["assignees"], [11])
        self.assertEqual(self.forge.update_issue(issue.number, spec(assignee="bob")).assignee, "bob")
        self.assertEqual(self.forge.update_issue(issue.number, spec(assignee="")).assignee, "")
        with self.assertRaises(ForgeRejected):
            self.forge.update_issue(issue.number, spec(assignee="mallory"))

    def test_label_with_a_comma_is_refused_before_any_request(self):
        before = len(self.fake.log)
        with self.assertRaises(ForgeRejected):
            self.forge.create_issue(spec(labels=("a,b",)))
        self.assertEqual(len(self.fake.log), before)

    def test_malformed_forge_json_is_rejected_not_defaulted(self):
        self.issue_with()
        self.fake.malformed_issue_reads = True
        with self.assertRaises(ForgeRejected):
            self.forge.read_issue(1)

    # ----- dependencies -----

    def test_link_dependency_is_idempotent(self):
        first = self.issue_with()
        second = self.forge.create_issue(spec("Second"))
        self.forge.link_dependency(second.number, first.number)
        self.forge.link_dependency(second.number, first.number)
        self.assertEqual(self.project().links, [(2, 1, "is_blocked_by")])
        body = self.fake.requests("POST", "/issues/2/links")[0]["body"]
        self.assertEqual(body["target_issue_iid"], 1)
        self.assertEqual(body["link_type"], "is_blocked_by")

    def test_link_dependency_other_failures_are_not_swallowed(self):
        self.issue_with()
        with self.assertRaises(ForgeRejected) as caught:
            self.forge.link_dependency(1, 99)
        self.assertEqual(caught.exception.status, 404)

    # ----- boards -----

    def test_ensure_board_creates_once_and_place_sets_exactly_one_column(self):
        board = self.forge.ensure_board(COLUMNS)
        self.assertEqual(board.columns, COLUMNS)
        self.assertTrue(board.url.endswith(f"/{PATH}/-/boards/{board.id}"))
        stored = self.project().boards
        self.assertEqual([b["name"] for b in stored], ["repo"])
        self.assertEqual([x["label"]["name"] for x in stored[0]["lists"]], [f"board::{c}" for c in COLUMNS])
        self.assertEqual(self.forge.ensure_board(COLUMNS), board)
        self.assertEqual(len(self.project().boards), 1)
        self.assertEqual(len(self.project().boards[0]["lists"]), len(COLUMNS))

        issue = self.issue_with(labels=("kind:task",))
        self.forge.place_on_board(board, issue.number, "Ready")
        self.forge.place_on_board(board, issue.number, "In progress")
        self.assertEqual(sorted(self.forge.read_issue(1).labels), ["board::In progress", "kind:task"])
        writes = len(self.fake.requests("PUT", "/issues/1"))
        self.forge.place_on_board(board, issue.number, "In progress")
        self.assertEqual(len(self.fake.requests("PUT", "/issues/1")), writes)

    def test_board_is_named_by_the_mirror_project_option(self):
        self.backend(project="Roadmap").ensure_board(COLUMNS)
        self.assertEqual([b["name"] for b in self.project().boards], ["Roadmap"])

    def test_board_id_round_trips_as_the_project_option(self):
        first = self.forge.ensure_board(COLUMNS)
        self.assertEqual(first.id, str(self.project().boards[0]["id"]))
        second = self.backend(project=first.id).ensure_board(COLUMNS)
        self.assertEqual(second, first)
        self.assertEqual(len(self.project().boards), 1)
        self.assertEqual(len(self.project().boards[0]["lists"]), len(COLUMNS))

    def test_existing_board_with_the_same_name_is_not_duplicated(self):
        self.backend(project="Roadmap").ensure_board(COLUMNS)
        self.backend(project="Roadmap").ensure_board(COLUMNS)
        self.assertEqual(len(self.project().boards), 1)

    def test_placing_in_an_unknown_column_is_refused(self):
        board = self.forge.ensure_board(("Ready",))
        issue = self.issue_with()
        with self.assertRaises(ForgeRejected):
            self.forge.place_on_board(board, issue.number, "Nope")

    # ----- merge requests -----

    def open_mr(self, draft=True):
        return self.forge.open_pull_request(PullRequestSpec("feature", "main", "Add it", "body", draft))

    def test_open_read_ready_merge(self):
        opened = self.open_mr()
        self.assertEqual((opened.number, opened.state, opened.draft), (1, "open", True))
        self.assertEqual((opened.head_branch, opened.base_branch), ("feature", "main"))
        self.assertEqual(self.project().merge_requests[0]["title"], "Draft: Add it")
        self.assertEqual(self.fake.requests("POST", "/merge_requests")[0]["body"]["remove_source_branch"], False)
        ready = self.forge.mark_pull_request_ready(1)
        self.assertFalse(ready.draft)
        self.assertEqual(self.project().merge_requests[0]["title"], "Add it")
        self.assertEqual(self.forge.mark_pull_request_ready(1), ready)
        merged = self.forge.merge_pull_request(1, ready.head_sha)
        self.assertEqual(merged.state, "merged")
        body = self.fake.requests("PUT", "/merge_requests/1/merge")[0]["body"]
        self.assertEqual(body["sha"], ready.head_sha)
        self.assertEqual(body["should_remove_source_branch"], False)
        self.assertEqual(body["squash"], False)

    def test_body_author_and_assignee_round_trip_and_an_edit_changes_only_the_body(self):
        """A backend that truncates, escapes or drops the opener would let a checklist or ownership drift."""
        opened = self.forge.open_pull_request(PullRequestSpec("f", "main", "T", "- [ ] one\r\n", False, "alice"))
        self.assertEqual((opened.body, opened.author, opened.assignee), ("- [ ] one\r\n", "opener", "alice"))
        edited = self.forge.update_pull_request_body(1, "- [x] one\r\n")
        self.assertEqual(edited, replace(opened, body="- [x] one\r\n"))
        self.project().merge_requests[0]["description"] = 4
        with self.assertRaises(ForgeRejected):
            self.forge.read_pull_request(1)

    def test_status_check_rule_needs_a_protected_branch_and_the_project_setting(self):
        """An unreadable rule set must not be read as 'no checks required'."""
        self.assertFalse(self.forge.requires_status_checks("main"))
        self.project().only_allow_merge_if_pipeline_succeeds = True
        self.assertFalse(self.forge.requires_status_checks("main"))
        self.project().protected_branches["main"] = {"name": "main"}
        self.assertTrue(self.forge.requires_status_checks("main"))
        self.fake.fail_next(403, "protected_branches")
        with self.assertRaises(ForgeAuth):
            self.forge.requires_status_checks("main")

    def test_auto_merge_waits_for_the_pipeline_and_a_bypass_merge_is_unsupported(self):
        opened = self.open_mr(draft=False)
        self.assertFalse(opened.auto_merge)
        self.assertTrue(self.forge.enable_auto_merge(1).auto_merge)
        self.assertEqual(self.fake.requests("PUT", "/merge_requests/1/merge")[0]["body"]["merge_when_pipeline_succeeds"], True)
        with self.assertRaises(Unsupported):
            self.forge.merge_pull_request(1, opened.head_sha, bypass_checks=True)
        self.assertEqual(self.forge.merge_pull_request(1, opened.head_sha).merge_sha, "d" * 40)

    def test_open_ready_directly(self):
        self.assertFalse(self.open_mr(draft=False).draft)
        self.assertEqual(self.project().merge_requests[0]["title"], "Add it")

    def test_merge_refuses_a_moved_head(self):
        opened = self.forge.open_pull_request(PullRequestSpec("f", "main", "T", "", False))
        self.fake.push_head(PATH, 1)
        with self.assertRaises(ForgeRejected) as caught:
            self.forge.merge_pull_request(1, opened.head_sha)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.forge.read_pull_request(1).state, "open")

    def test_merge_refuses_a_draft(self):
        opened = self.open_mr()
        with self.assertRaises(ForgeRejected):
            self.forge.merge_pull_request(1, opened.head_sha)

    def test_mergeable_and_checks_are_mapped(self):
        self.open_mr()
        cases = [
            ("mergeable", None, True, "none"),
            ("checking", "pending", None, "pending"),
            ("conflict", "failed", False, "failing"),
            ("ci_must_pass", "running", False, "pending"),
            ("mergeable", "success", True, "passing"),
            ("mergeable", "canceled", True, "failing"),
        ]
        for status, pipeline, mergeable, checks in cases:
            with self.subTest(status=status, pipeline=pipeline):
                self.fake.set_merge_status(PATH, 1, status)
                self.fake.set_pipeline(PATH, 1, pipeline)
                read = self.forge.read_pull_request(1)
                self.assertEqual((read.mergeable, read.checks), (mergeable, checks))

    def test_unknown_pipeline_status_is_rejected(self):
        self.open_mr()
        self.fake.set_pipeline(PATH, 1, "mystery")
        with self.assertRaises(ForgeRejected):
            self.forge.read_pull_request(1)

    # ----- failures -----

    def test_missing_token_is_an_auth_error_before_any_request(self):
        with self.assertRaises(ForgeAuth):
            self.backend(env={}).read_issue(1)
        self.assertEqual(self.fake.log, [])

    def test_wrong_token_is_an_auth_error(self):
        with self.assertRaises(ForgeAuth):
            self.backend(env={"GITLAB_TOKEN": "wrong"}).read_issue(1)

    def test_server_error_is_unavailable(self):
        self.fake.fail_next(503, "/issues")
        with self.assertRaises(ForgeUnavailable):
            self.forge.read_issue(1)

    def test_unreachable_host_is_unavailable(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        mirror = Mirror("gitlab", f"http://127.0.0.1:{port}/{PATH}", None, "", {})
        forge = GitLab(mirror, forge_http.urllib_transport(timeout=2), {"GITLAB_TOKEN": TOKEN})
        with self.assertRaises(ForgeUnavailable):
            forge.read_issue(1)

    def test_default_api_root_is_host_slash_api_v4(self):
        mirror = Mirror("gitlab", f"{self.fake.host}/{PATH}", None, "", {})
        forge = GitLab(mirror, forge_http.urllib_transport(timeout=5), {"GITLAB_TOKEN": TOKEN})
        self.issue_with()
        self.assertEqual(forge.read_issue(1).number, 1)


class GitLabReadSideWalk(GitLabBase):
    def test_listing_pages_oldest_first_with_closed_issues(self):
        """An import must see every issue, closed ones included, in creation order across pages."""
        self.fake.page_size = 2
        first = self.forge.create_issue(spec("one", "b", ("a",), assignee="alice"))
        self.forge.create_issue(spec("two", open=False))
        for n in range(3, 6):
            self.forge.create_issue(spec(f"n{n}"))
        listed = self.forge.list_issues()
        self.assertEqual([i.title for i in listed], ["one", "two", "n3", "n4", "n5"])
        self.assertEqual([i.open for i in listed], [True, False, True, True, True])
        self.assertEqual((listed[0].labels, listed[0].assignee, listed[0].body), (("a",), "alice", "b"))
        self.assertEqual(listed[0], self.forge.read_issue(first.number))
        sent = self.fake.requests("GET", "/issues")
        self.assertEqual({r["query"]["state"] for r in sent}, {"all"})
        self.assertGreaterEqual(len(sent), 3)

    def test_comments_in_order_without_system_notes_and_create_round_trips(self):
        """Pull must replay user comments only; GitLab's own activity lines are not comments."""
        self.fake.page_size = 2
        issue = self.issue_with()
        self.fake.add_note(PATH, issue.number, "alice", "first")
        self.fake.add_note(PATH, issue.number, "gitlab", "added ~label", system=True)
        posted = self.forge.create_comment(issue.number, "second")
        self.fake.add_note(PATH, issue.number, "bob", "third")
        self.fake.add_note(PATH, issue.number, "gitlab", "closed", system=True)
        comments = self.forge.list_comments(issue.number)
        self.assertEqual([c.body for c in comments], ["first", "second", "third"])
        self.assertEqual([c.author for c in comments], ["alice", "qew-bot", "bob"])
        self.assertEqual(comments[1], posted)
        self.assertTrue(all(c.id and c.created for c in comments))

    def test_comment_count_follows_create_comment_on_every_read_path(self):
        """Incremental pull relies on the count moving for user comments only, never system notes."""
        issue = self.issue_with()
        self.assertEqual(issue.comments, 0)
        self.forge.create_comment(issue.number, "hello")
        self.fake.add_note(PATH, issue.number, "gitlab", "added ~label", system=True)
        self.assertEqual(self.forge.read_issue(issue.number).comments, 1)
        self.assertEqual(self.forge.list_issues()[0].comments, 1)
        self.assertEqual(self.forge.update_issue(issue.number, spec(body=marker_for("A1"))).comments, 1)

    def test_null_bodies_read_as_empty(self):
        """A forge sends null for an empty body; that is empty text, not a rejected listing."""
        issue = self.forge.create_issue(spec())
        self.fake.add_note(PATH, issue.number, "alice", None)
        self.assertEqual(self.forge.list_issues()[0].body, "")
        self.assertEqual(self.forge.list_comments(issue.number)[0].body, "")

    def test_listing_longer_than_the_page_bound_is_rejected_not_truncated(self):
        """A silently truncated import would drop issues without anyone noticing."""
        issue = self.issue_with()
        self.fake.add_note(PATH, issue.number, "alice", "x")
        self.fake.endless_pages = True
        with mock.patch.object(forge_http, "MAX_PAGES", 3):
            with self.assertRaises(ForgeRejected):
                self.forge.list_issues()
            with self.assertRaises(ForgeRejected):
                self.forge.list_comments(issue.number)

    def test_malformed_json_is_rejected(self):
        """Garbage from the forge must stop the import, never become an empty listing."""
        issue = self.issue_with()
        for call in (
            lambda: self.forge.list_issues(),
            lambda: self.forge.list_comments(issue.number),
            lambda: self.forge.create_comment(issue.number, "hi"),
        ):
            self.fake.garble_next()
            with self.assertRaises(ForgeRejected):
                call()
        self.fake.add_note(PATH, issue.number, "alice", "ok")
        self.fake.malformed_issue_reads = True
        with self.assertRaises(ForgeRejected):
            self.forge.list_issues()

    def test_server_errors_are_unavailable(self):
        """An outage must read as retry-later, not as an empty forge."""
        issue = self.issue_with()
        for call in (
            lambda: self.forge.list_issues(),
            lambda: self.forge.list_comments(issue.number),
            lambda: self.forge.create_comment(issue.number, "x"),
        ):
            self.fake.fail_next(503, "/issues")
            with self.assertRaises(ForgeUnavailable):
                call()


if __name__ == "__main__":
    unittest.main()
