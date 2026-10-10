"""Add many comments to many items in one ledger write: planned and checked whole before the first event is written.

Used by `qew comment --manifest` and the `comments` of a create plan. Every row carries a `key`: the same key with the same
text is unchanged (a rerun adds nothing), with other text it is refused. The original author and time are kept (the event
records who imported them, the comment records who wrote them); a reply names the `key` of an earlier row or an existing
comment id.
"""

from dataclasses import dataclass
from typing import Any

from qew import comments, instants
from qew.errors import QewError
from qew.jsontypes import JsonObject
from qew.ledger_event import MAX_COMMENT_BYTES, valid_actor
from qew.ledger_store import Ledger

ROW_KEYS = {"item", "key", "text", "author", "at", "reply_to"}


@dataclass(frozen=True)
class Row:
    item_id: str
    key: str
    text: str
    author: str  # "" : the importing login
    at: str  # "" : now
    reply_to: str  # a key of this batch, or a comment id of the item; "" for none
    where: str


@dataclass(frozen=True)
class Batch:
    rows: tuple[Row, ...]  # what is new
    unchanged: int


def row(raw: JsonObject, item_id: str, where: str) -> Row:
    """One checked row; `item_id` is the item it belongs to."""
    unknown = sorted(set(raw) - ROW_KEYS)
    if unknown:
        raise QewError(f"{where}: unknown comment field `{unknown[0]}` (fields: key, text, author, at, reply_to)", 2)
    key, text = raw.get("key"), raw.get("text")
    if not isinstance(key, str) or not key.strip() or any(c.isspace() for c in key) or key.startswith("@"):
        raise QewError(f"{where}: a comment needs a `key`: one word, no leading @ (it makes a rerun a no-op)", 2)
    if not isinstance(text, str) or not text.strip():
        raise QewError(f"{where}: a comment needs text", 2)
    if len(text.encode("utf-8")) > MAX_COMMENT_BYTES:
        raise QewError(f"{where}: a comment is at most {MAX_COMMENT_BYTES} bytes", 2)
    author, at, reply = raw.get("author", ""), raw.get("at", ""), raw.get("reply_to", "")
    if not isinstance(author, str) or (author and not valid_actor(author)):
        raise QewError(f"{where}: `author` must be one word without spaces or commas", 2)
    if not isinstance(at, str) or (at and instants.parse_instant(at) is None):
        raise QewError(f"{where}: `at` must be a UTC time like 2026-01-31T09:30:00Z", 2)
    if not isinstance(reply, str):
        raise QewError(f"{where}: `reply_to` must be text", 2)
    return Row(item_id, key, text, author, at, reply, where)


def plan(ledger: Ledger, rows: list[Row]) -> Batch:
    """Check every row against the item's comments and each other; nothing is written."""
    new: list[Row] = []
    unchanged = 0
    by_item: dict[str, dict[str, str]] = {}  # item -> key -> text, existing then this batch
    existing_ids: dict[str, list[comments.Comment]] = {}
    for found in rows:
        if found.item_id not in by_item:
            exists = (ledger.items_dir / f"{found.item_id}.md").exists()
            events = ledger.load_events(found.item_id) if exists else []
            by_item[found.item_id] = {str(e.get("key")): str(e.get("text")) for e in events if e.type == "commented" and e.get("key")}
            existing_ids[found.item_id] = comments.comments_after(events)
        keys = by_item[found.item_id]
        if found.reply_to and found.reply_to not in keys:
            try:
                comments.find(existing_ids[found.item_id], found.reply_to)
            except QewError as error:
                raise QewError(f"{found.where}: reply_to `{found.reply_to}` is neither an earlier key nor a comment of the item ({error})", 2) from None
        if found.key in keys:
            if keys[found.key] != found.text:
                raise QewError(f"{found.where}: key `{found.key}` already holds different text on {found.item_id}; use another key", 2)
            unchanged += 1
            continue
        keys[found.key] = found.text
        new.append(found)
    return Batch(tuple(new), unchanged)


def apply(ledger: Ledger, batch: Batch) -> None:
    """Append the `commented` events in order, resolving a reply's key to the id its comment just got."""
    ids: dict[tuple[str, str], str] = {}
    for found in batch.rows:
        existing = {str(e.get("key")): e.id for e in ledger.load_events(found.item_id) if e.type == "commented" and e.get("key")}
        reply = ""
        if found.reply_to:
            reply = ids.get((found.item_id, found.reply_to)) or existing.get(found.reply_to) or \
                comments.find(comments.comments_after(ledger.load_events(found.item_id)), found.reply_to).id
        data: dict[str, Any] = {"text": found.text, "key": found.key, "provenance": "import"}
        if found.author:
            data["author"] = found.author
        if reply:
            data["reply_to"] = reply
        event = ledger.append_event(found.item_id, "commented", at=found.at, **data)
        ids[(found.item_id, found.key)] = event.id


def summary(batch: Batch, verb: str = "added") -> str:
    if not batch.rows:
        return f"comments unchanged ({batch.unchanged} already there)" if batch.unchanged else "nothing to add"
    same = f" ({batch.unchanged} unchanged)" if batch.unchanged else ""
    return f"{verb} {len(batch.rows)} comment(s) to {len({r.item_id for r in batch.rows})} item(s){same}"
