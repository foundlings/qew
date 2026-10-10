"""Record delivery evidence for many items in one ledger write.

Used by `qew evidence --manifest`. Rows are {item, change_ids: [..]}; every row is checked first (a draft or finished item,
another login's claim, a malformed change id refuse the whole manifest) and a row whose changes are already recorded is
`unchanged`. The events are the ones a single `qew evidence` writes; whether the changes landed is `qew complete`'s check.
"""

from dataclasses import dataclass

from qew import code_vcs
from qew.cmd_claim import holding, holds_ancestor
from qew.errors import QewError
from qew.jsontypes import JsonObject
from qew.ledger_event import State
from qew.ledger_store import Ledger, Snapshot, actor

ROW_KEYS = {"change_ids"}


@dataclass(frozen=True)
class Planned:
    item_id: str
    changes: tuple[str, ...]


@dataclass(frozen=True)
class Batch:
    rows: tuple[Planned, ...]
    unchanged: int
    vcs: str
    repo: str


def plan(snapshot: Snapshot, rows: list[tuple[str, JsonObject, str]], vcs: str, repo: str) -> Batch:
    planned: list[Planned] = []
    unchanged = 0
    for item_id, raw, where in rows:
        cited = raw.get("change_ids")
        if not isinstance(cited, list) or not cited or not all(isinstance(c, str) for c in cited):
            raise QewError(f"{where}: `change_ids` is a non-empty list of change ids", 2)
        for change in cited:
            if not code_vcs.valid_change(vcs, change):
                raise QewError(f"{where}: `{change}` is not a {vcs} change id", 2)
        folded = snapshot.folds[item_id]
        if folded.state not in (State.APPROVED, State.CLAIMED):
            raise QewError(f"{where}: cannot record evidence for {item_id}: it is {folded.state.value if folded.state else 'unknown'}; "
                           "evidence needs an approved or claimed item", 2)
        if folded.state == State.CLAIMED and folded.owner != actor() and not holds_ancestor(snapshot, item_id, actor()):
            raise QewError(f"{where}: cannot record evidence for {item_id}: it is claimed by {holding(snapshot, item_id)}", 3)
        recorded = {c for e in snapshot.events[item_id] if e.type == "evidence" and e.get("vcs") == vcs for c in e.get("change_ids", [])}
        if set(cited) <= recorded:
            unchanged += 1
        else:
            planned.append(Planned(item_id, tuple(cited)))
    return Batch(tuple(planned), unchanged, vcs, repo)


def apply(ledger: Ledger, batch: Batch) -> None:
    for each in batch.rows:
        ledger.append_event(each.item_id, "evidence", vcs=batch.vcs, change_ids=list(each.changes), repo=batch.repo)


def summary(batch: Batch, verb: str) -> str:
    line = f"{verb} {len(batch.rows)} item(s)"
    return line + (f"; unchanged {batch.unchanged}" if batch.unchanged else "")
