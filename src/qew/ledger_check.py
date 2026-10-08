"""`qew check`: validate every file of a ledger without writing anything."""

import json
from pathlib import Path
import tomllib
from typing import Dict, List

from qew import ledger_graph, untrusted_text
from qew.errors import QewError
from qew.ledger_config import LEDGER_FILE
from qew.ledger_event import State, fold, parse_event
from qew.ledger_item import Item, Kind, check_header_values, parse_item
from qew.ledger_store import Ledger, Snapshot

SCHEMA_VERSION = 1
_PARENT_KINDS = {Kind.EPIC: (), Kind.ISSUE: (Kind.EPIC,), Kind.TASK: (Kind.ISSUE, Kind.EPIC)}
_MIRROR_KEYS = ("forge", "url", "kind")


def check(ledger: Ledger) -> List[str]:
    return check_report(ledger)[0]


def check_report(ledger: Ledger):
    """(problems, notes): problems fail the check, notes are counts that are reported and not fatal."""
    problems: List[str] = []
    _check_metadata(ledger, problems)
    items: Dict[str, Item] = {}
    claimed: Dict[str, str] = {}
    folds = {}
    events = {}
    for path in sorted(ledger.items_dir.glob("*.md")) if ledger.items_dir.is_dir() else []:
        where = f"items/{path.name}"
        try:
            item = parse_item(path.read_text(encoding="utf-8"), where)
        except (QewError, OSError, UnicodeError) as error:
            problems.append(str(error))
            continue
        if item.id != path.stem:
            problems.append(f"{where}: header id {item.id} does not match the file name")
        if item.id.lower() in claimed:
            problems.append(f"{where}: duplicate id {item.id} (also in {claimed[item.id.lower()]})")
            continue
        claimed[item.id.lower()] = where
        problems.extend(f"{where}: {problem}" for problem in check_header_values(item))
        items[item.id] = item
        events[item.id] = _load_events(ledger, item.id, problems)
        result = fold(events[item.id])
        folds[item.id] = result
        if not events[item.id]:
            problems.append(f"{where}: no events (an item starts with `drafted` or `approved`)")
        problems.extend(f"events/{item.id}: {error}" for error in result.errors)
    _check_orphans(ledger, items, problems)
    snapshot = Snapshot(ledger, items, folds, events)
    _check_edges(snapshot, problems)
    imported = _check_completed(snapshot, problems)
    _check_mirrors(ledger, items, problems)
    _check_mirror_progress(snapshot, problems)
    problems.extend(f"text_findings: {item_id} {kind}" for item_id, kinds in text_findings(snapshot) for kind in kinds)
    return problems, ([f"imported without evidence: {imported}"] if imported else [])


def _check_metadata(ledger: Ledger, problems: List[str]) -> None:
    try:
        data = tomllib.loads((ledger.path / LEDGER_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        problems.append(f"{LEDGER_FILE}: {error}")
        return
    if data.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"{LEDGER_FILE}: schema_version must be {SCHEMA_VERSION}")


def _load_events(ledger: Ledger, item_id: str, problems: List[str]) -> list:
    directory = ledger.events_dir / item_id
    found = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            found.append(parse_event(path.stem, path.read_text(encoding="utf-8"), f"events/{item_id}/{path.name}"))
        except (QewError, OSError, UnicodeError) as error:
            problems.append(str(error))
    return found


def _check_orphans(ledger: Ledger, items: Dict[str, Item], problems: List[str]) -> None:
    for directory in (ledger.events_dir, ledger.mirrors_dir):
        if directory.is_dir():
            for child in sorted(directory.iterdir()):
                if child.is_dir() and child.name not in items:
                    problems.append(f"{directory.name}/{child.name}: belongs to no item")


def _check_edges(snapshot: Snapshot, problems: List[str]) -> None:
    for item in snapshot.items.values():
        where = f"items/{item.id}.md"
        for need in item.needs:
            if need not in snapshot.items:
                problems.append(f"{where}: dangling need {need}")
        if item.parent:
            parent = snapshot.items.get(item.parent)
            if parent is None:
                problems.append(f"{where}: dangling parent {item.parent}")
            elif parent.kind not in _PARENT_KINDS[item.kind]:
                problems.append(f"{where}: a {item.kind.value} cannot sit under a {parent.kind.value}")
    needs = {i.id: [n for n in i.needs if n in snapshot.items] for i in snapshot.items.values()}
    parents = {i.id: [i.parent] if i.parent in snapshot.items else [] for i in snapshot.items.values()}
    for label, edges in (("needs", needs), ("parent", parents)):
        for cycle in ledger_graph.find_cycles(edges):
            problems.append(f"{label} cycle: {' -> '.join(cycle)}")


def _check_completed(snapshot: Snapshot, problems: List[str]) -> int:
    imported = 0
    for item_id, item in snapshot.items.items():
        folded = snapshot.folds[item_id]
        if folded.state == State.COMPLETED and item.kind != Kind.EPIC and not folded.evidence:
            if folded.imported:
                imported += 1
            else:
                problems.append(f"events/{item_id}: completed without an evidence event")
    return imported


def _check_mirror_progress(snapshot: Snapshot, problems: List[str]) -> None:
    """A mirror cannot have synced an event the item does not have: that would hide pending work."""
    for item_id in snapshot.items:
        newest = max((e.id for e in snapshot.events[item_id]), default="")
        for mirror in snapshot.ledger.load_mirrors(item_id):
            claimed = mirror.get("synced_event", "")
            if isinstance(claimed, str) and claimed > newest:
                problems.append(f"mirrors/{item_id}/{mirror['_target']}.json: synced_event {claimed} is newer than the item's newest event")


def _check_mirrors(ledger: Ledger, items: Dict[str, Item], problems: List[str]) -> None:
    if not ledger.mirrors_dir.is_dir():
        return
    for path in sorted(ledger.mirrors_dir.glob("*/*.json")):
        where = f"mirrors/{path.parent.name}/{path.name}"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            problems.append(f"{where}: {error}")
            continue
        if not isinstance(value, dict) or any(key not in value for key in _MIRROR_KEYS):
            problems.append(f"{where}: a mirror needs {', '.join(_MIRROR_KEYS)}")


def text_findings(snapshot: Snapshot):
    """(item id, finding names) for every approved or claimed item whose title hides characters."""
    return [
        (item_id, untrusted_text.findings(item.title))
        for item_id, item in snapshot.items.items()
        if snapshot.folds[item_id].state in (State.APPROVED, State.CLAIMED) and untrusted_text.findings(item.title)
    ]
