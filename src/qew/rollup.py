"""What an item's whole subtree is doing: claimed, partly finished, or waiting on a gate.

Computed once per snapshot (children before parents, no recursion) so the board column of every epic and issue can follow
tasks that are never mirrored. It is display state only: nothing here changes an item's own state.
"""

from dataclasses import dataclass

from qew import ledger_graph
from qew.ledger_event import State
from qew.ledger_store import Snapshot


@dataclass(frozen=True)
class Rollup:
    claimed: bool  # some descendant is claimed
    finished: bool  # some descendant is completed
    open_work: int  # descendants still to do, counted at the leaves of the open work
    gated_work: int  # of those, the ones carrying the gated label


_cache: tuple[Snapshot, dict[str, Rollup]] | None = None
_OPEN = (State.DRAFT, State.APPROVED, State.CLAIMED, None)


def of(snapshot: Snapshot) -> dict[str, Rollup]:
    """The roll-up of every item that has children, by item id."""
    global _cache
    if _cache is not None and _cache[0] is snapshot:
        return _cache[1]
    kids = ledger_graph.children(snapshot)
    result: dict[str, Rollup] = {}
    order: list[str] = []
    stack = [item_id for item_id in kids if snapshot.items[item_id].parent not in kids or snapshot.items[item_id].parent not in snapshot.items]
    seen: set[str] = set()
    while stack:  # post-order over the parent links; a cycle is skipped here and reported by `qew check`
        item_id = stack.pop()
        if item_id in seen:
            continue
        seen.add(item_id)
        order.append(item_id)
        stack.extend(child for child in kids.get(item_id, ()) if child in kids)
    for item_id in reversed(order):
        claimed = finished = False
        open_work = gated_work = 0
        for child in kids[item_id]:
            state = snapshot.folds[child].state
            below = result.get(child)
            claimed = claimed or state == State.CLAIMED or (below is not None and below.claimed)
            finished = finished or state == State.COMPLETED or (below is not None and below.finished)
            if below is not None and below.open_work:
                open_work += below.open_work
                gated_work += below.gated_work
            elif state in _OPEN:
                open_work += 1
                gated_work += ledger_graph.GATED_LABEL in snapshot.items[child].labels
        result[item_id] = Rollup(claimed, finished, open_work, gated_work)
    _cache = (snapshot, result)
    return result
