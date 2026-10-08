"""An in-memory Forge for sync-logic walks: records calls, injects failures, forgets nothing."""

from typing import Dict, List, Optional, Tuple

from qew.forge import (
    Board,
    Forge,
    ForgeRejected,
    ForgeUnavailable,
    IssueSpec,
    RemoteIssue,
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

    def ensure_board(self, columns: Tuple[str, ...]) -> Board:
        if not self.supports_boards:
            raise Unsupported("ensure_board")
        self._live("board")
        return Board("B1", f"https://{self.host}/o/r/projects/1", columns)

    def place_on_board(self, board: Board, number: int, column: str) -> None:
        self._live("place")
        self.columns[number] = column

    def count(self, name: str) -> int:
        return sum(1 for call in self.calls if call[0] == name)
