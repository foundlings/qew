"""`qew gc [--now]`: collect the jj operation log of the ledger (see op_gc); a write starts it detached once a day."""

import argparse

from qew import ledger_config, op_gc, replication
from qew.errors import QewError
from qew.ledger_repo import LedgerRepo


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    gc = commands.add_parser("gc", help="collect the ledger's jj operation log now (a write starts it detached once a day); holds the write lock a bounded time")
    gc.add_argument("--now", action="store_true", help="run in the foreground and report what was dropped (the default for a person at a terminal)")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "gc":
        return False
    config = ledger_config.load()
    repo = LedgerRepo(config.ledger_path, config.replication.branch)
    if repo.vcs != "jj":
        raise QewError("the op-log collection is for a jj ledger; this ledger is not one", 2)
    print(op_gc.run_detached(config, repo, lambda: replication.exclusive(config, "gc", op_gc.LOCK_WAIT_S)))
    return True
