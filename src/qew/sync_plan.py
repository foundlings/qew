"""Pure planning for sync: which items a target mirrors, and what each should look like."""

from dataclasses import dataclass
import hashlib
import json
from typing import Dict, List, Optional, Tuple

from qew import ledger_graph
from qew.errors import QewError
from qew.forge import COLUMNS, IssueSpec, marker_for
from qew.ledger_event import State
from qew.ledger_item import Item
from qew.ledger_store import Snapshot

_CLOSE_REASON = {"completed": "completed", "not_planned": "not_planned", "duplicate": "not_planned", "superseded": "not_planned"}


def selected(snapshot: Snapshot, expression: str) -> List[str]:
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
        checks = {
            "label": lambda v: v in item.labels,
            "kind": lambda v: item.kind.value == v,
            "state": lambda v: state is not None and state.value == v,
            "priority": lambda v: str(item.priority) == v,
        }
        # `kind:epic,issue` matches either; separate terms must all match.
        return all(any(checks[key](option) for option in value.split(",")) for key, value in terms)

    return [item_id for item_id in snapshot.items if matches(item_id)]


def topological(snapshot: Snapshot, ids: List[str]) -> List[str]:
    """Needs and parents before the items that name them (the graph is acyclic after `check`)."""
    wanted = set(ids)
    order: List[str] = []
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


def column(snapshot: Snapshot, item_id: str) -> str:
    """Draft = not startable (draft or blocked), Ready = startable, then progress, then Done."""
    state = snapshot.folds[item_id].state
    if state in (State.COMPLETED, State.CLOSED):
        return COLUMNS[4]
    if state == State.CLAIMED:
        return COLUMNS[3] if _has_pull_request(snapshot, item_id) else COLUMNS[2]
    if state == State.APPROVED and not ledger_graph.blockers(snapshot, item_id):
        return COLUMNS[1]
    return COLUMNS[0]


def _has_pull_request(snapshot: Snapshot, item_id: str) -> bool:
    return any(m.get("kind") == "pr" for m in snapshot.ledger.load_mirrors(item_id))


def labels(snapshot: Snapshot, item: Item) -> Tuple[str, ...]:
    state = snapshot.folds[item.id].state
    managed = (f"qew:{state.value if state else 'unknown'}", f"kind:{item.kind.value}", f"priority:{item.priority}")
    return tuple(dict.fromkeys((*item.labels, *managed)))


def issue_spec(
    snapshot: Snapshot, item: Item, numbers: Dict[str, int], assignees: Dict[str, str]
) -> IssueSpec:
    """The desired remote issue; `numbers` maps already-mirrored item ids to this target's issue numbers."""
    folded = snapshot.folds[item.id]
    state = folded.state
    closed_reason = ""
    if state == State.COMPLETED:
        closed_reason = "completed"
    elif state == State.CLOSED:
        closing = [e for e in snapshot.events[item.id] if e.type == "closed"]
        closed_reason = _CLOSE_REASON.get(closing[-1].get("reason"), "not_planned") if closing else "not_planned"
    return IssueSpec(
        title=item.title,
        body=_body(item, state, numbers),
        labels=labels(snapshot, item),
        open=state not in (State.COMPLETED, State.CLOSED),
        close_reason=closed_reason,
        assignee=assignees.get(folded.owner, ""),
    )


def _body(item: Item, state: Optional[State], numbers: Dict[str, int]) -> str:
    lines = [marker_for(item.id), "", item.body.strip(), "", "---", f"{item.id} · {state.value if state else '?'} · {item.kind.value}"]
    if item.parent in numbers:
        lines.append(f"Parent: #{numbers[item.parent]}")
    blockers = [f"#{numbers[need]}" for need in item.needs if need in numbers]
    if blockers:
        lines.append(f"Blocked by: {', '.join(blockers)}")
    return "\n".join(lines).replace("\n\n\n", "\n\n") + "\n"


def fingerprint(spec: IssueSpec, extra: object = None) -> str:
    payload = json.dumps([spec.__dict__, extra], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
