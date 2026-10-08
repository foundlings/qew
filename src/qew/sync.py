"""Push ledger state to one mirror target.

A mirror file is written only after the forge accepted the call, so a failed
sync leaves nothing half-recorded. A crash between the forge accepting a create
and the mirror file landing is repaired by adopting the issue found through the
body marker. One target's failure never reaches another: callers run targets
independently and the report says what happened.
"""

from dataclasses import dataclass, field, replace
import time
from typing import Callable, Dict, List, Optional

from qew import sync_plan
from qew.forge import (
    COLUMNS, Board, Forge, ForgeError, ForgeRejected, ForgeUnavailable, IssueSpec, Placement, RemoteIssue, Unsupported, marker_for,
)
from qew.ledger_config import Config, Mirror
from qew.ledger_store import Ledger, Snapshot, now


# After a board write the forge may not show it at once: read it back this often, this far apart.
READBACK_ATTEMPTS = 3
READBACK_DELAY = 1.0


@dataclass(frozen=True)
class Target:
    mirror: Mirror
    slug: str
    forge: Forge
    read_only: bool = False  # a one-way mirror: pull never imports its edits


@dataclass
class TargetReport:
    slug: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    adopted: int = 0
    skipped: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    pruned: int = 0
    stopped: Optional[str] = None  # set when the forge went away and the rest of the target was not tried
    retry_after: float = 0.0  # seconds the forge asked to wait (rate limit), 0 when it named none


def sync_target(ledger: Ledger, target: Target, config: Config, only: Optional[str] = None,
                deadline: Optional[float] = None, tick: Optional[Callable[[], None]] = None) -> TargetReport:
    snapshot = ledger.snapshot()
    report = TargetReport(target.slug)
    assignees = {str(k): str(v) for k, v in dict(target.mirror.options.get("assignees", {})).items()}
    ids = sync_plan.topological(snapshot, sync_plan.selected(snapshot, target.mirror.select))
    chosen = ledger.resolve(only, ids) if only else None
    numbers: Dict[str, int] = {}
    for item_id in ids:
        record = _read_mirror(ledger, item_id, target.slug)
        if record:
            numbers[item_id] = record["number"]
    board = _board(target, report) if target.mirror.project is not None else None
    if report.stopped:
        return report
    placements = _listing(target, report, board) if board is not None else None
    if report.stopped:
        return report
    for item_id in ids:
        if chosen and item_id != chosen:
            continue
        if deadline is not None and time.monotonic() > deadline:
            report.stopped = "time budget used; the rest stays pending"
            break
        try:
            _sync_item(ledger, snapshot, target, report, item_id, numbers, assignees, board, placements)
            if tick is not None:
                tick()
        except sync_plan.InvalidAuthor as error:
            report.failures.append(f"{snapshot.short(item_id)}: author label: {error.detail}")
        except ForgeRejected as error:
            report.failures.append(f"{snapshot.short(item_id)}: {error.detail}")
        except ForgeError as error:
            report.failures.append(f"{snapshot.short(item_id)}: {error.detail}")
            _stop(report, error)
            break
    if board is not None and chosen is None and report.stopped is None:
        _prune(ledger, snapshot, target, report, set(ids), board)
    return report


def _stop(report: TargetReport, error: ForgeError) -> None:
    """The forge went away: the rest of the target is not tried, and a named wait is kept."""
    report.stopped = f"{type(error).__name__}: {error.detail}"
    if isinstance(error, ForgeUnavailable):
        report.retry_after = max(report.retry_after, error.retry_after)


def _listing(target: Target, report: TargetReport, board: Board) -> Optional[Dict[int, Placement]]:
    """One listing of the whole board (None where the forge cannot list it, then cards are read one by one)."""
    try:
        return dict(target.forge.read_board_placements(board))
    except Unsupported:
        return None
    except ForgeError as error:
        report.failures.append(f"board: {error.detail}")
        _stop(report, error)
        return None


def _prune(ledger: Ledger, snapshot: Snapshot, target: Target, report: TargetReport, selected: set, board: Board) -> None:
    """Take cards of items that left the select off the board; their issues stay exactly as they are."""
    for item_id in snapshot.items:
        record = _read_mirror(ledger, item_id, target.slug)
        if item_id in selected or record is None or "column" not in record or record.get("kind") != "issue":
            continue
        try:
            target.forge.remove_from_board(board, record["number"])
        except Unsupported:
            return
        except ForgeError as error:
            report.failures.append(f"{snapshot.short(item_id)}: {error.detail}")
            continue
        clean = {k: v for k, v in record.items() if k not in ("_target", "column", "priority")}
        ledger.write_mirror(item_id, target.slug, clean)
        report.pruned += 1


def _read_mirror(ledger: Ledger, item_id: str, slug: str) -> Optional[dict]:
    return next((m for m in ledger.load_mirrors(item_id) if m["_target"] == slug), None)


def _board(target: Target, report: TargetReport) -> Optional[Board]:
    try:
        return target.forge.ensure_board(COLUMNS, sync_plan.managed_filter(target.mirror.select))
    except Unsupported as error:
        report.skipped.append(error.capability)
        return None
    except ForgeError as error:
        report.failures.append(f"board: {error.detail}")
        _stop(report, error)
        return None


def _sync_item(
    ledger: Ledger,
    snapshot: Snapshot,
    target: Target,
    report: TargetReport,
    item_id: str,
    numbers: Dict[str, int],
    assignees: Dict[str, str],
    board: Optional[Board],
    placements: Optional[Dict[int, Placement]] = None,
) -> None:
    item = snapshot.items[item_id]
    spec = sync_plan.issue_spec(snapshot, item, numbers, assignees)
    column = sync_plan.column(snapshot, item_id)
    record = _read_mirror(ledger, item_id, target.slug)
    fingerprint = sync_plan.fingerprint(spec, column if board else None)
    if record is None:
        remote, adopted = _find_or_create(target.forge, item_id, spec)
        numbers[item_id] = remote.number
        # The create carried `spec`, so a created issue is already current; an adopted one is not.
        record = _new_record(target, remote, snapshot, item_id, "" if adopted else fingerprint)
        ledger.write_mirror(item_id, target.slug, record)
        if adopted:
            report.adopted += 1
            record = _push(ledger, target, record, item_id, spec, fingerprint)
        else:
            report.created += 1
    elif record.get("synced_hash") != fingerprint:
        _push(ledger, target, record, item_id, spec, fingerprint)
        report.updated += 1
    else:
        report.unchanged += 1
    _link(ledger, snapshot, target, report, item_id, numbers, record)
    if board is not None:
        _place(ledger, snapshot, target, report, item_id, board, column, record, placements)


def _find_or_create(forge: Forge, item_id: str, spec: IssueSpec):
    try:
        found = forge.find_issue(marker_for(item_id))
    except Unsupported:
        found = None
    if found is not None:
        return found, True
    created = forge.create_issue(spec)
    try:
        winner = forge.find_issue(marker_for(item_id))
    except Unsupported:
        return created, False
    if winner is not None and winner.number < created.number:
        # Another checkout created the same item at the same moment; the lowest number wins everywhere.
        forge.update_issue(created.number, replace(spec, body=spec.body.replace(marker_for(item_id), "<!-- qew-duplicate -->"),
                                                   open=False, close_reason="not_planned"))
        return winner, True
    return created, False


def _new_record(target: Target, remote: RemoteIssue, snapshot: Snapshot, item_id: str, fingerprint: str) -> dict:
    events = snapshot.events[item_id]
    return {
        "forge": target.mirror.forge,
        "url": target.mirror.repo,
        "kind": "issue",
        "number": remote.number,
        "node_id": remote.node_id,
        "issue_url": remote.url,
        "synced_at": now(),
        "synced_event": events[-1].id if events else "",
        "synced_hash": fingerprint,
        "linked": [],
        "seen": sync_plan.seen_of(remote, []),
    }


def _push(ledger: Ledger, target: Target, record: dict, item_id: str, spec: IssueSpec, fingerprint: str) -> dict:
    remote = target.forge.update_issue(record["number"], spec)
    events = ledger.load_events(item_id)
    updated = {
        **{k: v for k, v in record.items() if k != "_target"},
        "seen": sync_plan.seen_of(remote, (record.get("seen") or {}).get("comments", [])),
        "synced_hash": fingerprint,
        "synced_at": now(),
        "synced_event": events[-1].id if events else "",
    }
    ledger.write_mirror(item_id, target.slug, updated)
    return updated


def _link(ledger, snapshot, target, report, item_id, numbers, record) -> None:
    linked = list(record.get("linked", []))
    missing = [n for n in snapshot.items[item_id].needs if n in numbers and n not in linked]
    for need in missing:
        try:
            target.forge.link_dependency(numbers[item_id], numbers[need])
        except Unsupported as error:
            if error.capability not in report.skipped:
                report.skipped.append(error.capability)
            return
        linked.append(need)
    if missing:
        clean = {k: v for k, v in record.items() if k != "_target"}
        ledger.write_mirror(item_id, target.slug, {**clean, "linked": sorted(linked)})


def _place(ledger, snapshot, target, report, item_id, board, column, record, placements=None) -> None:
    """Make the card show `column` and the item's priority, verified by reading the forge back."""
    current = _read_mirror(ledger, item_id, target.slug) or record
    priority, clamped = sync_plan.priority_option(snapshot.items[item_id].priority)
    if clamped:
        report.warnings.append(f"{snapshot.short(item_id)}: priority {snapshot.items[item_id].priority} shown as {priority}")
    wanted = Placement(column, priority)
    number = current["number"]
    try:
        verifiable = True
        if placements is not None:
            seen = placements.get(number)
        else:
            try:
                seen = target.forge.read_placement(board, number)
            except Unsupported:
                verifiable, seen = False, None
        if verifiable and seen == wanted:
            _record_placement(ledger, target, item_id, current, wanted)
            return
        if not verifiable and (current.get("column"), current.get("priority")) == (column, priority):
            return
        target.forge.place_on_board(board, number, column, priority)
        if verifiable and not _visible(target.forge, board, number, wanted):
            report.failures.append(
                f"{snapshot.short(item_id)}: board placement not visible after {READBACK_ATTEMPTS} reads; will retry"
            )
            return
    except Unsupported as error:
        if error.capability not in report.skipped:
            report.skipped.append(error.capability)
        return
    _record_placement(ledger, target, item_id, current, wanted)


def _visible(forge: Forge, board: Board, number: int, wanted: Placement) -> bool:
    for attempt in range(READBACK_ATTEMPTS):
        if attempt:
            time.sleep(READBACK_DELAY)
        if forge.read_placement(board, number) == wanted:
            return True
    return False


def _record_placement(ledger, target, item_id, current, wanted: Placement) -> None:
    if (current.get("column"), current.get("priority")) == (wanted.column, wanted.priority):
        return
    clean = {k: v for k, v in current.items() if k != "_target"}
    ledger.write_mirror(item_id, target.slug, {**clean, "column": wanted.column, "priority": wanted.priority})
