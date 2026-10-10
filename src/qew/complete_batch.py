"""Complete many approved items in one ledger write, on a human's quoted words, without claims or evidence.

Used by `qew complete --manifest`. The manifest is JSON Lines of {item, reason?}. Everything is checked before the first
event is written: an unknown, draft, closed or duplicate item, a claim held by another login, an open child or an unmet need
that the same manifest does not finish refuses the whole thing, naming the line. An item already completed is unchanged.
"""

from dataclasses import dataclass

from qew import ledger_graph
from qew.errors import QewError
from qew.jsontypes import JsonObject
from qew.ledger_event import State
from qew.ledger_store import Ledger, Snapshot, actor

ROW_KEYS = {"item", "reason"}


@dataclass(frozen=True)
class Row:
    item_id: str
    reason: str  # "" : none given
    where: str


@dataclass(frozen=True)
class Batch:
    rows: tuple[Row, ...]  # what is new
    unchanged: int


def row(raw: JsonObject, item_id: str, where: str) -> Row:
    unknown = sorted(set(raw) - ROW_KEYS)
    if unknown:
        raise QewError(f"{where}: unknown field `{unknown[0]}` (fields: item, reason)", 2)
    reason = raw.get("reason", "")
    if not isinstance(reason, str) or (raw.get("reason") is not None and "reason" in raw and not reason.strip()):
        raise QewError(f"{where}: `reason` must be text", 2)
    return Row(item_id, reason.strip(), where)


def plan(snapshot: Snapshot, rows: list[Row]) -> Batch:
    """Check every row against the ledger and against the state the manifest leaves behind."""
    seen: dict[str, str] = {}
    fresh: list[Row] = []
    unchanged = 0
    for each in rows:
        if each.item_id in seen:
            raise QewError(f"{each.where}: {each.item_id} is already named on {seen[each.item_id]}", 2)
        seen[each.item_id] = each.where
        folded = snapshot.folds[each.item_id]
        if folded.state == State.COMPLETED:
            unchanged += 1
        elif folded.state == State.DRAFT:
            raise QewError(f"{each.where}: {each.item_id} is a draft; only a human's quoted words release it (qew draft approve)", 2)
        elif folded.state == State.CLOSED:
            raise QewError(f"{each.where}: {each.item_id} is closed (qew reopen first)", 2)
        elif folded.state == State.CLAIMED and folded.owner != actor():
            raise QewError(f"{each.where}: {each.item_id} is claimed by {folded.owner}; ask them to release it", 3)
        elif folded.state in (State.APPROVED, State.CLAIMED):
            fresh.append(each)
        else:
            raise QewError(f"{each.where}: {each.item_id} cannot be completed from its state", 2)
    finishing = {each.item_id: each for each in fresh}

    def done(item_id: str) -> bool:
        return item_id in finishing or snapshot.folds[item_id].state == State.COMPLETED

    children = ledger_graph.children(snapshot)
    for each in fresh:
        for need in snapshot.items[each.item_id].needs:
            if need in snapshot.items and not done(need):
                raise QewError(f"{each.where}: {each.item_id} needs {need}, which is not completed and not in this manifest", 2)
        for child in children.get(each.item_id, []):
            if not done(child) and snapshot.folds[child].state != State.CLOSED:
                raise QewError(f"{each.where}: {each.item_id} has an open child {child}, which is not in this manifest", 2)
    return Batch(tuple(fresh), unchanged)


def apply(ledger: Ledger, batch: Batch, quote: str) -> None:
    for each in batch.rows:
        data: JsonObject = {"bulk_quote": quote}
        if each.reason:
            data["reason"] = each.reason
        ledger.append_event(each.item_id, "completed", **data)


def summary(batch: Batch, verb: str) -> str:
    line = f"{verb} {len(batch.rows)} item(s)"
    return line + (f"; unchanged {batch.unchanged}" if batch.unchanged else "")
