"""Edge rules checked against the whole graph before any write: a bad edge never reaches storage.

`qew check` reports the same problems for hand-merged files; this module is the write-time half.
"""

from collections.abc import Iterable, Mapping

from qew.errors import QewError
from qew.ledger_item import Item, Kind

# Which parent kinds may hold each child kind: epic > issue > task, and a task may also sit directly under an epic.
PARENT_KINDS: dict[Kind, tuple[Kind, ...]] = {Kind.EPIC: (), Kind.ISSUE: (Kind.EPIC,), Kind.TASK: (Kind.ISSUE, Kind.EPIC)}


def _components(items: Mapping[str, Item]) -> dict[str, int]:
    """Strongly connected component number of every item along `needs` (iterative Tarjan, linear time)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    component: dict[str, int] = {}
    counted = 0
    for root in items:
        if root in index:
            continue
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(items[root].needs))]
        while work:
            node, neighbours = work[-1]
            descended = False
            for neighbour in neighbours:
                if neighbour not in items:
                    continue
                if neighbour not in index:
                    index[neighbour] = low[neighbour] = len(index)
                    stack.append(neighbour)
                    on_stack.add(neighbour)
                    work.append((neighbour, iter(items[neighbour].needs)))
                    descended = True
                    break
                if neighbour in on_stack:
                    low[node] = min(low[node], index[neighbour])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                number = counted
                counted += 1
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component[member] = number
                    if member == node:
                        break
    return component


def _cycle_path(items: Mapping[str, Item], start: str, members: set[str]) -> list[str]:
    """A path from `start` back to itself inside one component (iterative depth-first)."""
    seen = {start}
    path = [start]
    stack = [iter(items[start].needs)]
    while stack:
        for neighbour in stack[-1]:
            if neighbour == start:
                return path + [start]
            if neighbour in members and neighbour not in seen:
                seen.add(neighbour)
                path.append(neighbour)
                stack.append(iter(items[neighbour].needs))
                break
        else:
            stack.pop()
            path.pop()
    return [start, start]


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
    components = _components(items)  # parent cycles are impossible once nesting holds (epic > issue > task)
    sizes: dict[int, int] = {}
    for number in components.values():
        sizes[number] = sizes.get(number, 0) + 1
    for item_id in changed_ids:
        if sizes[components[item_id]] > 1:
            members = {i for i, number in components.items() if number == components[item_id]}
            raise QewError(f"needs cycle: {' -> '.join(_cycle_path(items, item_id, members))}", 2)


def _check_nesting(items: Mapping[str, Item], changed: list[str]) -> None:
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
