"""Apply a validated plan to the ledger: build every item, check the whole graph, then write.

Re-running the same plan adopts what an earlier run already wrote instead of creating it again: the first
event of each created item carries the plan's sha256 (`plan`), the entry key (`key`) and the plan's file
name (`source`). An event is written before its item file, so a run killed between the two leaves an
orphan event that the rerun adopts by writing the missing item file.
"""

from dataclasses import dataclass
import json
import re
from typing import Dict, List, Optional, Tuple

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

SCHEMA_VERSION = 1
_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Planned:
    entry: Entry
    item: Item
    quote: Optional[str]  # the human's words that release it; None files a draft
    events: Optional[List[Event]]  # imported history, else None
    adopted: Optional[str] = None  # None (new), "item" (already complete) or "events" (orphan events, item file missing)

    @property
    def state(self) -> str:
        return "approved" if self.quote else "draft"


def _marker(first_event: dict) -> Optional[Tuple[str, str, str]]:
    """(plan digest, key, source) of a canonical provenance marker, else None: lookalikes are not provenance."""
    plan, key, source = first_event.get("plan"), first_event.get("key"), first_event.get("source")
    if isinstance(plan, str) and _DIGEST.fullmatch(plan) and isinstance(key, str) and key and isinstance(source, str):
        return plan, key, source
    return None


def _first_event(directory) -> Optional[dict]:
    for path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return None
        return value if isinstance(value, dict) else None
    return None


def _markers(ledger: Ledger) -> Dict[str, Tuple[str, str, str]]:
    """item id -> marker, for every item (or orphan event directory) whose first event carries one."""
    found: Dict[str, Tuple[str, str, str]] = {}
    if ledger.events_dir.is_dir():
        for directory in sorted(ledger.events_dir.iterdir()):
            first = _first_event(directory) if directory.is_dir() else None
            marker = _marker(first) if first else None
            if marker:
                found[directory.name] = marker
    return found


def _adoptions(ledger: Ledger, plan: Plan) -> Dict[str, str]:
    """key -> id of items an earlier run of this very plan created; refuses ambiguous or revised plans."""
    keys = {entry.key for entry in plan.entries}
    same: Dict[str, List[str]] = {}
    revised: List[str] = []
    for item_id, (digest, key, source) in _markers(ledger).items():
        if key not in keys:
            continue
        if digest == plan.digest:
            same.setdefault(key, []).append(item_id)
        elif plan.source != "-" and source == plan.source:
            revised.append(item_id)
    for key, ids in same.items():
        if len(ids) > 1:
            raise QewError(f"ambiguous: entry `{key}` of this plan already exists as {' and '.join(sorted(ids))}", 2)
    if revised:
        raise QewError(
            f"plan revision mismatch: {plan.source} changed since it created {' '.join(sorted(revised))}; "
            "finish that work or file the new plan under another name",
            2,
        )
    return {key: ids[0] for key, ids in same.items()}


def _release(entry: Entry, default_quote: Optional[str], draft: bool) -> Optional[str]:
    """The human's words that release this entry, or None to file it as a draft."""
    spec = entry.spec
    own = spec.get("quote")
    verified = require_quote(own, f"{entry.label} quote") if own is not None else None
    if draft:
        if spec.get("approved") is True:
            raise QewError(f"{entry.label}: approved: true conflicts with --draft, which files every item as a draft", 2)
        return None
    if verified is None and default_quote is not None:
        verified = require_quote(default_quote)
    if spec.get("approved") is True and verified is None:
        raise QewError(f"{entry.label}: approved: true needs a quote (its own or --quote)", 2)
    return None if spec.get("approved") is False else verified


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
    ledger: Ledger, plan: Plan, default_quote: Optional[str], draft: bool, import_events: bool, mark: bool
) -> List[Planned]:
    """Every entry built and every rule checked; nothing has been written."""
    if draft and default_quote is not None:
        raise QewError("--draft files drafts; a --quote would release them (give one or the other)", 2)
    snapshot = ledger.snapshot()
    existing = list(snapshot.items)
    adopted = _adoptions(ledger, plan) if mark else {}
    for adopted_id in adopted.values():
        if adopted_id not in existing:
            existing.append(adopted_id)  # an orphan's id is in use even though no item file shows it yet
    taken = {name.lower() for name in existing}  # ids in use, for the explicit-id collision check
    latest: Dict[str, str] = {}  # newest id allocated per kind: all the id modes need to pick the next one
    ids: Dict[str, str] = {}
    for entry in plan.entries:
        if entry.key in adopted:
            ids[entry.key] = adopted[entry.key]
            continue
        imported = entry.spec.get("id")
        if imported is not None:
            if not valid_id(imported):
                raise QewError(f"{entry.label}: `{imported}` is not a valid id", 2)
            if imported.lower() in taken:
                raise QewError(f"{entry.label}: item {imported} already exists", 2)
            ids[entry.key] = imported
        else:
            kind = entry.spec.get("kind", "issue")
            ids[entry.key] = latest[kind] = ledger.new_id(kind, existing + list(latest.values()))
        taken.add(ids[entry.key].lower())

    def reference(value: str) -> str:
        # `@ref` names an entry; a bare id may be an item already in the ledger or an explicit `id` of this plan.
        return ids[value[1:]] if value.startswith("@") else ledger.resolve(value, list(dict.fromkeys(existing + list(ids.values()))))

    planned: List[Planned] = []
    for entry in plan.entries:
        spec = entry.spec
        if "events" in spec and not import_events:
            raise QewError(f"{entry.label}: `events` in a file are accepted only with --import", 2)
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
        item = from_header(header, spec.get("body") or "", entry.label)
        present = snapshot.items.get(item.id)
        how = None
        if entry.key in adopted:
            how = "item" if present else "events"
            item = present or item
        planned.append(
            Planned(
                entry,
                item,
                None if import_events else _release(entry, default_quote, draft),
                _import_events(item, spec) if import_events else None,
                how,
            )
        )
    graph_rules.validate_edges({**snapshot.items, **{p.item.id: p.item for p in planned}}, [p.item.id for p in planned])
    return planned


def operations(planned: List[Planned]) -> List[dict]:
    """The writes a run would do, in apply order: create (or adopt), then each item's parent and needs."""
    keys = {p.item.id: p.entry.key for p in planned}
    steps: List[dict] = []
    for p in planned:
        if p.adopted:
            steps.append({"op": "adopt", "ref": p.entry.key, "id": p.item.id, "title": p.item.title})
        else:
            steps.append({"op": "create", "ref": p.entry.key, "kind": p.item.kind.value, "title": p.item.title, "draft": not p.quote})
        if p.item.parent:
            steps.append({"op": "parent", "ref": p.entry.key, "parent": keys.get(p.item.parent, p.item.parent)})
        for need in p.item.needs:
            steps.append({"op": "need", "ref": p.entry.key, "needs": keys.get(need, need)})
    return steps


def apply(
    config: Config,
    plan: Plan,
    quote: Optional[str],
    draft: bool = False,
    import_events: bool = False,
    mark: bool = True,
    as_json: bool = False,
    dry_run: bool = False,
) -> List[str]:
    ledger = ledger_for(config)
    planned = prepare(ledger, plan, quote, draft, import_events, mark and not import_events)
    if dry_run:
        steps = operations(planned)
        if as_json:
            return [json.dumps({"schema_version": SCHEMA_VERSION, "plan_sha256": plan.digest, "operations": steps})]
        return [_describe(step) for step in steps]
    for p in planned:
        if p.adopted == "item":
            continue
        if p.events is not None:
            ledger.write_item(p.item)
            for event in p.events:
                ledger.append_event(p.item.id, event.type, at=event.at, by=event.actor, **event.data)
            continue
        if p.adopted != "events":
            extra = {"plan": plan.digest, "key": p.entry.key, "source": plan.source} if mark else {}
            if p.quote:
                ledger.append_event(p.item.id, "approved", quote=p.quote, **extra)
            else:
                ledger.append_event(p.item.id, "drafted", **extra)
        ledger.write_item(p.item)
    if as_json:
        return [json.dumps({"created": {p.entry.key: p.item.id for p in planned}, "adopted": [p.entry.key for p in planned if p.adopted]})]
    lines = []
    for p in planned:
        if p.events is not None:
            state = fold(p.events).state
            lines.append(f"{p.item.id} {state.value if state else '?'} {p.item.title}")
        else:
            lines.append(f"{p.item.id} {p.state} {p.item.title}")
    return lines


def _describe(step: dict) -> str:
    if step["op"] == "create":
        return f"create {step['ref']} {step['kind']} {step['title']!r}" + (" draft" if step["draft"] else "")
    if step["op"] == "adopt":
        return f"adopt {step['ref']} {step['id']}"
    if step["op"] == "parent":
        return f"parent {step['ref']} -> {step['parent']}"
    return f"need {step['ref']} -> {step['needs']}"
