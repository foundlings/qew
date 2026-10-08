"""Derived structure: reverse edges, cycles, readiness. Nothing here is stored."""

from typing import Dict, List, Optional

from qew.ledger_event import State
from qew.ledger_ids import sort_key
from qew.ledger_item import Kind
from qew.ledger_store import Snapshot


# A human gate: work carrying this label is never offered as ready, whatever else is true of it.
APPROVAL_LABEL = "needs:approval"


def state_of(snapshot: Snapshot, item_id: str) -> Optional[State]:
    return snapshot.folds[item_id].state


def blockers(snapshot: Snapshot, item_id: str) -> List[str]:
    """Needs that are not completed (dangling needs count as unmet)."""
    return [
        need
        for need in snapshot.items[item_id].needs
        if need not in snapshot.items or state_of(snapshot, need) != State.COMPLETED
    ]


def children(snapshot: Snapshot) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for item in snapshot.items.values():
        if item.parent:
            result.setdefault(item.parent, []).append(item.id)
    return {parent: sorted(ids) for parent, ids in result.items()}


def blocks(snapshot: Snapshot) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for item in snapshot.items.values():
        for need in item.needs:
            result.setdefault(need, []).append(item.id)
    return {need: sorted(ids) for need, ids in result.items()}


def ordered(snapshot: Snapshot, ids: List[str]) -> List[str]:
    return sorted(ids, key=lambda item_id: (snapshot.items[item_id].priority, sort_key(item_id)))


def ready(snapshot: Snapshot) -> List[str]:
    """Approved, unclaimed, not an epic, not awaiting a human gate, every need completed."""
    return ordered(
        snapshot,
        [
            item_id
            for item_id, item in snapshot.items.items()
            if item.kind != Kind.EPIC
            and APPROVAL_LABEL not in item.labels
            and state_of(snapshot, item_id) == State.APPROVED
            and not blockers(snapshot, item_id)
        ],
    )


def blocked(snapshot: Snapshot) -> List[str]:
    """Approved or claimed work that still has unmet needs."""
    return ordered(
        snapshot,
        [
            item_id
            for item_id in snapshot.items
            if state_of(snapshot, item_id) in (State.APPROVED, State.CLAIMED) and blockers(snapshot, item_id)
        ],
    )


def find_cycles(edges: Dict[str, List[str]]) -> List[List[str]]:
    """Every cycle found by depth-first search over `edges` (iterative, no recursion limit)."""
    cycles: List[List[str]] = []
    done: set = set()
    for start in sorted(edges):
        if start in done:
            continue
        stack = [(start, iter(sorted(edges.get(start, []))))]
        path = [start]
        on_path = {start}
        while stack:
            node, neighbours = stack[-1]
            advanced = False
            for neighbour in neighbours:
                if neighbour in on_path:
                    cycles.append(path[path.index(neighbour):] + [neighbour])
                elif neighbour not in done and neighbour in edges:
                    stack.append((neighbour, iter(sorted(edges.get(neighbour, [])))))
                    path.append(neighbour)
                    on_path.add(neighbour)
                    advanced = True
                    break
            if not advanced:
                stack.pop()
                done.add(node)
                on_path.discard(path.pop())
    return cycles
