"""Settle provisional (offline) claims once the arbiter can be reached again.

`qew start --offline` (or `[authority] offline_claims = "allow"` with the arbiter down) writes a `claimed` event
marked `provisional`. A sync then asks the arbiter: with a primary forge, whoever holds the earliest claim
comment in force; with the ledger remote, a rival claim that was confirmed or is older. The claim becomes
`claim_confirmed`, or `claim_lost` naming the winner. A claim whose arbiter is still unreachable stays
provisional and is tried again at the next sync.
"""

from dataclasses import dataclass
from enum import Enum
import secrets
from typing import Callable, List, Optional

from qew import claim_authority, ledger_config
from qew.forge import Forge, ForgeError
from qew.ledger_config import Config
from qew.ledger_event import Event, State
from qew.ledger_store import Ledger, Snapshot


class Verdict(Enum):
    CONFIRMED = "confirmed"
    LOST = "lost"
    DEFERRED = "deferred"  # the arbiter could not be asked; the claim stays provisional


@dataclass(frozen=True)
class Settled:
    item: str
    claim: str  # id of the provisional `claimed` event
    owner: str
    verdict: Verdict
    winner: Optional[str] = None

    def line(self) -> str:
        if self.verdict is Verdict.LOST:
            return (f"claim lost: {self.item} is held by {self.winner}; "
                    f"your offline claim was dropped (claim_lost {self.claim})")
        if self.verdict is Verdict.CONFIRMED:
            return f"claim confirmed: {self.item} (offline claim of {self.owner})"
        return f"claim provisional: {self.item} (the arbiter is unreachable; sync again later)"


def settled_refs(events: List[Event]) -> set:
    return {e.get("ref") for e in events if e.type in ("claim_confirmed", "claim_lost")}


def provisional_claims(snapshot: Snapshot) -> List[tuple]:
    """(item id, claimed event) of every provisional claim still waiting for the arbiter."""
    found = []
    for item_id, events in snapshot.events.items():
        done = settled_refs(events)
        completed_under = {e.get("provisional_claim") for e in events if e.type == "completed"}
        folded = snapshot.folds[item_id]
        for event in events:
            if event.type != "claimed" or not event.get("provisional") or event.id in done:
                continue
            holds = folded.state == State.CLAIMED and folded.owner == event.get("owner")
            if holds or event.id in completed_under:
                found.append((item_id, event))
    return found


def lost_provisional_claims(snapshot: Snapshot) -> List[tuple]:
    """(item id, winner) of offline claims that were lost and whose item is still open and not re-claimed by the loser."""
    found = []
    for item_id, events in snapshot.events.items():
        folded = snapshot.folds[item_id]
        if folded.state not in (State.APPROVED, State.CLAIMED):
            continue
        owners = {e.id: e.get("owner") for e in events if e.type == "claimed"}
        for event in events:
            if event.type == "claim_lost" and event.get("provisional"):
                if not (folded.state == State.CLAIMED and folded.owner == owners.get(event.get("ref"))):
                    found.append((item_id, event.get("winner")))
    return found


def provisional_claim_of(snapshot: Snapshot, item_id: str, owner: str) -> Optional[str]:
    """Id of the unsettled provisional claim `owner` holds on the item, or None."""
    for found_id, event in provisional_claims(snapshot):
        if found_id == item_id and event.get("owner") == owner:
            return event.id
    return None


def _on_primary(config: Config, ledger: Ledger, build: Callable, item_id: str, event: Event) -> Settled:
    owner = event.get("owner")
    mirror = next((m for m in config.mirrors if ledger_config.mirror_slug(m) == config.authority), None)
    record = next((r for r in ledger.load_mirrors(item_id)
                   if r["_target"] == config.authority and r.get("kind") == "issue"), None)
    if mirror is None or record is None:
        return Settled(item_id, event.id, owner, Verdict.DEFERRED)  # the first sync has not created its issue yet
    number = record["number"]
    try:
        forge: Forge = build(mirror)
        winner = claim_authority.holder(forge.list_comments(number), item_id)
        if winner is None:
            forge.create_comment(number, claim_authority.claim_comment(item_id, owner, secrets.token_hex(4)))
            winner = claim_authority.holder(forge.list_comments(number), item_id)
    except ForgeError:
        return Settled(item_id, event.id, owner, Verdict.DEFERRED)
    if winner is not None and winner[0] == owner:
        return Settled(item_id, event.id, owner, Verdict.CONFIRMED)
    return Settled(item_id, event.id, owner, Verdict.LOST, winner[0] if winner else "someone else")


def _rival(snapshot: Snapshot, item_id: str, event: Event) -> Optional[Event]:
    """The other owner's claim in the same claim period that beats `event`: confirmed, pushed online, or older."""
    events = sorted(snapshot.events[item_id], key=lambda e: e.id)
    lost = {e.get("ref") for e in events if e.type == "claim_lost"}
    confirmed = {e.get("ref") for e in events if e.type == "claim_confirmed"}
    period = max((e.id for e in events if e.type in ("released", "reopened", "closed", "reverted") and e.id < event.id), default="")
    for rival in events:
        if rival.type != "claimed" or rival.id == event.id or rival.id in lost or rival.id <= period:
            continue
        if rival.get("owner") == event.get("owner"):
            continue
        released_later = any(e.type == "released" and e.id > rival.id for e in events)
        # A claim that reached the remote under arbitration (not itself provisional) is first, whatever its id and
        # whether or not its claim_confirmed has followed yet; two provisional claims fall back to the older.
        if not released_later and (rival.id in confirmed or not rival.get("provisional") or rival.id < event.id):
            return rival
    return None


def _on_ledger_remote(config: Config, snapshot: Snapshot, item_id: str, event: Event) -> Settled:
    from qew.ledger_repo import LedgerRepo

    owner = event.get("owner")
    rep = config.replication
    repo = LedgerRepo(config.ledger_path, rep.branch)
    if repo.vcs is not None and repo.has_remote() and rep.auto_push:
        marker = config.ledger_path / ".qew" / "remote-state"
        if rep.offline or (marker.exists() and marker.read_text(encoding="utf-8").strip() == "offline"):
            return Settled(item_id, event.id, owner, Verdict.DEFERRED)
        rival = _rival(snapshot, item_id, event)
        if rival is not None:
            return Settled(item_id, event.id, owner, Verdict.LOST, rival.get("owner"))
    return Settled(item_id, event.id, owner, Verdict.CONFIRMED)  # a single writer, or nobody else claimed it


def reconcile(config: Config, ledger: Ledger, build: Callable, only_item: Optional[str] = None) -> List[Settled]:
    """Ask the arbiter about every provisional claim and record the verdicts as events."""
    snapshot = ledger.snapshot()
    results: List[Settled] = []
    for item_id, event in provisional_claims(snapshot):
        if only_item is not None and item_id != only_item:
            continue
        settled = (_on_primary(config, ledger, build, item_id, event) if config.authority != "none"
                   else _on_ledger_remote(config, snapshot, item_id, event))
        if settled.verdict is Verdict.CONFIRMED:
            ledger.append_event(item_id, "claim_confirmed", ref=event.id)
        elif settled.verdict is Verdict.LOST:
            ledger.append_event(item_id, "claim_lost", ref=event.id, winner=settled.winner, provisional=True,
                                reason="offline claim lost to the holder the arbiter recorded")
        results.append(settled)
    return results


def lost_message(results: List[Settled]) -> Optional[str]:
    lost = [r for r in results if r.verdict is Verdict.LOST]
    if not lost:
        return None
    named = ", ".join(f"{r.item} (held by {r.winner})" for r in lost)
    return f"offline claim lost: {named}; the work on {'it' if len(lost) == 1 else 'them'} needs a decision with the holder"
