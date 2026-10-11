"""`qew check`: validate every file of a ledger without writing anything."""

from dataclasses import dataclass, replace
import json
import tomllib

from qew import attachments, comments, board_config, ledger_aliases, ledger_graph, ledger_labels, ledger_invariants, stale_claims, text_findings as text_findings_module
from qew.errors import QewError
from qew.graph_rules import PARENT_KINDS
from qew.jsontypes import JsonObject
from qew.ledger_aliases import ITEM_FILE
from qew.code_vcs import Landing
from qew.evidence_resolve import Resolver
from qew.ledger_config import LEDGER_FILE, SCHEMA_VERSIONS
from qew.ledger_event import Event, State, attachments_after, fold, needs_after, parse_event, valid_actor
from qew.ledger_ids import sort_key
from qew.ledger_item import Item, Kind, check_header_values, clean_item_text, parse_item
from qew.ledger_store import Files, Ledger, Snapshot
from qew.snapshot_cache import Unreadable

SCHEMA_VERSION = 1
_MIRROR_KEYS = ("forge", "url", "kind")
MAX_PRIORITY = 9
DRAFT_MARKERS = ("draft", "qew:draft")


def check(ledger: Ledger, deep: bool = True) -> list[str]:
    """The problems found; `deep` also re-hashes every attachment (sync passes False: it only needs the structure)."""
    return check_report(ledger, deep)[0]


def load(ledger: Ledger, verify: bool = False) -> tuple[Snapshot, list[str]]:
    """Parse every file of the ledger: the snapshot of what parsed and the structural problems.

    Structural problems (unreadable or malformed files, duplicate ids, orphaned history, illegal
    transitions, bad metadata) make a ledger unreadable; edge and mirror problems are `check`'s own.
    """
    problems: list[str] = []
    _check_metadata(ledger, problems)
    items: dict[str, Item] = {}
    claimed: dict[str, str] = {}
    folds = {}
    events = {}
    files = ledger.read_files(verify)
    aliases = files.aliases
    owners: dict[str, str] = {}
    for stem in files.ids:  # a leftover under an old id is skipped: the renamed copy holds the item (`qew migrate-ids` removes it)
        where = f"items/{stem}/{ITEM_FILE}"
        try:
            if isinstance(files.texts[stem], Unreadable):
                raise QewError(str(files.texts[stem]), 1)
            item = parse_item(clean_item_text(files.texts[stem], where), where)
        except (QewError, OSError, UnicodeError) as error:
            problems.append(str(error))
            continue
        if item.id != stem:
            problems.append(f"{where}: header id {item.id} does not match the directory name")
        if item.id.lower() in claimed:
            problems.append(f"{where}: duplicate id {item.id} (also in {claimed[item.id.lower()]})")
            continue
        claimed[item.id.lower()] = where
        problems.extend(f"{where}: {problem}" for problem in check_header_values(item))
        for alias in item.aliases:
            if owners.setdefault(alias, item.id) != item.id:
                problems.append(f"{where}: alias {alias} is also an alias of {owners[alias]}")
        events[item.id] = _load_events(files, stem, problems)
        items[item.id] = replace(item, needs=needs_after(item.needs, events[item.id]))
        result = fold(events[item.id])
        folds[item.id] = result
        if not events[item.id]:
            problems.append(f"{where}: no events (an item starts with `drafted` or `approved`)")
        problems.extend(f"events/{item.id}: {error}" for error in result.errors)
        problems.extend(
            f"events/{item.id}/{event.id}: actor is not a single token without spaces or commas"
            for event in events[item.id]
            if not valid_actor(event.actor)
        )
    for alias, owner in sorted(owners.items()):
        if alias in items:
            problems.append(f"items/{owner}/item.md: alias {alias} is also the id of another item")
    items = {item_id: ledger_aliases.canonical_item(item, aliases) for item_id, item in items.items()}
    _check_orphans(ledger, items, aliases, problems)
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
    problems: list[str]
    notes: list[str]
    cycles: list[list[str]]
    contradictions: list[dict[str, str]]
    text_findings: list[dict[str, object]]
    advisories: list[str]

    def as_json(self) -> JsonObject:
        return {
            "schema_version": SCHEMA_VERSION, "valid": not self.problems, "cycles": self.cycles,
            "contradictions": self.contradictions, "text_findings": self.text_findings, "problems": self.problems,
        }


def check_report(ledger: Ledger, deep: bool = True, resolve: Resolver | None = None) -> tuple[list[str], list[str]]:
    """(problems, notes): problems fail the check; notes (counts and advisories) are reported and never fatal."""
    report = analyse(ledger, deep, resolve)
    return report.problems, [*report.notes, *report.advisories]


def analyse(ledger: Ledger, deep: bool = True, resolve: Resolver | None = None) -> Report:
    snapshot, problems = load(ledger, verify=deep)  # a deep check reads every file itself and repairs the snapshot cache
    items = snapshot.items
    _check_attachments(snapshot, problems, deep)
    _check_edges(snapshot, problems)
    unknown_evidence = _check_evidence_resolves(snapshot, problems, resolve) if resolve is not None else 0
    imported, without_code, by_quote = _check_completed(snapshot, problems, ledger.evidence_strict)
    _check_mirrors(ledger, items, problems)
    _check_mirror_progress(snapshot, problems)
    _check_claim_refs(snapshot, problems)
    problems.extend(board_config.problems(ledger))
    warnings: list[str] = []
    ledger_invariants.report(snapshot, problems, warnings)
    problems.extend(f"items/{item.id}/item.md: {found}" for item in snapshot.items.values()
                    for found in map(ledger_labels.problem, item.labels) if found)  # flagged by check; reads stay available
    found = contradictions(snapshot)
    findings = text_findings(snapshot)
    notes = [*warnings]
    if deep and ledger.cache_mismatches():
        notes.append(f"snapshot cache: {ledger.cache_mismatches()} entries differed from the files (repaired)")
    if imported:
        notes.append(f"imported without evidence: {imported}")
    if without_code:
        notes.append(f"completed without code: {without_code}")
    if by_quote:
        notes.append(f"completed on a human's quote (qew complete --manifest): {by_quote}")
    if unknown_evidence:
        notes.append(f"evidence ids unknown to this checkout (not fetched here yet?): {unknown_evidence}")
    contested = sum(1 for fold_ in snapshot.folds.values() for e in fold_.divergences if e.type == "claimed")
    if contested:
        notes.append(f"contested claims: {contested} (see `qew status`; settle with `qew accept` or `qew reject`)")
    notes.extend(_lost_claim_notes(snapshot))
    notes.extend(_stale_claim_notes(snapshot))
    return Report(problems, notes, _cycles(snapshot), found, findings, _advisories(found, findings))


def _check_metadata(ledger: Ledger, problems: list[str]) -> None:
    try:
        data = tomllib.loads((ledger.path / LEDGER_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        problems.append(f"{LEDGER_FILE}: {error}")
        return
    if data.get("schema_version", 1) not in SCHEMA_VERSIONS:
        problems.append(f"{LEDGER_FILE}: schema_version must be one of {', '.join(map(str, SCHEMA_VERSIONS))}")


def _load_events(files: Files, stem: str, problems: list[str]) -> list[Event]:
    found: list[Event] = []
    seen: set[str] = set()
    for home in files.homes[stem]:
        for name, text in files.rows.get(home, []):
            if name in seen:
                continue  # copied under the new id by a migration that stopped before removing this one
            seen.add(name)
            try:
                if isinstance(text, Unreadable):
                    raise QewError(str(text), 1)
                found.append(parse_event(name, text, f"events/{home}/{name}.json"))
            except QewError as error:
                problems.append(str(error))
    return sorted(found, key=lambda event: event.id)


def _check_attachments(snapshot: Snapshot, problems: list[str], deep: bool) -> None:
    """Every attached name has its stored file (size, and with `deep` the hash, matching) and every body link names an attachment."""
    ledger = snapshot.ledger
    for item_id, item in snapshot.items.items():
        tree = attachments_after(snapshot.events[item_id])
        for path, row in sorted(tree.items()):
            where = f"items/{item_id}/{attachments.DIRECTORY}/{row.blob}"
            blob = ledger.find_blob(item_id, row.blob)
            if blob is None:
                problems.append(f"{where}: missing, the stored file of attachment `{path}`")
            elif blob.stat().st_size != row.size:
                problems.append(f"{where}: size {blob.stat().st_size} does not match the {row.size} bytes attached as `{path}`")
            elif deep and attachments.hash_file(blob)[0] != row.digest:
                problems.append(f"{where}: the hash does not match attachment `{path}` (the file was changed)")
        problems.extend(comments.problems(item_id, snapshot.events[item_id], set(tree)))
        for name in attachments.REFERENCE.findall(item.body):
            if name not in tree:
                problems.append(f"items/{item_id}/item.md: the body links attachment:{name}, which is not attached")


def _check_orphans(ledger: Ledger, items: dict[str, Item], aliases: ledger_aliases.Aliases, problems: list[str]) -> None:
    for directory in (ledger.events_dir, ledger.mirrors_dir, ledger.items_dir):
        if directory.is_dir():
            for child in sorted(directory.iterdir()):
                if child.is_dir() and child.name not in items and aliases.canonical(child.name) not in items and (directory != ledger.items_dir or any(child.iterdir())):  # an empty item directory is a harmless leftover
                    problems.append(f"{directory.name}/{child.name}: belongs to no item")


def _check_edges(snapshot: Snapshot, problems: list[str]) -> None:
    for item in snapshot.items.values():
        where = f"items/{item.id}/item.md"
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


def _cited_ids(snapshot: Snapshot, item_id: str) -> set[str]:
    """Every change id the item's events cite: evidence events and what the completion recorded."""
    found: set[str] = set()
    for event in snapshot.events[item_id]:
        if event.type == "evidence":
            found.update(str(c) for c in event.get("change_ids", []))
        elif event.type == "completed":
            for key in ("evidence", "delivered_by", "commits"):
                found.update(str(c) for c in event.get(key, []) if isinstance(event.get(key), list))
    return found


def _check_evidence_resolves(snapshot: Snapshot, problems: list[str], resolve: Resolver) -> int:
    """Every id in a completed item's header that this checkout knows must be an ancestor of the target; returns how many ids
    it does not know (not fetched here yet, or another VCS), which are advisories, not errors."""
    unknown = 0
    resolve.prepare((change, str(e.get("vcs", ""))) for item_id, item in snapshot.items.items() if item.evidence
                    and snapshot.folds[item_id].state == State.COMPLETED for e in snapshot.events[item_id] if e.type == "evidence"
                    for change in e.get("change_ids", []))
    resolve.prepare((change, "") for item in snapshot.items.values() if item.evidence for change in item.evidence)
    for item_id, item in snapshot.items.items():
        if not item.evidence or snapshot.folds[item_id].state != State.COMPLETED:
            continue
        kinds = {str(c): str(e.get("vcs", "")) for e in snapshot.events[item_id] if e.type == "evidence" for c in e.get("change_ids", [])}
        for change in item.evidence:
            where = resolve(change, kinds.get(change, ""))
            if where is Landing.NOT_ANCESTOR:
                problems.append(f"items/{item_id}/item.md: evidence {change} is not an ancestor of the target")
            elif where is Landing.UNKNOWN:
                unknown += 1
    return unknown


def _check_completed(snapshot: Snapshot, problems: list[str], strict: bool) -> tuple[int, int, int]:
    """(imported without evidence, completed with an explicit no-code reason, completed on a human's quote); counted, not fatal.

    Under a strict ledger every completed item shows its proof in its own header: change ids that its events back, or a typed
    absence (`evidence_absent`)."""
    imported = without_code = by_quote = 0
    for item_id, item in snapshot.items.items():
        folded = snapshot.folds[item_id]
        if folded.state != State.COMPLETED:
            continue
        where = f"items/{item_id}/item.md"
        if ledger_graph.GATED_LABEL in item.labels:
            problems.append(f"{where}: completed while still carrying the label `gated` (reopen it, or lift the gate with a reason)")
        if item.evidence:
            unbacked = sorted(set(item.evidence) - _cited_ids(snapshot, item_id))
            if unbacked:
                problems.append(f"{where}: evidence {' '.join(unbacked)} is not cited by any of the item's events")
        elif strict and not item.evidence_absent:
            problems.append(f"{where}: completed without evidence ids or a typed absence (header evidence / evidence_absent)")
        if item.kind == Kind.EPIC or item.evidence or folded.evidence:
            continue
        if folded.imported or item.evidence_absent.startswith("historical:"):
            imported += 1
        elif (folded.completion is not None and (folded.completion.get("no_commit") or folded.completion.get("absent") == "no-code")) \
                or item.evidence_absent.startswith("no-code:"):
            without_code += 1
        elif folded.completion is not None and folded.completion.get("bulk_quote"):
            by_quote += 1
        elif item.evidence_absent or not strict:
            continue  # superseded-by, children: stated absences, not counted
        else:
            problems.append(f"events/{item_id}: completed without an evidence event")
    return imported, without_code, by_quote


def _stale_claim_notes(snapshot: Snapshot) -> list[str]:
    """Claimed items whose recorded pull request merged: the fix is `qew land`, nothing is written."""
    stale = {item_id: stale_claims.merged_pull_requests(snapshot, item_id) for item_id in snapshot.items}
    stale = {item_id: numbers for item_id, numbers in stale.items() if numbers}
    if not stale:
        return []
    fixes = sorted({n for numbers in stale.values() for n in numbers})
    return [f"claimed but its pull request merged: {' '.join(sorted(stale))} (fix: {'; '.join(f'qew land {n}' for n in fixes)})"]


def _lost_claim_notes(snapshot: Snapshot) -> list[str]:
    """A merged double claim is not corruption: the oldest claim holds, the later actors are named."""
    return [
        f"lost claim: {item_id} {actor} (held by {folded.owner}, event {event_id})"
        for item_id, folded in snapshot.folds.items()
        for event_id, actor in folded.lost_claims
    ]

def _check_claim_refs(snapshot: Snapshot, problems: list[str]) -> None:
    for item_id in snapshot.items:
        by_id = {e.id: e for e in snapshot.events[item_id]}
        for event in snapshot.events[item_id]:
            if event.type in ("claim_lost", "claim_confirmed"):
                target = by_id.get(event.get("ref"))
                if target is None or target.type != "claimed":
                    problems.append(f"events/{item_id}/{event.id}: {event.type} names no `claimed` event of this item")


def _check_mirror_progress(snapshot: Snapshot, problems: list[str]) -> None:
    """A mirror cannot have synced an event the item does not have: that would hide pending work."""
    for item_id in snapshot.items:
        newest = max((e.id for e in snapshot.events[item_id]), default="")
        for mirror in snapshot.ledger.load_mirrors(item_id):
            claimed: object = mirror.get("synced_event", "")  # read from disk: not trusted to be text
            if isinstance(claimed, str) and claimed > newest:
                problems.append(f"mirrors/{item_id}/{mirror['_target']}.json: synced_event {claimed} is newer than the item's newest event")


def _check_mirrors(ledger: Ledger, items: dict[str, Item], problems: list[str]) -> None:
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


def text_findings(snapshot: Snapshot) -> list[dict[str, object]]:
    """Advisory: hidden or confusable text per item and field, as `{item, source, kind: true}`."""
    return [
        {"item": item_id, **finding}
        for item_id in sorted(snapshot.items, key=sort_key)
        for finding in text_findings_module.item_findings(snapshot, item_id)
    ]


def _advisories(found: list[dict[str, str]], findings: list[dict[str, object]]) -> list[str]:
    lines = [f"advisory: {c['item']} {c['kind']}" for c in found]
    lines += [f"advisory: {f['item']} {text_findings_module.where(f)}: {text_findings_module.describe(f)}" for f in findings]
    return lines


def _cycles(snapshot: Snapshot) -> list[list[str]]:
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


def contradictions(snapshot: Snapshot) -> list[dict[str, str]]:
    """Advisory: states the ledger can hold but a board or a plan cannot show (they never fail the check)."""
    found = []
    for item_id, item in sorted(snapshot.items.items()):
        state = snapshot.folds[item_id].state
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
