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
from typing import cast
from collections.abc import Callable, Mapping, Sequence

from qew import attachment_links, comment_sync, forge_divergence, forge_retry, sync_milestones, sync_plan
from qew.pacing import PaceState, PaceStop, Pacer
from qew.forge import (
    DUPLICATE_MARKER,
    COLUMNS, Board, Forge, ForgeError, ForgeRejected, ForgeSecondaryLimit, ForgeUnavailable, IssueSpec, Placement, RemoteIssue, Unsupported, marker_for,
)
from qew.mirror_types import MirrorRecord, updated
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
    attachment_link: attachment_links.Link | None = None  # filled by sync_target: where an attachment's stored file can be viewed
    card_fields: dict[int, dict[str, str]] | None = None  # filled by sync_target when `project_fields` is mapped: what each card shows


@dataclass
class TargetReport:
    slug: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    adopted: int = 0
    skipped: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pruned: int = 0
    milestones_created: int = 0
    milestones_updated: int = 0
    milestones_unchanged: int = 0
    stopped: str | None = None  # set when the forge went away and the rest of the target was not tried
    retry_after: float = 0.0  # seconds the forge asked to wait (rate limit), 0 when it named none


def sync_target(ledger: Ledger, target: Target, config: Config, only: None | str | Sequence[str] = None,
                deadline: float | None = None, tick: Callable[[], None] | None = None) -> TargetReport:
    snapshot = ledger.snapshot()
    report = TargetReport(target.slug)
    target = replace(target, attachment_link=_attachment_link(ledger, target, config))
    assignees = {str(k): str(v) for k, v in dict(cast(Mapping[str, object], target.mirror.options.get("assignees", {}))).items()}
    ids = sync_plan.topological(snapshot, sync_plan.selected(snapshot, target.mirror.select))
    wanted = [only] if isinstance(only, str) else list(only or ())
    # an item this mirror's `select` leaves out has nothing to sync here
    chosen = [one for one in (ledger.resolve(w, list(snapshot.items)) for w in wanted) if one in ids] if wanted else None
    numbers: dict[str, int] = {}
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
    if board is not None and cached_layout is not None and chosen is not None:
        target.forge.adopt_board_layout(board, cached_layout)
    placements = _listing(target, report, board) if board is not None and chosen is None else None
    if report.stopped:
        return report  # type: ignore[unreachable]  # `_listing` sets it; mypy keeps the earlier narrowing
    if board is not None and chosen is None and isinstance(target.mirror.options.get("project_fields"), dict):
        try:
            target = replace(target, card_fields={n: dict(v) for n, v in target.forge.read_board_fields(board).items()})
        except Unsupported as error:
            report.skipped.append(error.capability)
        except ForgeError as error:
            report.failures.append(f"board fields: {error.detail}")
            _stop(report, error)
            return report
    # a targeted sync also refreshes the mirrored items whose card the change moves (dependents, parent epics)
    refresh = {*chosen, *(i for one in chosen for i in sync_plan.moved_by(snapshot, one) if i in numbers)} if chosen is not None else None
    todo = [item_id for item_id in ids if refresh is None or item_id in refresh]
    _run_items(ledger, snapshot, target, report, todo, numbers, assignees, board, placements, chosen is not None,
               deadline, tick, config.replication.forge_concurrency, config.replication.forge_failure_limit)
    if chosen is None and report.stopped is None and sync_milestones.enabled(target.mirror.options):
        sync_milestones.sync_milestones(ledger, snapshot, target, report, ids, numbers)
    if board is not None and chosen is None and report.stopped is None:
        _prune(ledger, snapshot, target, report, set(ids), board)
    layout = target.forge.board_layout(board) if board is not None else None
    if layout is not None and layout != cached_layout:
        ledger.write_board_layout(target.slug, layout)
    waits, waited = forge_retry.rate_summary()
    if waits:
        report.warnings.append(f"rate limited {waits} times; waited {waited:.0f}s in total, then went on")
    if forge_retry.retried_calls():
        report.warnings.append(f"{forge_retry.retried_calls()} forge calls retried after a 5xx or a lost connection")
    return report


def _sub_issue(ledger: Ledger, snapshot: Snapshot, target: Target, report: TargetReport, item_id: str, numbers: dict[str, int],
               record: MirrorRecord, pace: Callable[[], None]) -> MirrorRecord:
    """Show the ledger parent as the forge's own parent relation, set once and again only when the ledger parent moves.
    Each call is a mutation and counts against the create pacing; a mirror with `sub_issues = false` skips the mapping."""
    if target.mirror.options.get("sub_issues") is False:
        return record
    item = snapshot.items[item_id]
    desired = numbers.get(item.parent, 0) if item.parent else 0
    recorded = record.get("parent_number")
    if recorded == desired or (recorded is None and desired == 0):
        return record
    pace()
    try:
        target.forge.set_parent(record["number"], desired or None, recorded or None)
    except Unsupported as error:
        if error.capability not in report.skipped:
            report.skipped.append(error.capability)
        return record
    fresh = updated(record, parent_number=desired)
    ledger.write_mirror(item_id, target.slug, fresh)
    return fresh


def _attachment_link(ledger: Ledger, target: Target, config: Config) -> attachment_links.Link:
    """Links into the ledger repository's web view: the mirror's `ledger_web_url`, else the ledger checkout's origin."""
    address = target.mirror.options.get("ledger_web_url")
    base = attachment_links.web_base(address if isinstance(address, str) else attachment_links.origin_of(ledger.path))
    return attachment_links.link_for(base, config.replication.branch, target.forge.blob_url)


def _merge(report: TargetReport, local: TargetReport) -> None:
    """Fold one item's report into the target's."""
    for name in ("created", "updated", "unchanged", "adopted", "pruned", "milestones_created", "milestones_updated", "milestones_unchanged"):
        setattr(report, name, getattr(report, name) + getattr(local, name))
    report.skipped.extend(s for s in local.skipped if s not in report.skipped)
    report.failures.extend(local.failures)
    report.warnings.extend(local.warnings)
    report.stopped = report.stopped or local.stopped
    report.retry_after = max(report.retry_after, local.retry_after)


def _levels(snapshot: Snapshot, ids: list[str]) -> list[list[str]]:
    """`ids` (needs and parents first) grouped so that every item's needs and parent sit in an earlier group:
    the items of one group do not depend on each other and may sync side by side."""
    wanted = set(ids)
    depth: dict[str, int] = {}
    groups: list[list[str]] = []
    for item_id in ids:
        item = snapshot.items[item_id]
        deps = [d for d in (*item.needs, item.parent) if d and d in wanted]
        depth[item_id] = 1 + max((depth[d] for d in deps if d in depth), default=-1)
        while len(groups) <= depth[item_id]:
            groups.append([])
        groups[depth[item_id]].append(item_id)
    return groups


def _run_items(ledger: Ledger, snapshot: Snapshot, target: Target, report: TargetReport, ids: list[str], numbers: dict[str, int],
               assignees: dict[str, str], board: Board | None, placements: dict[int, Placement] | None, recent: bool,
               deadline: float | None, tick: Callable[[], None] | None, workers: int, failure_limit: int = 5) -> None:
    """Sync the items, `workers` at a time. An item's needs and parent are done before it starts; the first forge failure
    (a rate limit above all) stops every worker from starting another item, and the items not started stay pending."""
    halt = threading.Event()
    per_minute, per_hour = target.forge.create_pace()
    state = PaceState(ledger.pace_dir(target.slug))
    learned = state.window()  # what an earlier secondary-limit block taught about this account
    pacer = Pacer(int(cast(int, target.mirror.options.get("create_per_minute", per_minute))),
                  int(cast(int, target.mirror.options.get("create_per_hour", per_hour))), extra=(learned,) if learned else ())
    pacer.seed(state.ages())
    made = 0
    blocked = False
    run_lock = threading.Lock()
    in_a_row = 0  # consecutive items that failed on a 5xx or a lost connection

    def pace() -> None:
        nonlocal made
        wait = pacer.acquire(None if deadline is None else max(0.0, deadline - time.monotonic()))
        if wait:
            raise PaceStop(wait)
        state.record_create()
        made += 1

    def one(item_id: str) -> TargetReport | None:
        nonlocal blocked, in_a_row
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
        except ForgeSecondaryLimit as error:
            blocked = True
            state.record_block()
            local.failures.append(f"{snapshot.short(item_id)}: {error.detail}")
            _stop(local, error)
            halt.set()
        except ForgeUnavailable as error:
            local.failures.append(f"{snapshot.short(item_id)}: {error.detail}")
            if error.retry_after > 0:  # a rate limit: the forge named a wait, so every worker stops
                _stop(local, error)
                halt.set()
            else:
                with run_lock:
                    in_a_row += 1
                    over = in_a_row >= failure_limit
                if over:  # not one bad item: the forge or the connection is down
                    local.failures.append(f"{failure_limit} items in a row failed on the forge; the rest stays pending")
                    _stop(local, error)
                    halt.set()
        except ForgeError as error:
            local.failures.append(f"{snapshot.short(item_id)}: {error.detail}")
            _stop(local, error)
            halt.set()
        else:
            with run_lock:
                in_a_row = 0
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
        _relax(state, learned, made, blocked)
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
    _relax(state, learned, made, blocked)


def _relax(state: PaceState, learned: tuple[int, float] | None, made: int, blocked: bool) -> None:
    """A run with no block that reached the learned pace raises the learned budget, so one block does not cap the account."""
    if learned is not None and not blocked and made >= learned[0]:
        state.relax()


def _stop(report: TargetReport, error: ForgeError) -> None:
    """The forge went away: the rest of the target is not tried, and a named wait is kept."""
    report.stopped = f"{type(error).__name__}: {error.detail}"
    if isinstance(error, ForgeUnavailable):
        report.retry_after = max(report.retry_after, error.retry_after)


def _listing(target: Target, report: TargetReport, board: Board) -> dict[int, Placement] | None:
    """One listing of the whole board (None where the forge cannot list it, then cards are read one by one)."""
    try:
        return dict(target.forge.read_board_placements(board))
    except Unsupported:
        return None
    except ForgeError as error:
        report.failures.append(f"board: {error.detail}")
        _stop(report, error)
        return None


def _prune(ledger: Ledger, snapshot: Snapshot, target: Target, report: TargetReport, selected: set[str], board: Board) -> None:
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
        ledger.write_mirror(item_id, target.slug, cast(MirrorRecord, clean))
        report.pruned += 1


def _take_off_board(ledger: Ledger, snapshot: Snapshot, target: Target, report: TargetReport, item_id: str, board: Board,
                    record: MirrorRecord) -> None:
    """A diverged issue leaves the project board (its issue stays); settling the divergence puts the card back."""
    current = _read_mirror(ledger, item_id, target.slug) or record
    if "column" not in current or current.get("kind") != "issue":
        return
    try:
        target.forge.remove_from_board(board, current["number"])
    except Unsupported as error:
        if error.capability not in report.skipped:
            report.skipped.append(error.capability)
        return
    except ForgeError as error:
        report.failures.append(f"{snapshot.short(item_id)}: {error.detail}")
        return
    clean = {k: v for k, v in current.items() if k not in ("_target", "column", "priority", "fields")}
    ledger.write_mirror(item_id, target.slug, cast(MirrorRecord, clean))
    report.pruned += 1


def _read_mirror(ledger: Ledger, item_id: str, slug: str) -> MirrorRecord | None:
    return next((m for m in ledger.load_mirrors(item_id) if m["_target"] == slug), None)


def _recorded_board(ledger: Ledger, target: Target) -> Board | None:
    """The board `project ensure` recorded for this target, without asking the forge to ensure it again."""
    if target.mirror.project is None:
        return None
    record = ledger.read_board(target.slug)
    if record is None:
        return None
    return Board(id=record["id"], url=record.get("url", ""), columns=tuple(COLUMNS), title=record.get("title", ""))


def _board(target: Target, report: TargetReport) -> Board | None:
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
    numbers: dict[str, int],
    assignees: dict[str, str],
    board: Board | None,
    placements: dict[int, Placement] | None = None,
    recent: bool = False,
    pace: Callable[[], None] = lambda: None,
) -> None:
    item = snapshot.items[item_id]
    held = sync_plan.diverged_fields(snapshot, item_id, target.slug)
    diverged = bool(held)
    spec = sync_plan.issue_spec(snapshot, item, numbers, assignees, target.mirror.mode == "overlay", target.attachment_link, diverged)
    spec = replace(spec, issue_type=sync_plan.issue_type_for(target.mirror.options.get("issue_types"), item))
    if diverged:  # whatever was changed on the forge stays until a human accepts or rejects it
        spec = replace(spec, keep_state=True, issue_type="" if "type" in held else spec.issue_type)
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
        ledger.write_mirror(item_id, target.slug, updated(record, synced_hash=fingerprint, baselined=False))
        report.unchanged += 1
    elif record.get("synced_hash") != fingerprint:
        _push(ledger, target, record, item_id, spec, fingerprint)
        report.updated += 1
    else:
        report.unchanged += 1
        events = snapshot.events[item_id]
        newest = events[-1].id if events else ""
        if record.get("synced_event") != newest:
            # A comment or an accepted divergence changes no issue field: nothing to push, but the mirror is current.
            record = updated(record, synced_event=newest)
            ledger.write_mirror(item_id, target.slug, record)
    _link(ledger, snapshot, target, report, item_id, numbers, record)
    if record.get("kind") == "issue":
        record = comment_sync.push(ledger, snapshot, target, report, item_id, record, pace)
        if "parent" not in held:
            record = _sub_issue(ledger, snapshot, target, report, item_id, numbers, record, pace)
    if board is not None and diverged:
        _take_off_board(ledger, snapshot, target, report, item_id, board, record)
    elif board is not None:
        _place(ledger, snapshot, target, report, item_id, board, column, record, placements, fresh=unchecked)
        _card_fields(ledger, snapshot, target, report, item_id, board)
    if unchecked:
        _settle_duplicate(ledger, snapshot, target, report, item_id, numbers, assignees, board, column, record, spec, fingerprint, placements)


def _find_or_create(forge: Forge, item_id: str, spec: IssueSpec, aliases: tuple[str, ...] = (), recent: bool = False,
                    pace: Callable[[], None] = lambda: None) -> tuple[RemoteIssue, bool, bool]:
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


def _settle_duplicate(ledger: Ledger, snapshot: Snapshot, target: Target, report: TargetReport, item_id: str, numbers: dict[str, int],
                      assignees: dict[str, str], board: Board | None, column: str, record: MirrorRecord, spec: IssueSpec,
                      fingerprint: str, placements: dict[int, Placement] | None) -> None:
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
        _card_fields(ledger, snapshot, target, report, item_id, board)


def new_record(target: Target, remote: RemoteIssue, snapshot: Snapshot, item_id: str, fingerprint: str) -> MirrorRecord:
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


def _push(ledger: Ledger, target: Target, record: MirrorRecord, item_id: str, spec: IssueSpec, fingerprint: str) -> MirrorRecord:
    remote = target.forge.update_issue(record["number"], spec)
    events = ledger.load_events(item_id)
    seen = record.get("seen")
    refreshed = updated(record, seen=sync_plan.seen_of(remote, seen["comments"] if seen else []), synced_hash=fingerprint,
                        synced_at=now(), synced_event=events[-1].id if events else "")
    ledger.write_mirror(item_id, target.slug, refreshed)
    return refreshed


def _link(ledger: Ledger, snapshot: Snapshot, target: Target, report: TargetReport, item_id: str, numbers: dict[str, int],
          record: MirrorRecord) -> None:
    """Make the forge's blocked-by links match the ledger's needs: add the missing ones, remove the ones qew linked that the ledger
    dropped. A link qew did not record is never removed."""
    linked = list(record.get("linked", []))
    needs = snapshot.items[item_id].needs
    missing = [n for n in needs if n in numbers and n not in linked]
    stale = [n for n in linked if n not in needs and n in numbers]
    changed = False
    for need in missing:
        try:
            target.forge.link_dependency(numbers[item_id], numbers[need])
        except Unsupported as error:
            if error.capability not in report.skipped:
                report.skipped.append(error.capability)
            break
        linked.append(need)
        changed = True
    for need in stale:
        try:
            target.forge.unlink_dependency(numbers[item_id], numbers[need])
        except Unsupported as error:
            if error.capability not in report.skipped:
                report.skipped.append(error.capability)
            break
        linked.remove(need)
        changed = True
    if changed:
        ledger.write_mirror(item_id, target.slug, updated(record, linked=sorted(linked)))


def _place(ledger: Ledger, snapshot: Snapshot, target: Target, report: TargetReport, item_id: str, board: Board, column: str,
           record: MirrorRecord, placements: dict[int, Placement] | None = None, fresh: bool = False) -> None:
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


def _card_fields(ledger: Ledger, snapshot: Snapshot, target: Target, report: TargetReport, item_id: str, board: Board) -> None:
    """Set the card's mapped custom fields when the ledger side changed; a value a human changed on the card is a divergence."""
    mapping = target.mirror.options.get("project_fields")
    wanted = sync_plan.project_field_values(mapping, snapshot, snapshot.items[item_id])
    current = _read_mirror(ledger, item_id, target.slug)
    if not wanted or current is None:
        return
    if current.get("fields") == wanted:
        shown = (target.card_fields or {}).get(current["number"], {})
        for name, value in wanted.items():
            if name in shown and shown[name] != value:
                forge_divergence.record(ledger, snapshot, target.slug, item_id, current["number"], f"project:{name}", shown[name], value)
        return
    try:
        target.forge.set_card_fields(board, current["number"], wanted)
    except Unsupported as error:
        if error.capability not in report.skipped:
            report.skipped.append(error.capability)
        return
    ledger.write_mirror(item_id, target.slug, updated(current, fields=wanted))


def _visible(forge: Forge, board: Board, number: int, wanted: Placement) -> bool:
    for attempt in range(READBACK_ATTEMPTS):
        if attempt:
            time.sleep(READBACK_DELAY)
        if forge.read_placement(board, number) == wanted:
            return True
    return False


def _record_placement(ledger: Ledger, target: Target, item_id: str, current: MirrorRecord, wanted: Placement) -> None:
    if (current.get("column"), current.get("priority")) == (wanted.column, wanted.priority):
        return
    ledger.write_mirror(item_id, target.slug, updated(current, column=wanted.column, priority=wanted.priority))
