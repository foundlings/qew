"""Per-command remote selection, inherited by Qew's sync workers without editing VCS configuration."""

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import os
import re

from qew.errors import QewError


def selected(ledger: bool = False) -> str:
    """A named remote, safe in both argv and quoted jj revsets."""
    key = "QEW_LEDGER_REMOTE" if ledger else "QEW_REMOTE"
    name = os.environ.get(key, "origin")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) or ".." in name or name.endswith("."):
        raise QewError(f"invalid remote name for {key}: {name!r}", 2)
    return name


def register(parser: argparse.ArgumentParser) -> None:
    """Accept the selectors before or after any subcommand without overriding an earlier selection."""
    parser.add_argument("--remote", metavar="NAME", default=argparse.SUPPRESS,
                        help="code repository remote for this command (default: origin)")
    parser.add_argument("--ledger-remote", metavar="NAME", default=argparse.SUPPRESS,
                        help="ledger repository remote for this command (default: origin)")
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                register(child)


@contextmanager
def session(arguments: argparse.Namespace) -> Iterator[None]:
    """Keep process-local overrides scoped; subprocess workers inherit the chosen remote."""
    previous = {key: os.environ.get(key) for key in ("QEW_REMOTE", "QEW_LEDGER_REMOTE")}
    try:
        for attribute, key in (("remote", "QEW_REMOTE"), ("ledger_remote", "QEW_LEDGER_REMOTE")):
            value = getattr(arguments, attribute, None)
            if value is not None:
                os.environ[key] = value
        selected()
        selected(ledger=True)
        yield
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
