"""The forge backend interface: one contract for GitHub, Forgejo/Gitea and GitLab.

A forge is a mirror. Backends translate these calls into that forge's REST (and,
for GitHub Projects v2, GraphQL) API and nothing else; they know nothing about
ledgers. A capability a forge lacks raises `Unsupported`, never a faked result.
"""

from abc import ABC
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Tuple

COLUMNS = ("Draft", "Ready", "In progress", "In review", "Done")
MARKER_PREFIX = "qew:"


class ForgeError(Exception):
    """Base of every forge failure; `detail` is forge text and therefore untrusted data."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ForgeUnavailable(ForgeError):
    """Network failure, timeout, 429 or 5xx: the forge is down; retry later."""


class ForgeAuth(ForgeError):
    """No token, or 401/403: fix credentials, retrying will not help."""


class ForgeRejected(ForgeError):
    """The forge refused the request (other 4xx); `status` is the HTTP status."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


class Unsupported(ForgeError):
    """This forge cannot do `capability`; the caller skips it and says so."""

    def __init__(self, capability: str) -> None:
        super().__init__(f"unsupported: {capability}")
        self.capability = capability


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: Optional[bytes] = None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[HttpRequest], HttpResponse]


@dataclass(frozen=True)
class IssueSpec:
    """What an issue should look like; `labels` replaces the issue's whole label set."""

    title: str
    body: str
    labels: Tuple[str, ...]
    open: bool
    close_reason: str = ""  # "", "completed" or "not_planned"; honored where the forge has reasons
    assignee: str = ""  # forge login; "" clears


@dataclass(frozen=True)
class RemoteIssue:
    number: int
    url: str
    node_id: str  # GraphQL node id (GitHub), global id, or "" where the forge has none
    open: bool
    title: str
    body: str
    labels: Tuple[str, ...]
    assignee: str


@dataclass(frozen=True)
class PullRequestSpec:
    head: str
    base: str
    title: str
    body: str
    draft: bool


@dataclass(frozen=True)
class RemotePullRequest:
    number: int
    url: str
    state: str  # "open", "closed" or "merged"
    draft: bool
    head_sha: str
    head_branch: str
    base_branch: str
    mergeable: Optional[bool]  # None while the forge is still computing it
    checks: str  # "passing", "failing", "pending" or "none"


@dataclass(frozen=True)
class Board:
    id: str
    url: str
    columns: Tuple[str, ...]


class Forge(ABC):
    """Implement what the forge can do; leave the rest raising `Unsupported`.

    A backend is built as `Backend(mirror, transport, env)` (see forge_registry).
    Every method is idempotent where it can be, and raises only ForgeError
    subclasses: transport failures map to ForgeUnavailable, 401/403 and a missing
    token to ForgeAuth, other 4xx to ForgeRejected.
    """

    name = ""

    def find_issue(self, marker: str) -> Optional[RemoteIssue]:
        """The issue whose body carries `marker` (the crash-recovery lookup), else None."""
        raise Unsupported("find_issue")

    def create_issue(self, spec: IssueSpec) -> RemoteIssue:
        raise Unsupported("create_issue")

    def update_issue(self, number: int, spec: IssueSpec) -> RemoteIssue:
        raise Unsupported("update_issue")

    def read_issue(self, number: int) -> RemoteIssue:
        raise Unsupported("read_issue")

    def link_dependency(self, number: int, blocked_by: int) -> None:
        """Record on the forge that issue `number` is blocked by issue `blocked_by` (idempotent)."""
        raise Unsupported("link_dependency")

    def ensure_board(self, columns: Tuple[str, ...]) -> Board:
        """Find or create the board named by the mirror's `project` with these columns."""
        raise Unsupported("ensure_board")

    def place_on_board(self, board: Board, number: int, column: str) -> None:
        """Put the issue on the board in `column`, moving it if already there (idempotent)."""
        raise Unsupported("place_on_board")

    def open_pull_request(self, spec: PullRequestSpec) -> RemotePullRequest:
        raise Unsupported("open_pull_request")

    def read_pull_request(self, number: int) -> RemotePullRequest:
        raise Unsupported("read_pull_request")

    def mark_pull_request_ready(self, number: int) -> RemotePullRequest:
        raise Unsupported("mark_pull_request_ready")

    def merge_pull_request(self, number: int, expected_head_sha: str) -> RemotePullRequest:
        """Merge exactly `expected_head_sha` (no squash); refuse if the head moved."""
        raise Unsupported("merge_pull_request")


def marker_for(item_id: str) -> str:
    """The HTML-comment marker embedded in every mirrored body; unique per ledger item."""
    return f"<!-- {MARKER_PREFIX}{item_id} -->"
