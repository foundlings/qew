"""Apply a validated plan to the ledger: build every item, check the whole graph, then write."""

from dataclasses import dataclass
import json
from typing import Dict, List, Optional

from qew import graph_rules, ledger_labels
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import Event, State, fold, parse_event
from qew.ledger_ids import valid_id
from qew.ledger_item import DEFAULT_PRIORITY, Item, Kind, from_header
from qew.ledger_store import Ledger, now
from qew.plan_file import Entry, Plan
from qew.requester import require_quote


@dataclass(frozen=True)
class Planned:
    entry: Entry
    item: Item
    quote: Optional[str]  # the human's words that release it; None files a draft
    events: Optional[List[Event]]  # imported history, else None

    @property
    def state(self) -> str:
        return "approved" if self.quote else "draft"


def _release(entry: Entry, default_quote: Optional[str]) -> Optional[str]:
    return require_quote(default_quote) if default_quote is not None else None


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


def prepare(
    ledger: Ledger, plan: Plan, default_quote: Optional[str], import_events: bool
) -> List[Planned]:
    """Every entry built and every rule checked; nothing has been written."""
    snapshot = ledger.snapshot()
    known = list(snapshot.items)
    ids: Dict[str, str] = {}
    for entry in plan.entries:
        imported = entry.spec.get("id")
        if imported is not None:
            if not valid_id(imported):
                raise QewError(f"entry {entry.number}: `{imported}` is not a valid id", 2)
            if imported.lower() in {k.lower() for k in known}:
                raise QewError(f"entry {entry.number}: item {imported} already exists", 2)
            ids[entry.key] = imported
        else:
            ids[entry.key] = ledger.new_id(entry.spec.get("kind", "issue"), known)
        known.append(ids[entry.key])

    def reference(value: str) -> str:
        return ids[value[1:]] if value.startswith("@") else ledger.resolve(value, list(snapshot.items))

    planned: List[Planned] = []
    for entry in plan.entries:
        spec = entry.spec
        if "events" in spec and not import_events:
            raise QewError(f"entry {entry.number}: `events` in a file are accepted only with --import", 2)
        ledger_labels.require_valid(spec.get("labels", []))
        header = {
            "id": ids[entry.key],
            "kind": spec.get("kind", "issue"),
            "title": spec["title"],
            "parent": reference(spec["parent"]) if spec.get("parent") else "",
            "needs": " ".join(reference(need) for need in spec.get("needs", [])),
            "labels": " ".join(spec.get("labels", [])),
            "owner": "",
            "priority": str(spec.get("priority", DEFAULT_PRIORITY)),
            "created": spec.get("created") or now(),
        }
        item = from_header(header, spec.get("body") or "", f"entry {entry.number}")
        planned.append(
            Planned(
                entry,
                item,
                None if import_events else _release(entry, default_quote),
                _import_events(item, spec) if import_events else None,
            )
        )
    graph_rules.validate_edges({**snapshot.items, **{p.item.id: p.item for p in planned}}, [p.item.id for p in planned])
    return planned


def apply(
    config: Config, plan: Plan, quote: Optional[str], import_events: bool = False
) -> List[str]:
    ledger = ledger_for(config)
    planned = prepare(ledger, plan, quote, import_events)
    for p in planned:
        ledger.write_item(p.item)
        if p.events is not None:
            for event in p.events:
                ledger.append_event(p.item.id, event.type, at=event.at, by=event.actor, **event.data)
        elif p.quote:
            ledger.append_event(p.item.id, "approved", quote=p.quote)
        else:
            ledger.append_event(p.item.id, "drafted")
    lines = []
    for p in planned:
        if p.events is not None:
            state = fold(p.events).state
            lines.append(f"{p.item.id} {state.value if state else '?'} {p.item.title}")
        else:
            lines.append(f"{p.item.id} {p.state} {p.item.title}")
    return lines
