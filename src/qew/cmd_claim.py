"""`qew start` and `qew release`: take a claim on approved work, or give one up.

Refusals are distinct for agent loops: 2 the work may not start or the usage is wrong, 3 someone else holds it
(or another lane of you does), 5 you already hold exactly this claim, 6 the per-actor claim cap is reached.
"""

import argparse
from collections import deque
from typing import Any

from qew import claim_authority, claim_fields, ledger_graph, outcome, replication
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import Folded, State, fold
from qew.ledger_item import Kind
from qew.ledger_store import Ledger, Snapshot, actor

_STATE_REASON: dict[State | None, str] = {
    State.DRAFT: "it is a draft; only a human's quoted words release it (qew draft approve)",
    State.COMPLETED: "it is completed (qew reopen)",
    State.CLOSED: "it is closed (qew reopen)",
}


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    start = commands.add_parser("start", help="claim approved, unblocked work")
    start.add_argument("item", help="item id or alias")
    start.add_argument("--take-over", action="store_true", help="take over a claim another actor holds (needs --reason)")
    start.add_argument("--reason", help="with --take-over: why the claim is taken")
    start.add_argument("--lane", help="agent lane holding the claim, to tell lanes of one actor apart")
    start.add_argument("--workspace", help="with --lane: absolute path of the lane's workspace")
    start.add_argument("--kind", help="with --lane: compute (default) or token")
    start.add_argument("--offline", action="store_true",
                       help="take a provisional claim without asking the arbiter (primary forge or ledger remote); sync settles it")
    start.add_argument("--json", action="store_true", help="machine-readable output")
    release = commands.add_parser("release", help="give up a claim without closing the work")
    release.add_argument("item", help="item id or alias")
    release.add_argument("--reason", required=True, help="why the claim is given up (kept in the audit trail)")
    release.add_argument("--force", action="store_true", help="release a claim another actor or lane holds")
    release.add_argument("--lane", help="the lane releasing; a different lane than the holder needs --force")
    release.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command == "start":
        config = _config()
        result = start(config, arguments.item, arguments.take_over, arguments.reason,
                       arguments.lane, arguments.workspace, arguments.kind, arguments.offline)
        line = outcome.render(arguments, str(result.pop("line")), **result)
        if not claim_authority.PENDING:
            print(line)
        else:
            replication.after_settle(lambda: print(line))  # only once the ledger remote confirmed the claim
        return True
    if arguments.command == "release":
        result = release(_config(), arguments.item, arguments.reason, arguments.force, arguments.lane)
        print(outcome.render(arguments, str(result.pop("line")), **result))
        return True
    return False


def _config() -> Config:
    from qew import ledger_config

    return ledger_config.load()


def holds_ancestor(snapshot: Snapshot, item_id: str, who: str) -> bool:
    """True when `who` holds an epic above the item (the cascade of an epic claim is theirs to work)."""
    seen, cursor = set(), snapshot.items[item_id].parent
    while cursor and cursor in snapshot.items and cursor not in seen:
        seen.add(cursor)
        if snapshot.folds[cursor].state == State.CLAIMED and snapshot.folds[cursor].owner == who:
            return True
        cursor = snapshot.items[cursor].parent
    return False


def holding(snapshot: Snapshot, item_id: str) -> str:
    """Who holds the claim, with the lane when it has one."""
    folded = snapshot.folds[item_id]
    lane = (folded.claim or {}).get("lane")
    return f"{folded.owner} (lane {lane})" if lane else folded.owner


def _lane_of(folded: Folded) -> str | None:
    return (folded.claim or {}).get("lane")


def _refuse_state(snapshot: Snapshot, item_id: str, verb: str) -> QewError:
    state = snapshot.folds[item_id].state
    why = _STATE_REASON.get(state, "its state is unknown")
    return QewError(f"cannot {verb} {item_id}: {why}", 2)


def start(config: Config, reference: str, take_over: bool = False, reason: str | None = None,
          lane: str | None = None, workspace: str | None = None, kind: str | None = None,
          offline: bool = False) -> dict[str, object]:
    claim_fields.validate(lane, workspace, kind)
    if offline and take_over:
        raise QewError("--offline cannot be combined with --take-over: a takeover is arbitrated, never provisional", 2)
    if take_over and not (reason or "").strip():
        raise QewError("--take-over needs --reason TEXT: say why the claim is taken", 2)
    if reason is not None and not take_over:
        raise QewError("--reason is only for --take-over", 2)
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    item_id = ledger.resolve(reference, list(snapshot.items))
    folded = snapshot.folds[item_id]
    me = actor()
    holder = folded.owner
    if folded.state == State.CLAIMED:
        if holder == me:
            _refuse_own_claim(item_id, folded, lane, take_over)
        elif not take_over:
            raise QewError(
                f"{item_id} is claimed by {holding(snapshot, item_id)}; "
                f"to take it over: qew start {item_id} --take-over --reason TEXT", 3)
    elif take_over and folded.state == State.APPROVED:
        raise QewError(f"nothing to take over: {item_id} is unclaimed", 2)
    elif folded.state != State.APPROVED:
        raise _refuse_state(snapshot, item_id, "start")
    unmet = [] if take_over else ledger_graph.blockers(snapshot, item_id)  # needs gate starting work, not a claim changing hands
    if unmet:
        raise QewError(f"cannot start {item_id}: it needs {' '.join(unmet)} completed first", 2)
    fields = {key: value for key, value in (("lane", lane), ("workspace", workspace), ("kind", kind)) if value is not None}
    mode = claim_authority.ClaimMode.OFFLINE if offline else claim_authority.ClaimMode.ARBITRATED
    if snapshot.items[item_id].kind == Kind.EPIC and not take_over:
        return _start_epic(config, ledger, snapshot, item_id, me, fields, mode)
    _enforce_cap(config, snapshot, me)
    if take_over:
        claim_authority.release(config, ledger, item_id, snapshot)
        ledger.append_event(item_id, "released", reason=(reason or "").strip(), previous_owner=holder, forced=True)
    claimed = claim_authority.claim(config, ledger, item_id, me, mode, snapshot, **fields)
    title = snapshot.items[item_id].title
    short = title if len(title) <= TITLE_SHOWN else title[:TITLE_SHOWN - 1] + "…"
    return {"line": f"{item_id} claimed by {me}: {short}" + _PROVISIONAL_NOTE[claimed], "id": item_id, "state": "started",
            "actor": me, **_provisional_field(claimed), **fields}


TITLE_SHOWN = 60  # the claim line names the work so the agent needs no `qew show` to start

_PROVISIONAL_NOTE = {
    claim_authority.Claimed.CONFIRMED: "",
    claim_authority.Claimed.PROVISIONAL: " (provisional: offline; `qew sync` settles it with the arbiter)",
}


def _provisional_field(*claimed: claim_authority.Claimed) -> dict[str, object]:
    return {"provisional": True} if claim_authority.Claimed.PROVISIONAL in claimed else {}


def _descendants(snapshot: Snapshot, root: str) -> list[str]:
    """Every item below `root`, breadth first, each once."""
    kids = ledger_graph.children(snapshot)
    found: list[str] = []
    seen: set[str] = set()
    queue = deque(kids.get(root, []))
    while queue:
        item_id = queue.popleft()
        if item_id not in seen:
            seen.add(item_id)
            found.append(item_id)
            queue.extend(kids.get(item_id, []))
    return found


def _start_epic(config: Config, ledger: Ledger, snapshot: Snapshot, epic_id: str, me: str, fields: dict[str, str],
                mode: claim_authority.ClaimMode) -> dict[str, object]:
    """Claim an epic and its open descendants in one operation: all of them or none.

    Descendants that cannot start (drafts, work with unmet needs, finished work) are left alone; one held by
    another actor refuses the whole claim. If a claim fails midway, no event stays (the session rolls the
    writes back) and the reservations already posted on the primary forge are released.
    """
    targets = [epic_id] if snapshot.folds[epic_id].state == State.APPROVED else []
    foreign = []
    for item_id in _descendants(snapshot, epic_id):
        folded = snapshot.folds[item_id]
        if folded.state == State.CLAIMED and folded.owner != me:
            foreign.append(f"{item_id} (held by {holding(snapshot, item_id)})")
        elif folded.state == State.APPROVED and not ledger_graph.blockers(snapshot, item_id):
            targets.append(item_id)
    if foreign:
        raise QewError(f"cannot claim {epic_id}: {', '.join(foreign)}; nothing was claimed", 3)
    cap = config.max_active_claims
    if cap is not None:
        mine = sum(1 for folded in snapshot.folds.values() if folded.state == State.CLAIMED and folded.owner == me)
        if mine + len(targets) > cap:
            raise QewError(f"claiming {len(targets)} items would give {me} more than max_active_claims = {cap}", 6)
    posted: list[str] = []
    outcomes: list[claim_authority.Claimed] = []
    try:
        for item_id in targets:
            outcomes.append(claim_authority.claim(config, ledger, item_id, me, mode, snapshot, **fields))
            posted.append(item_id)
    except QewError:
        for item_id in posted:
            try:
                claim_authority.release(config, ledger, item_id, snapshot)
            except QewError:
                pass  # best effort: the refusal that stopped the cascade is the error to report
        claim_authority.take_pending()
        raise
    note = _PROVISIONAL_NOTE[claim_authority.Claimed.PROVISIONAL] if _provisional_field(*outcomes) else ""
    return {"line": f"{epic_id} claimed by {me} ({len(targets)} items)" + note, "id": epic_id, "state": "started",
            "actor": me, "claimed": targets, **_provisional_field(*outcomes), **fields}


def _refuse_own_claim(item_id: str, folded: Folded, lane: str | None, take_over: bool) -> None:
    if take_over:
        raise QewError(f"nothing to take over: you already hold {item_id}", 2)
    held = _lane_of(folded)
    if held == lane:
        raise QewError(f"{item_id} is already claimed by you{f' (lane {held})' if held else ''}", 5)
    where = f"lane {held}" if held else "without a lane"
    raise QewError(f"{item_id} is already claimed by you {where}; another lane cannot start it", 3)


def _enforce_cap(config: Config, snapshot: Snapshot, me: str) -> None:
    cap = config.max_active_claims
    if cap is None:
        return
    mine = sum(1 for folded in snapshot.folds.values() if folded.state == State.CLAIMED and folded.owner == me)
    if mine >= cap:
        raise QewError(f"{me} already holds {mine} active claims; max_active_claims is {cap}", 6)


def release(config: Config, reference: str, reason: str, force: bool = False, lane: str | None = None) -> dict[str, object]:
    if not (reason or "").strip():
        raise QewError("release needs --reason TEXT", 2)
    if lane is not None:
        claim_fields.lane(lane)
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    item_id = ledger.resolve(reference, list(snapshot.items))
    folded = snapshot.folds[item_id]
    me = actor()
    if folded.state == State.APPROVED:
        last = snapshot.events[item_id][-1] if snapshot.events[item_id] else None
        if last is not None and last.type == "released" and last.actor == me:  # a retry of a release that already landed
            return {"line": f"{item_id} released (unchanged)", "id": item_id, "state": "released", "previous_owner": last.get("previous_owner", ""),
                    "released": [], "by": me, "lane": last.get("previous_lane") or None}
    if folded.state != State.CLAIMED:
        stale = claim_authority.stale_holder(config, ledger, item_id, snapshot) if force else None
        if stale is None:
            raise QewError(f"cannot release {item_id}: it is not claimed", 2)
        claim_authority.release(config, ledger, item_id, snapshot)  # the primary holds a claim the ledger does not: the ledger has nothing to record
        return {"line": f"{item_id} released: cleared {stale}'s claim on the primary (the ledger held none)", "id": item_id, "state": "released",
                "previous_owner": stale, "released": [], "by": me, "lane": None}
    held_lane = _lane_of(folded)
    overrides = folded.owner != me or lane != held_lane  # a lane-scoped claim is released by naming its lane
    if overrides and not force:
        hint = f"; name its lane with --lane {held_lane} or use --force" if held_lane and lane is None and folded.owner == me else "; to release it anyway use --force"
        raise QewError(f"{item_id} is claimed by {holding(snapshot, item_id)}{hint}", 3)
    data: dict[str, Any] = {"reason": reason.strip(), "previous_owner": folded.owner}
    if lane is not None:
        data["lane"] = lane
    if held_lane:
        data["previous_lane"] = held_lane
    if overrides:
        data["forced"] = True
    released = [item_id]
    if snapshot.items[item_id].kind == Kind.EPIC:  # the cascade the epic claim made goes with it
        released += [d for d in _descendants(snapshot, item_id)
                     if snapshot.folds[d].state == State.CLAIMED and snapshot.folds[d].owner == folded.owner]
    for released_id in released:
        claim_authority.release(config, ledger, released_id, snapshot)
        ledger.append_event(released_id, "released", **data)
    after = fold(ledger.load_events(item_id))  # this item's own events: re-reading the whole ledger here would cost minutes
    if after.state == State.CLAIMED and after.owner == folded.owner and _lane_of(after) == held_lane:
        raise QewError(f"release of {item_id} did not take effect: it is still claimed by {after.owner}", 3)
    return {"line": f"{item_id} released", "id": item_id, "state": "released", "previous_owner": folded.owner,
            "released": released, "by": me, "lane": held_lane}
