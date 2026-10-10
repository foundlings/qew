"""An in-memory Forge for sync-logic walks: records calls, injects failures, forgets nothing."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


from datetime import datetime
from collections.abc import Callable

from qew import instants

from qew.forge import (
    Milestone,
    MilestoneSpec,
    Card,
    Board,
    Forge,
    ForgeError,
    ForgeRejected,
    ForgeUnavailable,
    IssueSpec,
    Placement,
    PullRequestSpec,
    RemoteBranch,
    RemoteComment,
    RemoteIssue,
    RemotePullRequest,
    Unsupported,
)


class MemoryForge(Forge):
    name = "memory"

    def __init__(self, host: str = "memory.test", boards: bool = True, links: bool = True) -> None:
        self.host = host
        self.issues: dict[int, RemoteIssue] = {}
        self.calls: list[tuple] = []
        self.links: list[tuple[int, int]] = []
        self.columns: dict[int, str] = {}
        self.priorities: dict[int, str] = {}
        self.down = False
        self.board_id = "B1"
        self.milestones: dict[int, Milestone] = {}
        self.milestone_of: dict[int, int] = {}  # issue number -> milestone number
        self.supports_milestones = True
        self.foreign_cards: list[Card] = []
        self.stubborn_cards = False  # a card delete the forge accepts but does not carry out
        self.hide_next_find = False
        self.reject_title: str | None = None
        self.supports_boards = boards
        self.supports_links = links
        self.comments: dict[int, list[RemoteComment]] = {}
        self.defined_labels: set = set()  # label definitions nothing carries
        self.pulls: dict[int, RemotePullRequest] = {}
        self.pull_specs: dict[int, PullRequestSpec] = {}
        self.merged: list[tuple[int, str]] = []
        self.raise_on: dict[str, ForgeError] = {}  # call name -> the error that call raises
        self.merge_calls: list[tuple[int, str, bool]] = []  # (number, sha, bypass_checks)
        self.body_writes: list[str] = []
        self.assign_refused = False  # the forge opens the pull request but will not assign the login
        self.close_ignored = False  # accept the close yet stay open
        self.ready_ignored = False  # accept mark-ready yet stay a draft
        self.merge_lag_reads = 0  # reads after a merge call that still show the pull request open
        self.merge_lag: dict[int, int] = {}
        self.after_read: list[Callable[[MemoryForge], None]] = []  # run once after the next read, as a concurrent edit
        self.body_write_ignored = False
        self.required_checks: dict[str, bool] = {}
        self.rules_error: ForgeError | None = None
        self.supports_rules = True
        self.supports_auto_merge = True
        self.auto_merge_ignored = False
        self.bypass_supported = True
        self.branches: dict[str, list[str]] = {}  # branch -> branches whose history already holds its tip
        self.default_branch = "main"
        self.protected_branches: set = set()
        self.supports_branches = True
        self.deleted_branches: list[str] = []
        self.stubborn_branches: set = set()  # a delete the forge accepts but does not carry out
        self.clock: Callable[[], datetime] = instants.now  # the forge's clock; walks move it to place edits in time
        self.updated: dict[int, str] = {}  # issue number -> updated_at, bumped by every edit, comment and label change
        self.list_requests = 0  # issue listings served
        self.listed: list[list[int]] = []  # the issue numbers each listing returned

    def _touch(self, number: int) -> None:
        self.updated[number] = instants.format_instant(self.clock())

    def delete_issue(self, number: int) -> None:
        """A human deletes the issue on the forge, outside qew."""
        self.issues.pop(number)
        self.comments.pop(number, None)

    def _live(self, name: str) -> None:
        self.calls.append((name,))
        if name in self.raise_on:
            raise self.raise_on[name]
        if self.down:
            raise ForgeUnavailable("forge is down")

    def _issue(self, number: int, spec: IssueSpec) -> RemoteIssue:
        return RemoteIssue(
            number, f"https://{self.host}/o/r/issues/{number}", f"N{number}", spec.open,
            spec.title, spec.body, spec.labels, spec.assignee, milestone=self.milestone_of.get(number, 0),
        )

    def find_issue(self, marker: str, recent: bool = False) -> RemoteIssue | None:
        self._live("find")
        if self.hide_next_find:  # a concurrent creator the first lookup could not see yet
            self.hide_next_find = False
            return None
        return next((i for i in self.issues.values() if marker in i.body), None)

    def create_issue(self, spec: IssueSpec) -> RemoteIssue:
        self._live("create")
        if self.reject_title and self.reject_title in spec.title:
            raise ForgeRejected(422, "no")
        number = len(self.issues) + 1
        self.issues[number] = self._issue(number, spec)
        self._touch(number)
        return self.issues[number]

    def update_issue(self, number: int, spec: IssueSpec) -> RemoteIssue:
        self._live("update")
        self.issues[number] = self._issue(number, spec)
        self._touch(number)
        return self.issues[number]

    def link_dependency(self, number: int, blocked_by: int) -> None:
        if not self.supports_links:
            raise Unsupported("link_dependency")
        self._live("link")
        if (number, blocked_by) not in self.links:
            self.links.append((number, blocked_by))

    def ensure_board(self, columns: tuple[str, ...], managed_filter: str = "") -> Board:
        if not self.supports_boards:
            raise Unsupported("ensure_board")
        self._live("board")
        return Board(self.board_id, f"https://{self.host}/o/r/projects/{self.board_id}", columns)

    def place_on_board(self, board: Board, number: int, column: str, priority: str = "") -> None:
        self._live("place")
        self.columns[number] = column
        self.priorities[number] = priority

    def read_placement(self, board: Board, number: int) -> Placement | None:
        if not self.supports_boards:
            raise Unsupported("read_placement")
        self._live("read_placement")
        if number not in self.columns:
            return None
        return Placement(self.columns[number], self.priorities.get(number, ""))

    def _milestones(self, name: str) -> None:
        if not self.supports_milestones:
            raise Unsupported(name)
        self._live(name)

    def find_milestone(self, title: str) -> Milestone | None:
        self._milestones("find_milestone")
        return next((m for m in self.milestones.values() if m.title == title), None)

    def create_milestone(self, spec: MilestoneSpec) -> Milestone:
        self._milestones("create_milestone")
        number = len(self.milestones) + 1
        self.milestones[number] = Milestone(number, spec.title, spec.description, spec.due, True)
        return self.milestones[number]

    def update_milestone(self, number: int, spec: MilestoneSpec) -> Milestone:
        self._milestones("update_milestone")
        from dataclasses import replace
        self.milestones[number] = replace(self.milestones[number], title=spec.title, description=spec.description, due=spec.due)
        return self.milestones[number]

    def read_milestone(self, number: int) -> Milestone:
        self._milestones("read_milestone")
        return self.milestones[number]

    def set_milestone_open(self, number: int, open: bool) -> Milestone:
        self._milestones("set_milestone_open")
        from dataclasses import replace
        self.milestones[number] = replace(self.milestones[number], open=open)
        return self.milestones[number]

    def assign_milestone(self, number: int, milestone: int) -> RemoteIssue:
        self._milestones("assign_milestone")
        from dataclasses import replace
        self.milestone_of[number] = milestone
        self.issues[number] = replace(self.issues[number], milestone=milestone)
        self._touch(number)
        return self.issues[number]

    def read_board_cards(self, board: Board) -> tuple[Card, ...]:
        if not self.supports_boards:
            raise Unsupported("read_board_cards")
        self._live("read_cards")
        local = tuple(Card(f"C{n}", "o/r", n, c, self.priorities.get(n, "")) for n, c in sorted(self.columns.items()))
        return local + tuple(self.foreign_cards)

    def delete_card(self, board: Board, card_id: str) -> None:
        self._live("delete_card")
        if self.stubborn_cards:
            return
        for card in list(self.foreign_cards):
            if card.id == card_id:
                self.foreign_cards.remove(card)
        if card_id.startswith("C") and card_id[1:].isdigit():
            self.columns.pop(int(card_id[1:]), None)

    def remove_from_board(self, board: Board, number: int) -> None:
        self._live("remove")
        self.columns.pop(number, None)
        self.priorities.pop(number, None)

    def count(self, name: str) -> int:
        return sum(1 for call in self.calls if call[0] == name)

    def open_pull_request(self, spec: PullRequestSpec) -> RemotePullRequest:
        self._live("open_pr")
        number = len(self.pulls) + 1
        self.pull_specs[number] = spec
        self.pulls[number] = RemotePullRequest(
            number, f"https://{self.host}/o/r/pulls/{number}", "open", spec.draft, f"sha{number}", spec.head, spec.base, True,
            "passing", spec.body, "opener", "" if self.assign_refused else spec.assignee,
        )
        return self.pulls[number]

    def read_pull_request(self, number: int) -> RemotePullRequest:
        self._live("read_pr")
        if self.merge_lag.get(number, -1) == 0:
            self.pulls[number] = self._with(number, state="merged", merge_sha="m" * 40)
        elif number in self.merge_lag:
            self.merge_lag[number] -= 1
        seen = self.pulls[number]
        for hook in self.after_read:
            hook(self)
        self.after_read.clear()
        return seen

    def mark_pull_request_ready(self, number: int) -> RemotePullRequest:
        self._live("ready_pr")
        if not self.ready_ignored:
            self.pulls[number] = self._with(number, draft=False)
        return self.pulls[number]

    def merge_pull_request(self, number: int, expected_head_sha: str, bypass_checks: bool = False) -> RemotePullRequest:
        self._live("merge_pr")
        if bypass_checks and not self.bypass_supported:
            raise Unsupported("merge_pull_request with bypass_checks")
        if self.pulls[number].head_sha != expected_head_sha:
            raise ForgeRejected(409, "head moved")
        self.merged.append((number, expected_head_sha))
        self.merge_calls.append((number, expected_head_sha, bypass_checks))
        if self.merge_lag.get(number) is None and self.merge_lag_reads:
            self.merge_lag[number] = self.merge_lag_reads
        if number not in self.merge_lag:
            self.pulls[number] = self._with(number, state="merged", merge_sha="m" * 40)
        return self.pulls[number]

    def find_open_pull_requests(self, head: str) -> list[RemotePullRequest]:
        self._live("find_pr")
        return [pull for pull in self.pulls.values() if pull.state == "open" and pull.head_branch == head]

    def close_pull_request(self, number: int) -> RemotePullRequest:
        self._live("close_pr")
        if not self.close_ignored:
            self.pulls[number] = self._with(number, state="closed")
        return self.pulls[number]

    def update_pull_request_body(self, number: int, body: str) -> RemotePullRequest:
        self._live("update_pr")
        if not self.body_write_ignored:
            self.pulls[number] = self._with(number, body=body)
        self.body_writes.append(body)
        return self.pulls[number]

    def requires_status_checks(self, branch: str) -> bool:
        self._live("rules")
        if self.rules_error is not None:
            raise self.rules_error
        if not self.supports_rules:
            raise Unsupported("requires_status_checks")
        return self.required_checks.get(branch, False)

    def enable_auto_merge(self, number: int) -> RemotePullRequest:
        self._live("auto_merge")
        if not self.supports_auto_merge:
            raise Unsupported("enable_auto_merge")
        if not self.auto_merge_ignored:
            self.pulls[number] = self._with(number, auto_merge=True)
        return self.pulls[number]

    def add_branch(self, name: str, contained_in: tuple[str, ...] = ()) -> None:
        """A branch on the forge; `contained_in` names the branches that already hold its tip."""
        self.branches[name] = list(contained_in)

    def list_branches(self) -> list[RemoteBranch]:
        self._live("list_branches")
        if not self.supports_branches:
            raise Unsupported("list_branches")
        return [RemoteBranch(n, n == self.default_branch, n in self.protected_branches) for n in self.branches]

    def branch_is_merged(self, name: str, base: str) -> bool:
        self._live("branch_is_merged")
        if not self.supports_branches:
            raise Unsupported("branch_is_merged")
        return name == base or base in self.branches[name]

    def delete_branch(self, name: str) -> None:
        self._live("delete_branch")
        if not self.supports_branches:
            raise Unsupported("delete_branch")
        if name not in self.stubborn_branches:
            self.branches.pop(name, None)
        self.deleted_branches.append(name)

    def list_open_pull_heads(self) -> list[str]:
        self._live("list_open_pull_heads")
        if not self.supports_branches:
            raise Unsupported("list_open_pull_heads")
        return [pull.head_branch for pull in self.pulls.values() if pull.state == "open"]

    def edit_pull(self, number: int, **changes) -> None:
        """Change the remote pull request behind qew's back, as a teammate or a CI run would."""
        self.pulls[number] = self._with(number, **changes)

    def _with(self, number: int, **changes) -> RemotePullRequest:
        from dataclasses import replace

        return replace(self.pulls[number], **changes)

    # -- read side
    def list_issues(self, since: str = ""):
        self._live("list")
        self.list_requests += 1
        found = [self._counted(i) for n, i in sorted(self.issues.items()) if self.updated.get(n, "") >= since]
        self.listed.append([i.number for i in found])
        return found

    def _counted(self, issue: RemoteIssue) -> RemoteIssue:
        from dataclasses import replace

        return replace(issue, comments=len(self.comments.get(issue.number, [])))

    def list_comments(self, number: int):
        self._live("comments")
        return list(self.comments.get(number, []))

    def list_repo_labels(self):
        self._live("labels")
        return sorted({*self.defined_labels, *self.labels_in_use()})

    def labels_in_use(self):
        self._live("labels")
        return {label for issue in self.issues.values() for label in issue.labels}

    def delete_label(self, name: str) -> None:
        self._live("delete_label")
        self.defined_labels.discard(name)

    def create_comment(self, number: int, body: str) -> RemoteComment:
        self._live("comment")
        comment = RemoteComment(str(len(self.comments.get(number, [])) + 1), "bot", body, "2026-01-01T00:00:00Z")
        self.comments.setdefault(number, []).append(comment)
        self._touch(number)
        return comment

    def edit(self, number: int, **changes) -> None:
        """A human edits the issue on the forge, outside qew."""
        from dataclasses import replace

        self.issues[number] = replace(self.issues[number], **changes)
        self._touch(number)

    def comment_as_human(self, number: int, text: str, author: str = "alice") -> None:
        comments = self.comments.setdefault(number, [])
        comments.append(RemoteComment(str(len(comments) + 1), author, text, "2026-01-02T00:00:00Z"))
        self._touch(number)

    def add_foreign_issue(self, title: str, body: str = "", labels=("bug",), open: bool = True) -> int:
        number = max(self.issues, default=0) + 1
        self.issues[number] = RemoteIssue(number, f"https://{self.host}/o/r/issues/{number}", f"N{number}", open,
                                          title, body, tuple(labels), "")
        self._touch(number)
        return number
