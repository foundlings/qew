"""`qew accept` and `qew reject`: resolve a forge-side divergence explicitly.

Accepting applies the forge value through the normal gates (a forge close never counts as completion);
rejecting keeps the ledger value and makes the next push restore the forge.
"""

import argparse
from collections.abc import Collection

from qew import cmd_finish, ledger_config
from qew.mirror_types import updated
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.jsontypes import JsonObject
from qew.ledger_config import Config
from qew.ledger_event import CLOSE_REASONS, Event, State
from qew.ledger_store import Ledger, Snapshot


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    accept = commands.add_parser("accept", help="apply a forge-side change the ledger had not made")
    accept.add_argument("divergence", nargs="+", help="divergence event ids, or items with exactly one open divergence")
    accept.add_argument("--reason", default="not_planned", help="close reason when a forge close is accepted")
    accept.add_argument("--completed", action="store_true", help="treat an accepted forge close as completion (runs the completion gate)")
    reject = commands.add_parser("reject", help="keep the ledger value; the next push restores the forge")
    reject.add_argument("divergence", nargs="+", help="divergence event ids, or items with exactly one open divergence")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command == "accept":
        config = ledger_config.load()
        print("\n".join(accept(config, ref, arguments.reason, arguments.completed) for ref in arguments.divergence))
        return True
    if arguments.command == "reject":
        config = ledger_config.load()
        print("\n".join(reject(config, ref) for ref in arguments.divergence))
        return True
    return False


def _find(ledger: Ledger, reference: str) -> tuple[str, Event]:
    snapshot = ledger.snapshot()
    try:
        item_id = ledger.resolve(reference, list(snapshot.items))
    except QewError:
        item_id = None
    if item_id:
        open_ones = snapshot.folds[item_id].divergences
        if len(open_ones) != 1:
            raise QewError(f"{item_id} has {len(open_ones)} open divergences; name one by its event id", 2)
        return item_id, open_ones[0]
    found: list[tuple[str, Event]] = [
        (i, e) for i in snapshot.items for e in snapshot.folds[i].divergences if e.id.startswith(reference)
    ]
    if len(found) != 1:
        raise QewError(f"`{reference}` names {len(found)} open divergences", 2)
    return found[0]


def accept(config: Config, reference: str, reason: str = "not_planned", completed: bool = False, note: str = "") -> str:
    if reason not in CLOSE_REASONS:
        raise QewError(f"--reason must be one of {', '.join(CLOSE_REASONS)}", 2)
    ledger = ledger_for(config)
    item_id, divergence = _find(ledger, reference)
    snapshot = ledger.snapshot()
    state = snapshot.folds[item_id].state
    if divergence.type == "claimed":
        return _settle_contest(ledger, snapshot, item_id, divergence, keep_contender=True, note=note)
    field = divergence.get("field")
    forge = divergence.get("forge")
    if field == "state" and forge == "closed":
        if completed:
            cmd_finish.complete(config, item_id)  # the gate decides; nothing is written when it refuses
        elif state not in (State.CLOSED, State.COMPLETED):
            # the reason the forge gave decides, unless the caller named one: a completed close is never recorded as not planned
            given = divergence.get("forge_reason")
            if given in ("", None) or reason != "not_planned":
                ledger.append_event(item_id, "closed", reason=reason, provenance="forge")
            elif given == "duplicate":
                ledger.append_event(item_id, "closed", reason="duplicate", provenance="forge")
            elif given == "completed" and state in (State.APPROVED, State.CLAIMED):
                ledger.append_event(item_id, "completed", provenance="forge-import")
            else:
                ledger.append_event(item_id, "closed", reason=reason, provenance="forge")
    elif field == "state" and forge == "open" and state in (State.CLOSED, State.COMPLETED):
        ledger.append_event(item_id, "reopened", provenance="forge")
    elif field == "parent":
        _accept_parent(ledger, item_id, divergence)
    elif field in ("title", "labels"):
        item = snapshot.items[item_id]
        ledger.write_item(item.with_value(field, forge), overwrite=True)
        ledger.append_event(item_id, "field", key=field, old=item.header()[field], new=forge, provenance="forge")
    ledger.append_event(item_id, "divergence_accepted", ref=divergence.id, **_why(note))
    return f"{item_id} accepted {field}={forge}"


def _accept_parent(ledger: Ledger, item_id: str, divergence: Event) -> None:
    """The forge's parent becomes the ledger's: the item mirrored as that issue number is the new parent."""
    slug = divergence.get("target")
    wanted = str(divergence.get("forge"))
    number = int(wanted[1:]) if wanted.startswith("#") else 0
    parent = ""
    if number:
        parent = next((other for other in ledger.item_ids() for record in ledger.load_mirrors(other)
                       if record["_target"] == slug and record.get("number") == number and record.get("kind") == "issue"), "")
        if not parent:
            raise QewError(f"no ledger item is mirrored as issue #{number}; settle the parent on the forge or in the ledger by hand", 2)
    item = ledger.load_item(item_id)
    ledger.write_item(item.with_value("parent", parent), overwrite=True)
    ledger.append_event(item_id, "field", key="parent", old=item.parent, new=parent, provenance="forge")
    for record in ledger.load_mirrors(item_id):
        if record["_target"] == slug:
            ledger.write_mirror(item_id, slug, updated(record, parent_number=number))


AUTO_REASON = "auto: the ledger is the source of truth"


def settle_automatically(config: Config, slug: str, only: Collection[str] | None = None) -> int:
    """Reject every open divergence of the target, or of the items in `only` (contested claims keep their arbitration): the
    ledger value stays and the push that follows restores it on the forge. Reads the ledger once, however many there are.
    Returns how many were settled."""
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    settled = 0
    for item_id in sorted(snapshot.items if only is None else set(only) & set(snapshot.items)):
        for divergence in snapshot.folds[item_id].divergences:
            if divergence.type == "claimed" or divergence.get("target") != slug:
                continue
            _reject_one(ledger, item_id, divergence, AUTO_REASON)
            settled += 1
    return settled


def _why(note: str) -> JsonObject:
    return {"reason": note} if note else {}


def reject(config: Config, reference: str | list[str] | tuple[str, ...], note: str = "") -> str:
    if isinstance(reference, (list, tuple)):
        return "\n".join(reject(config, ref, note) for ref in reference)
    ledger = ledger_for(config)
    item_id, divergence = _find(ledger, reference)
    if divergence.type == "claimed":
        return _settle_contest(ledger, ledger.snapshot(), item_id, divergence, keep_contender=False, note=note)
    return _reject_one(ledger, item_id, divergence, note)


def _reject_one(ledger: Ledger, item_id: str, divergence: Event, note: str) -> str:
    """Record the rejection of one open (non-claim) divergence and force the next push to rewrite the forge."""
    ledger.append_event(item_id, "divergence_rejected", ref=divergence.id, **_why(note))
    slug = divergence.get("target")
    for record in ledger.load_mirrors(item_id):
        if record["_target"] == slug:
            if divergence.get("field") == "parent":  # the push compares the ledger parent with what is on the forge now
                forge_parent = str(divergence.get("forge"))
                record = updated(record, parent_number=int(forge_parent[1:]) if forge_parent.startswith("#") else 0)
            if str(divergence.get("field")).startswith("project:"):
                record = updated(record, fields={})  # forget the pushed values so the next sync sets the card field again
            ledger.write_mirror(item_id, slug, updated(record, synced_hash=""))  # forces the next push to rewrite the forge
    return f"{item_id} rejected {divergence.get('field')}={divergence.get('forge')}"


def _settle_contest(ledger: Ledger, snapshot: Snapshot, item_id: str, contender: Event, keep_contender: bool, note: str = "") -> str:
    """Two effective claims: the loser gets a `claim_lost` naming the winner; no clock is consulted."""
    holder = snapshot.folds[item_id].owner
    lost_refs = {e.get("ref") for e in snapshot.events[item_id] if e.type == "claim_lost"}
    first = next(e for e in sorted(snapshot.events[item_id], key=lambda item: item.id)
                 if e.type == "claimed" and e.id not in lost_refs and e.get("owner") == holder)
    loser, winner = (first, contender.get("owner")) if keep_contender else (contender, holder)
    ledger.append_event(item_id, "claim_lost", ref=loser.id, winner=winner, **_why(note))
    return f"{item_id} {'accepted' if keep_contender else 'rejected'} owner={contender.get('owner')}"
