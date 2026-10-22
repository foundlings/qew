"""`qew pull`: bring the ledger remote's new commits in, and nothing else.

One fetch and merge of the ledger repository. It makes no forge call, starts no sync worker and asks no claim arbiter, so it is
safe to run often and from every workspace; `qew sync` stays the forge reconcile.
"""

import argparse
import json

from qew import ledger_config, replication

SHOWN = 12


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    pull = commands.add_parser("pull", help="fetch the ledger remote and merge it: other machines' items, states, evidence and pull requests, no forge call")
    pull.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "pull":
        return False
    pulled = replication.pull(ledger_config.load())
    if arguments.json:
        print(json.dumps({"schema_version": 1, "commits": pulled.commits, "items": list(pulled.items), "from": pulled.before, "to": pulled.after}))
    elif not pulled.commits:
        print("up to date")
    else:
        names = ", ".join(pulled.items[:SHOWN]) + (f" and {len(pulled.items) - SHOWN} more" if len(pulled.items) > SHOWN else "")
        print(f"pulled {pulled.commits} commits; {len(pulled.items)} items changed" + (f": {names}" if names else ""))
    return True
