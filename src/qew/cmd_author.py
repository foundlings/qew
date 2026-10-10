"""`qew correct-author`: a reviewed bulk correction of who authored items, by appending events (history is never rewritten)."""

import argparse

from qew import ledger_author, ledger_config, requester
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.ledger_event import valid_actor


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    correct = commands.add_parser("correct-author", help="credit items to their human author: one correction event each, the tool kept as via")
    correct.add_argument("--from", dest="source", required=True, metavar="ACTOR", help="the author to correct, e.g. the importer qew-import")
    correct.add_argument("--to", required=True, metavar="ACTOR", help="the author the items belong to")
    correct.add_argument("--quote", help="a human's words, \"Name: 'their words'\" (required unless --dry-run)")
    correct.add_argument("--dry-run", action="store_true", help="say how many items would change and write nothing")


def correct_author(config: ledger_config.Config, source: str, target: str, quote: str | None, dry_run: bool) -> str:
    """Append one `author_corrected` event to each item whose author is `source`; a rerun finds none."""
    for name, who in (("--from", source), ("--to", target)):
        if not valid_actor(who):
            raise QewError(f"{name}: `{who}` is not an actor (one word, no spaces or commas)", 2)
    if source == target:
        raise QewError("--from and --to are the same author: nothing to correct", 2)
    verified = None if dry_run else requester.require_quote(quote)
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    wanted = sorted(item_id for item_id in snapshot.items if ledger_author.author(snapshot, item_id) == source)
    if not dry_run:
        for item_id in wanted:
            ledger.append_event(item_id, "author_corrected", author=target, via=source, quote=verified)
    return f"{'would correct' if dry_run else 'corrected'} {len(wanted)} item(s): author {source} -> {target}, via {source}"


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "correct-author":
        return False
    print(correct_author(ledger_config.load(), arguments.source, arguments.to, arguments.quote, arguments.dry_run))
    return True
