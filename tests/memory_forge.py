"""An in-memory Forge for sync-logic walks: records calls, injects failures, forgets nothing."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


from typing import Dict, List, Optional, Tuple

from qew.forge import (
    Board,
    Forge,
    ForgeRejected,
    ForgeUnavailable,
    IssueSpec,
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
        self.down = False
        self.reject_title: Optional[str] = None
        self.supports_boards = boards
        self.supports_links = links
        self.comments: Dict[int, List[RemoteComment]] = {}
        self.pulls: Dict[int, RemotePullRequest] = {}
        self.pull_specs: Dict[int, PullRequestSpec] = {}
        self.merged: List[Tuple[int, str]] = []

    def _live(self, name: str) -> None:
        self.calls.append((name,))
        if self.down:
            raise ForgeUnavailable("forge is down")

    def _issue(self, number: int, spec: IssueSpec) -> RemoteIssue:
        return RemoteIssue(
            number, f"https://{self.host}/o/r/issues/{number}", f"N{number}", spec.open,
            spec.title, spec.body, spec.labels, spec.assignee,
        )

    def find_issue(self, marker: str) -> Optional[RemoteIssue]:
        self._live("find")
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
        return Board("B1", f"https://{self.host}/o/r/projects/1", columns)

    def place_on_board(self, board: Board, number: int, column: str) -> None:
        self._live("place")
        self.columns[number] = column

    def count(self, name: str) -> int:
        return sum(1 for call in self.calls if call[0] == name)

    def open_pull_request(self, spec: PullRequestSpec) -> RemotePullRequest:
        self._live("open_pr")
        number = len(self.pulls) + 1
        self.pull_specs[number] = spec
        self.pulls[number] = RemotePullRequest(
            number, f"https://{self.host}/o/r/pulls/{number}", "open", spec.draft, f"sha{number}", spec.head, spec.base, True, "passing"
        )
        return self.pulls[number]

    def read_pull_request(self, number: int) -> RemotePullRequest:
        self._live("read_pr")
        return self.pulls[number]

    def mark_pull_request_ready(self, number: int) -> RemotePullRequest:
        self._live("ready_pr")
        self.pulls[number] = self._with(number, draft=False)
        return self.pulls[number]

    def merge_pull_request(self, number: int, expected_head_sha: str) -> RemotePullRequest:
        self._live("merge_pr")
        if self.pulls[number].head_sha != expected_head_sha:
            raise ForgeRejected(409, "head moved")
        self.merged.append((number, expected_head_sha))
        self.pulls[number] = self._with(number, state="merged")
        return self.pulls[number]

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
