"""What waits on one person, derived from the ledger alone: replies, drafts they authored, work they hold.

A reply is a comment by someone else (not a bot, not a machine marker) at or after the window start,
on an item the person created, claimed or commented on. Every event of every candidate item is scanned.
"""

from dataclasses import dataclass
from datetime import datetime
import re

from qew import instants, ledger_author, ledger_graph, read_view
from qew.errors import QewError
from qew.ledger_event import State
from qew.ledger_store import Snapshot

# Letters, digits, `-`, `_`, `.` and an optional `[bot]`: no whitespace, colon or quote can ride along.
_LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,49}(\[bot\])?")
EXCERPT = 100
MARKER = "<!-- qew:"
WINDOW_DAYS = 7
COUNT_LISTED = 5


def require_login(text: str) -> str:
    if not _LOGIN.fullmatch(text):
        raise QewError(f"`{text}` is not a valid login (letters, digits, `-`, `_`, `.`)", 2)
    return text


@dataclass(frozen=True)
class Reply:
    item: str
    author: str
    at: str
    excerpt: str


def excerpt(text: str) -> str:
    """The first line, control characters as spaces, cut to 100 characters with an ellipsis."""
    lines = text.strip().splitlines()
    line = read_view.plain(lines[0]) if lines else ""
    return line if len(line) <= EXCERPT else line[: EXCERPT - 1] + "…"


def _is_machine(author: str, text: str) -> bool:
    return author.lower().endswith("[bot]") or text.lstrip().startswith(MARKER)


def _involved(snapshot: Snapshot, item_id: str, me: str) -> bool:
    if ledger_author.author(snapshot, item_id).casefold() == me:
        return True
    return any(
        (e.type == "claimed" and str(e.get("owner")).casefold() == me)
        or (e.type == "commented" and (e.get("author") or e.actor).casefold() == me)
        for e in snapshot.events[item_id]
    )


def replies(snapshot: Snapshot, login: str, since: datetime) -> list[Reply]:
    me = login.casefold()
    found = []
    for item_id in snapshot.items:
        if not _involved(snapshot, item_id, me):
            continue
        for event in snapshot.events[item_id]:
            if event.type != "commented":
                continue
            author = str(event.get("author") or event.actor)
            moment = instants.parse_instant(event.at)
            if moment is None:
                raise QewError(f"events/{item_id}/{event.id}: event time `{event.at}` is not a UTC instant", 1)
            text = str(event.get("text"))
            if author.casefold() == me or _is_machine(author, text) or moment < since:
                continue
            found.append((event.at, item_id, Reply(item_id, author, event.at, excerpt(text))))
    return [reply for *_, reply in sorted(found, key=lambda entry: entry[:2])]


def waiting(snapshot: Snapshot, login: str) -> list[str]:
    me = login.casefold()
    return [i for i in ledger_graph.ordered(snapshot, list(snapshot.items))
            if snapshot.folds[i].state == State.DRAFT and ledger_author.author(snapshot, i).casefold() == me]


def assigned(snapshot: Snapshot, login: str) -> list[str]:
    me = login.casefold()
    return [i for i in ledger_graph.ordered(snapshot, list(snapshot.items))
            if snapshot.folds[i].state == State.CLAIMED and snapshot.folds[i].owner.casefold() == me]


def count_line(snapshot: Snapshot, login: str, since: datetime, listed: int = COUNT_LISTED) -> str:
    """`qew inbox (NAME): N issue(s) with replies since T: ID (author), ...; M waiting for a human; K assigned.`"""
    found = replies(snapshot, login, since)
    latest = {}
    for reply in found:  # oldest first, so the last write per item is its latest author
        latest[reply.item] = reply.author
    items = list(reversed(list(latest.items())))
    head = f"qew inbox ({login}): {len(items)} issue(s) with replies since {instants.format_instant(since)}"
    if items and listed:
        shown = ", ".join(f"{item} ({author})" for item, author in items[:listed])
        head += f": {shown}" + (f" +{len(items) - listed} more" if len(items) > listed else "")
    return f"{head}; {len(waiting(snapshot, login))} waiting for a human; {len(assigned(snapshot, login))} assigned."


def hook_line(snapshot: Snapshot, login: str, room: int) -> str | None:
    """The count line for the session-start context when something waits and it fits in `room` bytes."""
    since = instants.days_ago(WINDOW_DAYS)
    if not (replies(snapshot, login, since) or waiting(snapshot, login) or assigned(snapshot, login)):
        return None
    for listed in (COUNT_LISTED, 2, 0):
        line = count_line(snapshot, login, since, listed)
        if len(line.encode("utf-8")) + 1 <= room:
            return line
    return None
