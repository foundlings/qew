"""`qew inbox`: one person's waiting drafts and assigned work, read from the ledger only."""

import json
import re
from typing import List, Tuple

from qew import ledger_author, ledger_config, ledger_graph, ledger_store
from qew.cmd_items import ledger_for, row, row_data
from qew.errors import QewError
from qew.ledger_event import State

SCHEMA_VERSION = 1
_LOGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$")


def register(commands) -> None:
    inbox = commands.add_parser("inbox", help="drafts waiting for a human and work assigned to one person (offline)")
    inbox.add_argument("--login", help="whose inbox (default: the configured actor)")
    inbox.add_argument("--count", action="store_true", help="print one summary line")
    inbox.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments) -> bool:
    if arguments.command != "inbox":
        return False
    if arguments.count and arguments.json:
        raise QewError("--count and --json cannot be combined", 2)
    login = arguments.login if arguments.login is not None else ledger_store.actor()
    if not _LOGIN.match(login):
        raise QewError(f"`{login}` is not a valid login", 2)
    snapshot = ledger_for(ledger_config.load()).snapshot()
    waiting, assigned = sections(snapshot, login)
    if arguments.count:
        print(f"inbox: {len(waiting)} waiting, {len(assigned)} assigned")
        return True
    listed = [("waiting", i) for i in waiting] + [("assigned", i) for i in assigned]
    if arguments.json:
        print(json.dumps([{**row_data(snapshot, i), "section": name, "schema_version": SCHEMA_VERSION} for name, i in listed]))
    else:
        for name, item_id in listed:
            print(f"{name} {row(snapshot, item_id)}")
    return True


def sections(snapshot, login: str) -> Tuple[List[str], List[str]]:
    """(waiting, assigned): drafts I authored, and items I hold the claim on."""
    me = login.casefold()
    ids = ledger_graph.ordered(snapshot, list(snapshot.items))
    waiting = [i for i in ids if snapshot.folds[i].state == State.DRAFT and ledger_author.author(snapshot, i).casefold() == me]
    assigned = [i for i in ids if snapshot.folds[i].state == State.CLAIMED and snapshot.folds[i].owner.casefold() == me]
    return waiting, assigned
