"""Graph invariants `qew check` reports with counts: orphans, cross-epic parents, depth, needs on closed items."""

from collections.abc import Iterable

from qew.ledger_event import State
from qew.ledger_ids import sort_key
from qew.ledger_store import Snapshot

MAX_NEEDS_DEPTH = 30  # a longer chain is a warning: real corpora chain deeper
AREA = "area:"
_ORPHAN = "belongs to no item"


def _areas(labels: Iterable[str]) -> frozenset[str]:
    return frozenset(label[len(AREA):] for label in labels if label.startswith(AREA))


def cross_parent(snapshot: Snapshot) -> list[str]:
    """Children filed under a parent that shares none of their `area:` labels (only when both have one)."""
    found = []
    for item_id in sorted(snapshot.items, key=sort_key):
        item = snapshot.items[item_id]
        parent = snapshot.items.get(item.parent)
        if parent is None:
            continue
        own, theirs = _areas(item.labels), _areas(parent.labels)
        if own and theirs and not own & theirs:
            found.append(
                f"items/{item_id}.md: area {' '.join(sorted(own))} differs from parent {parent.id} (area {' '.join(sorted(theirs))})"
            )
    return found


def deep_needs(snapshot: Snapshot) -> list[str]:
    """Items whose longest needs chain exceeds the limit and that no deeper item builds on. Cycles are skipped."""
    depth: dict[str, int] = {}
    for start in snapshot.items:
        stack = [(start, iter(snapshot.items[start].needs))]
        on_path = {start}
        while stack:
            node, rest = stack[-1]
            descended = False
            for need in rest:
                if need in snapshot.items and need not in on_path and need not in depth:
                    stack.append((need, iter(snapshot.items[need].needs)))
                    on_path.add(need)
                    descended = True
                    break
            if not descended:
                stack.pop()
                on_path.discard(node)
                depth[node] = max((1 + depth[n] for n in snapshot.items[node].needs if n in depth), default=0)
    needed = {n for item in snapshot.items.values() for n in item.needs}
    return [
        f"items/{i}.md: needs chain of {depth[i]} edges exceeds the limit of {MAX_NEEDS_DEPTH}"
        for i in sorted(snapshot.items, key=sort_key)
        if depth[i] > MAX_NEEDS_DEPTH and i not in needed
    ]


def closed_needs(snapshot: Snapshot) -> list[str]:
    """Open work that needs a closed item: it can never become ready."""
    found = []
    for item_id in sorted(snapshot.items, key=sort_key):
        if (snapshot.folds[item_id].state or State.DRAFT) in (State.COMPLETED, State.CLOSED):
            continue
        for need in snapshot.items[item_id].needs:
            if need in snapshot.items and snapshot.folds[need].state == State.CLOSED:
                found.append(f"items/{item_id}.md: needs closed item {need}")
    return found


def report(snapshot: Snapshot, problems: list[str], notes: list[str]) -> None:
    """Add the invariant problems (and the counts line) to `problems`; a deep chain is only a warning in `notes`."""
    orphans = sum(1 for problem in problems if problem.endswith(_ORPHAN))
    cross_found, closed_found, deep_found = cross_parent(snapshot), closed_needs(snapshot), deep_needs(snapshot)
    problems.extend(cross_found)
    problems.extend(closed_found)
    notes.extend(deep_found)
    cross, closed, deep = len(cross_found), len(closed_found), len(deep_found)
    if orphans or cross or deep or closed:
        line = f"graph invariants: orphans={orphans} cross_epic={cross} depth={deep} closed_needs={closed}"
        (problems if orphans or cross or closed else notes).append(line)
