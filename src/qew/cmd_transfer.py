"""`qew export` writes items as Markdown files or JSON; `qew import DIR` reads such a tree back.

The tree is `DIR/items/<ID>.md` (the ledger's own item files) plus `DIR/events.json`, a summary that
import ignores. Import is mechanical: header-only parse, every file validated before the first write.
"""

import argparse
import json
from pathlib import Path

from qew import cmd_items, cmd_read, graph_rules, ledger_config, ledger_graph, ledger_labels, untrusted_text
from qew.errors import QewError
from qew.jsontypes import JsonObject
from qew.ledger_aliases import ITEM_FILE
from qew.ledger_config import Config
from qew.ledger_ids import sort_key, valid_id
from qew.ledger_item import Item, check_header_values, parse_item, render_item
from qew.ledger_store import Snapshot
from qew.requester import require_quote

MAX_FILE_BYTES = 1024 * 1024
USAGE = "export needs a directory or --json, not both and not neither"


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    export = commands.add_parser("export", help="write every item as Markdown files in a new directory, or as JSON")
    export.add_argument("directory", nargs="?", help="new or empty directory for items/ID.md files and events.json")
    export.add_argument("--json", action="store_true", help="print the items as the JSON array `create --from-file` accepts")


def run(arguments: argparse.Namespace) -> bool:
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


def as_specs(snapshot: Snapshot) -> list[JsonObject]:
    """Every item as a `create --from-file` object, each after the items it references (ties by id)."""
    remaining = {i: {*snapshot.items[i].needs, *([snapshot.items[i].parent] if snapshot.items[i].parent else [])}
                 for i in snapshot.items}
    ordered: list[str] = []
    while remaining:
        free = sorted((i for i, deps in remaining.items() if not deps), key=sort_key)
        if not free:
            raise QewError("parent edges form a cycle; fix the ledger (`qew check`) before exporting", 1)
        ordered.append(free[0])
        del remaining[free[0]]
        for deps in remaining.values():
            deps.discard(free[0])
    return [_spec(snapshot.items[i]) for i in ordered]


def _spec(item: Item) -> JsonObject:
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
            (directory / "items" / item_id).mkdir()
            (directory / "items" / item_id / ITEM_FILE).write_text(render_item(snapshot.items[item_id]), encoding="utf-8")
        (directory / "events.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as error:
        raise QewError(f"cannot write {directory}: {error}", 3) from None
    return len(ids)


def import_directory(config: Config, directory: str, quote: str | None) -> list[str]:
    """Read DIR/items/ID/item.md into the ledger as drafts (approved with a human's quote), ids kept."""
    verified = require_quote(quote) if quote is not None else None
    ledger = cmd_items.ledger_for(config)
    parsed = _read_tree(Path(directory))
    existing = ledger.snapshot().items
    taken = {i.lower() for i in existing}
    for item in parsed.values():
        if item.id.lower() in taken:
            raise QewError(f"item {item.id} already exists in the ledger; nothing was imported", 2)
    combined = {**existing, **parsed}
    for name, item in parsed.items():
        for reference in (*item.needs, *([item.parent] if item.parent else [])):
            if reference not in combined:
                raise QewError(f"{name}.md: refers to unknown item {reference}", 2)
    graph_rules.validate_edges(combined, [item.id for item in parsed.values()])
    lines = []
    for item_id in sorted(parsed, key=sort_key):
        item = parsed[item_id]
        ledger.write_item(item)
        if verified:
            ledger.append_event(item.id, "approved", quote=verified)
        else:
            ledger.append_event(item.id, "drafted")
        lines.append(f"{item.id} {'approved' if verified else 'draft'} {untrusted_text.show(item.title).text}")
    return lines


def _read_tree(root: Path) -> dict[str, Item]:
    items_dir = root / "items"
    if not items_dir.is_dir() or items_dir.is_symlink():
        raise QewError(f"{root}: no items directory (an exported tree holds items/ID/item.md files)", 2)
    parsed: dict[str, Item] = {}
    lowered: dict[str, str] = {}
    for directory in sorted(items_dir.iterdir(), key=lambda p: p.name):
        name = directory.name
        if directory.is_symlink():
            raise QewError(f"{name}: is a symbolic link; import reads regular files only", 2)
        path = directory / ITEM_FILE
        if path.is_symlink():
            raise QewError(f"{name}: {ITEM_FILE} is a symbolic link; import reads regular files only", 2)
        if not directory.is_dir() or not path.is_file():
            raise QewError(f"{name}: is not an item directory holding {ITEM_FILE}", 2)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise QewError(f"{name}: exceeds {MAX_FILE_BYTES} bytes", 2)
        try:
            item = parse_item(path.read_text(encoding="utf-8"), name)
        except QewError as error:
            raise QewError(str(error), 2) from None
        except (OSError, UnicodeError) as error:
            raise QewError(f"{name}: cannot read: {error}", 2) from None
        if item.id != name:
            raise QewError(f"{name}: header id {item.id} does not match the directory name", 2)
        problems = check_header_values(item)
        if problems:
            raise QewError(f"{name}: {problems[0]}", 2)
        ledger_labels.require_valid(item.labels)
        if item.id.lower() in lowered:
            raise QewError(f"{name}: duplicate id (also {lowered[item.id.lower()]})", 2)
        lowered[item.id.lower()] = name
        parsed[item.id] = item
    if not parsed:
        raise QewError(f"{items_dir}: holds no item directories", 2)
    return parsed
