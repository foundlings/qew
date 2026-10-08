"""Claim, release, evidence, complete, close, reopen: the gated transitions."""

from typing import List, Optional

from qew import claim_authority, code_vcs, ledger_graph
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import CLOSE_REASONS, State
from qew.ledger_item import Kind
from qew.ledger_store import Snapshot, actor

_HELP = {
    State.DRAFT: "it is a draft; only a human's quoted words release it (qew draft approve)",
    State.APPROVED: "it is approved and unclaimed",
    State.COMPLETED: "it is completed",
    State.CLOSED: "it is closed (qew reopen)",
}


def _refuse(snapshot: Snapshot, item_id: str, verb: str, needs: str) -> QewError:
    folded = snapshot.folds[item_id]
    state = folded.state
    why = f"it is claimed by {folded.owner}" if state == State.CLAIMED else _HELP.get(state, "its state is unknown")
    return QewError(f"cannot {verb} {snapshot.short(item_id)}: {why} (needs {needs})", 2)


def _load(config: Config, reference: str):
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    return ledger, snapshot, ledger.resolve(reference, list(snapshot.items))


def start(config: Config, reference: str) -> str:
    ledger, snapshot, item_id = _load(config, reference)
    if snapshot.folds[item_id].state != State.APPROVED:
        raise _refuse(snapshot, item_id, "start", "an approved, unclaimed item")
    unmet = ledger_graph.blockers(snapshot, item_id)
    if unmet:
        shown = " ".join(snapshot.short(n) if n in snapshot.items else n for n in unmet)
        raise QewError(f"cannot start {snapshot.short(item_id)}: it needs {shown} completed first", 2)
    claim_authority.claim(config, ledger, item_id, actor())
    return f"{snapshot.short(item_id)} claimed by {actor()}"


def release(config: Config, reference: str) -> str:
    ledger, snapshot, item_id = _load(config, reference)
    if snapshot.folds[item_id].state != State.CLAIMED:
        raise _refuse(snapshot, item_id, "release", "a claimed item")
    claim_authority.release(config, ledger, item_id)
    ledger.append_event(item_id, "released")
    return f"{snapshot.short(item_id)} released"


def evidence(config: Config, reference: str, changes: List[str], vcs: Optional[str]) -> str:
    ledger, snapshot, item_id = _load(config, reference)
    if snapshot.folds[item_id].state not in (State.APPROVED, State.CLAIMED):
        raise _refuse(snapshot, item_id, "record evidence for", "an approved or claimed item")
    kind = vcs or code_vcs.detect(config.root)
    cited = changes or [code_vcs.current_change(config.root, kind)]
    for change in cited:
        if not code_vcs.valid_change(kind, change):
            raise QewError(f"`{change}` is not a {kind} change id", 2)
    ledger.append_event(
        item_id, "evidence", vcs=kind, change_ids=cited, repo=code_vcs.repository_url(config.root)
    )
    return f"{snapshot.short(item_id)} evidence {kind} {' '.join(cited)}"


def complete(config: Config, reference: str) -> str:
    ledger, snapshot, item_id = _load(config, reference)
    item = snapshot.items[item_id]
    folded = snapshot.folds[item_id]
    if folded.state == State.DRAFT and item.kind == Kind.EPIC:
        raise QewError(
            f"cannot complete {snapshot.short(item_id)}: it is a draft plan; release it with "
            f"qew draft approve {snapshot.short(item_id)} --tree --quote Q first", 2)
    if folded.state == State.COMPLETED:
        return f"{snapshot.short(item_id)} completed (unchanged)"
    if folded.state != State.CLAIMED:
        raise _refuse(snapshot, item_id, "complete", "a claimed item")
    unmet = ledger_graph.blockers(snapshot, item_id)
    if unmet:
        shown = " ".join(snapshot.short(n) if n in snapshot.items else n for n in unmet)
        raise QewError(f"cannot complete {snapshot.short(item_id)}: it needs {shown} completed first", 2)
    kids = ledger_graph.children(snapshot).get(item_id, [])
    open_kids = [k for k in kids if snapshot.folds[k].state not in (State.COMPLETED, State.CLOSED)]
    if open_kids:
        shown = " ".join(snapshot.short(k) for k in open_kids)
        count = f"{len(open_kids)} open child{'ren' if len(open_kids) != 1 else ''}"
        raise QewError(f"cannot complete {snapshot.short(item_id)}: {count}, unfinished children {shown}", 2)
    if item.kind == Kind.EPIC:
        if not kids:
            raise QewError(f"cannot complete {snapshot.short(item_id)}: an epic with no children has no completed work; close it", 2)
    else:
        _require_landed_evidence(config, snapshot, item_id)
    ledger.append_event(item_id, "completed")
    return f"{snapshot.short(item_id)} completed"


def _require_landed_evidence(config: Config, snapshot: Snapshot, item_id: str) -> None:
    events = snapshot.folds[item_id].evidence
    short = snapshot.short(item_id)
    if not events:
        raise QewError(f"cannot complete {short}: no evidence recorded (qew evidence {short} --change ID)", 2)
    target = config.target
    missing: List[str] = []
    for event in events:
        kind = event.get("vcs")
        if kind != code_vcs.detect(config.root):
            raise QewError(f"evidence uses {kind} but this checkout is {code_vcs.detect(config.root)}", 3)
        absent = [c for c in event.get("change_ids") if not code_vcs.is_ancestor(config.root, kind, c, target)]
        if not absent:
            return
        missing.extend(absent)
    raise QewError(
        f"cannot complete {short}: evidence {' '.join(dict.fromkeys(missing))} is not an ancestor of {target}", 2
    )


def close(config: Config, reference: str, reason: str) -> str:
    if reason not in CLOSE_REASONS:
        raise QewError(f"--reason must be one of {', '.join(CLOSE_REASONS)}", 2)
    ledger, snapshot, item_id = _load(config, reference)
    if snapshot.folds[item_id].state == State.CLOSED:
        raise QewError(f"{snapshot.short(item_id)} is already closed", 2)
    ledger.append_event(item_id, "closed", reason=reason)
    return f"{snapshot.short(item_id)} closed ({reason})"


def reopen(config: Config, reference: str) -> str:
    ledger, snapshot, item_id = _load(config, reference)
    if snapshot.folds[item_id].state not in (State.COMPLETED, State.CLOSED):
        raise _refuse(snapshot, item_id, "reopen", "a completed or closed item")
    ledger.append_event(item_id, "reopened")
    return f"{snapshot.short(item_id)} reopened"
