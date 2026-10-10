"""Derived structure: reverse edges, cycles, readiness. Nothing here is stored."""


from qew.errors import QewError
from qew.ledger_event import State
from qew.ledger_ids import sort_key
from qew.ledger_item import Kind
from qew.ledger_store import Snapshot


# A human gate: work carrying this label is never offered as ready, whatever else is true of it.
APPROVAL_LABEL = "needs:approval"  # waiting for a human to authorize the work
GATED_LABEL = "gated"  # blocked on something outside the repository: keys, credentials, hardware, a live account


def gated_refusal(item_id: str) -> str:
    return (f"cannot complete {item_id}: it carries the label `gated` (blocked on something outside the repository); "
            f"lift the gate first: qew set {item_id} --remove-label gated --reason TEXT")


def state_of(snapshot: Snapshot, item_id: str) -> State | None:
    return snapshot.folds[item_id].state


def blockers(snapshot: Snapshot, item_id: str) -> list[str]:
    """Needs that are not completed (dangling needs count as unmet)."""
    return [
        need
        for need in snapshot.items[item_id].needs
        if need not in snapshot.items or state_of(snapshot, need) != State.COMPLETED
    ]


def children(snapshot: Snapshot) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in snapshot.items.values():
        if item.parent:
            result.setdefault(item.parent, []).append(item.id)
    return {parent: sorted(ids) for parent, ids in result.items()}


def blocks(snapshot: Snapshot) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in snapshot.items.values():
        for need in item.needs:
            result.setdefault(need, []).append(item.id)
    return {need: sorted(ids) for need, ids in result.items()}


def ordered(snapshot: Snapshot, ids: list[str]) -> list[str]:
    return sorted(ids, key=lambda item_id: (snapshot.items[item_id].priority, sort_key(item_id)))


def _open_dependents(snapshot: Snapshot) -> dict[str, list[str]]:
    """For each item, the unfinished items that need it (completed and closed work needs nothing more)."""
    return {
        need: [i for i in ids if state_of(snapshot, i) not in (State.COMPLETED, State.CLOSED)]
        for need, ids in blocks(snapshot).items()
    }


def chain_depths(snapshot: Snapshot, roots: list[str]) -> dict[str, int]:
    """Longest chain of unfinished dependents below each root. Iterative; a needs cycle is refused."""
    dependents = _open_dependents(snapshot)
    depth: dict[str, int] = {}
    for root in roots:
        stack = [(root, iter(dependents.get(root, [])))]
        on_path = {root}
        while stack:
            node, rest = stack[-1]
            descended = False
            for child in rest:
                if child in on_path:
                    raise QewError(f"needs cycle through {child} (run `qew check`)", 1)
                if child not in depth:
                    stack.append((child, iter(dependents.get(child, []))))
                    on_path.add(child)
                    descended = True
                    break
            if not descended:
                stack.pop()
                on_path.discard(node)
                depth[node] = max((1 + depth[c] for c in dependents.get(node, [])), default=0)
    return depth


def ready(snapshot: Snapshot) -> list[str]:
    """Approved, unclaimed, not an epic, not awaiting a human gate, every need completed.

    Within a priority: the longest chain of unfinished work it unblocks first, then the most direct
    dependents, then id, so work that frees others is never starved by leaf work.
    """
    found = [
        item_id
        for item_id, item in snapshot.items.items()
        if item.kind != Kind.EPIC
        and APPROVAL_LABEL not in item.labels
        and GATED_LABEL not in item.labels
        and state_of(snapshot, item_id) == State.APPROVED
        and not blockers(snapshot, item_id)
    ]
    depth = chain_depths(snapshot, found)
    direct = _open_dependents(snapshot)
    return sorted(
        found,
        key=lambda i: (snapshot.items[i].priority, -depth[i], -len(direct.get(i, [])), sort_key(i)),
    )


def blocked(snapshot: Snapshot) -> list[str]:
    """Approved or claimed work that still has unmet needs."""
    return ordered(
        snapshot,
        [
            item_id
            for item_id in snapshot.items
            if state_of(snapshot, item_id) in (State.APPROVED, State.CLAIMED) and blockers(snapshot, item_id)
        ],
    )


WORK_STATES = ("ready", "blocked", "claimed", "plan", "needs_decision", "completed", "closed")


def work_state(snapshot: Snapshot, item_id: str) -> str:
    """The derived class of an item, most urgent first (see WORK_STATES for the order).

    An epic is a `plan`; a draft `needs_decision`; open work with an unmet need is `blocked` even when
    claimed (a stuck claim is the more urgent fact); otherwise `claimed` or `ready`.
    """
    state = state_of(snapshot, item_id)
    if state in (None, State.DRAFT):
        return "needs_decision"
    if state in (State.COMPLETED, State.CLOSED):
        return str(state.value)
    if snapshot.items[item_id].kind == Kind.EPIC:
        return "plan"
    if blockers(snapshot, item_id):
        return "blocked"
    return "claimed" if state == State.CLAIMED else "ready"


def find_cycles(edges: dict[str, list[str]]) -> list[list[str]]:
    """Every cycle found by depth-first search over `edges` (iterative, no recursion limit)."""
    cycles: list[list[str]] = []
    done: set[str] = set()
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
