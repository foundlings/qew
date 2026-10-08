"""`qew comment`: append a comment to an item's history. Comments are data and never change state."""

import argparse
import os
import sys

from qew import cmd_items, code_text, ledger_config
from qew.errors import QewError
from qew.ledger_event import MAX_COMMENT_BYTES
from qew.ledger_store import actor


def register(commands) -> None:
    comment = commands.add_parser("comment", help="add a comment to an item")
    comment.add_argument("item", help="item id or alias")
    comment.add_argument("--body", help="the comment text")
    comment.add_argument("--body-file", help="read the comment from a file, or - for stdin")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "comment":
        return False
    text = _text(arguments)
    ledger = cmd_items.ledger_for(ledger_config.load())
    item_id = ledger.resolve(arguments.item, ledger.item_ids())
    ledger.append_event(item_id, "commented", text=text, author=actor())
    for line in code_text.warnings(text):
        print(line, file=sys.stderr)
    print(f"commented on {item_id}")
    return True


def _text(arguments: argparse.Namespace) -> str:
    if (arguments.body is None) == (arguments.body_file is None):
        raise QewError("give exactly one of --body and --body-file", 2)
    text = arguments.body
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
