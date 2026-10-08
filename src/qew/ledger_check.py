"""`qew check`: validate every file of a ledger without writing anything."""

from dataclasses import dataclass, replace
import json
from pathlib import Path
import tomllib
from typing import Dict, List, Tuple

from qew import board_config, ledger_graph, ledger_invariants, stale_claims, text_findings as text_findings_module
from qew.errors import QewError
from qew.graph_rules import PARENT_KINDS
from qew.ledger_config import LEDGER_FILE
from qew.ledger_event import State, fold, needs_after, parse_event
from qew.ledger_ids import sort_key
from qew.ledger_item import Item, Kind, check_header_values, parse_item
from qew.ledger_store import Ledger, Snapshot

SCHEMA_VERSION = 1
_MIRROR_KEYS = ("forge", "url", "kind")
MAX_PRIORITY = 9
DRAFT_MARKERS = ("draft", "qew:draft")


def check(ledger: Ledger) -> List[str]:
    return check_report(ledger)[0]


def load(ledger: Ledger) -> Tuple[Snapshot, List[str]]:
    """Parse every file of the ledger: the snapshot of what parsed and the structural problems.

    Structural problems (unreadable or malformed files, duplicate ids, orphaned history, illegal
    transitions, bad metadata) make a ledger unreadable; edge and mirror problems are `check`'s own.
    """
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
        events[item.id] = _load_events(ledger, item.id, problems)
        items[item.id] = replace(item, needs=needs_after(item.needs, events[item.id]))
        result = fold(events[item.id])
        folds[item.id] = result
        if not events[item.id]:
            problems.append(f"{where}: no events (an item starts with `drafted` or `approved`)")
        problems.extend(f"events/{item.id}: {error}" for error in result.errors)
    _check_orphans(ledger, items, problems)
    return Snapshot(ledger, items, folds, events), problems


def read_snapshot(ledger: Ledger) -> Snapshot:
    """The snapshot every read command answers from; an unreadable ledger is refused, never listed in part."""
    snapshot, problems = load(ledger)
    if problems:
        more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
        raise QewError(f"the ledger is not readable: {problems[0]}{more}; run `qew check`", 1)
    return snapshot


@dataclass(frozen=True)
class Report:
    problems: List[str]
    notes: List[str]
    cycles: List[List[str]]
    contradictions: List[Dict[str, str]]
    text_findings: List[Dict[str, object]]
    advisories: List[str]

    def as_json(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION, "valid": not self.problems, "cycles": self.cycles,
            "contradictions": self.contradictions, "text_findings": self.text_findings, "problems": self.problems,
        }


def check_report(ledger: Ledger):
    """(problems, notes): problems fail the check; notes (counts and advisories) are reported and never fatal."""
    report = analyse(ledger)
    return report.problems, [*report.notes, *report.advisories]


def analyse(ledger: Ledger) -> Report:
    snapshot, problems = load(ledger)
    items = snapshot.items
    _check_edges(snapshot, problems)
    imported, without_code = _check_completed(snapshot, problems)
    _check_mirrors(ledger, items, problems)
    _check_mirror_progress(snapshot, problems)
    _check_claim_refs(snapshot, problems)
    problems.extend(board_config.problems(ledger))
    ledger_invariants.report(snapshot, problems)
    found = contradictions(snapshot)
    findings = text_findings(snapshot)
    notes = [f"imported without evidence: {imported}"] if imported else []
    if without_code:
        notes.append(f"completed without code: {without_code}")
    contested = sum(1 for fold_ in snapshot.folds.values() for e in fold_.divergences if e.type == "claimed")
    if contested:
        notes.append(f"contested claims: {contested} (see `qew status`; settle with `qew accept` or `qew reject`)")
    notes.extend(_lost_claim_notes(snapshot))
    notes.extend(_stale_claim_notes(snapshot))
    return Report(problems, notes, _cycles(snapshot), found, findings, _advisories(found, findings))


def _check_metadata(ledger: Ledger, problems: List[str]) -> None:
    try:
        data = tomllib.loads((ledger.path / LEDGER_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        problems.append(f"{LEDGER_FILE}: {error}")
        return
    if data.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
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
        for key in ("tracks", "pr"):
            link = getattr(item, key)
            if link and link not in snapshot.items:
                problems.append(f"{where}: dangling {key} {link}")
        if item.parent:
            parent = snapshot.items.get(item.parent)
            if parent is None:
                problems.append(f"{where}: dangling parent {item.parent}")
            elif parent.kind not in PARENT_KINDS[item.kind]:
                problems.append(f"{where}: a {item.kind.value} cannot sit under a {parent.kind.value}")
    needs = {i.id: [n for n in i.needs if n in snapshot.items] for i in snapshot.items.values()}
    parents = {i.id: [i.parent] if i.parent in snapshot.items else [] for i in snapshot.items.values()}
    for label, edges in (("needs", needs), ("parent", parents)):
        for cycle in ledger_graph.find_cycles(edges):
            problems.append(f"{label} cycle: {' -> '.join(cycle)}")


def _check_completed(snapshot: Snapshot, problems: List[str]):
    """(imported without evidence, completed with an explicit no-code reason); both are counted, not fatal."""
    imported = without_code = 0
    for item_id, item in snapshot.items.items():
        folded = snapshot.folds[item_id]
        if folded.state == State.COMPLETED and item.kind != Kind.EPIC and not folded.evidence:
            if folded.imported:
                imported += 1
            elif folded.completion is not None and folded.completion.get("no_commit"):
                without_code += 1
            else:
                problems.append(f"events/{item_id}: completed without an evidence event")
    return imported, without_code


def _stale_claim_notes(snapshot: Snapshot) -> List[str]:
    """Claimed items whose recorded pull request merged: the fix is `qew land`, nothing is written."""
    stale = {item_id: stale_claims.merged_pull_requests(snapshot, item_id) for item_id in snapshot.items}
    stale = {item_id: numbers for item_id, numbers in stale.items() if numbers}
    if not stale:
        return []
    fixes = sorted({n for numbers in stale.values() for n in numbers})
    return [f"claimed but its pull request merged: {' '.join(sorted(stale))} (fix: {'; '.join(f'qew land {n}' for n in fixes)})"]


def _lost_claim_notes(snapshot: Snapshot) -> List[str]:
    """A merged double claim is not corruption: the oldest claim holds, the later actors are named."""
    return [
        f"lost claim: {item_id} {actor} (held by {folded.owner}, event {event_id})"
        for item_id, folded in snapshot.folds.items()
        for event_id, actor in folded.lost_claims
    ]

def _check_claim_refs(snapshot: Snapshot, problems: List[str]) -> None:
    for item_id in snapshot.items:
        by_id = {e.id: e for e in snapshot.events[item_id]}
        for event in snapshot.events[item_id]:
            if event.type in ("claim_lost", "claim_confirmed"):
                target = by_id.get(event.get("ref"))
                if target is None or target.type != "claimed":
                    problems.append(f"events/{item_id}/{event.id}: {event.type} names no `claimed` event of this item")


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


def text_findings(snapshot: Snapshot) -> List[Dict[str, object]]:
    """Advisory: hidden or confusable text per item and field, as `{item, source, kind: true}`."""
    return [
        {"item": item_id, **finding}
        for item_id in sorted(snapshot.items, key=sort_key)
        for finding in text_findings_module.item_findings(snapshot, item_id)
    ]


def _advisories(found: List[Dict[str, str]], findings: List[Dict[str, object]]) -> List[str]:
    lines = [f"advisory: {c['item']} {c['kind']}" for c in found]
    lines += [f"advisory: {f['item']} {text_findings_module.where(f)}: {text_findings_module.describe(f)}" for f in findings]
    return lines


def _cycles(snapshot: Snapshot) -> List[List[str]]:
    """Each needs or parent cycle once, rotated to start at its smallest id and closed back on it."""
    needs = {i.id: [n for n in i.needs if n in snapshot.items] for i in snapshot.items.values()}
    parents = {i.id: [i.parent] if i.parent in snapshot.items else [] for i in snapshot.items.values()}
    found = set()
    for edges in (needs, parents):
        for cycle in ledger_graph.find_cycles(edges):
            ring = cycle[:-1]
            start = ring.index(min(ring))
            found.add(tuple([*ring[start:], *ring[:start]]) + (min(ring),))
    return [list(cycle) for cycle in sorted(found)]


def contradictions(snapshot: Snapshot) -> List[Dict[str, str]]:
    """Advisory: states the ledger can hold but a board or a plan cannot show (they never fail the check)."""
    found = []
    for item_id, item in sorted(snapshot.items.items()):
        state = snapshot.folds[item_id].state
        if item.kind == Kind.EPIC and state == State.CLAIMED:
            found.append({"item": item_id, "kind": "claimed_epic"})
        if state not in (None, State.DRAFT) and any(label.lower() in DRAFT_MARKERS for label in item.labels):
            found.append({"item": item_id, "kind": "draft_marker_label"})
        if item.priority > MAX_PRIORITY:
            found.append({"item": item_id, "kind": "priority_out_of_range"})
        if state == State.COMPLETED:
            if any(n in snapshot.items and snapshot.folds[n].state not in (State.COMPLETED, State.CLOSED) for n in item.needs):
                found.append({"item": item_id, "kind": "completed_with_open_needs"})
        parent = snapshot.items.get(item.parent) if item.parent else None
        if parent is not None and snapshot.folds[parent.id].state == State.COMPLETED:
            if state not in (State.COMPLETED, State.CLOSED):
                found.append({"item": item_id, "kind": "open_child_of_completed_epic"})
    return found
