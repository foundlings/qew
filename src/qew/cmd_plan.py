"""`qew plan status`: a rollup of one epic's direct children, derived from files only."""

import argparse
import json

from qew import ledger_config, ledger_graph
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.jsontypes import JsonObject
from qew.ledger_config import Config
from qew.ledger_event import State
from qew.ledger_item import Kind
from qew.ledger_store import Snapshot

SCHEMA_VERSION = 1
NEXT_REASON = "highest-priority ready child"


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    plan = commands.add_parser("plan", help="plan rollups")
    sub = plan.add_subparsers(dest="plan_command", required=True)
    status = sub.add_parser("status", help="roll up the direct children of an epic (every open epic when none is named)")
    status.add_argument("epic", nargs="?", help="epic id or alias")
    status.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "plan":
        return False
    print(status(ledger_config.load(), arguments.epic, arguments.json))
    return True


def _loud(snapshot: Snapshot, item_id: str) -> None:
    """Refuse to roll up data that cannot be read: a guessed count is worse than an error."""
    for need in snapshot.items[item_id].needs:
        if need not in snapshot.items:
            raise QewError(f"{item_id}: needs {need}, which is not in the ledger; run `qew check`", 1)
    if snapshot.folds[item_id].errors:
        raise QewError(f"{item_id}: {snapshot.folds[item_id].errors[0]}; run `qew check`", 1)
    if snapshot.folds[item_id].state is None:
        raise QewError(f"{item_id} has no events; run `qew check`", 1)


def rollup(snapshot: Snapshot, epic_id: str) -> JsonObject:
    _loud(snapshot, epic_id)
    kids = ledger_graph.children(snapshot).get(epic_id, [])
    for kid in kids:
        _loud(snapshot, kid)
    ready = set(ledger_graph.ready(snapshot))
    counts = {"total": len(kids), "draft": 0, "ready": 0, "blocked": 0, "claimed": 0, "closed": 0}
    for kid in kids:
        state = snapshot.folds[kid].state
        if state == State.DRAFT:
            counts["draft"] += 1
        elif state == State.CLAIMED:
            counts["claimed"] += 1
        elif state in (State.COMPLETED, State.CLOSED):
            counts["closed"] += 1
        elif kid in ready:
            counts["ready"] += 1
        else:
            counts["blocked"] += 1
    best = ledger_graph.ordered(snapshot, [k for k in kids if k in ready])
    epic = snapshot.items[epic_id]
    state = snapshot.folds[epic_id].state
    if state is None:  # _loud refused this already
        raise QewError(f"{epic_id} has no events; run `qew check`", 1)
    draft = state == State.DRAFT
    completable = not draft and counts["total"] > 0 and counts["closed"] == counts["total"]
    return {
        "id": epic_id,
        "title": epic.title,
        "state": state.value,
        "draft": draft,
        "counts": counts,
        "next": {"id": best[0], "title": snapshot.items[best[0]].title, "reason": NEXT_REASON} if best else None,
        "completable": completable,
        "complete_command": f"qew complete {epic_id}" if completable else None,
    }


def _text(plan: JsonObject) -> list[str]:
    counts = plan["counts"]
    head = f"{plan['id']} {plan['state']} {plan['title']}" + (" (draft plan)" if plan["draft"] else "")
    progress = f"{counts['closed']}/{counts['total']} closed" + (f", {counts['draft']} drafts" if counts["draft"] else "")
    lines = [head, progress]
    if plan["next"]:
        lines.append(f"next: {plan['next']['id']} {plan['next']['title']}")
    if plan["complete_command"]:
        lines.append(f"complete: {plan['complete_command']}")
    return lines


def status(config: Config, reference: str | None, as_json: bool) -> str:
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    if reference:
        epic_id = ledger.resolve(reference, list(snapshot.items))
        if snapshot.items[epic_id].kind != Kind.EPIC:
            raise QewError(f"{epic_id} is a {snapshot.items[epic_id].kind.value}, not an epic", 2)
        epics = [epic_id]
    else:
        epics = [
            i for i in ledger_graph.ordered(snapshot, list(snapshot.items))
            if snapshot.items[i].kind == Kind.EPIC and snapshot.folds[i].state not in (State.COMPLETED, State.CLOSED)
        ]
    plans = [rollup(snapshot, epic_id) for epic_id in epics]
    if as_json:
        return json.dumps({"schema_version": SCHEMA_VERSION, "plans": plans})
    return "\n".join(line for plan in plans for line in _text(plan))
