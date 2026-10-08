"""An in-memory Forge for sync-logic walks: records calls, injects failures, forgets nothing."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


from typing import Callable, Dict, List, Optional, Tuple

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
    RemoteComment,
    RemoteIssue,
    RemotePullRequest,
    Unsupported,
)


class MemoryForge(Forge):
    name = "memory"

    def __init__(self, host: str = "memory.test", boards: bool = True, links: bool = True) -> None:
        self.host = host
        self.issues: Dict[int, RemoteIssue] = {}
        self.calls: List[Tuple] = []
        self.links: List[Tuple[int, int]] = []
        self.columns: Dict[int, str] = {}
        self.priorities: Dict[int, str] = {}
        self.down = False
        self.board_id = "B1"
        self.milestones: Dict[int, Milestone] = {}
        self.milestone_of: Dict[int, int] = {}  # issue number -> milestone number
        self.supports_milestones = True
        self.foreign_cards: List[Card] = []
        self.stubborn_cards = False  # a card delete the forge accepts but does not carry out
        self.hide_next_find = False
        self.reject_title: Optional[str] = None
        self.supports_boards = boards
        self.supports_links = links
        self.comments: Dict[int, List[RemoteComment]] = {}
        self.pulls: Dict[int, RemotePullRequest] = {}
        self.pull_specs: Dict[int, PullRequestSpec] = {}
        self.merged: List[Tuple[int, str]] = []
        self.raise_on: Dict[str, ForgeError] = {}  # call name -> the error that call raises
        self.merge_calls: List[Tuple[int, str, bool]] = []  # (number, sha, bypass_checks)
        self.body_writes: List[str] = []
        self.assign_refused = False  # the forge opens the pull request but will not assign the login
        self.ready_ignored = False  # accept mark-ready yet stay a draft
        self.merge_lag_reads = 0  # reads after a merge call that still show the pull request open
        self.merge_lag: Dict[int, int] = {}
        self.after_read: List[Callable[["MemoryForge"], None]] = []  # run once after the next read, as a concurrent edit
        self.body_write_ignored = False
        self.required_checks: Dict[str, bool] = {}
        self.rules_error: Optional[ForgeError] = None
        self.supports_rules = True
        self.supports_auto_merge = True
        self.auto_merge_ignored = False
        self.bypass_supported = True

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

    def find_issue(self, marker: str) -> Optional[RemoteIssue]:
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
        return self.issues[number]

    def update_issue(self, number: int, spec: IssueSpec) -> RemoteIssue:
        self._live("update")
        self.issues[number] = self._issue(number, spec)
        return self.issues[number]

    def link_dependency(self, number: int, blocked_by: int) -> None:
        if not self.supports_links:
            raise Unsupported("link_dependency")
        self._live("link")
        if (number, blocked_by) not in self.links:
            self.links.append((number, blocked_by))

    def ensure_board(self, columns: Tuple[str, ...], managed_filter: str = "") -> Board:
        if not self.supports_boards:
            raise Unsupported("ensure_board")
        self._live("board")
        return Board(self.board_id, f"https://{self.host}/o/r/projects/{self.board_id}", columns)

    def place_on_board(self, board: Board, number: int, column: str, priority: str = "") -> None:
        self._live("place")
        self.columns[number] = column
        self.priorities[number] = priority

    def read_placement(self, board: Board, number: int) -> Optional[Placement]:
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

    def find_milestone(self, title: str) -> Optional[Milestone]:
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
        return self.issues[number]

    def read_board_cards(self, board: Board) -> Tuple[Card, ...]:
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

    def edit_pull(self, number: int, **changes) -> None:
        """Change the remote pull request behind qew's back, as a teammate or a CI run would."""
        self.pulls[number] = self._with(number, **changes)

    def _with(self, number: int, **changes) -> RemotePullRequest:
        from dataclasses import replace

        return replace(self.pulls[number], **changes)

    # -- read side
    def list_issues(self):
        self._live("list")
        return [self._counted(i) for _, i in sorted(self.issues.items())]

    def _counted(self, issue: RemoteIssue) -> RemoteIssue:
        from dataclasses import replace

        return replace(issue, comments=len(self.comments.get(issue.number, [])))

    def list_comments(self, number: int):
        self._live("comments")
        return list(self.comments.get(number, []))

    def create_comment(self, number: int, body: str) -> RemoteComment:
        self._live("comment")
        comment = RemoteComment(str(len(self.comments.get(number, [])) + 1), "bot", body, "2026-01-01T00:00:00Z")
        self.comments.setdefault(number, []).append(comment)
        return comment

    def edit(self, number: int, **changes) -> None:
        """A human edits the issue on the forge, outside qew."""
        from dataclasses import replace

        self.issues[number] = replace(self.issues[number], **changes)

    def comment_as_human(self, number: int, text: str, author: str = "alice") -> None:
        comments = self.comments.setdefault(number, [])
        comments.append(RemoteComment(str(len(comments) + 1), author, text, "2026-01-02T00:00:00Z"))

    def add_foreign_issue(self, title: str, body: str = "", labels=("bug",), open: bool = True) -> int:
        number = max(self.issues, default=0) + 1
        self.issues[number] = RemoteIssue(number, f"https://{self.host}/o/r/issues/{number}", f"N{number}", open,
                                          title, body, tuple(labels), "")
        return number
