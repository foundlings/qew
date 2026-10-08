"""`qew export` writes items as Markdown files or JSON.

The tree is `DIR/items/<ID>.md` (the ledger's own item files) plus `DIR/events.json`, a summary.
"""

import json
import os
from pathlib import Path
from typing import List

from qew import cmd_read, ledger_config, ledger_graph
from qew.errors import QewError
from qew.ledger_ids import sort_key, valid_id
from qew.ledger_item import Item, render_item
from qew.ledger_store import Snapshot



def register(commands) -> None:
    export = commands.add_parser("export", help="write every item as Markdown files in a new directory, or as JSON")
    export.add_argument("directory", nargs="?", help="new or empty directory for items/ID.md files and events.json")
    export.add_argument("--json", action="store_true", help="print the items as the JSON array `create --from-file` accepts")


def run(arguments) -> bool:
    if arguments.command != "export":
        return False
    if arguments.json == (arguments.directory is not None):
        raise QewError("export needs a directory or --json, not both and not neither (a directory or --json)", 2)
    snapshot = cmd_read.snapshot_for(ledger_config.load())
    _require_whole_graph(snapshot)
    if arguments.json:
        print(json.dumps(as_specs(snapshot), sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    else:
        print(f"exported {write_tree(snapshot, Path(arguments.directory))} items to {arguments.directory}")
    return True


def _require_whole_graph(snapshot: Snapshot) -> None:
    """An export of a graph with missing or circular edges would import into a broken ledger."""
    for item_id in sorted(snapshot.items, key=sort_key):
        item = snapshot.items[item_id]
        for need in item.needs:
            if need not in snapshot.items:
                raise QewError(f"{item_id}: dangling need {need}; fix the ledger (`qew check`) before exporting", 1)
        if item.parent and item.parent not in snapshot.items:
            raise QewError(f"{item_id}: dangling parent {item.parent}; fix the ledger (`qew check`) before exporting", 1)
    edges = {i: [n for n in item.needs] for i, item in snapshot.items.items()}
    for cycle in ledger_graph.find_cycles(edges):
        raise QewError(f"needs cycle {' -> '.join(cycle)}; fix the ledger (`qew check`) before exporting", 1)


def as_specs(snapshot: Snapshot) -> List[dict]:
    """Every item as a `create --from-file` object, each after the items it references (ties by id)."""
    remaining = {i: {*snapshot.items[i].needs, *([snapshot.items[i].parent] if snapshot.items[i].parent else [])}
                 for i in snapshot.items}
    ordered: List[str] = []
    while remaining:
        free = sorted((i for i, deps in remaining.items() if not deps), key=sort_key)
        if not free:
            raise QewError("parent edges form a cycle; fix the ledger (`qew check`) before exporting", 1)
        ordered.append(free[0])
        del remaining[free[0]]
        for deps in remaining.values():
            deps.discard(free[0])
    return [_spec(snapshot.items[i]) for i in ordered]


def _spec(item: Item) -> dict:
    return {
        "id": item.id,
        "kind": item.kind.value,
        "title": item.title,
        "parent": item.parent,
        "needs": list(item.needs),
        "labels": list(item.labels),
        "priority": item.priority,
        "created": item.created,
        "body": item.body,
    }


def write_tree(snapshot: Snapshot, directory: Path) -> int:
    if directory.is_symlink():
        raise QewError(f"{directory} is a symbolic link; export to a real directory", 2)
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise QewError(f"{directory} is not empty; export writes a new tree and never overwrites", 2)
    ids = sorted(snapshot.items, key=sort_key)
    for item_id in ids:
        if not valid_id(item_id):
            raise QewError(f"`{item_id}` is not a valid id", 1)
    summary = {
        item_id: [{"type": e.type, "at": e.at, "actor": e.actor} for e in snapshot.events[item_id]] for item_id in ids
    }
    try:
        (directory / "items").mkdir(parents=True)
        for item_id in ids:
            (directory / "items" / f"{item_id}.md").write_text(render_item(snapshot.items[item_id]), encoding="utf-8")
        (directory / "events.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as error:
        raise QewError(f"cannot write {directory}: {error}", 3) from None
    return len(ids)
