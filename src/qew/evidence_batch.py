"""Record delivery evidence for many items in one ledger write.

Used by `qew evidence --manifest`. Rows are {item, change_ids: [..]}; every row is checked first (a draft or finished item,
another login's claim, a malformed change id refuse the whole manifest) and a row whose changes are already recorded is
`unchanged`. The events are the ones a single `qew evidence` writes; whether the changes landed is `qew complete`'s check.
"""

from dataclasses import dataclass, replace

from qew import code_vcs
from qew.code_vcs import Landing
from qew.evidence_resolve import Resolver
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


def plan(snapshot: Snapshot, rows: list[tuple[str, JsonObject, str]], vcs: str, repo: str, resolve: Resolver | None = None) -> Batch:
    planned: list[Planned] = []
    unchanged = 0
    if resolve is not None:  # one batched lookup for the whole manifest
        cited_all = [c for _item, raw, _where in rows for c in (raw.get("change_ids") or []) if isinstance(raw.get("change_ids"), list) and isinstance(c, str)]
        resolve.prepare((c, vcs) for c in cited_all)
    for item_id, raw, where in rows:
        cited = raw.get("change_ids")
        if not isinstance(cited, list) or not cited or not all(isinstance(c, str) for c in cited):
            raise QewError(f"{where}: `change_ids` is a non-empty list of change ids", 2)
        for change in cited:
            if not code_vcs.valid_change(vcs, change):
                raise QewError(f"{where}: `{change}` is not a {vcs} change id", 2)
        folded = snapshot.folds[item_id]
        item = snapshot.items[item_id]
        if folded.state == State.COMPLETED:
            if item.evidence_absent and not item.evidence_absent.startswith("historical:"):
                raise QewError(f"{where}: {item_id} was completed with the stated absence `{item.evidence_absent.split(':', 1)[0]}`; that is a recorded fact, not a gap to fill", 2)
        elif folded.state not in (State.APPROVED, State.CLAIMED):
            raise QewError(f"{where}: cannot record evidence for {item_id}: it is {folded.state.value if folded.state else 'unknown'}; "
                           "evidence needs an approved or claimed item", 2)
        if folded.state == State.CLAIMED and folded.owner != actor() and not holds_ancestor(snapshot, item_id, actor()):
            raise QewError(f"{where}: cannot record evidence for {item_id}: it is claimed by {holding(snapshot, item_id)}", 3)
        recorded = {c for e in snapshot.events[item_id] if e.type == "evidence" and e.get("vcs") == vcs for c in e.get("change_ids", [])}
        if folded.state == State.COMPLETED:
            recorded |= set(item.evidence)
            if resolve is None:
                raise QewError(f"{where}: recovering evidence for completed {item_id} needs the code repository (run from the code checkout)", 2)
            for change in cited:
                if change not in recorded and resolve(change, vcs) is not Landing.LANDED:
                    raise QewError(f"{where}: {change} does not resolve to a change that landed on the target", 2)
        if set(cited) <= recorded:
            unchanged += 1
        else:
            planned.append(Planned(item_id, tuple(cited)))
    return Batch(tuple(planned), unchanged, vcs, repo)


def apply(ledger: Ledger, batch: Batch) -> None:
    for each in batch.rows:
        ledger.append_event(each.item_id, "evidence", vcs=batch.vcs, change_ids=list(each.changes), repo=batch.repo)
        item = ledger.load_item(each.item_id)
        if ledger.snapshot().folds[each.item_id].state == State.COMPLETED:  # recovered proof: the header shows it, the historical absence goes
            ledger.write_item(replace(item, evidence=tuple(dict.fromkeys((*item.evidence, *each.changes))), evidence_absent=""), overwrite=True)


def summary(batch: Batch, verb: str) -> str:
    line = f"{verb} {len(batch.rows)} item(s)"
    return line + (f"; unchanged {batch.unchanged}" if batch.unchanged else "")
