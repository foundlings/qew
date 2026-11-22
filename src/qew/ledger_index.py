"""The edge index: who is whose parent, who needs whom, who holds a claim, kept so a write verb need not parse the ledger to ask.

`complete` asks which items are children of an epic and which a completed item releases; `start` counts the claims its actor holds.
Those are questions about every item's parent, needs and claim, and answering them from the full snapshot parses 28,000 items (5 s,
three times for `complete`). This keeps one small record per item in `.qew/index.json` (local, rebuildable, never committed).

A record is trusted only while the signature of what it was read from is unchanged: the item file (mtime, size, inode) and the item's
events directory (mtime, inode: a new event is a new file there). Both are statted on every use (two stats per item, about 0.5 s for
28,000 items), and an item whose signature moved, or that is new, is read again through the lazy snapshot, so a qew write, a hand edit
and a git merge are all seen. A signature younger than `RACY_NS` is read again next time (a second change in the same timestamp tick
would go unseen). Without a file, or when most items changed, the full snapshot builds every record at once. A damaged file or
a format change is a miss: the records are read again from the ledger, never an error. An item that cannot be read is not skipped
(an epic would look finished without the child that is broken): the full snapshot is built, and it names the damage. An item that
carries old ids (aliases) is signed by every events directory it has. With nowhere to store the index (no `.qew`, or
`QEW_NO_CACHE=1`) it is built from the full snapshot each time, in memory.
"""

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from qew.errors import QewError
from qew.ledger_event import Event, Folded, State
from qew.ledger_item import Item

FORMAT = 1
FILE = "index.json"
RACY_NS = 2_000_000_000
REBUILD_FROM = 2000  # more changed items than this are cheaper to read through the full snapshot
RACY = "r"

type Read = Callable[[str], tuple[Item, list[Event], Folded]]


@dataclass(frozen=True)
class Node:
    parent: str
    needs: tuple[str, ...]
    claimed_by: str  # the owner of a claim, "" when the item is not claimed


class LedgerIndex:
    def __init__(self, nodes: dict[str, Node]) -> None:
        self.nodes = nodes
        self._children: dict[str, list[str]] | None = None
        self._blocks: dict[str, list[str]] | None = None

    def children(self) -> dict[str, list[str]]:
        """Parent -> its children, sorted: what `ledger_graph.children` builds from every item."""
        if self._children is None:
            found: dict[str, list[str]] = {}
            for item_id, node in self.nodes.items():
                if node.parent:
                    found.setdefault(node.parent, []).append(item_id)
            self._children = {parent: sorted(ids) for parent, ids in found.items()}
        return self._children

    def blocks(self) -> dict[str, list[str]]:
        """Need -> the items that need it, sorted: what `ledger_graph.blocks` builds from every item."""
        if self._blocks is None:
            found: dict[str, list[str]] = {}
            for item_id, node in self.nodes.items():
                for need in node.needs:
                    found.setdefault(need, []).append(item_id)
            self._blocks = {need: sorted(ids) for need, ids in found.items()}
        return self._blocks

    def claims_of(self, who: str) -> int:
        return sum(1 for node in self.nodes.values() if node.claimed_by == who)


def _node(item: Item, folded: Folded) -> Node:
    return Node(item.parent, tuple(item.needs), folded.owner if folded.state == State.CLAIMED and folded.owner else "")


def _signature(path: Path, directory: bool) -> tuple[str, int]:
    """(signature, mtime_ns) of a path, or ("-", 0) when it is missing."""
    try:
        status = os.stat(path)
    except OSError:
        return "-", 0
    ident = f"{status.st_mtime_ns}:{status.st_ino}" if directory else f"{status.st_mtime_ns}:{status.st_size}:{status.st_ino}"
    return ident, status.st_mtime_ns


def _restore(path: Path) -> dict[str, list[object]] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document["format"] != FORMAT or not isinstance(document["records"], dict):
            return None
        return {str(item_id): list(record) for item_id, record in document["records"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _persist(path: Path, records: dict[str, list[object]]) -> None:
    if not path.parent.is_dir():
        return
    temporary = path.with_name(f".{FILE}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps({"format": FORMAT, "records": records}, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)  # a read-only `.qew` keeps no index; every use reads the records again


def _record(item_signature: str, events_signature: str, racy: bool, node: Node) -> list[object]:
    return [item_signature, events_signature, RACY if racy else "", node.parent, list(node.needs), node.claimed_by]


def _usable(record: list[object] | None, item_signature: str, events_signature: str) -> Node | None:
    """The record's node when it was read from exactly these files and was not racy."""
    if record is None or len(record) != 6 or record[0] != item_signature or record[1] != events_signature or record[2] == RACY:
        return None
    parent, needs, claimed = record[3], record[4], record[5]
    if not isinstance(parent, str) or not isinstance(needs, list) or not isinstance(claimed, str):
        return None
    return Node(parent, tuple(str(need) for need in needs), claimed)


def current(store: Path | None, ids: list[str], item_file: Callable[[str], Path], events_dirs: Callable[[str], list[Path]], read: Read,
            full: Callable[[], tuple[Mapping[str, Item], Mapping[str, Folded]]]) -> LedgerIndex:
    """The index of these items as the files say now. `read` is the lazy reader of one item; `full` builds the whole snapshot and
    returns its (items, folds): used when there is nothing stored, too much changed, or an item cannot be read (it raises then)."""
    path = None if store is None else store / FILE
    stored = (_restore(path) if path is not None else None) or {}
    now = time.time_ns()
    signed: dict[str, tuple[str, str, bool]] = {}
    nodes: dict[str, Node] = {}
    stale: list[str] = []
    for item_id in ids:
        item_signature, item_time = _signature(item_file(item_id), False)
        homes = [_signature(home, True) for home in events_dirs(item_id)]
        events_signature = "|".join(signature for signature, _ in homes)
        newest = max([item_time, *(moment for _, moment in homes)])
        signed[item_id] = (item_signature, events_signature, newest > now - RACY_NS)
        kept = _usable(stored.get(item_id), item_signature, events_signature)
        if kept is None:
            stale.append(item_id)
        else:
            nodes[item_id] = kept
    if stale:
        if len(stale) > REBUILD_FROM or not stored:
            items, folds = full()
            for item_id in stale:
                nodes[item_id] = _node(items[item_id], folds[item_id])
        else:
            for item_id in stale:
                try:
                    item, _, folded = read(item_id)
                except QewError:
                    items, folds = full()  # raises the damage by name; if the damage was only a write in flight, this is a sound snapshot
                    for again in stale:
                        nodes[again] = _node(items[again], folds[again])
                    break
                nodes[item_id] = _node(item, folded)
    if path is not None and (stale or set(stored) != set(ids)):
        _persist(path, {item_id: _record(*signed[item_id], nodes[item_id]) for item_id in ids})
    return LedgerIndex(nodes)


class Lazy:
    """The index of a lazy snapshot, built the first time a question needs it: evidence reads no edges and must not pay for them."""

    def __init__(self, build: Callable[[], LedgerIndex]) -> None:
        self._build = build
        self._value: LedgerIndex | None = None

    def get(self) -> LedgerIndex:
        if self._value is None:
            self._value = self._build()
        return self._value
