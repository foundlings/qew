"""Qew v1 command line."""

import argparse
import importlib
import json
import os
import sys
from typing import List, Optional, Sequence

from qew import __version__, outcome, replication, cmd_draft, cmd_items, ledger_check, ledger_config
from qew.errors import QewError


EPILOG = """Commands by workflow:
Read
  qew show        one item with derived edges and evidence
  qew list        items, filtered
  qew ready       approved, unclaimed, unblocked work
  qew blocked     open work with unmet needs
  qew graph       dependency edges
  qew check       validate the ledger
Work
  qew create      file an item (a draft unless a human's quote releases it)
  qew set         change header fields
  qew comment     add an append-only comment
  qew block       make an item wait for another
  qew unblock     remove a wait
  qew start       claim work
  qew release     give a claim up
  qew evidence    record the code changes that deliver an item
  qew complete    finish claimed work once the gate passes
  qew close       close work that will not be completed
  qew reopen      reopen completed or closed work
Drafts (only a human's quoted words release work)
  qew draft       list | approve | revert
Pull requests
  qew pr          open | check | ready | merge
  qew land        complete the items of a merged pull request
Mirrors
  qew sync        push the ledger to every mirror target
  qew project     ensure the board on each target
  qew status      replication state: uncommitted, unpushed, pending per target, divergences
  qew import      read a forge project into the ledger
  qew restore     recreate a forge project from the ledger alone
  qew accept      apply a forge-side change the ledger had not made
  qew reject      keep the ledger value; the next push restores the forge
Setup
  qew init        create the config and the ledger
  qew hook        install | remove agent hooks; the events agents call

Exit codes: 0 ok, 1 invalid ledger or forge failure, 2 refusal or usage, 3 ownership conflict or environment,
4 indeterminate data (nothing written), 5 already yours, 6 claim cap reached.

Help for one command: qew COMMAND --help. Finishing work is `qew complete`, never a forge-side close."""

# Command modules that own their own subcommands. Each exposes
# `register(commands)` (add its subparsers) and `run(arguments) -> bool` (True when handled).
# A module that does not exist yet is skipped; any other import failure is loud.
_MODULES = ("cmd_read", "cmd_hook", "cmd_sync", "cmd_project", "cmd_pr", "cmd_land", "cmd_status", "cmd_divergence", "cmd_forgeio", "cmd_claim", "cmd_finish", "cmd_edit")


def _plugins() -> list:
    found = []
    for name in _MODULES:
        try:
            found.append(importlib.import_module(f"qew.{name}"))
        except ModuleNotFoundError as error:
            if error.name != f"qew.{name}":
                raise
    return found


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qew",
        description="Offline-first work ledger mirrored to forges.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"qew {__version__}", help="print the installed version and exit")
    parser.add_argument("--json", dest="json_first", action="store_true", help="machine-readable result, also accepted after a command")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create .qew/config.toml and the ledger")
    init.add_argument("--ledger", help="path to the ledger checkout, relative to the repo root")

    commands.add_parser("check", help="validate the ledger: schema, cycles, dangling needs, illegal state")

    draft = commands.add_parser("draft", help="drafts and their release by a human")
    draft_commands = draft.add_subparsers(dest="draft_command", required=True)
    draft_list = draft_commands.add_parser("list", help="list drafts")
    draft_list.add_argument("--parent", help="only the direct draft children of this item")
    draft_list.add_argument("--json", action="store_true", help="machine-readable output")
    approve = draft_commands.add_parser("approve", help="release drafts with a human's quoted words")
    approve.add_argument("items", nargs="+", help="items to release")
    approve.add_argument("--tree", action="store_true", help="release every draft below the items too")
    approve.add_argument("--quote", required=True, help="a human's words, \"Name: 'their words'\"")
    approve.add_argument("--json", action="store_true", help="machine-readable output")
    revert = draft_commands.add_parser("revert", help="return approved, unclaimed work to draft")
    revert.add_argument("items", nargs="+", help="items to revert")
    revert.add_argument("--tree", action="store_true", help="return every approved, unclaimed item below the items too")
    revert.add_argument("--reason", required=True, help="why the release is withdrawn")
    revert.add_argument("--quote", required=True, help="a human's words, \"Name: 'their words'\"")
    revert.add_argument("--json", action="store_true", help="machine-readable output")
    for plugin in _plugins():
        plugin.register(commands)
    return parser


def _run(arguments: argparse.Namespace) -> None:
    command = arguments.command
    for plugin in _plugins():
        if plugin.run(arguments):
            return
    if command == "init":
        print(cmd_items.init(arguments.ledger))
        return
    config = ledger_config.load()
    if command == "check":
        ledger = cmd_items.ledger_for(config)
        problems, notes = ledger_check.check_report(ledger)
        if problems:
            print("\n".join(problems))
            raise QewError(f"{len(problems)} problem(s)", 1)
        print("\n".join([f"ok {len(ledger.item_ids())} items", *notes]))
    elif command == "draft":
        if arguments.draft_command == "list":
            cmd_draft.list_drafts(config, arguments.parent, arguments.json)
        elif arguments.draft_command == "approve":
            print("\n".join(cmd_draft.approve(config, arguments.items, arguments.tree, arguments.quote, arguments.json)))
        else:
            print("\n".join(cmd_draft.revert(config, arguments.items, arguments.tree, arguments.reason, arguments.quote, arguments.json)))


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    arguments.json = outcome.wants_json(arguments)
    try:
        with replication.session(arguments):
            _run(arguments)
    except QewError as error:
        print(f"qew: {error}", file=sys.stderr)
        return error.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
