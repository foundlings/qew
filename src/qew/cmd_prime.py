"""`qew prime`: the bounded session context, for agents that run without hooks."""

import argparse

from qew import cmd_read, ledger_config, prime, readfmt


def register(commands) -> None:
    command = commands.add_parser("prime", help="bounded session context: ready, active and blocked work")
    command.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "prime":
        return False
    text = prime.build(cmd_read.snapshot_for(ledger_config.load()), prime.PRIME)
    if arguments.json:
        print(readfmt.document({"additional_context": text, "context_bytes": len(text.encode("utf-8"))}))
    else:
        print(text)
    return True
