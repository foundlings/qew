"""Edge rules checked against the whole graph before any write: a bad edge never reaches storage.

`qew check` reports the same problems for hand-merged files; this module is the write-time half.
"""

from typing import Dict, Iterable, List, Mapping, Optional

from qew.errors import QewError
from qew.ledger_item import Item, Kind

# Which parent kinds may hold each child kind: epic > issue > task, and a task may also sit directly under an epic.
PARENT_KINDS: Dict[Kind, tuple] = {Kind.EPIC: (), Kind.ISSUE: (Kind.EPIC,), Kind.TASK: (Kind.ISSUE, Kind.EPIC)}


def _cycle_through(items: Mapping[str, Item], start: str, edges) -> Optional[List[str]]:
    """A path from `start` back to itself along `edges`, found iteratively (a long chain cannot overflow)."""
    seen = {start}
    path = [start]
    stack = [iter(edges(items[start]))]
    while stack:
        advanced = False
        for neighbour in stack[-1]:
            if neighbour == start:
                return path + [start]
            if neighbour in items and neighbour not in seen:
                seen.add(neighbour)
                path.append(neighbour)
                stack.append(iter(edges(items[neighbour])))
                advanced = True
                break
        if not advanced:
            stack.pop()
            path.pop()
    return None


def validate_edges(items: Mapping[str, Item], changed: Iterable[str]) -> None:
    """Refuse (exit 2) a graph where a changed item, or an item under a changed one, has a bad edge."""
    changed_ids = list(dict.fromkeys(changed))
    for item_id in changed_ids:
        item = items[item_id]
        if len(set(item.needs)) != len(item.needs):
            raise QewError(f"{item_id}: needs lists an item twice (duplicate need)", 2)
        for need in item.needs:
            if need == item_id:
                raise QewError(f"{item_id} cannot need itself", 2)
            if need not in items:
                raise QewError(f"{item_id}: needs unknown item {need}", 2)
        if item.parent == item_id:
            raise QewError(f"{item_id} cannot be its own parent", 2)
        if item.parent and item.parent not in items:
            raise QewError(f"{item_id}: unknown parent {item.parent}", 2)
    _check_nesting(items, changed_ids)
    for item_id in changed_ids:  # parent cycles are impossible once nesting holds (epic > issue > task)
        cycle = _cycle_through(items, item_id, lambda i: i.needs)
        if cycle:
            raise QewError(f"needs cycle: {' -> '.join(cycle)}", 2)


def _check_nesting(items: Mapping[str, Item], changed: List[str]) -> None:
    """The changed items under their parents, and the items under the changed ones (a kind change can orphan them)."""
    wanted = set(changed)
    for item in items.values():
        if not item.parent or item.parent not in items:
            continue
        if item.id not in wanted and item.parent not in wanted:
            continue
        parent = items[item.parent]
        if parent.kind not in PARENT_KINDS[item.kind]:
            raise QewError(f"{item.id}: a {item.kind.value} cannot sit under a {parent.kind.value} ({parent.id})", 2)
