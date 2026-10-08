"""The Forgejo/Gitea backend through the real urllib transport against the in-memory fake."""

import unittest
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


class ForgejoWalk(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeForgejo().start()
        self.addCleanup(self.fake.stop)
        self.backend = self.build()

    def build(self, env: Optional[Dict[str, str]] = None, **options) -> Forgejo:
        mirror = Mirror("forgejo", self.fake.repo_url, None, "", {"api": self.fake.api, **options})
        return Forgejo(mirror, urllib_transport(timeout=5), {"FORGEJO_TOKEN": TOKEN} if env is None else env)

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


if __name__ == "__main__":
    unittest.main()
