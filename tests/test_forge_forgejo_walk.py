"""The Forgejo/Gitea backend through the real urllib transport against the in-memory fake."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


import unittest
from dataclasses import replace
from typing import Dict, Optional

from qew.forge import (
    ForgeAuth,
    ForgeRejected,
    ForgeUnavailable,
    IssueSpec,
    PullRequestSpec,
    Unsupported,
    marker_for,
)
from qew.forge_forgejo import Forgejo
from qew.forge_http import MAX_PAGES, urllib_transport
from qew.ledger_config import Mirror
from tests.fake_forgejo import TOKEN, FakeForgejo

MARKER = marker_for("TST-01ABC")


def spec(title: str = "Title", labels=("qew:ready",), open: bool = True, **extra) -> IssueSpec:
    return IssueSpec(title, f"body\n{MARKER}", tuple(labels), open, **extra)


class ForgejoBase(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        self.backend = self.build()

    def build(self, env: Optional[Dict[str, str]] = None, **options) -> Forgejo:
        mirror = Mirror("forgejo", self.fake.repo_url, None, "", {"api": self.fake.api, **options})
        return Forgejo(mirror, urllib_transport(timeout=5), {"FORGEJO_TOKEN": TOKEN} if env is None else env)



class ForgejoWalk(ForgejoBase):
    def test_create_find_update_read_round_trip(self) -> None:
        self.assertIsNone(self.backend.find_issue(MARKER))
        created = self.backend.create_issue(spec(assignee="alice"))
        self.assertTrue(created.open)
        self.assertEqual(created.title, "Title")
        self.assertEqual(created.labels, ("qew:ready",))
        self.assertEqual(created.assignee, "alice")
        self.assertTrue(created.url.endswith(f"/issues/{created.number}"))
        found = self.backend.find_issue(MARKER)
        self.assertEqual(found, created)
        self.assertIsNone(self.backend.find_issue(marker_for("TST-OTHER")))
        updated = self.backend.update_issue(created.number, spec("Renamed", assignee=""))
        self.assertEqual((updated.title, updated.assignee), ("Renamed", ""))
        self.assertEqual(self.backend.read_issue(created.number), updated)

    def test_find_requires_the_marker_in_the_body_not_just_the_search_hit(self) -> None:
        self.backend.create_issue(IssueSpec(f"mentions {MARKER}", "no marker here", (), True))
        self.assertIsNone(self.backend.find_issue(MARKER))

    def test_close_and_reopen_with_a_reason(self) -> None:
        created = self.backend.create_issue(spec())
        closed = self.backend.update_issue(created.number, spec(open=False, close_reason="not_planned"))
        self.assertFalse(closed.open)
        self.assertEqual(self.fake.issues[created.number]["state"], "closed")
        self.assertIs(self.backend.find_issue(MARKER).open, False)
        reopened = self.backend.update_issue(created.number, spec(open=True))
        self.assertTrue(reopened.open)

    def test_create_closed_issue(self) -> None:
        created = self.backend.create_issue(spec(open=False))
        self.assertFalse(created.open)

    def test_labels_replace_the_whole_set_and_are_created_by_name(self) -> None:
        created = self.backend.create_issue(spec(labels=("a", "b")))
        self.assertEqual(sorted(created.labels), ["a", "b"])
        updated = self.backend.update_issue(created.number, spec(labels=("b", "c")))
        self.assertEqual(sorted(updated.labels), ["b", "c"])
        self.assertEqual(sorted(self.fake.issue_labels(created.number)), ["b", "c"])
        cleared = self.backend.update_issue(created.number, spec(labels=()))
        self.assertEqual(cleared.labels, ())
        self.assertEqual(sorted(self.fake.labels.values()), ["a", "b", "c"], "existing labels are reused, not duplicated")

    def test_label_lookup_pages_through_every_label(self) -> None:
        self.fake.max_limit = 2
        names = tuple(f"label-{n}" for n in range(5))
        self.backend.create_issue(spec(labels=names))
        again = self.backend.create_issue(IssueSpec("Two", "other", names, True))
        self.assertEqual(sorted(again.labels), list(names))
        self.assertEqual(len(self.fake.labels), 5, "no label was re-created because a later page was missed")

    def test_dependency_link_is_idempotent(self) -> None:
        blocked = self.backend.create_issue(spec("Blocked"))
        blocker = self.backend.create_issue(IssueSpec("Blocker", "x", (), True))
        self.backend.link_dependency(blocked.number, blocker.number)
        self.backend.link_dependency(blocked.number, blocker.number)
        self.assertEqual(self.fake.dependencies[blocked.number], [blocker.number])

    def test_dependency_on_a_missing_issue_is_rejected(self) -> None:
        blocked = self.backend.create_issue(spec())
        with self.assertRaises(ForgeRejected) as caught:
            self.backend.link_dependency(blocked.number, 999)
        self.assertEqual(caught.exception.status, 404)

    def test_boards_are_unsupported(self) -> None:
        with self.assertRaises(Unsupported) as caught:
            self.backend.ensure_board(("Draft", "Ready"))
        self.assertEqual(caught.exception.capability, "ensure_board")
        from qew.forge import Board

        with self.assertRaises(Unsupported):
            self.backend.place_on_board(Board("1", "u", ("Draft",)), 1, "Draft")

    def open_pull(self, draft: bool = True):
        self.fake.set_head("feature", "a" * 40)
        return self.backend.open_pull_request(PullRequestSpec("feature", "main", "Add feature", "body", draft))

    def test_pull_request_open_ready_merge(self) -> None:
        pull = self.open_pull(draft=True)
        self.assertEqual((pull.state, pull.draft, pull.head_sha), ("open", True, "a" * 40))
        self.assertEqual((pull.head_branch, pull.base_branch, pull.mergeable), ("feature", "main", True))
        self.assertEqual(pull.checks, "none")
        self.assertEqual(self.fake.pulls[pull.number]["title"], "WIP: Add feature")
        ready = self.backend.mark_pull_request_ready(pull.number)
        self.assertFalse(ready.draft)
        self.assertEqual(self.fake.pulls[pull.number]["title"], "Add feature")
        self.assertFalse(self.backend.mark_pull_request_ready(pull.number).draft, "ready is idempotent")
        merged = self.backend.merge_pull_request(pull.number, "a" * 40)
        self.assertEqual(merged.state, "merged")
        self.assertTrue(self.fake.pulls[pull.number]["merged"])

    def test_body_author_and_assignee_round_trip_and_an_edit_changes_only_the_body(self) -> None:
        """A backend that truncates, escapes or drops the opener would let a checklist or ownership drift."""
        self.fake.set_head("feature", "a" * 40)
        opened = self.backend.open_pull_request(PullRequestSpec("feature", "main", "T", "- [ ] one\r\n", False, "alice"))
        self.assertEqual((opened.body, opened.author, opened.assignee), ("- [ ] one\r\n", "opener", "alice"))
        edited = self.backend.update_pull_request_body(opened.number, "- [x] one\r\n")
        self.assertEqual(edited, replace(opened, body="- [x] one\r\n"))
        self.fake.pulls[opened.number]["body"] = 4
        with self.assertRaises(ForgeRejected):
            self.backend.read_pull_request(opened.number)

    def test_status_check_rule_comes_from_branch_protection_and_an_unreadable_one_is_an_error(self) -> None:
        """An unreadable rule set must not be read as 'no checks required'."""
        self.assertFalse(self.backend.requires_status_checks("main"))
        self.fake.protections["main"] = {"enable_status_check": False}
        self.assertFalse(self.backend.requires_status_checks("main"))
        self.fake.protections["main"] = {"enable_status_check": True}
        self.assertTrue(self.backend.requires_status_checks("main"))
        self.fake.fail.append((403, "no"))
        with self.assertRaises(ForgeAuth):
            self.backend.requires_status_checks("main")

    def test_auto_merge_is_unsupported_and_a_bypass_merge_forces_the_merge(self) -> None:
        pull = self.open_pull(draft=False)
        with self.assertRaises(Unsupported):
            self.backend.enable_auto_merge(pull.number)
        merged = self.backend.merge_pull_request(pull.number, "a" * 40, bypass_checks=True)
        self.assertEqual(merged.merge_sha, "e" * 40)
        sent = [body for method, path, body in self.fake.requests if path.endswith("/merge")]
        self.assertEqual(sent, [{"Do": "merge", "head_commit_id": "a" * 40, "force_merge": True}])

    def test_pull_request_not_draft_keeps_its_title(self) -> None:
        self.fake.set_head("feature", "b" * 40)
        pull = self.backend.open_pull_request(PullRequestSpec("feature", "main", "Plain", "b", False))
        self.assertFalse(pull.draft)
        self.assertEqual(self.fake.pulls[pull.number]["title"], "Plain")

    def test_merge_refuses_a_moved_head(self) -> None:
        pull = self.open_pull(draft=False)
        self.fake.set_head("feature", "c" * 40)
        with self.assertRaises(ForgeRejected) as caught:
            self.backend.merge_pull_request(pull.number, "a" * 40)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.backend.read_pull_request(pull.number).state, "open")
        sent = [body for method, path, body in self.fake.requests if path.endswith("/merge")]
        self.assertEqual(sent, [{"Do": "merge", "head_commit_id": "a" * 40}])

    def test_check_states_map_from_the_combined_commit_status(self) -> None:
        pull = self.open_pull(draft=False)
        for state, expected in (("success", "passing"), ("failure", "failing"), ("error", "failing"), ("pending", "pending")):
            self.fake.statuses["a" * 40] = (state, 1)
            self.assertEqual(self.backend.read_pull_request(pull.number).checks, expected, state)
        self.fake.statuses["a" * 40] = ("pending", 0)
        self.assertEqual(self.backend.read_pull_request(pull.number).checks, "none")
        self.fake.statuses["a" * 40] = ("mystery", 1)
        with self.assertRaises(ForgeRejected):
            self.backend.read_pull_request(pull.number)

    def test_open_pull_request_for_a_missing_branch_is_rejected(self) -> None:
        with self.assertRaises(ForgeRejected):
            self.backend.open_pull_request(PullRequestSpec("ghost", "main", "T", "b", False))

    def test_unauthorized_token_is_forge_auth(self) -> None:
        with self.assertRaises(ForgeAuth):
            self.build(env={"FORGEJO_TOKEN": "wrong"}).read_issue(1)

    def test_missing_token_is_forge_auth_without_a_request(self) -> None:
        with self.assertRaises(ForgeAuth):
            self.build(env={}).find_issue(MARKER)
        self.assertEqual(self.fake.requests, [])

    def test_token_is_sent_as_a_token_header_and_never_in_the_url(self) -> None:
        self.backend.find_issue(MARKER)
        self.assertTrue(all(TOKEN not in path for _, path, _ in self.fake.requests))

    def test_server_error_is_forge_unavailable(self) -> None:
        self.fake.fail.append((503, "down"))
        with self.assertRaises(ForgeUnavailable):
            self.backend.find_issue(MARKER)

    def test_unreachable_server_is_forge_unavailable(self) -> None:
        backend = self.build(api="http://127.0.0.1:1/api/v1")
        with self.assertRaises(ForgeUnavailable):
            backend.find_issue(MARKER)

    def test_malformed_issue_json_is_rejected_not_defaulted(self) -> None:
        created = self.backend.create_issue(spec())
        for broken in (b'{"number": 1}', b'["not", "an", "object"]', b"not json", b'{"number":1,"state":"weird"}'):
            self.fake.raw_issue_body = broken
            with self.assertRaises(ForgeRejected, msg=broken):
                self.backend.read_issue(created.number)

    def test_endless_pagination_is_bounded(self) -> None:
        self.backend.create_issue(spec(labels=("only",)))
        self.fake.endless = True
        self.fake.requests.clear()
        with self.assertRaises(ForgeRejected):
            self.backend.create_issue(spec(labels=("another",)))
        label_pages = [path for _, path, _ in self.fake.requests if "/labels?" in path]
        self.assertLessEqual(len(label_pages), MAX_PAGES)


class ForgejoReadSideWalk(ForgejoBase):
    def test_listing_pages_oldest_first_with_closed_issues_and_without_pull_requests(self) -> None:
        """An import must see every issue, closed ones included, and never mistake a pull request for one."""
        self.fake.max_limit = 2
        first = self.backend.create_issue(spec("one", labels=("a",), assignee="alice"))
        self.fake.branches["topic"] = "a" * 40
        self.backend.open_pull_request(PullRequestSpec("topic", "main", "T", "b", False))
        self.backend.create_issue(spec("two", open=False))
        for n in range(3, 6):
            self.backend.create_issue(spec(f"n{n}"))
        listed = self.backend.list_issues()
        self.assertEqual([i.title for i in listed], ["one", "two", "n3", "n4", "n5"])
        self.assertEqual([i.open for i in listed], [True, False, True, True, True])
        self.assertEqual((listed[0].labels, listed[0].assignee), (("a",), "alice"))
        self.assertEqual(listed[0], self.backend.read_issue(first.number))
        self.assertGreaterEqual(len([p for _, p, _ in self.fake.requests if "state=all" in p and "type=issues" in p]), 3)

    def test_comments_come_back_in_creation_order_and_create_round_trips(self) -> None:
        """Pull must replay a thread in order, and a posted comment must read back as the same comment."""
        self.fake.max_limit = 2
        issue = self.backend.create_issue(spec())
        self.fake.add_comment(issue.number, "alice", "first")
        posted = self.backend.create_comment(issue.number, "second")
        self.fake.add_comment(issue.number, "bob", "third")
        comments = self.backend.list_comments(issue.number)
        self.assertEqual([c.body for c in comments], ["first", "second", "third"])
        self.assertEqual([c.author for c in comments], ["alice", "qew-bot", "bob"])
        self.assertEqual(comments[1], posted)
        self.assertTrue(all(c.id and c.created for c in comments))

    def test_comment_count_follows_create_comment_on_every_read_path(self) -> None:
        """Incremental pull relies on the count moving whenever a comment is posted."""
        issue = self.backend.create_issue(spec())
        self.assertEqual(issue.comments, 0)
        self.backend.create_comment(issue.number, "hello")
        self.assertEqual(self.backend.read_issue(issue.number).comments, 1)
        self.assertEqual(self.backend.list_issues()[0].comments, 1)
        self.assertEqual(self.backend.update_issue(issue.number, spec()).comments, 1)

    def test_null_bodies_read_as_empty(self) -> None:
        """A forge sends null for an empty body; that is empty text, not a rejected listing."""
        issue = self.backend.create_issue(spec())
        self.fake.issues[issue.number]["body"] = None
        self.fake.add_comment(issue.number, "alice", None)
        self.assertEqual(self.backend.list_issues()[0].body, "")
        self.assertEqual(self.backend.list_comments(issue.number)[0].body, "")

    def test_listing_longer_than_the_page_bound_is_rejected_not_truncated(self) -> None:
        """A silently truncated import would drop issues without anyone noticing."""
        issue = self.backend.create_issue(spec())
        self.fake.add_comment(issue.number, "alice", "x")
        self.fake.endless = True
        with self.assertRaises(ForgeRejected):
            self.backend.list_issues()
        with self.assertRaises(ForgeRejected):
            self.backend.list_comments(issue.number)

    def test_malformed_json_is_rejected(self) -> None:
        """Garbage from the forge must stop the import, never become an empty listing."""
        issue = self.backend.create_issue(spec())
        for call in (
            lambda: self.backend.list_issues(),
            lambda: self.backend.list_comments(issue.number),
            lambda: self.backend.create_comment(issue.number, "hi"),
        ):
            self.fake.fail.append((200, "<html>not json"))
            with self.assertRaises(ForgeRejected):
                call()
        self.fake.fail.append((200, '{"id": "x"}'))
        with self.assertRaises(ForgeRejected):
            self.backend.create_comment(issue.number, "hi")

    def test_server_errors_are_unavailable(self) -> None:
        """An outage must read as retry-later, not as an empty forge."""
        issue = self.backend.create_issue(spec())
        self.fake.down = True
        for call in (
            lambda: self.backend.list_issues(),
            lambda: self.backend.list_comments(issue.number),
            lambda: self.backend.create_comment(issue.number, "x"),
        ):
            with self.assertRaises(ForgeUnavailable):
                call()


if __name__ == "__main__":
    unittest.main()
