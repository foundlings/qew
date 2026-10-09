"""Push ledger state to one mirror target.

A mirror file is written only after the forge accepted the call, so a failed
sync leaves nothing half-recorded. A crash between the forge accepting a create
and the mirror file landing is repaired by adopting the issue found through the
body marker. One target's failure never reaches another: callers run targets
independently and the report says what happened.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

from qew import sync_milestones, sync_plan
from qew.pacing import PaceStop, Pacer
from qew.forge import (
    DUPLICATE_MARKER,
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
    milestones_created: int = 0
    milestones_updated: int = 0
    milestones_unchanged: int = 0
    stopped: Optional[str] = None  # set when the forge went away and the rest of the target was not tried
    retry_after: float = 0.0  # seconds the forge asked to wait (rate limit), 0 when it named none


def sync_target(ledger: Ledger, target: Target, config: Config, only: Union[None, str, Sequence[str]] = None,
                deadline: Optional[float] = None, tick: Optional[Callable[[], None]] = None) -> TargetReport:
    snapshot = ledger.snapshot()
    report = TargetReport(target.slug)
    assignees = {str(k): str(v) for k, v in dict(target.mirror.options.get("assignees", {})).items()}
    ids = sync_plan.topological(snapshot, sync_plan.selected(snapshot, target.mirror.select))
    wanted = [only] if isinstance(only, str) else list(only or ())
    # an item this mirror's `select` leaves out has nothing to sync here
    chosen = [one for one in (ledger.resolve(w, list(snapshot.items)) for w in wanted) if one in ids] if wanted else None
    numbers: Dict[str, int] = {}
    for item_id in ids:
        record = _read_mirror(ledger, item_id, target.slug)
        if record:
            numbers[item_id] = record["number"]
    # a targeted sync reuses the board `project ensure` recorded and reads cards one by one: its cost follows the
    # items it touches, never the size of the board
    board = (_recorded_board(ledger, target) if chosen is not None else None) or (_board(target, report) if target.mirror.project is not None else None)
    if report.stopped:
        return report
    cached_layout = ledger.read_board_layout(target.slug) if board is not None else None
    if cached_layout is not None and chosen is not None:
        target.forge.adopt_board_layout(board, cached_layout)
    placements = _listing(target, report, board) if board is not None and chosen is None else None
    if report.stopped:
        return report
    # a targeted sync also refreshes the mirrored items whose card the change moves (dependents, parent epics)
    refresh = {*chosen, *(i for one in chosen for i in sync_plan.moved_by(snapshot, one) if i in numbers)} if chosen is not None else None
    todo = [item_id for item_id in ids if refresh is None or item_id in refresh]
    _run_items(ledger, snapshot, target, report, todo, numbers, assignees, board, placements, chosen is not None,
               deadline, tick, config.replication.forge_concurrency)
    if chosen is None and report.stopped is None and sync_milestones.enabled(target.mirror.options):
        sync_milestones.sync_milestones(ledger, snapshot, target, report, ids, numbers)
    if board is not None and chosen is None and report.stopped is None:
        _prune(ledger, snapshot, target, report, set(ids), board)
    layout = target.forge.board_layout(board) if board is not None else None
    if layout is not None and layout != cached_layout:
        ledger.write_board_layout(target.slug, layout)
    return report


def _merge(report: TargetReport, local: TargetReport) -> None:
    """Fold one item's report into the target's."""
    for name in ("created", "updated", "unchanged", "adopted", "pruned", "milestones_created", "milestones_updated", "milestones_unchanged"):
        setattr(report, name, getattr(report, name) + getattr(local, name))
    report.skipped.extend(s for s in local.skipped if s not in report.skipped)
    report.failures.extend(local.failures)
    report.warnings.extend(local.warnings)
    report.stopped = report.stopped or local.stopped
    report.retry_after = max(report.retry_after, local.retry_after)


def _levels(snapshot: Snapshot, ids: List[str]) -> List[List[str]]:
    """`ids` (needs and parents first) grouped so that every item's needs and parent sit in an earlier group:
    the items of one group do not depend on each other and may sync side by side."""
    wanted = set(ids)
    depth: Dict[str, int] = {}
    groups: List[List[str]] = []
    for item_id in ids:
        item = snapshot.items[item_id]
        deps = [d for d in (*item.needs, item.parent) if d and d in wanted]
        depth[item_id] = 1 + max((depth[d] for d in deps if d in depth), default=-1)
        while len(groups) <= depth[item_id]:
            groups.append([])
        groups[depth[item_id]].append(item_id)
    return groups


def _run_items(ledger: Ledger, snapshot: Snapshot, target: Target, report: TargetReport, ids: List[str], numbers: Dict[str, int],
               assignees: Dict[str, str], board: Optional[Board], placements: Optional[Dict[int, Placement]], recent: bool,
               deadline: Optional[float], tick: Optional[Callable[[], None]], workers: int) -> None:
    """Sync the items, `workers` at a time. An item's needs and parent are done before it starts; the first forge failure
    (a rate limit above all) stops every worker from starting another item, and the items not started stay pending."""
    halt = threading.Event()
    per_minute, per_hour = target.forge.create_pace()
    pacer = Pacer(int(target.mirror.options.get("create_per_minute", per_minute)),
                  int(target.mirror.options.get("create_per_hour", per_hour)))

    def pace() -> None:
        wait = pacer.acquire(None if deadline is None else max(0.0, deadline - time.monotonic()))
        if wait:
            raise PaceStop(wait)

    def one(item_id: str) -> Optional[TargetReport]:
        if halt.is_set():
            return None
        if deadline is not None and time.monotonic() > deadline:
            halt.set()
            local = TargetReport(target.slug)
            local.stopped = "time budget used; the rest stays pending"
            return local
        local = TargetReport(target.slug)
        try:
            _sync_item(ledger, snapshot, target, local, item_id, numbers, assignees, board, placements, recent, pace)
        except PaceStop as stop:
            local.stopped = f"paced: {stop}; a rerun continues"
            local.retry_after = stop.seconds
            halt.set()
        except sync_plan.InvalidAuthor as error:
            local.failures.append(f"{snapshot.short(item_id)}: author label: {error.detail}")
        except ForgeRejected as error:
            local.failures.append(f"{snapshot.short(item_id)}: {error.detail}")
        except ForgeError as error:
            local.failures.append(f"{snapshot.short(item_id)}: {error.detail}")
            _stop(local, error)
            halt.set()
        return local

    if workers <= 1:
        for item_id in ids:
            local = one(item_id)
            if local is None:
                break
            _merge(report, local)
            if halt.is_set():
                break
            if tick is not None:
                tick()
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for group in _levels(snapshot, ids):
            futures = [pool.submit(one, item_id) for item_id in group]
            for future in as_completed(futures):
                local = future.result()
                if local is None:
                    continue
                _merge(report, local)
                if tick is not None and not halt.is_set():
                    tick()
            if halt.is_set():
                break


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


def _recorded_board(ledger: Ledger, target: Target) -> Optional[Board]:
    """The board `project ensure` recorded for this target, without asking the forge to ensure it again."""
    if target.mirror.project is None:
        return None
    record = ledger.read_board(target.slug)
    if record is None:
        return None
    return Board(id=record["id"], url=record.get("url", ""), columns=tuple(COLUMNS), title=record.get("title", ""))


def _board(target: Target, report: TargetReport) -> Optional[Board]:
    try:
        return target.forge.ensure_board(COLUMNS, sync_plan.managed_filter(target.mirror.mode))
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
    recent: bool = False,
    pace: Callable[[], None] = lambda: None,
) -> None:
    item = snapshot.items[item_id]
    spec = sync_plan.issue_spec(snapshot, item, numbers, assignees, target.mirror.mode == "overlay")
    column = sync_plan.column(snapshot, item_id)
    record = _read_mirror(ledger, item_id, target.slug)
    fingerprint = sync_plan.fingerprint(spec, column if board else None)
    unchecked = False  # a scoped create whose duplicate guard runs after the card is placed
    if record is None:
        # A create this machine started and never recorded may have been applied: look for it among ALL issues first.
        interrupted = item_id in ledger.creating_intents(target.slug)
        ledger.record_creating(target.slug, item_id)
        remote, adopted, unchecked = _find_or_create(target.forge, item_id, spec, item.aliases, recent and not interrupted, pace)
        numbers[item_id] = remote.number
        # The create carried `spec`, so a created issue is already current; an adopted one is not.
        record = new_record(target, remote, snapshot, item_id, "" if adopted else fingerprint)
        ledger.write_mirror(item_id, target.slug, record)
        ledger.clear_creating(target.slug, item_id)
        if adopted:
            report.adopted += 1
            record = _push(ledger, target, record, item_id, spec, fingerprint)
        else:
            report.created += 1
    elif record.get("baselined") and not record.get("synced_hash"):
        # First contact from this machine: the pull took the forge's state as the baseline; do not push over it.
        ledger.write_mirror(item_id, target.slug, {**{k: v for k, v in record.items() if k != "_target"}, "synced_hash": fingerprint, "baselined": False})
        report.unchanged += 1
    elif record.get("synced_hash") != fingerprint:
        _push(ledger, target, record, item_id, spec, fingerprint)
        report.updated += 1
    else:
        report.unchanged += 1
    _link(ledger, snapshot, target, report, item_id, numbers, record)
    if board is not None:
        _place(ledger, snapshot, target, report, item_id, board, column, record, placements, fresh=unchecked)
    if unchecked:
        _settle_duplicate(ledger, snapshot, target, report, item_id, numbers, assignees, board, column, record, spec, fingerprint, placements)


def _find_or_create(forge: Forge, item_id: str, spec: IssueSpec, aliases: Tuple[str, ...] = (), recent: bool = False,
                    pace: Callable[[], None] = lambda: None):
    """(issue, adopted, unchecked): adopt the issue carrying the item's marker, or one still carrying the marker of an id
    it had before a migration. A scoped create (`recent`, no aliases) skips the lookup before the create and leaves the
    duplicate guard to `_settle_duplicate`, which runs once the card is on the board; `unchecked` says so."""
    if recent and not aliases:
        pace()
        return forge.create_issue(spec), False, True
    try:
        found = next((hit for hit in (forge.find_issue(marker_for(name), recent) for name in (item_id, *aliases)) if hit is not None), None)
    except Unsupported:
        found = None
    if found is not None:
        return found, True, False
    pace()
    created = forge.create_issue(spec)
    try:
        winner = forge.find_issue(marker_for(item_id), True)  # the newest page holds a concurrent creator
    except Unsupported:
        return created, False, False
    if winner is not None and winner.number < created.number:
        # Another checkout created the same item at the same moment; the lowest number wins everywhere.
        forge.update_issue(created.number, replace(spec, body=spec.body.replace(marker_for(item_id), DUPLICATE_MARKER),
                                                   open=False, close_reason="duplicate"))
        return winner, True, False
    return created, False, False


def _settle_duplicate(ledger, snapshot, target, report, item_id, numbers, assignees, board, column, record, spec, fingerprint, placements) -> None:
    """The duplicate guard of a scoped create, after the card is placed: a lower-numbered issue with the item's marker
    (an orphan of a crash, or another checkout's create) wins; ours closes, loses its card, and the mirror follows the winner."""
    try:
        winner = target.forge.find_issue(marker_for(item_id), True)
    except Unsupported:
        return
    mine = record["number"]
    if winner is None or winner.number >= mine:
        return
    target.forge.update_issue(mine, replace(spec, body=spec.body.replace(marker_for(item_id), DUPLICATE_MARKER),
                                            open=False, close_reason="duplicate"))
    if board is not None:
        try:
            target.forge.remove_from_board(board, mine)
        except Unsupported:
            pass
    numbers[item_id] = winner.number
    adopted = new_record(target, winner, snapshot, item_id, "")
    ledger.write_mirror(item_id, target.slug, {**adopted, "column": None, "priority": None})
    report.created -= 1
    report.adopted += 1
    adopted = _push(ledger, target, adopted, item_id, spec, fingerprint)
    _link(ledger, snapshot, target, report, item_id, numbers, adopted)
    if board is not None:
        _place(ledger, snapshot, target, report, item_id, board, column, adopted, placements)


def new_record(target: Target, remote: RemoteIssue, snapshot: Snapshot, item_id: str, fingerprint: str) -> dict:
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


def _place(ledger, snapshot, target, report, item_id, board, column, record, placements=None, fresh: bool = False) -> None:
    """Make the card show `column` and the item's priority, verified by reading the forge back.

    `fresh`: the issue was created a moment ago, so it has no card to read first."""
    current = _read_mirror(ledger, item_id, target.slug) or record
    priority, clamped = sync_plan.priority_option(snapshot.items[item_id].priority)
    if clamped:
        report.warnings.append(f"{snapshot.short(item_id)}: priority {snapshot.items[item_id].priority} shown as {priority}")
    wanted = Placement(column, priority)
    number = current["number"]
    try:
        verifiable = True
        if fresh and placements is None:
            seen = None
        elif placements is not None:
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
