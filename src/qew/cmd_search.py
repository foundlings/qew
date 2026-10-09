"""`qew search`: offline search over the ids, titles and labels of approved work.

Bodies and comments are never searched or printed; unapproved text is reachable only through `qew triage`.
Terms are data: compared as text, never evaluated or passed to a shell.
"""

import argparse
import re
import sys

from qew import cmd_read, ledger_config, read_filter, read_view, readfmt
from qew.ledger_event import State
from qew.ledger_ids import sort_key
from qew.ledger_store import Snapshot

SEARCH_LIMIT = 30


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    search = commands.add_parser("search", help="find approved work by words in its id, title or labels")
    search.add_argument("terms", nargs="+", metavar="TERM", help="every term must match (put `--` before a term that starts with a dash)")
    search.add_argument("--limit", type=cmd_read.limit_type, default=SEARCH_LIMIT, help=f"at most N items, default {SEARCH_LIMIT} (0 = all)")
    search.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "search":
        return False
    snapshot = cmd_read.snapshot_for(ledger_config.load())
    found = search(snapshot, arguments.terms)
    shown = found[: arguments.limit] if arguments.limit else found
    if len(shown) < len(found):
        print(f"qew: search results capped: showing {len(shown)} of {len(found)} (--limit 0 shows all)", file=sys.stderr)
    if not found:
        print("qew: no approved work matches; unapproved text is searched with `qew triage --search TERMS`", file=sys.stderr)
    if arguments.json:
        print(readfmt.envelope(read_view.rows(snapshot, shown), len(found), len(shown) < len(found)))
    else:
        for item_id in shown:
            print(read_view.text_row(snapshot, item_id))
    return True


def search(snapshot: Snapshot, terms: list[str]) -> list[str]:
    """Matching non-draft items, best match first, then priority, then id."""
    wanted = [read_filter.fold(term) for term in terms]
    whole = " ".join(wanted)
    ranked = []
    for item_id, item in snapshot.items.items():
        if snapshot.folds[item_id].state in (None, State.DRAFT):
            continue
        title = read_filter.fold(item.title)
        fields = [read_filter.fold(item_id), title, *(read_filter.fold(label) for label in item.labels)]
        if not all(any(term in field for field in fields) for term in wanted):
            continue
        if title == whole:
            quality = 0
        elif all(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", title) for term in wanted):
            quality = 1
        else:
            quality = 2
        ranked.append((quality, item.priority, sort_key(item_id), item_id))
    return [item_id for *_, item_id in sorted(ranked)]
