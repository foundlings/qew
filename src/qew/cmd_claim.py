"""`qew start` and `qew release`: take a claim on approved work, or give one up.

Refusals are distinct for agent loops: 2 the work may not start or the usage is wrong, 3 someone else holds it
(or another lane of you does), 5 you already hold exactly this claim, 6 the per-actor claim cap is reached.
"""

from typing import Dict, Optional

from qew import claim_authority, claim_fields, ledger_graph, outcome, replication
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import Folded, State
from qew.ledger_store import Ledger, Snapshot, actor

_STATE_REASON = {
    State.DRAFT: "it is a draft; only a human's quoted words release it (qew draft approve)",
    State.COMPLETED: "it is completed (qew reopen)",
    State.CLOSED: "it is closed (qew reopen)",
}


def register(commands) -> None:
    start = commands.add_parser("start", help="claim approved, unblocked work")
    start.add_argument("item", help="item id or alias")
    start.add_argument("--take-over", action="store_true", help="take over a claim another actor holds (needs --reason)")
    start.add_argument("--reason", help="with --take-over: why the claim is taken")
    start.add_argument("--lane", help="agent lane holding the claim, to tell lanes of one actor apart")
    start.add_argument("--workspace", help="with --lane: absolute path of the lane's workspace")
    start.add_argument("--kind", help="with --lane: compute (default) or token")
    start.add_argument("--json", action="store_true", help="machine-readable output")
    release = commands.add_parser("release", help="give up a claim without closing the work")
    release.add_argument("item", help="item id or alias")
    release.add_argument("--reason", required=True, help="why the claim is given up (kept in the audit trail)")
    release.add_argument("--force", action="store_true", help="release a claim another actor or lane holds")
    release.add_argument("--lane", help="the lane releasing; a different lane than the holder needs --force")
    release.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments) -> bool:
    if arguments.command == "start":
        config = _config()
        result = start(config, arguments.item, arguments.take_over, arguments.reason,
                       arguments.lane, arguments.workspace, arguments.kind)
        line = outcome.render(arguments, result.pop("line"), **result)
        if claim_authority.PENDING is None:
            print(line)
        else:
            replication.after_settle(lambda: print(line))  # only once the ledger remote confirmed the claim
        return True
    if arguments.command == "release":
        result = release(_config(), arguments.item, arguments.reason, arguments.force, arguments.lane)
        print(outcome.render(arguments, result.pop("line"), **result))
        return True
    return False


def _config() -> Config:
    from qew import ledger_config

    return ledger_config.load()


def holding(snapshot: Snapshot, item_id: str) -> str:
    """Who holds the claim, with the lane when it has one."""
    folded = snapshot.folds[item_id]
    lane = (folded.claim or {}).get("lane")
    return f"{folded.owner} (lane {lane})" if lane else folded.owner


def _lane_of(folded: Folded) -> Optional[str]:
    return (folded.claim or {}).get("lane")


def _refuse_state(snapshot: Snapshot, item_id: str, verb: str) -> QewError:
    state = snapshot.folds[item_id].state
    why = _STATE_REASON.get(state, "its state is unknown")
    return QewError(f"cannot {verb} {item_id}: {why}", 2)


def start(config: Config, reference: str, take_over: bool = False, reason: Optional[str] = None,
          lane: Optional[str] = None, workspace: Optional[str] = None, kind: Optional[str] = None) -> Dict[str, object]:
    claim_fields.validate(lane, workspace, kind)
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
    unmet = ledger_graph.blockers(snapshot, item_id)
    if unmet:
        raise QewError(f"cannot start {item_id}: it needs {' '.join(unmet)} completed first", 2)
    _enforce_cap(config, snapshot, me)
    fields = {key: value for key, value in (("lane", lane), ("workspace", workspace), ("kind", kind)) if value is not None}
    if take_over:
        claim_authority.release(config, ledger, item_id)
        ledger.append_event(item_id, "released", reason=reason.strip(), previous_owner=holder, forced=True)
    claim_authority.claim(config, ledger, item_id, me, **fields)
    return {"line": f"{item_id} claimed by {me}", "id": item_id, "state": "started", "actor": me, **fields}


def _refuse_own_claim(item_id: str, folded: Folded, lane: Optional[str], take_over: bool) -> None:
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


def release(config: Config, reference: str, reason: str, force: bool = False, lane: Optional[str] = None) -> Dict[str, object]:
    if not (reason or "").strip():
        raise QewError("release needs --reason TEXT", 2)
    if lane is not None:
        claim_fields.lane(lane)
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    item_id = ledger.resolve(reference, list(snapshot.items))
    folded = snapshot.folds[item_id]
    if folded.state != State.CLAIMED:
        raise QewError(f"cannot release {item_id}: it is not claimed", 2)
    me = actor()
    overrides = folded.owner != me or (lane is not None and lane != _lane_of(folded))
    if overrides and not force:
        raise QewError(f"{item_id} is claimed by {holding(snapshot, item_id)}; to release it anyway use --force", 3)
    data: Dict[str, object] = {"reason": reason.strip(), "previous_owner": folded.owner}
    if overrides:
        data["forced"] = True
    claim_authority.release(config, ledger, item_id)
    ledger.append_event(item_id, "released", **data)
    return {"line": f"{item_id} released", "id": item_id, "state": "released", "previous_owner": folded.owner}
