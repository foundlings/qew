"""Commands that create, change and read items: init, create, set, show, list, ready, blocked, graph."""

import json
from pathlib import Path
import sys
from typing import Callable, Dict, List, Optional

from qew import graph_rules, ledger_config, ledger_labels
from qew.errors import QewError
from qew.ledger_config import CONFIG, LEDGER_FILE, Config
from qew.ledger_event import Event, State, fold, parse_event
from qew.ledger_ids import valid_id
from qew.ledger_item import DEFAULT_PRIORITY, Item, Kind, from_header
from qew.ledger_store import Ledger, actor, now, open_ledger
from qew.requester import require_quote


def ledger_for(config: Config) -> Ledger:
    return open_ledger(config.ledger_path, config.prefix)


def init(ledger: Optional[str]) -> str:
    root = Path.cwd().resolve()
    config_path = root / CONFIG
    created = []
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            f'[ledger]\npath = "{ledger or "../" + root.name + "-ledger"}"\n',
            encoding="utf-8",
        )
        created.append(str(CONFIG))
    config = ledger_config.load(root)
    marker = config.ledger_path / LEDGER_FILE
    if not marker.exists():
        config.ledger_path.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            f'name = "{config.ledger_path.name}"\nschema_version = 1\n# id_mode = "time"  # or "letters" / "counter", or a table: [id_mode] epic = "letters"\n',
            encoding="utf-8",
        )
        created.append(str(marker))
    return "\n".join(f"created {path}" for path in created) or "already initialized"


def build_item(ledger: Ledger, spec: dict, resolved: Dict[str, str], known: List[str]) -> Item:
    def reference(value: str) -> str:
        if value.startswith("@"):
            if value[1:] not in resolved:
                raise QewError(f"unknown ref `{value}`", 2)
            return resolved[value[1:]]
        return ledger.resolve(value, known)

    parent = spec.get("parent") or ""
    kind = str(spec.get("kind", "issue"))
    if kind not in ledger.id_modes:
        raise QewError(f"kind must be epic, issue or task, not `{kind}`", 2)
    imported = spec.get("id")
    if imported is not None:
        if not valid_id(str(imported)):
            raise QewError(f"`{imported}` is not a valid id", 2)
        if str(imported).lower() in {k.lower() for k in known}:
            raise QewError(f"item {imported} already exists", 2)
    header = {
        "id": str(imported) if imported is not None else ledger.new_id(kind, known),
        "kind": kind,
        "title": str(spec.get("title", "")),
        "parent": reference(parent) if parent else "",
        "needs": " ".join(reference(need) for need in spec.get("needs", [])),
        "labels": " ".join(spec.get("labels", [])),
        "owner": "",
        "priority": str(spec.get("priority", DEFAULT_PRIORITY)),
        "created": str(spec.get("created") or now()),
    }
    ledger_labels.require_valid(spec.get("labels", []))
    return from_header(header, str(spec.get("body", "")), "new item")


def _import_events(item: Item, spec: dict) -> List[Event]:
    """The item's own events from an import file, validated as a whole before anything is written."""
    raw = spec.get("events")
    if not isinstance(raw, list) or not raw:
        raise QewError(f"{item.id}: --import needs a non-empty `events` list", 2)
    events = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise QewError(f"{item.id}: event {index} is not an object", 2)
        events.append(parse_event(f"{index:06d}", json.dumps(entry), f"{item.id} event {index}"))
    folded = fold(events)
    if folded.errors:
        raise QewError(f"{item.id}: {folded.errors[0]}", 2)
    if folded.state == State.COMPLETED and item.kind != Kind.EPIC and not folded.evidence and not folded.imported:
        raise QewError(f"{item.id}: completed without evidence or import provenance", 2)
    return events


def create(config: Config, specs: List[dict], quote: Optional[str], import_events: bool = False) -> List[str]:
    ledger = ledger_for(config)
    verified = require_quote(quote) if quote is not None else None
    known = ledger.item_ids()
    resolved: Dict[str, str] = {}
    built = []
    for spec in specs:
        if "events" in spec and not import_events:
            raise QewError("`events` in a file are accepted only with --import", 2)
        item = build_item(ledger, spec, resolved, known)
        if spec.get("ref"):
            resolved[str(spec["ref"])] = item.id
        built.append((item, _import_events(item, spec) if import_events else None))
        known.append(item.id)
    graph_rules.validate_edges({**ledger.snapshot().items, **{item.id: item for item, _ in built}}, [item.id for item, _ in built])
    lines = []
    for item, events in built:
        ledger.write_item(item)
        if events is not None:
            for event in events:
                ledger.append_event(item.id, event.type, at=event.at, by=event.actor, **event.data)
            state = fold(events).state
            lines.append(f"{item.id} {state.value if state else '?'} {item.title}")
            continue
        if verified:
            ledger.append_event(item.id, "approved", quote=verified)
        else:
            ledger.append_event(item.id, "drafted")
        lines.append(f"{item.id} {'approved' if verified else 'draft'} {item.title}")
    return lines


def set_fields(
    config: Config, references: List[str], changes: Dict[str, str], body: Optional[str], allow_closed: bool = False
) -> List[str]:
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    if "labels" in changes:
        ledger_labels.require_valid(changes["labels"].split())
    ids = list(dict.fromkeys(ledger.resolve(reference, list(snapshot.items)) for reference in references))
    if len(ids) > 1 and (body is not None or "title" in changes):
        raise QewError("--title and --body name one item; several items may share parent, needs, labels or priority", 2)
    for item_id in ids:
        state = snapshot.folds[item_id].state
        if state in (State.COMPLETED, State.CLOSED) and not allow_closed:
            raise QewError(f"{item_id} is {state.value}; pass --closed to edit finished work", 2)
    proposed = dict(snapshot.items)
    audit = []  # (id, key, old, new) in write order
    for item_id in ids:
        item = proposed[item_id]
        for key, value in changes.items():
            if key in ("needs", "parent", "tracks", "pr"):
                value = " ".join(ledger.resolve(part, list(snapshot.items)) for part in value.split())
            old = item.header()[key]
            if old == value:
                continue
            try:
                item = item.with_value(key, value)
            except QewError as error:
                raise QewError(str(error).split(": ", 1)[-1], 2) from None
            audit.append((item_id, key, old, value))
        proposed[item_id] = item
    graph_rules.validate_edges(proposed, ids)
    lines = []
    for item_id in ids:
        original = snapshot.items[item_id]
        if proposed[item_id] != original:
            ledger.write_item(proposed[item_id], overwrite=True)
        for _, key, old, new in (entry for entry in audit if entry[0] == item_id):
            ledger.append_event(item_id, "field", key=key, old=old, new=new)
            lines.append(f"{item_id} {key}: {old} -> {new}")
        if body is not None and body != original.body:
            ledger.write_item(Item(**{**proposed[item_id].__dict__, "body": body}), overwrite=True)
            ledger.append_event(item_id, "field", key="body", old="", new="(changed)")
            lines.append(f"{item_id} body changed")
    return lines or [f"{' '.join(ids)} unchanged"]
