"""Why work is blocked: for each blocked item, the longest chain of unmet needs down to a root blocker."""


from qew.errors import QewError
from qew.ledger_event import State
from qew.ledger_graph import blockers, state_of
from qew.ledger_ids import sort_key
from qew.ledger_store import Snapshot

_OPEN = (State.DRAFT, State.APPROVED, State.CLAIMED)


def _unmet(snapshot: Snapshot, item_id: str) -> list[str]:
    """Unmet needs to walk through. A dangling, closed or completed id ends a chain and is never walked."""
    if item_id not in snapshot.items or (state_of(snapshot, item_id) or State.DRAFT) not in _OPEN:
        return []
    return sorted(blockers(snapshot, item_id), key=sort_key)


def root_state(snapshot: Snapshot, item_id: str) -> str:
    """What a chain ends on: `dangling` for a missing item, else the item's state."""
    if item_id not in snapshot.items:
        return "dangling"
    return (state_of(snapshot, item_id) or State.DRAFT).value


def chains(snapshot: Snapshot, ids: list[str]) -> dict[str, list[str]]:
    """The longest path [item, need, ... root] per id; equal lengths go to the lowest id. Iterative."""
    best: dict[str, list[str]] = {}
    for start in ids:
        stack = [(start, iter(_unmet(snapshot, start)))]
        on_path = {start}
        while stack:
            node, rest = stack[-1]
            descended = False
            for child in rest:
                if child in on_path:
                    raise QewError(f"needs cycle through {child} (run `qew check`)", 1)
                if child not in best:
                    stack.append((child, iter(_unmet(snapshot, child))))
                    on_path.add(child)
                    descended = True
                    break
            if not descended:
                stack.pop()
                on_path.discard(node)
                longest: list[str] = []
                for child in _unmet(snapshot, node):
                    if len(best[child]) > len(longest):
                        longest = best[child]
                best[node] = [node, *longest]
    return {item_id: best[item_id] for item_id in ids}
