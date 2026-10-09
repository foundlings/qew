"""`qew inbox`: what waits on one person, read from the ledger only."""

import argparse

from qew import cmd_read, inbox, instants, ledger_config, ledger_store, read_view, readfmt
from qew.errors import QewError


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = commands.add_parser("inbox", help="replies, drafts waiting for a human and work assigned to one person (offline)")
    command.add_argument("--login", help="whose inbox (default: the configured actor)")
    command.add_argument("--since", help="reply window start: YYYY-MM-DDTHH:MM:SSZ or a date (midnight UTC); default 7 days ago")
    command.add_argument("--count", action="store_true", help="print one summary line")
    command.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "inbox":
        return False
    if arguments.count and arguments.json:
        raise QewError("--count and --json cannot be combined", 2)
    login = inbox.require_login(arguments.login if arguments.login is not None else ledger_store.actor())
    since = instants.require_window_start(arguments.since) if arguments.since is not None else instants.days_ago(inbox.WINDOW_DAYS)
    snapshot = cmd_read.snapshot_for(ledger_config.load())
    if arguments.count:
        print(inbox.count_line(snapshot, login, since))
        return True
    found = inbox.replies(snapshot, login, since)
    waiting, assigned = inbox.waiting(snapshot, login), inbox.assigned(snapshot, login)
    if arguments.json:
        rows: list[dict[str, object]] = [{"section": "replies", "id": r.item, "author": r.author, "at": r.at, "excerpt": r.excerpt} for r in found]
        rows += [{**read_view.row_data(snapshot, i), "section": name} for name, ids in (("waiting", waiting), ("assigned", assigned)) for i in ids]
        for line in readfmt.ndjson(rows):
            print(line)
        return True
    print(f"replies ({len(found)}) since {instants.format_instant(since)}")
    for reply in found:
        print(f"  {reply.item} @{read_view.plain(reply.author)} {reply.at}: {reply.excerpt}")
    print(f"waiting for a human ({len(waiting)})")
    for item_id in waiting:
        print(f"  {read_view.text_row(snapshot, item_id)}")
    print(f"assigned ({len(assigned)})")
    for item_id in assigned:
        print(f"  {read_view.text_row(snapshot, item_id)}")
    return True
