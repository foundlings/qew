"""The forge backend interface: one contract for GitHub, Forgejo/Gitea and GitLab.

A forge is a mirror. Backends translate these calls into that forge's REST (and,
for GitHub Projects v2, GraphQL) API and nothing else; they know nothing about
ledgers. A capability a forge lacks raises `Unsupported`, never a faked result.
"""

from abc import ABC
from dataclasses import dataclass
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Literal, get_args

from qew.jsontypes import JsonObject

# (name, GitHub color, description): the nine Status values, in board order.
STATUS_OPTIONS = (
    ("Draft", "GRAY", "Not workable yet: drafts, and work missing approval, a type or a priority"),
    ("Backlog", "GREEN", "Approved but blocked, deferred, or a planning container"),
    ("Ready", "BLUE", "Qew reports this work as actionable now"),
    ("Needs approval", "PINK", "Waiting for a human to authorize the work before it may start"),
    ("Gated", "RED", "Blocked on something outside the repository: keys, credentials, hardware, a live account"),
    ("In progress", "YELLOW", "Claimed and actively being implemented"),
    ("In review", "PURPLE", "Implementation has an open linked pull request"),
    ("Done", "ORANGE", "Closed and completed"),
    ("Closed", "GRAY", "Closed without delivery: not planned, superseded or duplicate"),
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
    """Network failure, timeout, rate limit or 5xx: the forge is down; retry later.

    `retry_after` is the number of seconds the forge asked callers to wait (0 when it did not say).
    """

    def __init__(self, detail: str, retry_after: float = 0.0) -> None:
        super().__init__(detail)
        self.retry_after = retry_after


class ForgeSecondaryLimit(ForgeUnavailable):
    """The forge blocked content creation for a while (GitHub's secondary rate limit): the pace was too fast."""


class ForgeAuth(ForgeError):
    """No token, or 401/403: fix credentials, retrying will not help."""


class ForgeRejected(ForgeError):
    """The forge refused the request (other 4xx); `status` is the HTTP status."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


class LabelNotPersisted(ForgeRejected):
    """The forge accepted a label create but the label is not there afterwards: the item is indeterminate."""

    def __init__(self, names: Iterable[str]) -> None:
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
    body: bytes | None = None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[HttpRequest], HttpResponse]


type CloseReason = Literal["", "completed", "not_planned", "duplicate"]  # "": no reason given or none known
CLOSE_REASONS: tuple[str, ...] = get_args(CloseReason.__value__)


def close_reason_of(value: object) -> CloseReason:
    """The reason a forge or a cache named, or "" for anything this version does not know."""
    match value:
        case "completed" | "not_planned" | "duplicate":
            return value
        case _:
            return ""


@dataclass(frozen=True)
class IssueSpec:
    """What an issue should look like; `labels` replaces the issue's whole label set."""

    title: str
    body: str
    labels: tuple[str, ...]
    open: bool
    close_reason: CloseReason = ""  # honored where the forge has reasons
    assignee: str = ""  # forge login; "" clears
    assignees: tuple[str, ...] = ()  # further logins beside `assignee`
    issue_type: str = ""  # the forge's issue type name; "" leaves it alone
    keep_state: bool = False  # an update leaves the forge's open or closed state as it is (a diverged issue waits for a human)


@dataclass(frozen=True)
class RemoteIssue:
    number: int
    url: str
    node_id: str  # GraphQL node id (GitHub), global id, or "" where the forge has none
    open: bool
    title: str
    body: str
    labels: tuple[str, ...]
    assignee: str
    comments: int = 0  # user comment count when the listing reports one (cheap change signal for pull)
    milestone: int = 0  # the milestone the issue sits in (0: none)
    close_reason: CloseReason = ""  # why a closed issue closed, where the forge says
    assignees: tuple[str, ...] = ()  # every login assigned (assignee is the first)
    issue_type: str = ""  # the forge's issue type name, "" when none
    reactions: tuple[tuple[str, int], ...] = ()  # (reaction, count) with a count above zero, in the forge's order
    issue_id: int = 0  # the forge's numeric id (GitHub sub-issues are linked by it), 0 where unknown
    parent: int = 0  # the number of the issue this one is a sub-issue of, 0 for none


@dataclass(frozen=True)
class CachedPage:
    """One page of the creation-ordered issue listing as last fetched: its ETag and the issues it held."""

    etag: str
    issues: tuple[RemoteIssue, ...]
    more: bool  # the forge said another page follows
    raw: int  # entries on the wire page (pull requests included), to tell a full page from a short one


@dataclass(frozen=True)
class PageListing:
    """The complete current issue set, page by page. `fetched` pages came over the wire, `reused` answered 304."""

    pages: tuple[CachedPage, ...]
    fetched: int
    reused: int

    def issues(self) -> list[RemoteIssue]:
        return [issue for page in self.pages for issue in page.issues]


@dataclass(frozen=True)
class RemoteComment:
    id: str  # the forge's comment id, as text
    author: str  # forge login; untrusted data like every forge field
    body: str
    created: str  # ISO timestamp as the forge reports it


@dataclass(frozen=True)
class TimelineEntry:
    kind: str  # the forge's event name (labeled, assigned, renamed, ...); untrusted text
    actor: str
    when: str  # ISO timestamp as the forge reports it
    detail: str  # what changed, as plain text; "" when nothing more is known


@dataclass(frozen=True)
class BodyRevision:
    editor: str  # forge login; untrusted
    when: str  # ISO timestamp
    text: str  # the body as that edit left it


@dataclass(frozen=True)
class RemoteReview:
    id: str
    author: str  # forge login; untrusted data like every forge field
    state: str  # APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED as the forge says
    body: str
    submitted: str  # ISO timestamp


@dataclass(frozen=True)
class RemoteReviewComment:
    id: str
    author: str
    body: str
    path: str
    line: int  # 0 when the forge gives none (an outdated comment)
    created: str


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
    mergeable: bool | None  # None while the forge is still computing it
    checks: str  # "passing", "failing", "pending" or "none"
    body: str = ""  # a null body reads as ""
    author: str = ""  # forge login of the opener
    assignee: str = ""  # forge login it is assigned to, "" when nobody
    auto_merge: bool = False  # the forge will merge it once its checks pass
    merge_sha: str = ""  # the merge commit once state is "merged", else ""
    title: str = ""  # the forge's title (untrusted text); "" from a backend that does not read it
    labels: tuple[str, ...] = ()  # label names on the pull request (untrusted text); () from a backend that does not read them


@dataclass(frozen=True)
class RemoteBranch:
    name: str  # untrusted forge text
    default: bool  # the repository's default branch
    protected: bool  # the forge protects it from deletion


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
    columns: tuple[str, ...]
    title: str = ""  # the board's current title where the forge has one (a renamed board keeps its number)
    notes: tuple[str, ...] = ()  # corrections and foreign leftovers `ensure_board` reports


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


@dataclass(frozen=True)
class MilestoneSpec:
    """What a milestone should look like; `due` is YYYY-MM-DD or empty."""

    title: str
    description: str
    due: str = ""


@dataclass(frozen=True)
class Milestone:
    number: int
    title: str
    description: str
    due: str
    open: bool


class Forge(ABC):
    """Implement what the forge can do; leave the rest raising `Unsupported`.

    A backend is built as `Backend(mirror, transport, env)` (see forge_registry).
    Every method is idempotent where it can be, and raises only ForgeError
    subclasses: transport failures map to ForgeUnavailable, 401/403 and a missing
    token to ForgeAuth, other 4xx to ForgeRejected.
    """

    name = ""

    def find_issue(self, marker: str, recent: bool = False) -> RemoteIssue | None:
        """The issue whose body carries `marker` (the crash-recovery lookup), else None.

        `recent` lets a backend that must scan look only at the newest issues (the cheap lookup after a write)."""
        raise Unsupported("find_issue")

    def create_issue(self, spec: IssueSpec) -> RemoteIssue:
        raise Unsupported("create_issue")

    def update_issue(self, number: int, spec: IssueSpec) -> RemoteIssue:
        raise Unsupported("update_issue")

    def read_issue(self, number: int) -> RemoteIssue:
        raise Unsupported("read_issue")

    def blob_url(self, base: str, ref: str, path: str) -> str:
        """The web URL of a file in a repository on this kind of forge (GitHub's scheme by default)."""
        return f"{base}/blob/{ref}/{path}"

    def set_parent(self, number: int, parent: int | None, previous: int | None) -> None:
        """Make issue `number` a sub-issue of `parent` (None removes the relation, leaving `previous`)."""
        raise Unsupported("set_parent")

    def create_pace(self) -> tuple[int, int]:
        """(creates per minute, creates per hour) this backend allows before it blocks content creation; 0 means no limit."""
        return (0, 0)

    def close_history(self, number: int) -> tuple[str, ...]:
        """The reason of every close the issue ever had, oldest first ("" where the forge gave none)."""
        raise Unsupported("close_history")

    def list_issues(self, since: str = "") -> list[RemoteIssue]:
        """Issues of the repository, open and closed, pull requests excluded, oldest first.

        `since` (an ISO instant, empty = everything) keeps only issues created or modified at or after it; a
        comment, label or title edit counts as a modification. Only the empty listing is the complete set.
        Paginated up to forge_http.MAX_PAGES; a longer listing raises ForgeRejected rather than truncating.
        """
        raise Unsupported("list_issues")

    def list_issue_pages(self, cached: Mapping[int, CachedPage]) -> PageListing:
        """The complete issue set as creation-ordered pages, each requested with the ETag of `cached[page]`
        (pages numbered from 1): a 304 reuses the cached page. Raises Unsupported("conditional_listing") when
        the forge sends no ETags; the caller then falls back to `list_issues(since)`.
        """
        raise Unsupported("conditional_listing")

    def list_comments(self, number: int) -> list[RemoteComment]:
        """User comments of one issue in creation order (system notes excluded)."""
        raise Unsupported("list_comments")

    def create_comment(self, number: int, body: str) -> RemoteComment:
        raise Unsupported("create_comment")

    def body_history(self, issue: RemoteIssue) -> list[BodyRevision]:
        """Earlier wordings of the issue body, oldest first."""
        raise Unsupported("body_history")

    def timeline(self, number: int) -> list[TimelineEntry]:
        """The issue's activity history (who did what, when), oldest first."""
        raise Unsupported("timeline")

    def list_reviews(self, number: int) -> list[RemoteReview]:
        """The reviews of a pull request in submission order."""
        raise Unsupported("list_reviews")

    def list_review_comments(self, number: int) -> list[RemoteReviewComment]:
        """The inline review comments of a pull request in creation order."""
        raise Unsupported("list_review_comments")

    def update_comment(self, number: int, comment_id: str, body: str) -> RemoteComment:
        raise Unsupported("update_comment")

    def delete_comment(self, number: int, comment_id: str) -> None:
        raise Unsupported("delete_comment")

    def list_repo_labels(self) -> list[str]:
        """Names of every label defined in the repository, used or not."""
        raise Unsupported("list_repo_labels")

    def labels_in_use(self) -> set[str]:
        """Label names carried by any issue or pull request of the repository, open or closed."""
        raise Unsupported("labels_in_use")

    def delete_label(self, name: str) -> None:
        """Delete the label definition (it is removed from whatever still carries it); a missing label is fine."""
        raise Unsupported("delete_label")

    def link_dependency(self, number: int, blocked_by: int) -> None:
        """Record on the forge that issue `number` is blocked by issue `blocked_by` (idempotent)."""
        raise Unsupported("link_dependency")

    def unlink_dependency(self, number: int, blocked_by: int) -> None:
        """Remove the forge link that issue `number` is blocked by issue `blocked_by` (idempotent: absent is success)."""
        raise Unsupported("unlink_dependency")

    def ensure_board(self, columns: tuple[str, ...], managed_filter: str = "") -> Board:
        """Find or create the board named by the mirror's `project` with these columns.

        `managed_filter` (when non-empty) is prefixed to every view filter on forges with views.
        """
        raise Unsupported("ensure_board")

    def read_board_placements(self, board: Board) -> Mapping[int, Placement]:
        """Where every issue of this repository sits on the board, from one listing (number -> placement)."""
        raise Unsupported("read_board_placements")

    def read_board_fields(self, board: Board) -> Mapping[int, Mapping[str, str]]:
        """Every field value (single-select option name or text) each of this repository's issues shows on the board."""
        raise Unsupported("read_board_fields")

    def ensure_text_fields(self, board: Board, names: Sequence[str]) -> list[str]:
        """Create a text field for each name the board lacks (an existing field of any type stays); returns the names created."""
        raise Unsupported("ensure_text_fields")

    def set_card_fields(self, board: Board, number: int, values: Mapping[str, str]) -> None:
        """Set the named custom fields of the issue's card (single-select by option name, text as given)."""
        raise Unsupported("set_card_fields")

    def place_on_board(self, board: Board, number: int, column: str, priority: str = "") -> None:
        """Put the issue on the board in `column` (and `priority` where the board has one), idempotent."""
        raise Unsupported("place_on_board")

    def read_placement(self, board: Board, number: int) -> Placement | None:
        """What the forge shows now for the issue on the board, or None when it is not on it."""
        raise Unsupported("read_placement")

    def board_layout(self, board: Board) -> JsonObject | None:
        """The board's structure as this run read it (JSON-serialisable and opaque), or None where nothing is worth caching."""
        return None

    def adopt_board_layout(self, board: Board, layout: JsonObject) -> None:
        """Start from a layout cached earlier instead of reading the structure again; a stale one is repaired on first refusal."""

    def find_milestone(self, title: str) -> Milestone | None:
        """The milestone with exactly this title (any state), else None."""
        raise Unsupported("find_milestone")

    def create_milestone(self, spec: MilestoneSpec) -> Milestone:
        raise Unsupported("create_milestone")

    def update_milestone(self, number: int, spec: MilestoneSpec) -> Milestone:
        raise Unsupported("update_milestone")

    def read_milestone(self, number: int) -> Milestone:
        raise Unsupported("read_milestone")

    def set_milestone_open(self, number: int, open: bool) -> Milestone:
        raise Unsupported("set_milestone_open")

    def assign_milestone(self, number: int, milestone: int) -> RemoteIssue:
        """Put issue `number` in milestone `milestone`; the returned issue shows where it sits."""
        raise Unsupported("assign_milestone")

    def read_board_cards(self, board: Board) -> tuple[Card, ...]:
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

    def find_open_pull_requests(self, head: str) -> list[RemotePullRequest]:
        """Every open pull request (drafts included) whose head branch is exactly `head`; a truncated listing raises."""
        raise Unsupported("find_open_pull_requests")

    def close_pull_request(self, number: int) -> RemotePullRequest:
        """Close an unmerged pull request and return it re-read (idempotent: an already closed one is success)."""
        raise Unsupported("close_pull_request")

    def update_pull_request_title(self, number: int, title: str) -> RemotePullRequest:
        """Set an open pull request's title, then read it back."""
        raise Unsupported("update_pull_request_title")

    def update_pull_request_body(self, number: int, body: str) -> RemotePullRequest:
        """Replace the description and return the re-read pull request (idempotent)."""
        raise Unsupported("update_pull_request_body")

    def set_pull_request_labels(self, number: int, labels: tuple[str, ...]) -> RemotePullRequest:
        """Make `labels` the pull request's whole label set (creating the ones the repository lacks) and return it re-read."""
        raise Unsupported("set_pull_request_labels")

    def requires_status_checks(self, branch: str) -> bool:
        """True when `branch` is protected by a rule that requires status checks.

        An unreadable rule set raises a typed forge error; it is never read as False.
        """
        raise Unsupported("requires_status_checks")

    def enable_auto_merge(self, number: int) -> RemotePullRequest:
        """Merge the pull request once its checks pass; returns the re-read pull request."""
        raise Unsupported("enable_auto_merge")

    def list_branches(self) -> list[RemoteBranch]:
        """Every branch of the repository with its default and protected flags; a truncated listing raises."""
        raise Unsupported("list_branches")

    def branch_is_merged(self, name: str, base: str) -> bool:
        """True when the tip of branch `name` is already contained in `base` (nothing would be lost by deleting it)."""
        raise Unsupported("branch_is_merged")

    def delete_branch(self, name: str) -> None:
        """Delete branch `name`; a branch that is already gone is success (idempotent)."""
        raise Unsupported("delete_branch")

    def list_open_pull_heads(self) -> list[str]:
        """The head branch names of every open pull request (drafts included); a truncated listing raises."""
        raise Unsupported("list_open_pull_heads")


DUPLICATE_MARKER = "<!-- qew-duplicate -->"  # replaces the item marker of an issue closed as a twin


def is_dead_twin(body: str, open_: bool) -> bool:
    """A closed issue qew retired as a duplicate; what it carries keeps no label alive."""
    return not open_ and DUPLICATE_MARKER in (body or "")


def marker_for(item_id: str) -> str:
    """The HTML-comment marker embedded in every mirrored body; unique per ledger item."""
    return f"<!-- {MARKER_PREFIX}{item_id} -->"


def marker_alone(body: str, marker: str) -> bool:
    """True when `marker` is a whole line of `body` (the first line or any other), never merely quoted in text."""
    return any(line.strip() == marker for line in body.splitlines())
