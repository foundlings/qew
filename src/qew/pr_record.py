"""A pull request as the ledger records it: who opened it and which items it covers.

The forge's text is never the source of either fact; both come from the `pr` mirror file qew
wrote after the forge accepted the open.
"""

from collections import deque
from dataclasses import dataclass
from typing import cast

from qew.mirror_types import MirrorRecord
from qew.errors import NotFound, QewError
from qew.ledger_event import State
from qew.ledger_graph import children
from qew.ledger_item import Kind
from qew.ledger_store import Ledger, Snapshot

CLOSED_STATES = (State.COMPLETED, State.CLOSED)


@dataclass(frozen=True)
class PrRecord:
    host: str  # the item whose mirror directory holds the file
    number: int
    mirror_file: str  # the file stem, `<target>-pr-<N>`
    target: str  # the mirror target slug the pull request lives on
    opener: str  # the actor who opened it; "" for a record from before openers were kept
    items: tuple[str, ...]  # the covered items


def positive_number(number: int) -> int:
    if number <= 0:
        raise QewError(f"pull request number must be a positive integer, not {number}", 2)
    return number


def find(ledger: Ledger, number: int, target: str | None) -> PrRecord:
    found: list[PrRecord] = []
    for item_id in ledger.item_ids():
        for record in ledger.load_mirrors(item_id):
            if record.get("kind") != "pr" or record.get("number") != number:
                continue
            if target and target not in record["_target"]:
                continue
            found.append(_record(item_id, record))
    if not found:
        raise NotFound(f"pull request #{number} is not recorded in the ledger; open it with `qew pr open`, or adopt and close an unmerged legacy one with `qew pr close {number} --item ID --reason TEXT`")
    if len(found) > 1:
        raise QewError(f"pull request #{number} exists on several targets; pass --target", 2)
    return found[0]


def _record(host: str, mirror: MirrorRecord) -> PrRecord:
    stem = mirror["_target"]
    items: object = mirror.get("items", [host])  # a record from before covered items were kept covers its host; read from disk
    opener: object = mirror.get("opener", "")
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items) or not isinstance(opener, str):
        raise QewError(f"mirrors/{host}/{stem}.json: `items` must be a list of ids and `opener` a string", 1)
    return PrRecord(host, mirror["number"], stem, stem.rsplit("-pr-", 1)[0], opener, tuple(items))


def covered_by(snapshot: Snapshot, root: str) -> list[str]:
    """What a pull request opened for `root` covers: an epic and its open descendants, else the item."""
    if snapshot.items[root].kind != Kind.EPIC:
        return [root]
    kids = children(snapshot)
    found: list[str] = []
    seen: set[str] = set()
    queue = deque([root])
    while queue:
        item_id = queue.popleft()
        if item_id in seen:
            continue
        seen.add(item_id)
        if item_id == root or snapshot.folds[item_id].state not in (State.CLOSED,):
            found.append(item_id)
            queue.extend(kids.get(item_id, []))
    return found


def known_states(snapshot: Snapshot, record: PrRecord) -> dict[str, State]:
    """State of every covered item; a missing item or unreadable history is exit 4, never a guess."""
    states: dict[str, State] = {}
    for item_id in record.items:
        folded = snapshot.folds.get(item_id)
        if folded is None or folded.errors or folded.state is None or any(e.type == "claimed" for e in folded.divergences):
            raise QewError(
                f"pull request #{record.number} covers {item_id}, whose history cannot be read or whose owner is contested; run `qew check` and `qew status`", 4
            )
        states[item_id] = folded.state
    return states


def drafts(states: dict[str, State]) -> list[str]:
    return [item_id for item_id, state in states.items() if state == State.DRAFT]


def require_owner(snapshot: Snapshot, record: PrRecord, states: dict[str, State], who: str, reason: str, merging: bool) -> str:
    """Let `who` act on the pull request, or refuse with the ways forward (exit 3).

    Allowed: the opener, or the holder of every claim on the items still open. A merge is stricter: even
    the opener may not merge an item someone else now holds. Returns the note to print when `reason`
    (merge only) overrides it, else "". Readiness rules are never touched here.
    """
    open_items = [i for i, state in states.items() if state not in CLOSED_STATES]
    foreign = {i: snapshot.folds[i].owner for i in open_items if states[i] == State.CLAIMED and snapshot.folds[i].owner != who}
    holds_all = bool(open_items) and all(states[i] == State.CLAIMED and snapshot.folds[i].owner == who for i in open_items)
    opener = record.opener or "an unknown opener"
    if holds_all or (record.opener == who and not (merging and foreign)):
        return ""
    if merging and reason:
        return f"merging {opener}'s pull request: {reason}"
    ways = "take the items over with `qew start ITEM --take-over --reason TEXT`"
    if merging:
        ways += " or merge it with `--other-author --reason TEXT`"
    if record.opener == who:
        held = ", ".join(f"{item} is claimed by {owner}" for item, owner in foreign.items())
        raise QewError(f"pr #{record.number} cannot merge: {held}; {ways}", 3)
    covers = " ".join(open_items) or "no open item"
    raise QewError(
        f"pr #{record.number} was opened by {opener}; {who} neither opened it nor holds "
        f"the claim on every item it covers ({covers}); {ways}",
        3,
    )


def update_mirror(ledger: Ledger, record: PrRecord, **changes: object) -> None:
    """Rewrite the pull request's mirror file with `changes`, keeping every other field."""
    current = next(m for m in ledger.load_mirrors(record.host) if m["_target"] == record.mirror_file)
    ledger.write_mirror(record.host, record.mirror_file, cast(MirrorRecord, {**{k: v for k, v in current.items() if k != "_target"}, **changes}))


def covering_open(ledger: Ledger, item_id: str) -> list[int]:
    """Numbers of the recorded pull requests that cover `item_id`, are not finished and are not drafts."""
    found: list[int] = []
    for host in ledger.item_ids():
        for mirror in ledger.load_mirrors(host):
            if mirror.get("kind") != "pr" or mirror.get("state", "open") in ("merged", "closed") or mirror.get("draft") is True:
                continue
            if item_id in mirror.get("items", [host]) and isinstance(mirror.get("number"), int):
                found.append(mirror["number"])
    return sorted(found)
