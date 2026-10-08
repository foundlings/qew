"""Finish work: `evidence`, `complete`, `close`, `reopen`.

`complete` is the only way to reach the completed state: the caller holds the claim, every child is
finished, and (except for epics) the delivering code is an ancestor of the target branch.
"""

from typing import Dict, List, Optional

from qew import code_vcs, ledger_graph, outcome
from qew.cmd_claim import holding
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import CLOSE_REASONS, State
from qew.ledger_item import Kind
from qew.ledger_store import Ledger, Snapshot, actor


def register(commands) -> None:
    evidence = commands.add_parser("evidence", help="record the code changes that deliver an item")
    evidence.add_argument("item", help="item id or alias")
    evidence.add_argument("--change", action="append", default=[], help="change id (jj) or commit (git), repeatable; default is the current one")
    evidence.add_argument("--vcs", choices=["jj", "git"], help="override the detected VCS")
    complete = commands.add_parser("complete", help="finish claimed work once the gate passes")
    complete.add_argument("item", help="item id or alias")
    complete.add_argument("--json", action="store_true", help="machine-readable output")
    close = commands.add_parser("close", help="close work that will not be completed")
    close.add_argument("item", help="item id or alias")
    close.add_argument("--reason", required=True, help="not_planned, duplicate or superseded")
    close.add_argument("--json", action="store_true", help="machine-readable output")
    reopen = commands.add_parser("reopen", help="reopen completed or closed work")
    reopen.add_argument("item", help="item id or alias")
    reopen.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments) -> bool:
    if arguments.command not in ("evidence", "complete", "close", "reopen"):
        return False
    from qew import ledger_config

    config = ledger_config.load()
    if arguments.command == "evidence":
        print(evidence(config, arguments.item, arguments.change, arguments.vcs))
        return True
    if arguments.command == "complete":
        result = complete(config, arguments.item)
    elif arguments.command == "close":
        result = close(config, arguments.item, arguments.reason)
    else:
        result = reopen(config, arguments.item)
    print(outcome.render(arguments, result.pop("line"), **result))
    return True


_STATE_WHY = {
    State.DRAFT: "it is a draft; only a human's quoted words release it (qew draft approve)",
    State.APPROVED: "it is approved and unclaimed",
    State.COMPLETED: "it is completed",
    State.CLOSED: "it is closed (qew reopen)",
}


def _load(config: Config, reference: str):
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    return ledger, snapshot, ledger.resolve(reference, list(snapshot.items))


def _refuse(snapshot: Snapshot, item_id: str, verb: str, needs: str) -> QewError:
    state = snapshot.folds[item_id].state
    return QewError(f"cannot {verb} {item_id}: {_STATE_WHY.get(state, 'its state is unknown')} (needs {needs})", 2)


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
    return f"{item_id} evidence {kind} {' '.join(cited)}"


def _require_holder(snapshot: Snapshot, item_id: str) -> None:
    """Complete only work the caller holds; every other state names the command that fixes it."""
    folded = snapshot.folds[item_id]
    if folded.state == State.COMPLETED:
        raise QewError(f"cannot complete {item_id}: it is already completed", 2)
    if folded.state == State.APPROVED:
        raise QewError(f"cannot complete {item_id}: nobody holds it; claim it first with `qew start {item_id}`", 2)
    if folded.state != State.CLAIMED:
        raise _refuse(snapshot, item_id, "complete", "a claimed item")
    if folded.owner != actor():
        raise QewError(
            f"cannot complete {item_id}: it is claimed by {holding(snapshot, item_id)}; "
            f"to finish it anyway: qew complete {item_id} --take-over --reason TEXT", 3)


def complete(config: Config, reference: str) -> Dict[str, object]:
    ledger, snapshot, item_id = _load(config, reference)
    item = snapshot.items[item_id]
    _require_holder(snapshot, item_id)
    kids = ledger_graph.children(snapshot).get(item_id, [])
    open_kids = [k for k in kids if snapshot.folds[k].state not in (State.COMPLETED, State.CLOSED)]
    if open_kids:
        raise QewError(f"cannot complete {item_id}: unfinished children {' '.join(open_kids)}", 2)
    if item.kind == Kind.EPIC:
        if not kids:
            raise QewError(f"cannot complete {item_id}: an epic with no children has nothing to prove; close it", 2)
    else:
        _require_landed_evidence(config, snapshot, item_id)
    ledger.append_event(item_id, "completed")
    return {"line": f"{item_id} completed", "id": item_id, "state": "completed"}


def _require_landed_evidence(config: Config, snapshot: Snapshot, item_id: str) -> None:
    events = snapshot.folds[item_id].evidence
    if not events:
        raise QewError(f"cannot complete {item_id}: no evidence recorded (qew evidence {item_id} --change ID)", 2)
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
        f"cannot complete {item_id}: evidence {' '.join(dict.fromkeys(missing))} is not an ancestor of {target}", 2
    )


def close(config: Config, reference: str, reason: str) -> Dict[str, object]:
    if reason not in CLOSE_REASONS:
        raise QewError(f"--reason must be one of {', '.join(CLOSE_REASONS)}", 2)
    ledger, snapshot, item_id = _load(config, reference)
    if snapshot.folds[item_id].state == State.CLOSED:
        raise QewError(f"{item_id} is already closed", 2)
    ledger.append_event(item_id, "closed", reason=reason)
    return {"line": f"{item_id} closed ({reason})", "id": item_id, "state": "closed"}


def reopen(config: Config, reference: str) -> Dict[str, object]:
    ledger, snapshot, item_id = _load(config, reference)
    if snapshot.folds[item_id].state not in (State.COMPLETED, State.CLOSED):
        raise _refuse(snapshot, item_id, "reopen", "a completed or closed item")
    ledger.append_event(item_id, "reopened")
    return {"line": f"{item_id} reopened", "id": item_id, "state": "reopened"}
