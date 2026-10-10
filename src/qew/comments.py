"""Comments as records: a stable id (the `commented` event's id), replies, edits and deletes as events.

A comment is never rewritten in place. `comment_edited` and `comment_deleted` are separate event files naming the comment
by id, the last one by id wins, and the original stays in history: two machines editing one comment merge without a conflict.
"""

from dataclasses import dataclass
import re

from qew import attachments
from qew.errors import QewError
from qew.ledger_event import Event


@dataclass(frozen=True)
class Comment:
    id: str
    author: str
    at: str
    text: str  # the latest edit, or the original
    reply_to: str  # the id of the comment this answers, or ""
    edited: bool
    deleted: bool
    remote_ref: str  # the forge comment this came from, or ""


def comments_after(events: list[Event]) -> list[Comment]:
    """Every comment in id order with edits and deletes applied (an edit or delete naming no comment is ignored here and
    reported by `qew check`)."""
    ordered = sorted(events, key=lambda e: e.id)
    found: dict[str, Comment] = {}
    for event in ordered:
        if event.type == "commented":
            found[event.id] = Comment(event.id, str(event.get("author") or event.actor), event.at, str(event.get("text")),
                                      str(event.get("reply_to") or ""), False, False, str(event.get("remote_ref") or ""))
        elif event.type == "comment_edited" and event.get("ref") in found:
            current = found[event.get("ref")]
            found[current.id] = Comment(current.id, current.author, current.at, str(event.get("text")), current.reply_to,
                                        True, current.deleted, current.remote_ref)
        elif event.type == "comment_deleted" and event.get("ref") in found:
            current = found[event.get("ref")]
            found[current.id] = Comment(current.id, current.author, current.at, current.text, current.reply_to,
                                        current.edited, True, current.remote_ref)
    return list(found.values())


def find(comments: list[Comment], reference: str) -> Comment:
    """The comment with this id or unique id prefix."""
    exact = [c for c in comments if c.id == reference]
    matches = exact or [c for c in comments if reference and c.id.startswith(reference)]
    if not matches:
        raise QewError(f"no comment matches `{reference}` on this item", 2)
    if len(matches) > 1:
        raise QewError(f"`{reference}` is ambiguous: {', '.join(c.id for c in matches[:6])}", 2)
    return matches[0]


def problems(item_id: str, events: list[Event], attached: set[str]) -> list[str]:
    """What `qew check` reports: references to nothing, edits by someone else without --force, links to unattached names."""
    ordered = sorted(events, key=lambda e: e.id)
    authors = {e.id: str(e.get("author") or e.actor) for e in ordered if e.type == "commented"}
    found: list[str] = []
    for event in ordered:
        where = f"events/{item_id}/{event.id}"
        if event.type == "commented" and event.get("reply_to") and event.get("reply_to") not in authors:
            found.append(f"{where}: replies to comment {event.get('reply_to')}, which this item does not have")
        if event.type in ("comment_edited", "comment_deleted"):
            ref = event.get("ref")
            if ref not in authors:
                found.append(f"{where}: `{event.type}` names comment {ref}, which this item does not have")
            elif event.actor.casefold() != authors[ref].casefold() and not event.get("forced") and event.get("provenance") != "forge":
                found.append(f"{where}: `{event.type}` by {event.actor} on {authors[ref]}'s comment without --force")
    for comment in comments_after(events):
        if comment.deleted:
            continue
        for name in re.findall(attachments.REFERENCE, comment.text):
            if name not in attached:
                found.append(f"events/{item_id}: comment {comment.id} links attachment:{name}, which is not attached")
    return found
