"""A pull request as the ledger records it: who opened it and which items it covers.

The forge's text is never the source of either fact; both come from the `pr` mirror file qew
wrote after the forge accepted the open.
"""

import os
from collections import deque
from dataclasses import dataclass
from typing import cast

from qew import ledger_layout, ledger_store, pr_links
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
    blockers: tuple[int, ...] = ()  # pull requests this one waits on
    title: str = ""  # the title set for this pull request alone (`qew pr title`); "": the item's title
    closing: tuple[str, ...] | None = None  # the covered items this pull request completes; None: all of them (a record from before declarations)


def positive_number(number: int) -> int:
    if number <= 0:
        raise QewError(f"pull request number must be a positive integer, not {number}", 2)
    return number


_INDEX: tuple[tuple[str, int, int], list[tuple[str, MirrorRecord]]] | None = None


def entries(ledger: Ledger) -> list[tuple[str, MirrorRecord]]:
    """Every recorded pull request as (host item, mirror record), read in one pass and kept while nothing in this process writes a
    mirror: only the item directories that hold a `-pr-` file are loaded, so judging many pull requests together costs one scan, not
    one per pull request (a 26-PR union on 60,000 events took 373 s when each number rescanned every item)."""
    global _INDEX
    prs = ledger.path / "prs"
    key = (str(ledger.path), ledger_store.MIRROR_EPOCH,
           (ledger_layout.stamp(ledger.mirrors_dir, ledger.sharded) if ledger.mirrors_dir.is_dir() else 0) + (ledger_layout.stamp(prs, True) if prs.is_dir() else 0))
    if _INDEX is not None and _INDEX[0] == key:
        return _INDEX[1]
    found: list[tuple[str, MirrorRecord]] = []
    hosts: set[str] = set()
    for directory in ledger.homes_in(ledger.mirrors_dir):
        with os.scandir(directory) as listing:
            if any("-pr-" in entry.name and entry.name.endswith(".json") for entry in listing):
                hosts.add(ledger.aliases().canonical(directory.name))
    for host in sorted(hosts):
        found.extend((host, record) for record in ledger.load_mirrors(host) if record.get("kind") == "pr")
    if ledger.schema >= pr_links.LINKS_SCHEMA:
        found.extend(("", cast(MirrorRecord, record)) for _, record in pr_links.hostless(ledger))  # pull requests opened with no item
    _INDEX = (key, found)
    return found


def mirror_of(ledger: Ledger, record: PrRecord) -> MirrorRecord | None:
    """The mirror record behind `record`, from the index."""
    return next((m for host, m in entries(ledger) if host == record.host and m["_target"] == record.mirror_file), None)


def find(ledger: Ledger, number: int, target: str | None) -> PrRecord:
    found: list[PrRecord] = []
    for item_id, record in entries(ledger):
        if record.get("number") != number:
            continue
        if target and target not in record["_target"]:
            continue
        found.append(_record(ledger, item_id, record))
    if not found:
        raise NotFound(f"pull request #{number} is not recorded in the ledger; open it with `qew pr open`, or adopt and close an unmerged legacy one with `qew pr close {number} --item ID --reason TEXT`")
    if len(found) > 1:
        raise QewError(f"pull request #{number} exists on several targets; pass --target", 2)
    return found[0]


def covered_items(ledger: Ledger, host: str, mirror: MirrorRecord) -> tuple[tuple[str, ...], tuple[str, ...] | None]:
    """(covered, closing) of a recorded pull request: from its link declaration on a schema 9 ledger once it has one (covered is what
    it closes and leaves open; closing is what it completes), else the items the record froze at open (all of them closing)."""
    if ledger.schema >= pr_links.LINKS_SCHEMA:
        declaration = pr_links.load(ledger, mirror["_target"])
        if declaration.declared:
            closing = tuple(declaration.items("closes"))
            return (*closing, *declaration.items("remains")), closing
    frozen: object = mirror.get("items", [host] if host else [])
    if isinstance(frozen, list) and all(isinstance(i, str) for i in frozen):
        return tuple(frozen), None
    return (), None


def _record(ledger: Ledger, host: str, mirror: MirrorRecord) -> PrRecord:
    stem = mirror["_target"]
    items: object = mirror.get("items", [host] if host else [])  # a record from before covered items were kept covers its host; read from disk
    opener: object = mirror.get("opener", "")
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items) or not isinstance(opener, str):
        raise QewError(f"mirrors/{host}/{stem}.json: `items` must be a list of ids and `opener` a string", 1)
    blockers: object = mirror.get("blocked_by", [])
    if not isinstance(blockers, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in blockers):
        raise QewError(f"mirrors/{host}/{stem}.json: `blocked_by` must be a list of pull request numbers", 1)
    title: object = mirror.get("pr_title", "")
    if not isinstance(title, str):
        raise QewError(f"mirrors/{host}/{stem}.json: `pr_title` must be text", 1)
    covered, closing = covered_items(ledger, host, mirror)
    return PrRecord(host, mirror["number"], stem, stem.rsplit("-pr-", 1)[0], opener, covered, tuple(blockers), title, closing)


def waiting_on(ledger: Ledger, number: int) -> list[PrRecord]:
    """The recorded pull requests that list `number` among their blockers."""
    found: list[PrRecord] = []
    for item_id, record in entries(ledger):
        built = _record(ledger, item_id, record)
        if number in built.blockers:
            found.append(built)
    return found


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
    if not record.host:  # opened with no item: its record is events under prs/
        pr_links.mirror_changes(ledger, record.mirror_file, changes)
        return
    current = next(m for m in ledger.load_mirrors(record.host) if m["_target"] == record.mirror_file)
    ledger.write_mirror(record.host, record.mirror_file, cast(MirrorRecord, {**{k: v for k, v in current.items() if k != "_target"}, **changes}))


def covering_open(ledger: Ledger, item_id: str, drafts: bool = False) -> list[int]:
    """Numbers of the recorded pull requests that cover `item_id` and are not finished; drafts only when `drafts`."""
    found: list[int] = []
    for host, mirror in entries(ledger):
        if mirror.get("state", "open") in ("merged", "closed") or (mirror.get("draft") is True and not drafts):
            continue
        if item_id in covered_items(ledger, host, mirror)[0] and isinstance(mirror.get("number"), int):
            found.append(mirror["number"])
    return sorted(found)


def note(ledger: Ledger, record: PrRecord, text: str) -> None:
    """Comment on the pull request's host item; one opened with no item has none, and its record keeps no comments."""
    if record.host:
        ledger.append_event(record.host, "commented", text=text, author=ledger_store.actor())


def comment_targets(record: PrRecord) -> list[str]:
    """The items to leave a note about the pull request on: what it covers, else its host; none for a pull request with no item."""
    return [item_id for item_id in (record.items or (record.host,)) if item_id]
