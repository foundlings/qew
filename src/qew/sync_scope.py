"""Which items one `qew sync` run covers: the scope flags narrow it, and an unscoped run that would write too much is refused."""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from qew import instants, ledger_author, ledger_graph, read_filter
from qew.errors import QewError
from qew.ledger_store import Ledger, Snapshot


@dataclass(frozen=True)
class Scope:
    everything: bool = False  # --all: no size refusal
    since: datetime | None = None  # items with an event at or after this instant
    author: str = ""
    owner: str = ""
    tree: str = ""  # an item and everything below it
    limit: int = 0  # at most this many pending items (0: no cap)

    @property
    def narrows(self) -> bool:
        return bool(self.since or self.author or self.owner or self.tree)


class SyncTooLarge(QewError):
    """An unscoped sync would write more items than `[replication] sync_limit`."""


def needs_limit(scope: Scope, items: Sequence[str]) -> bool:
    """Whether the run is whole-ledger or filtered without an explicit cap: a bare `--item` list is the caller's own choice."""
    return not scope.everything and not scope.limit and not (items and not scope.narrows)


def chosen(ledger: Ledger, snapshot: Snapshot, items: Sequence[str], scope: Scope) -> list[str]:
    """The item ids the named items and every filter agree on, in id order."""
    ids = {ledger.resolve(one, list(snapshot.items)) for one in items} or set(snapshot.items)
    if scope.tree:
        root = ledger.resolve(scope.tree, list(snapshot.items))
        ids &= {root, *_below(snapshot, root)}
    if scope.author:
        ids = {i for i in ids if read_filter.fold(ledger_author.author(snapshot, i)) == read_filter.fold(scope.author)}
    if scope.owner:
        ids = {i for i in ids if read_filter.fold(snapshot.folds[i].owner) == read_filter.fold(scope.owner)}
    if scope.since is not None:
        start = instants.format_instant(scope.since)
        ids = {i for i in ids if any(event.at >= start for event in snapshot.events[i])}
    return sorted(ids)


def _below(snapshot: Snapshot, root: str) -> list[str]:
    kids = ledger_graph.children(snapshot)
    found: list[str] = []
    queue = deque(kids.get(root, []))
    while queue:
        item_id = queue.popleft()
        if item_id not in found:
            found.append(item_id)
            queue.extend(kids.get(item_id, []))
    return found


def refuse(count: int, targets: int, limit: int) -> SyncTooLarge:
    return SyncTooLarge(
        f"qew sync would write {count} item(s) to {targets} target(s), more than [replication] sync_limit ({limit}); "
        "narrow it with --tree ITEM, --since DATE, --author NAME, --owner NAME or --limit N, write it all with `qew sync --all`, "
        "or raise sync_limit (0 lifts it)", 2)
