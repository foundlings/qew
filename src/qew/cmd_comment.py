"""`qew comment`: append a comment to an item's history. Comments are data and never change state."""

import argparse
import os
import json
import sys
from pathlib import Path
from typing import Any

from qew import cmd_items, code_text, comment_batch, comments, ledger_config
from qew.errors import QewError
from qew.ledger_event import MAX_COMMENT_BYTES
from qew.ledger_store import Ledger, actor


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    comment = commands.add_parser("comment", help="add, reply to, edit or delete a comment on an item")
    comment.add_argument("item", nargs="?", help="item id or alias")
    comment.add_argument("--manifest", help="instead of ITEM: a JSON Lines file of {item, key, text, author, at, reply_to} rows, added in one commit")
    comment.add_argument("--dry-run", action="store_true", help="with --manifest: say what would be added and write nothing")
    comment.add_argument("--body", help="the comment text")
    comment.add_argument("--body-file", help="read the comment from a file, or - for stdin")
    comment.add_argument("--key", help="idempotency key (one word): a retry with the same key and text posts nothing new")
    comment.add_argument("--reply-to", help="answer this comment (its id or a unique prefix)")
    comment.add_argument("--edit", metavar="COMMENT", help="replace the text of this comment (its id or a unique prefix); the old text stays in history")
    comment.add_argument("--delete", metavar="COMMENT", help="hide this comment; the event stays in history")
    comment.add_argument("--force", action="store_true", help="with --edit or --delete on another author's comment: recorded as forced")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "comment":
        return False
    ledger = cmd_items.ledger_for(ledger_config.load())
    if arguments.manifest:
        if arguments.item or arguments.body or arguments.body_file or arguments.edit or arguments.delete or arguments.reply_to or arguments.key:
            raise QewError("--manifest replaces ITEM and the comment options: give the manifest alone", 2)
        print(_manifest(ledger, Path(arguments.manifest), arguments.dry_run))
        return True
    if not arguments.item:
        raise QewError("give ITEM, or --manifest FILE", 2)
    item_id = ledger.resolve(arguments.item, ledger.item_ids())
    if arguments.edit and arguments.delete:
        raise QewError("--edit and --delete are separate commands", 2)
    if (arguments.edit or arguments.delete) and (arguments.reply_to or arguments.key):
        raise QewError("--reply-to and --key belong to a new comment, not to --edit or --delete", 2)
    if arguments.delete:
        target = _own(ledger, item_id, arguments.delete, arguments.force)
        gone: dict[str, Any] = {"forced": True} if _forced(target, arguments.force) else {}
        ledger.append_event(item_id, "comment_deleted", ref=target.id, **gone)
        print(f"deleted comment {target.id} on {item_id}")
        return True
    text = _text(arguments)
    if arguments.edit:
        target = _own(ledger, item_id, arguments.edit, arguments.force)
        changed: dict[str, Any] = {"forced": True} if _forced(target, arguments.force) else {}
        ledger.append_event(item_id, "comment_edited", ref=target.id, text=text, **changed)
        print(f"edited comment {target.id} on {item_id}")
        return True
    key = arguments.key
    if key is not None:
        if not key.strip() or any(c.isspace() for c in key) or key.startswith("@"):
            raise QewError("--key must be one word without a leading @", 2)
        earlier = [e for e in ledger.load_events(item_id) if e.type == "commented" and e.get("key") == key]
        if earlier:
            if earlier[0].get("text") != text:
                raise QewError(f"key `{key}` already posted a different comment on {item_id}; use another key", 2)
            print(f"commented on {item_id} as {earlier[0].id} (unchanged)")
            return True
    extra: dict[str, Any] = {}
    if key is not None:
        extra["key"] = key
    if arguments.reply_to:
        extra["reply_to"] = comments.find(comments.comments_after(ledger.load_events(item_id)), arguments.reply_to).id
    event = ledger.append_event(item_id, "commented", text=text, author=actor(), **extra)
    for line in code_text.warnings(text):
        print(line, file=sys.stderr)
    print(f"commented on {item_id} as {event.id}")
    return True


def _manifest(ledger: Ledger, manifest: Path, dry_run: bool) -> str:
    """JSON Lines of comment rows, each with its `item`; checked whole, written in one commit."""
    try:
        text = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise QewError(f"cannot read {manifest}: {error}", 2) from None
    rows: list[comment_batch.Row] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"line {number}"
        try:
            raw = json.loads(line)
        except ValueError as error:
            raise QewError(f"{where}: invalid JSON ({error})", 2) from None
        if not isinstance(raw, dict) or not isinstance(raw.get("item"), str):
            raise QewError(f"{where}: a row is an object with an `item` (and key, text, author, at, reply_to)", 2)
        try:
            item_id = ledger.resolve(raw["item"], ledger.item_ids())
        except QewError as error:
            raise QewError(f"{where}: {error}", error.exit_code) from None
        rows.append(comment_batch.row({k: v for k, v in raw.items() if k != "item"}, item_id, where))
    batch = comment_batch.plan(ledger, rows)
    if not dry_run:
        comment_batch.apply(ledger, batch)
    return comment_batch.summary(batch, "would add" if dry_run else "added")


def _own(ledger: Ledger, item_id: str, reference: str, force: bool) -> comments.Comment:
    """The live comment to change; another author's needs --force."""
    target = comments.find(comments.comments_after(ledger.load_events(item_id)), reference)
    if target.deleted:
        raise QewError(f"comment {target.id} is already deleted", 2)
    if _forced(target, False):
        if not force:
            raise QewError(f"comment {target.id} is {target.author}'s; only the author edits or deletes it (--force records an override)", 2)
    return target


def _forced(target: comments.Comment, force: bool) -> bool:
    """True when the acting login is not the comment's author."""
    return actor().casefold() != target.author.casefold()


def _text(arguments: argparse.Namespace) -> str:
    if (arguments.body is None) == (arguments.body_file is None):
        raise QewError("give exactly one of --body and --body-file", 2)
    text: str | None = arguments.body
    if text is None:
        if arguments.body_file == "-":
            text = sys.stdin.read()
        else:
            if os.path.islink(arguments.body_file):
                raise QewError(f"{arguments.body_file} is a symlink; name the real file", 2)
            try:
                with open(arguments.body_file, encoding="utf-8", newline="") as handle:
                    text = handle.read()
            except (OSError, UnicodeError) as error:
                raise QewError(f"cannot read {arguments.body_file}: {error}", 2) from None
    if not text.strip():
        raise QewError("a comment needs text", 2)
    if len(text.encode("utf-8")) > MAX_COMMENT_BYTES:
        raise QewError(f"a comment is at most {MAX_COMMENT_BYTES} bytes", 2)
    return text
