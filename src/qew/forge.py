"""The forge backend interface: one contract for GitHub, Forgejo/Gitea and GitLab.

A forge is a mirror. Backends translate these calls into that forge's REST (and,
for GitHub Projects v2, GraphQL) API and nothing else; they know nothing about
ledgers. A capability a forge lacks raises `Unsupported`, never a faked result.
"""

from abc import ABC
from dataclasses import dataclass
from typing import Callable, List, Mapping, Optional, Tuple

# (name, GitHub color, description): the seven Status values, in board order.
STATUS_OPTIONS = (
    ("Draft", "GRAY", "Not workable yet: drafts, and work missing approval, a type or a priority"),
    ("Backlog", "GREEN", "Approved but blocked, deferred, or a planning container"),
    ("Ready", "BLUE", "Qew reports this work as actionable now"),
    ("Needs approval", "PINK", "Waiting for a human to authorize the work before it may start"),
    ("In progress", "YELLOW", "Claimed and actively being implemented"),
    ("In review", "PURPLE", "Implementation has an open linked pull request"),
    ("Done", "ORANGE", "Closed and completed"),
)
COLUMNS = tuple(name for name, _color, _description in STATUS_OPTIONS)
# Priority P0..P4 (index = item priority); larger priorities clamp to the last.
PRIORITY_OPTIONS = (
    ("P0", "RED", "Critical priority"),
    ("P1", "ORANGE", "High priority"),
    ("P2", "YELLOW", "Normal priority"),
    ("P3", "GREEN", "Low priority"),
    ("P4", "GRAY", "Backlog priority"),
)
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


class LabelNotPersisted(ForgeRejected):
    """The forge accepted a label create but the label is not there afterwards: the item is indeterminate."""

    def __init__(self, names) -> None:
        super().__init__(502, f"label(s) {', '.join(sorted(names))} did not persist after creation")


LABEL_COLOR = "ededed"  # the neutral colour of every label sync creates; existing labels are never recoloured
LABEL_DESCRIPTION = "Managed by qew"


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
    comments: int = 0  # user comment count when the listing reports one (cheap change signal for pull)


@dataclass(frozen=True)
class RemoteComment:
    id: str  # the forge's comment id, as text
    author: str  # forge login; untrusted data like every forge field
    body: str
    created: str  # ISO timestamp as the forge reports it


@dataclass(frozen=True)
class PullRequestSpec:
    head: str
    base: str
    title: str
    body: str
    draft: bool
    assignee: str = ""  # forge login the pull request is assigned to; "" assigns nobody


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
    body: str = ""  # a null body reads as ""
    author: str = ""  # forge login of the opener
    assignee: str = ""  # forge login it is assigned to, "" when nobody
    auto_merge: bool = False  # the forge will merge it once its checks pass
    merge_sha: str = ""  # the merge commit once state is "merged", else ""


# (name, layout, filter): the views `project ensure` keeps, in order. Layouts are GitHub's names.
BOARD_VIEWS = (
    ("Kanban", "BOARD_LAYOUT", ""),
    ("Table", "TABLE_LAYOUT", ""),
    ("Ready", "TABLE_LAYOUT", "status:Ready"),
    ("Blocked", "TABLE_LAYOUT", "is:blocked"),
    ("Active", "TABLE_LAYOUT", 'status:"In progress"'),
    ("Review", "TABLE_LAYOUT", 'status:"In review"'),
    ("Drafts", "TABLE_LAYOUT", "status:Draft"),
    ("Roadmap", "ROADMAP_LAYOUT", ""),
)
# Prefixed to every view filter when the mirror only selects part of the repository.
MANAGED_FILTER = "label:qew:managed"
# Old Status option names and the current option each one becomes.
LEGACY_STATUS = {"To triage": "Draft"}


@dataclass(frozen=True)
class Board:
    id: str
    url: str
    columns: Tuple[str, ...]
    title: str = ""  # the board's current title where the forge has one (a renamed board keeps its number)
    notes: Tuple[str, ...] = ()  # corrections and foreign leftovers `ensure_board` reports


@dataclass(frozen=True)
class Placement:
    """Where an issue sits on a board: its Status column and Priority option name ("" when unset)."""

    column: str
    priority: str


@dataclass(frozen=True)
class Card:
    """One card on a board: its id, the repository and number of its issue, and its Status and Priority names."""

    id: str
    repository: str  # OWNER/REPO of the card's issue; a shared board holds several
    number: int
    column: str
    priority: str


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

    def list_issues(self) -> List[RemoteIssue]:
        """Every issue of the repository, open and closed, pull requests excluded, oldest first.

        Paginated up to forge_http.MAX_PAGES; a longer listing raises ForgeRejected rather than truncating.
        """
        raise Unsupported("list_issues")

    def list_comments(self, number: int) -> List[RemoteComment]:
        """User comments of one issue in creation order (system notes excluded)."""
        raise Unsupported("list_comments")

    def create_comment(self, number: int, body: str) -> RemoteComment:
        raise Unsupported("create_comment")

    def link_dependency(self, number: int, blocked_by: int) -> None:
        """Record on the forge that issue `number` is blocked by issue `blocked_by` (idempotent)."""
        raise Unsupported("link_dependency")

    def ensure_board(self, columns: Tuple[str, ...], managed_filter: str = "") -> Board:
        """Find or create the board named by the mirror's `project` with these columns.

        `managed_filter` (when non-empty) is prefixed to every view filter on forges with views.
        """
        raise Unsupported("ensure_board")

    def place_on_board(self, board: Board, number: int, column: str, priority: str = "") -> None:
        """Put the issue on the board in `column` (and `priority` where the board has one), idempotent."""
        raise Unsupported("place_on_board")

    def read_placement(self, board: Board, number: int) -> Optional[Placement]:
        """What the forge shows now for the issue on the board, or None when it is not on it."""
        raise Unsupported("read_placement")

    def read_board_cards(self, board: Board) -> Tuple[Card, ...]:
        """Every card on the board (all repositories), or `Unsupported`; a truncated listing raises, never returns."""
        raise Unsupported("read_board_cards")

    def delete_card(self, board: Board, card_id: str) -> None:
        """Remove one card from the board without touching its issue."""
        raise Unsupported("delete_card")

    def remove_from_board(self, board: Board, number: int) -> None:
        """Take the issue's card off the board; the issue itself is never touched."""
        raise Unsupported("remove_from_board")

    def open_pull_request(self, spec: PullRequestSpec) -> RemotePullRequest:
        raise Unsupported("open_pull_request")

    def read_pull_request(self, number: int) -> RemotePullRequest:
        raise Unsupported("read_pull_request")

    def mark_pull_request_ready(self, number: int) -> RemotePullRequest:
        raise Unsupported("mark_pull_request_ready")

    def merge_pull_request(self, number: int, expected_head_sha: str, bypass_checks: bool = False) -> RemotePullRequest:
        """Merge exactly `expected_head_sha` (no squash); refuse if the head moved.

        `bypass_checks` merges past branch protection only; a forge with no bypass raises `Unsupported`.
        """
        raise Unsupported("merge_pull_request")

    def update_pull_request_body(self, number: int, body: str) -> RemotePullRequest:
        """Replace the description and return the re-read pull request (idempotent)."""
        raise Unsupported("update_pull_request_body")

    def requires_status_checks(self, branch: str) -> bool:
        """True when `branch` is protected by a rule that requires status checks.

        An unreadable rule set raises a typed forge error; it is never read as False.
        """
        raise Unsupported("requires_status_checks")

    def enable_auto_merge(self, number: int) -> RemotePullRequest:
        """Merge the pull request once its checks pass; returns the re-read pull request."""
        raise Unsupported("enable_auto_merge")


def marker_for(item_id: str) -> str:
    """The HTML-comment marker embedded in every mirrored body; unique per ledger item."""
    return f"<!-- {MARKER_PREFIX}{item_id} -->"


def marker_alone(body: str, marker: str) -> bool:
    """True when `marker` is a whole line of `body` (the first line or any other), never merely quoted in text."""
    return any(line.strip() == marker for line in body.splitlines())
