"""Qew v1 command line."""

import argparse
import importlib
import json
import os
import sys
from typing import List, Optional, Sequence

from qew import __version__, editor, replication, cmd_draft, cmd_flow, cmd_items, ledger_check, ledger_config
from qew.errors import QewError


EPILOG = """Commands by workflow:
Read
  qew show        one item with derived edges and evidence
  qew list        items, filtered
  qew ready       approved, unclaimed, unblocked work
  qew blocked     open work with unmet needs
  qew graph       dependency edges
  qew board       the offline kanban: seven status columns
  qew check       validate the ledger
  qew inbox       one person's waiting drafts and assigned work
Work
  qew create      file an item (a draft unless a human's quote releases it)
  qew set         change header fields
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
  qew mirror      add | remove | list mirror targets
  qew forge       migrate the primary forge to another target
  qew project     ensure the board on each target
  qew status      replication state: uncommitted, unpushed, pending per target, divergences
  qew import      read a forge project into the ledger
  qew restore     recreate a forge project from the ledger alone
  qew accept      apply a forge-side change the ledger had not made
  qew reject      keep the ledger value; the next push restores the forge
Setup
  qew init        create the config and the ledger
  qew ledger      fork the ledger into a new identity
  qew hook        install | remove agent hooks; the events agents call

Help for one command: qew COMMAND --help. Finishing work is `qew complete`, never a forge-side close."""

# Command modules that own their own subcommands. Each exposes
# `register(commands)` (add its subparsers) and `run(arguments) -> bool` (True when handled).
# A module that does not exist yet is skipped; any other import failure is loud.
_MODULES = ("cmd_read", "cmd_hook", "cmd_sync", "cmd_project", "cmd_pr", "cmd_land", "cmd_status", "cmd_divergence", "cmd_forgeio", "cmd_inbox")
_MODULES += ("cmd_board",)
_MODULES += ("cmd_mirror", "cmd_ledger", "cmd_forge")


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
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create .qew/config.toml and the ledger")
    init.add_argument("--ledger", help="path to the ledger checkout, relative to the repo root")

    create = commands.add_parser("create", help="file an item (a draft unless --quote releases it)")
    create.add_argument("--kind", choices=["epic", "issue", "task"], default="issue", help="item kind")
    create.add_argument("--title", help="short imperative title")
    create.add_argument("--parent", help="parent item")
    create.add_argument("--needs", nargs="*", default=[], help="items this one is blocked by")
    create.add_argument("--label", action="append", default=[], help="label, repeatable")
    create.add_argument("--priority", type=int, default=2, help="lower is sooner")
    create.add_argument("--body", help="Markdown body")
    create.add_argument("--body-file", help="read the body from a file, or - for stdin")
    create.add_argument("--edit", action="store_true", help="write the body in $VISUAL or $EDITOR")
    create.add_argument("--quote", help="a human's words, \"Name: 'their words'\"; releases the item")
    create.add_argument("--from-file", help="JSON object or array of items, or - for stdin")
    create.add_argument("--import", dest="import_events", action="store_true", help="with --from-file: keep each item's own events (imported history)")

    set_ = commands.add_parser("set", help="change an item's header fields")
    set_.add_argument("items", nargs="+", help="item ids or aliases")
    set_.add_argument("--title", help="new title")
    set_.add_argument("--parent", help="new parent")
    set_.add_argument("--needs", nargs="*", help="replace the needs list")
    set_.add_argument("--tracks", help="ledger id of the tracking issue item (empty clears it)")
    set_.add_argument("--pr", help="ledger id of the pull request item (empty clears it)")
    set_.add_argument("--labels", nargs="*", help="replace the labels")
    set_.add_argument("--priority", type=int, help="new priority")
    set_.add_argument("--body", help="replace the body")
    set_.add_argument("--edit", action="store_true", help="rewrite the body in $VISUAL or $EDITOR")
    set_.add_argument("--closed", action="store_true", help="allow editing completed or closed work")

    for name, text in (("start", "claim approved, unblocked work"), ("release", "give up a claim"),
                       ("complete", "finish claimed work once the gate passes"), ("reopen", "reopen completed or closed work")):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("item", help="item id or alias")
    evidence = commands.add_parser("evidence", help="record the code changes that deliver an item")
    evidence.add_argument("item", help="item id or alias")
    evidence.add_argument("--change", action="append", default=[], help="change id (jj) or commit (git), repeatable; default is the current one")
    evidence.add_argument("--vcs", choices=["jj", "git"], help="override the detected VCS")
    close = commands.add_parser("close", help="close work that will not be completed")
    close.add_argument("item", help="item id or alias")
    close.add_argument("--reason", required=True, help="not_planned, duplicate or superseded")
    check = commands.add_parser("check", help="validate the ledger: schema, cycles, dangling needs, illegal state")
    check.add_argument("--json", action="store_true", help="machine-readable report: valid, cycles, contradictions, text_findings")

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


def _specs(arguments: argparse.Namespace) -> List[dict]:
    if arguments.from_file:
        text = sys.stdin.read() if arguments.from_file == "-" else _read(arguments.from_file)
        try:
            value = json.loads(text)
        except ValueError as error:
            raise QewError(f"--from-file is not valid JSON: {error}", 2) from None
        specs = value if isinstance(value, list) else [value]
        if not all(isinstance(spec, dict) for spec in specs):
            raise QewError("--from-file holds an object or an array of objects", 2)
        return specs
    if not arguments.title:
        raise QewError("--title is required", 2)
    body = arguments.body
    sources = [bool(arguments.body is not None), bool(arguments.body_file), arguments.edit]
    if sum(sources) > 1:
        raise QewError("use only one of --body, --body-file and --edit", 2)
    if arguments.body_file:
        body = sys.stdin.read() if arguments.body_file == "-" else editor.read_body_file(arguments.body_file)
    if arguments.edit:
        body = editor.edit_body()
    return [
        {
            "kind": arguments.kind,
            "title": arguments.title,
            "parent": arguments.parent,
            "needs": arguments.needs,
            "labels": arguments.label,
            "priority": arguments.priority,
            "body": body or "",
        }
    ]


def _read(path: str) -> str:
    if os.path.islink(path):
        raise QewError(f"{path} is a symlink; name the real file", 2)
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeError) as error:
        raise QewError(f"cannot read {path}: {error}", 2) from None


def _run(arguments: argparse.Namespace) -> None:
    command = arguments.command
    for plugin in _plugins():
        if plugin.run(arguments):
            return
    if command == "init":
        print(cmd_items.init(arguments.ledger))
        return
    config = ledger_config.load()
    if command == "create":
        print("\n".join(cmd_items.create(config, _specs(arguments), arguments.quote, arguments.import_events)))
    elif command == "set":
        changes = {}
        for key in ("title", "parent", "priority", "tracks", "pr"):
            value = getattr(arguments, key)
            if value is not None:
                changes[key] = str(value)
        for key in ("needs", "labels"):
            value = getattr(arguments, key)
            if value is not None:
                changes[key] = " ".join(value)
        if arguments.edit and arguments.body is not None:
            raise QewError("use only one of --body and --edit", 2)
        body = editor.edit_body() if arguments.edit else arguments.body
        print("\n".join(cmd_items.set_fields(config, arguments.items, changes, body, arguments.closed)))
    elif command in ("start", "release", "complete", "reopen"):
        print(getattr(cmd_flow, command)(config, arguments.item))
    elif command == "evidence":
        print(cmd_flow.evidence(config, arguments.item, arguments.change, arguments.vcs))
    elif command == "close":
        print(cmd_flow.close(config, arguments.item, arguments.reason))
    elif command == "check":
        ledger = cmd_items.ledger_for(config)
        if arguments.json:
            report = ledger_check.analyse(ledger)
            print(json.dumps(report.as_json()))
            if report.problems:
                raise QewError(f"{len(report.problems)} problem(s)", 1)
            return
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
    try:
        with replication.session(arguments):
            _run(arguments)
    except QewError as error:
        print(f"qew: {error}", file=sys.stderr)
        return error.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
