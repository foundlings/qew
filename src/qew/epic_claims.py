"""The derived claim of an epic: in progress while a direct child is claimed, held by those children's actors.

Nothing here is stored. Only open direct non-epic children count (not `needs` edges, grandchildren or child
epics); a completed or closed epic shows nothing derived.
"""


from qew.ledger_event import State
from qew.ledger_item import Kind
from qew.ledger_store import Snapshot


def derive(snapshot: Snapshot) -> dict[str, tuple[State | None, tuple[str, ...]]]:
    """Every item's effective state and holders; only epics differ from their folded state."""
    held: dict[str, set[str]] = {}
    for item in snapshot.items.values():
        if item.parent and item.kind != Kind.EPIC and snapshot.folds[item.id].state == State.CLAIMED:
            held.setdefault(item.parent, set()).add(snapshot.folds[item.id].owner)
    result: dict[str, tuple[State | None, tuple[str, ...]]] = {}
    for item_id, item in snapshot.items.items():
        folded = snapshot.folds[item_id]
        holders = {folded.owner} if folded.owner else set()
        state = folded.state
        if item.kind == Kind.EPIC and state in (State.APPROVED, State.CLAIMED):
            holders |= held.get(item_id, set())
            state = State.CLAIMED if holders else State.APPROVED
        result[item_id] = (state, tuple(sorted(holders)))
    return result
