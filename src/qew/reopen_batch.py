"""Reopen many completed or closed items in one ledger write, each with its reason.

Used by `qew reopen --manifest`. Rows are {item, reason}; every row is checked first (an unknown or duplicate item, a missing
reason on finished work refuse the whole manifest) and an item that is already open is `unchanged`, so a rerun adds nothing.
The events are the `reopened` events a single `qew reopen` writes.
"""

from dataclasses import dataclass

from qew.errors import QewError
from qew.jsontypes import JsonObject
from qew.ledger_event import State
from qew.ledger_store import Ledger, Snapshot

ROW_KEYS = {"reason"}


@dataclass(frozen=True)
class Planned:
    item_id: str
    reason: str


@dataclass(frozen=True)
class Batch:
    rows: tuple[Planned, ...]
    unchanged: int


def plan(snapshot: Snapshot, rows: list[tuple[str, JsonObject, str]]) -> Batch:
    planned: list[Planned] = []
    unchanged = 0
    for item_id, raw, where in rows:
        if snapshot.folds[item_id].state in (State.DRAFT, State.APPROVED, State.CLAIMED):
            unchanged += 1
            continue
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise QewError(f"{where}: {item_id} is finished, so reopening it needs a `reason`: say why the work is required again", 2)
        planned.append(Planned(item_id, reason.strip()))
    return Batch(tuple(planned), unchanged)


def apply(ledger: Ledger, batch: Batch) -> None:
    for each in batch.rows:
        ledger.append_event(each.item_id, "reopened", reason=each.reason)


def summary(batch: Batch, verb: str) -> str:
    line = f"{verb} {len(batch.rows)} item(s)"
    return line + (f"; unchanged {batch.unchanged}" if batch.unchanged else "")
