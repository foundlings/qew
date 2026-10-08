"""`qew comment`: discuss any item, in any state, without changing it."""

import sys

from qew import ledger_config
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.ledger_config import Config


def register(commands) -> None:
    comment = commands.add_parser("comment", help="add a comment to any item; state never changes")
    comment.add_argument("item", help="item id or alias")
    comment.add_argument("--body", help="the comment text")
    comment.add_argument("--body-file", help="read the comment from a file, or - for stdin")


def run(arguments) -> bool:
    if arguments.command != "comment":
        return False
    print(comment(ledger_config.load(), arguments.item, arguments.body, arguments.body_file))
    return True


def _text(body, body_file) -> str:
    if (body is None) == (body_file is None):
        raise QewError("give exactly one of --body and --body-file", 2)
    if body is None:
        if body_file == "-":
            body = sys.stdin.read()
        else:
            try:
                with open(body_file, encoding="utf-8") as handle:
                    body = handle.read()
            except (OSError, UnicodeError) as error:
                raise QewError(f"cannot read {body_file}: {error}", 2) from None
    if not body.strip():
        raise QewError("a comment needs a non-blank body", 2)
    return body


def comment(config: Config, reference: str, body, body_file) -> str:
    text = _text(body, body_file)
    ledger = ledger_for(config)
    item_id = ledger.resolve(reference, ledger.item_ids())
    ledger.append_event(item_id, "comment", body=text)
    return f"commented {item_id}"
