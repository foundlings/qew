"""Pure planning for sync: which items a target mirrors, and what each should look like."""

import hashlib
import json
import re
from collections.abc import Callable, Iterable

from qew import label_style, ledger_author, ledger_graph
from qew.mirror_types import Seen
from qew.errors import QewError
from qew.forge import COLUMNS, CloseReason, MANAGED_FILTER, MARKER_PREFIX, PRIORITY_OPTIONS, IssueSpec, RemoteIssue, marker_for
from qew.assignees import assignees_after
from qew.attachments import Attachment
from qew.ledger_event import State, attachments_after
from qew.ledger_item import Item, Kind
from qew.ledger_store import Snapshot

# GitHub has no "superseded" reason; a replaced item is the closest to a duplicate, and neither shows as "not planned"
_CLOSE_REASON: dict[str, CloseReason] = {"completed": "completed", "not_planned": "not_planned", "duplicate": "duplicate", "superseded": "duplicate"}


def selected(snapshot: Snapshot, expression: str) -> list[str]:
    """Item ids matching every space-separated `label:`, `kind:`, `state:` or `priority:` term."""
    terms = []
    for term in expression.split():
        key, colon, value = term.partition(":")
        if not colon or key not in ("label", "kind", "state", "priority") or not value:
            raise QewError(f"select term `{term}` must be label:X, kind:X, state:X or priority:N", 1)
        terms.append((key, value))

    def matches(item_id: str) -> bool:
        item = snapshot.items[item_id]
        state = snapshot.folds[item_id].state
        checks: dict[str, Callable[[str], bool]] = {
            "label": lambda v: v in item.labels,
            "kind": lambda v: item.kind.value == v,
            "state": lambda v: state is not None and state.value == v,
            "priority": lambda v: str(item.priority) == v,
        }
        # `kind:epic,issue` matches either; separate terms must all match.
        return all(any(checks[key](option) for option in value.split(",")) for key, value in terms)

    return [item_id for item_id in snapshot.items if matches(item_id)]


def topological(snapshot: Snapshot, ids: list[str]) -> list[str]:
    """Needs and parents before the items that name them (the graph is acyclic after `check`)."""
    wanted = set(ids)
    order: list[str] = []
    seen = set()
    for start in sorted(ids):
        stack = [(start, False)]
        while stack:
            item_id, expanded = stack.pop()
            if expanded:
                order.append(item_id)
                continue
            if item_id in seen:
                continue
            seen.add(item_id)
            stack.append((item_id, True))
            item = snapshot.items[item_id]
            for dependency in sorted({*item.needs, *([item.parent] if item.parent else [])}, reverse=True):
                if dependency in wanted and dependency not in seen:
                    stack.append((dependency, False))
    return order


DRAFT, BACKLOG, READY, NEEDS_APPROVAL, IN_PROGRESS, IN_REVIEW, DONE = COLUMNS


def column(snapshot: Snapshot, item_id: str) -> str:
    """The Status of an item, first match wins; the one derivation shared by sync, the board and `ready`."""
    item = snapshot.items[item_id]
    state = snapshot.folds[item_id].state
    if state in (State.COMPLETED, State.CLOSED):
        return DONE
    if state == State.DRAFT or state is None:
        return DRAFT
    if item.kind == Kind.EPIC:
        return IN_PROGRESS if _child_claimed(snapshot, item_id) else BACKLOG
    if ledger_graph.APPROVAL_LABEL in item.labels:
        return NEEDS_APPROVAL
    if _has_open_pull_request(snapshot, item_id):
        return IN_REVIEW
    if state == State.CLAIMED:
        return IN_PROGRESS
    if not ledger_graph.blockers(snapshot, item_id):
        return READY
    return BACKLOG


def moved_by(snapshot: Snapshot, item_id: str) -> list[str]:
    """Items whose board status can change because `item_id` changed: its direct dependents and its ancestors."""
    moved = [other.id for other in snapshot.items.values() if item_id in other.needs]
    parent = snapshot.items[item_id].parent
    seen = set()
    while parent and parent in snapshot.items and parent not in seen:
        seen.add(parent)
        moved.append(parent)
        parent = snapshot.items[parent].parent
    return moved


def _child_claimed(snapshot: Snapshot, item_id: str) -> bool:
    return any(
        snapshot.folds[child].state == State.CLAIMED
        for child in ledger_graph.children(snapshot).get(item_id, ())
    )


def _has_open_pull_request(snapshot: Snapshot, item_id: str) -> bool:
    """A recorded pull request counts until its recorded state says merged or closed."""
    return any(
        m.get("kind") == "pr" and m.get("state", "open") not in ("merged", "closed")
        for m in snapshot.ledger.load_mirrors(item_id)
    )


class InvalidAuthor(QewError):
    """The item's author cannot be a label; the item is reported and nothing is sent for it."""

    def __init__(self, item_id: str, detail: str) -> None:
        super().__init__(f"{item_id}: author label: {detail}", 1)
        self.item_id = item_id
        self.detail = detail


def priority_option(priority: int) -> tuple[str, bool]:
    """The Priority option name for an item priority, and whether it was clamped to the last option."""
    last = len(PRIORITY_OPTIONS) - 1
    return PRIORITY_OPTIONS[min(priority, last)][0], priority > last


def managed_filter(mode: str) -> str:
    """Overlay mirrors keep human issues off the board; repository mode (qew owns the tracker) is bare."""
    return MANAGED_FILTER if mode == "overlay" else ""


def labels(snapshot: Snapshot, item: Item, overlay: bool = False) -> tuple[str, ...]:
    """The item's own labels, its priority, its author and `epic`/`task`; overlay mirrors add the state and Status labels.

    Repository mode keeps the tracker quiet: the board's Status field carries state, so no `qew:` or `status:` label.
    """
    managed = [priority_option(item.priority)[0]]
    if item.kind != Kind.ISSUE:
        managed.append(item.kind.value)
    if overlay:
        state = snapshot.folds[item.id].state
        managed = ["qew:managed", f"qew:{state.value if state else 'unknown'}", f"status:{column(snapshot, item.id)}", *managed]
    author = ledger_author.author(snapshot, item.id)
    if author:
        problem = ledger_author.label_problem(author)
        if problem:
            raise InvalidAuthor(item.id, problem)
        managed.append(f"author:{author}")
    return tuple(dict.fromkeys((*item.labels, *managed)))


def issue_spec(
    snapshot: Snapshot, item: Item, numbers: dict[str, int], assignees: dict[str, str], overlay: bool = False,
    link: Callable[[str, str], str | None] | None = None,
) -> IssueSpec:
    """The desired remote issue; `numbers` maps already-mirrored item ids to this target's issue numbers."""
    folded = snapshot.folds[item.id]
    state = folded.state
    closed_reason: CloseReason = ""
    if state == State.COMPLETED:
        closed_reason = "completed"
    elif state == State.CLOSED:
        closing = [e for e in snapshot.events[item.id] if e.type == "closed"]
        closed_reason = _CLOSE_REASON.get(closing[-1].get("reason"), "not_planned") if closing else "not_planned"
    return IssueSpec(
        title=item.title,
        body=_body(item, state, numbers, attachments_after(snapshot.events[item.id]), link),
        labels=labels(snapshot, item, overlay),
        open=state not in (State.COMPLETED, State.CLOSED),
        close_reason=closed_reason,
        assignee=assignees.get(folded.owner, ""),
        assignees=tuple(login for login in assignees_after(snapshot.events[item.id]) if login != assignees.get(folded.owner, "")),
    )


def _size(size: int) -> str:
    return f"{size} B" if size < 1024 else f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f} MB"


def _escaped(name: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>#+!|~&])", r"\\\1", name)


def _body(item: Item, state: State | None, numbers: dict[str, int], files: dict[str, Attachment] | None = None,
          link: Callable[[str, str], str | None] | None = None) -> str:
    lines = [marker_for(item.id), "", item.body.strip(), "", "---", f"{item.id} · {state.value if state else '?'} · {item.kind.value}"]
    if item.parent in numbers:
        lines.append(f"Parent: #{numbers[item.parent]}")
    blockers = [f"#{numbers[need]}" for need in item.needs if need in numbers]
    if blockers:
        lines.append(f"Blocked by: {', '.join(blockers)}")
    if files:
        lines.append("Attachments (stored in the ledger repository):")
        for name, row in sorted(files.items()):
            url = link(item.id, row.blob) if link else None
            label = f"[{_escaped(name)}]({url})" if url else _escaped(name)
            lines.append(f"- {label} · {_size(row.size)} · {row.digest[:12]}")
    return "\n".join(lines).replace("\n\n\n", "\n\n") + "\n"


def fingerprint(spec: IssueSpec, extra: object = None) -> str:
    payload = json.dumps([spec.__dict__, extra], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def user_labels(labels: Iterable[str]) -> list[str]:
    """Labels qew does not manage: the ones a human may add on the forge."""
    return sorted(l for l in labels if not label_style.is_managed(l))


def body_from_remote(text: str) -> str:
    """The item's own body inside a mirrored issue body (inverse of `_body`): no marker, no footer."""
    lines = text.split("\n")
    if lines and lines[0].startswith("<!-- " + MARKER_PREFIX):
        lines = lines[1:]
    joined = "\n".join(lines)
    if "\n---\n" in joined:
        joined = joined.rsplit("\n---\n", 1)[0]
    return joined.strip()


def seen_of(remote: RemoteIssue, comment_ids: Iterable[str]) -> Seen:
    """What the forge showed at the last sync or pull: the baseline a later pull diffs against."""
    return {
        "title": remote.title,
        "body": body_from_remote(remote.body),
        "labels": user_labels(remote.labels),
        "open": remote.open,
        "assignee": remote.assignee,
        "assignees": list(remote.assignees),
        "comments": list(comment_ids),
    }
