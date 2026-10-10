"""Change labels and priorities of many items in one ledger write; a gate carries its reason.

Used by `qew set --manifest`. Rows are {item, add_label?, remove_label?, fields?, priority?, reason?}; every row is checked first
(finished work, a bad label or priority, a gate without a reason refuse the whole manifest), a row that changes nothing is
`unchanged`, and the writes are the same audited `field` events a single `qew set` makes.
"""

from dataclasses import dataclass

from qew import ledger_fields, ledger_graph, ledger_labels
from qew.errors import QewError
from qew.jsontypes import JsonObject
from qew.ledger_check import MAX_PRIORITY
from qew.ledger_event import State
from qew.ledger_item import Item, check_header_values
from qew.ledger_store import Ledger, Snapshot, actor

ROW_KEYS = {"add_label", "remove_label", "priority", "reason", "fields"}
Change = tuple[str, str, str]  # key, old, new


@dataclass(frozen=True)
class Planned:
    item_id: str
    item: Item
    changes: tuple[Change, ...]
    gate_reason: str  # "" unless the gate label is newly added
    lift_reason: str = ""  # "" unless the gate label is removed with a reason


@dataclass(frozen=True)
class Batch:
    rows: tuple[Planned, ...]  # what changes
    unchanged: int


def _labels(value: object, where: str, key: str) -> tuple[str, ...]:
    found = [value] if isinstance(value, str) else value
    if not isinstance(found, list) or not all(isinstance(x, str) and x.strip() for x in found):
        raise QewError(f"{where}: `{key}` is a label or a list of labels", 2)
    return tuple(dict.fromkeys(x.strip() for x in found))


def plan(snapshot: Snapshot, rows: list[tuple[str, JsonObject, str]], allow_closed: bool = False) -> Batch:
    planned: list[Planned] = []
    unchanged = 0
    for item_id, raw, where in rows:
        if snapshot.folds[item_id].state in (State.COMPLETED, State.CLOSED) and not allow_closed:
            raise QewError(f"{where}: {item_id} is finished; pass --closed to change finished work", 2)
        add, remove = _labels(raw.get("add_label", []), where, "add_label"), _labels(raw.get("remove_label", []), where, "remove_label")
        reason = raw.get("reason", "")
        if not isinstance(reason, str):
            raise QewError(f"{where}: `reason` must be text", 2)
        item = snapshot.items[item_id]
        wanted = dict(item.header())
        try:
            ledger_labels.require_valid(add)
        except QewError as error:
            raise QewError(f"{where}: {error}", 2) from None
        kept = [label for label in item.labels if label not in remove]
        wanted["labels"] = " ".join(kept + [label for label in add if label not in kept])
        if "priority" in raw:
            if type(raw["priority"]) is not int or not 0 <= raw["priority"] <= MAX_PRIORITY:
                raise QewError(f"{where}: `priority` is a whole number from 0 to {MAX_PRIORITY}", 2)
            wanted["priority"] = str(raw["priority"])
        if "fields" in raw:
            if not isinstance(raw["fields"], dict):
                raise QewError(f"{where}: `fields` is an object of NAME: value-or-list (an empty list removes the field)", 2)
            try:
                wanted["fields"] = ledger_fields.encode(ledger_fields.changed(item.fields, raw["fields"]))
            except QewError as error:
                raise QewError(f"{where}: {error}", 2) from None
        changes: list[Change] = []
        new_item = item
        for key in ("labels", "fields", "priority"):
            if wanted[key] != item.header()[key]:
                try:
                    new_item = new_item.with_value(key, wanted[key])
                except QewError as error:
                    raise QewError(f"{where}: {str(error).split(': ', 1)[-1]}", 2) from None
                changes.append((key, item.header()[key], wanted[key]))
        problems = check_header_values(new_item)
        if problems:
            raise QewError(f"{where}: {problems[0]}", 2)
        newly_gated = ledger_graph.GATED_LABEL in new_item.labels and ledger_graph.GATED_LABEL not in item.labels
        if newly_gated and not reason.strip():
            raise QewError(f"{where}: a gated item needs a `reason`: what outside the repository it waits on", 2)
        lifted = ledger_graph.GATED_LABEL in item.labels and ledger_graph.GATED_LABEL not in new_item.labels
        if changes:
            planned.append(Planned(item_id, new_item, tuple(changes), reason.strip() if newly_gated else "", reason.strip() if lifted else ""))
        else:
            unchanged += 1
    return Batch(tuple(planned), unchanged)


def apply(ledger: Ledger, batch: Batch) -> None:
    for each in batch.rows:
        ledger.write_item(each.item, overwrite=True)
        for key, old, new in each.changes:
            ledger.append_event(each.item_id, "field", key=key, old=old, new=new)
        if each.gate_reason:
            ledger.append_event(each.item_id, "commented", text=f"gated: {each.gate_reason}", author=actor())
        if each.lift_reason:
            ledger.append_event(each.item_id, "commented", text=f"gate lifted: {each.lift_reason}", author=actor())


def summary(batch: Batch, verb: str) -> str:
    line = f"{verb} {len(batch.rows)} item(s)"
    return line + (f"; unchanged {batch.unchanged}" if batch.unchanged else "")
