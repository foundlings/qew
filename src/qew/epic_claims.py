"""The derived claim of an epic: in progress while a direct child is claimed, held by those children's actors.

Nothing here is stored. Only claimed non-epic items under it count, directly or through nested epics (not `needs`
edges); a completed or closed epic shows nothing derived.
"""


from qew.ledger_event import State
from qew.ledger_item import Kind
from qew.ledger_store import Snapshot


def derive(snapshot: Snapshot) -> dict[str, tuple[State | None, tuple[str, ...]]]:
    """Every item's effective state and holders; only epics differ from their folded state."""
    held: dict[str, set[str]] = {}
    for item in snapshot.items.values():
        if item.kind == Kind.EPIC or snapshot.folds[item.id].state != State.CLAIMED:
            continue
        seen = {item.id}
        above = item.parent
        while above and above in snapshot.items and above not in seen:  # every epic above, through nested epics
            seen.add(above)
            held.setdefault(above, set()).add(snapshot.folds[item.id].owner)
            above = snapshot.items[above].parent
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
