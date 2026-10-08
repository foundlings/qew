"""Append-only events and the state machine folded from them.

Current state is the fold of an item's events in ID order. A transition the
machine does not allow is a typed refusal, never a silent no-op.
"""

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any, Dict, List, Optional, Tuple

from qew.errors import QewError

CLOSE_REASONS = ("not_planned", "duplicate", "superseded")


class State(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    CLOSED = "closed"


# type -> (required string fields, allowed source states; None = initial event only)
_RULES: Dict[str, Tuple[Tuple[str, ...], Optional[Tuple[State, ...]]]] = {
    "drafted": ((), None),
    "approved": (("quote",), (State.DRAFT,)),
    "reverted": (("reason", "quote"), (State.APPROVED,)),
    "claimed": (("owner",), (State.APPROVED,)),
    "released": ((), (State.CLAIMED,)),
    "evidence": (("vcs", "repo"), (State.APPROVED, State.CLAIMED)),
    "completed": ((), (State.CLAIMED,)),
    "reopened": ((), (State.COMPLETED, State.CLOSED)),
    "closed": (("reason",), (State.DRAFT, State.APPROVED, State.CLAIMED, State.COMPLETED)),
    "field": (("key",), (State.DRAFT, State.APPROVED, State.CLAIMED, State.COMPLETED, State.CLOSED)),
}
_INITIAL = {"drafted": State.DRAFT, "approved": State.APPROVED}
_NEXT = {
    "approved": State.APPROVED,
    "reverted": State.DRAFT,
    "claimed": State.CLAIMED,
    "released": State.APPROVED,
    "completed": State.COMPLETED,
    "reopened": State.APPROVED,
    "closed": State.CLOSED,
}
TYPES = tuple(_RULES)


@dataclass(frozen=True)
class Event:
    id: str
    type: str
    at: str
    actor: str
    data: Dict[str, Any]

    def get(self, key: str, default: Any = "") -> Any:
        return self.data.get(key, default)

    def to_json(self) -> str:
        return json.dumps(
            {"type": self.type, "at": self.at, "actor": self.actor, **self.data},
            sort_keys=True,
            ensure_ascii=False,
        ) + "\n"


def parse_event(event_id: str, text: str, where: str = "event") -> Event:
    try:
        value = json.loads(text)
    except ValueError as error:
        raise QewError(f"{where}: not valid JSON ({error})", 1) from None
    if not isinstance(value, dict):
        raise QewError(f"{where}: an event is one JSON object", 1)
    kind = value.get("type")
    if kind not in _RULES:
        raise QewError(f"{where}: unknown event type `{kind}`", 1)
    for key in ("at", "actor"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise QewError(f"{where}: event is missing `{key}`", 1)
    for key in _RULES[kind][0]:
        if key not in value or value[key] in ("", None):
            raise QewError(f"{where}: `{kind}` event is missing `{key}`", 1)
    if kind == "closed" and value["reason"] not in CLOSE_REASONS:
        raise QewError(f"{where}: close reason must be one of {', '.join(CLOSE_REASONS)}", 1)
    if kind == "evidence":
        ids = value.get("change_ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
            raise QewError(f"{where}: evidence needs a non-empty change_ids list", 1)
        if value["vcs"] not in ("jj", "git"):
            raise QewError(f"{where}: evidence vcs must be jj or git", 1)
    data = {key: item for key, item in value.items() if key not in ("type", "at", "actor")}
    return Event(event_id, kind, value["at"], value["actor"], data)


@dataclass(frozen=True)
class Folded:
    state: Optional[State]
    owner: str
    evidence: Tuple[Event, ...]
    errors: Tuple[str, ...]


def fold(events: List[Event]) -> Folded:
    """Apply events in ID order; collect every illegal transition instead of stopping."""
    state: Optional[State] = None
    owner = ""
    evidence: List[Event] = []
    errors: List[str] = []
    for event in sorted(events, key=lambda item: item.id):
        _, sources = _RULES[event.type]
        if sources is None:
            if state is not None:
                errors.append(f"{event.id}: `drafted` is only valid as the first event")
            else:
                state = _INITIAL[event.type]
            continue
        if state is None:
            if event.type == "approved":
                state = State.APPROVED
            else:
                errors.append(f"{event.id}: `{event.type}` before the item was drafted or approved")
            continue
        if state not in sources:
            errors.append(f"{event.id}: `{event.type}` is illegal from state {state.value}")
            continue
        if event.type == "claimed":
            owner = event.get("owner")
        elif event.type in ("released", "reverted", "reopened", "closed"):
            owner = ""
        elif event.type == "evidence":
            evidence.append(event)
        if event.type in _NEXT:
            state = _NEXT[event.type]
    return Folded(state, owner, tuple(evidence), tuple(errors))


def transition_error(state: Optional[State], event_type: str) -> Optional[str]:
    """Why `event_type` is refused from `state`, or None when allowed."""
    _, sources = _RULES[event_type]
    if sources is None or state is None:
        return None if event_type in _INITIAL and state is None else f"`{event_type}` has no item state to act on"
    if state in sources:
        return None
    allowed = ", ".join(source.value for source in sources)
    return f"cannot {event_type} an item that is {state.value} (needs: {allowed})"
