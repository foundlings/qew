"""Qew v1 command line."""

import argparse
import importlib
import json
import sys
from typing import List, Optional, Sequence

from qew import cmd_draft, cmd_flow, cmd_items, ledger_check, ledger_config
from qew.errors import QewError


# Command modules that own their own subcommands. Each exposes
# `register(commands)` (add its subparsers) and `run(arguments) -> bool` (True when handled).
# A module that does not exist yet is skipped; any other import failure is loud.
_MODULES = ("cmd_hook", "cmd_sync", "cmd_project", "cmd_pr")


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
    parser = argparse.ArgumentParser(prog="qew", description="Offline-first work ledger mirrored to forges.")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create .qew/config.toml and the ledger")
    init.add_argument("--prefix", help="item id prefix, upper-case letters or digits")
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
    create.add_argument("--quote", help="a human's words, \"Name: 'their words'\"; releases the item")
    create.add_argument("--from-file", help="JSON object or array of items, or - for stdin")

    set_ = commands.add_parser("set", help="change an item's header fields")
    set_.add_argument("item", help="item id or alias")
    set_.add_argument("--title", help="new title")
    set_.add_argument("--parent", help="new parent")
    set_.add_argument("--needs", nargs="*", help="replace the needs list")
    set_.add_argument("--labels", nargs="*", help="replace the labels")
    set_.add_argument("--priority", type=int, help="new priority")
    set_.add_argument("--body", help="replace the body")

    show = commands.add_parser("show", help="show one item with derived edges and evidence")
    show.add_argument("item", help="item id or alias")
    show.add_argument("--json", action="store_true", help="machine-readable output")

    list_ = commands.add_parser("list", help="list items")
    list_.add_argument("--kind", choices=["epic", "issue", "task"], help="only this kind")
    list_.add_argument("--state", choices=["draft", "approved", "claimed", "completed", "closed"], help="only this state")
    list_.add_argument("--label", help="only items carrying this label")
    list_.add_argument("--json", action="store_true", help="machine-readable output")

    ready = commands.add_parser("ready", help="approved, unclaimed work with every need completed")
    ready.add_argument("--json", action="store_true", help="machine-readable output")
    blocked = commands.add_parser("blocked", help="open work with unmet needs")
    blocked.add_argument("--json", action="store_true", help="machine-readable output")
    graph = commands.add_parser("graph", help="print dependency edges")
    graph.add_argument("--json", action="store_true", help="machine-readable output")

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
    commands.add_parser("check", help="validate the ledger: schema, cycles, dangling needs, illegal state")

    draft = commands.add_parser("draft", help="drafts and their release by a human")
    draft_commands = draft.add_subparsers(dest="draft_command", required=True)
    draft_list = draft_commands.add_parser("list", help="list drafts")
    draft_list.add_argument("--json", action="store_true", help="machine-readable output")
    approve = draft_commands.add_parser("approve", help="release drafts with a human's quoted words")
    approve.add_argument("items", nargs="+", help="items to release")
    approve.add_argument("--tree", action="store_true", help="release every draft below the items too")
    approve.add_argument("--quote", required=True, help="a human's words, \"Name: 'their words'\"")
    revert = draft_commands.add_parser("revert", help="return approved, unclaimed work to draft")
    revert.add_argument("items", nargs="+", help="items to revert")
    revert.add_argument("--reason", required=True, help="why the release is withdrawn")
    revert.add_argument("--quote", required=True, help="a human's words, \"Name: 'their words'\"")
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
    if arguments.body_file:
        body = sys.stdin.read() if arguments.body_file == "-" else _read(arguments.body_file)
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
        print(cmd_items.init(arguments.prefix, arguments.ledger))
        return
    config = ledger_config.load()
    if command == "create":
        print("\n".join(cmd_items.create(config, _specs(arguments), arguments.quote)))
    elif command == "set":
        changes = {}
        for key in ("title", "parent", "priority"):
            value = getattr(arguments, key)
            if value is not None:
                changes[key] = str(value)
        for key in ("needs", "labels"):
            value = getattr(arguments, key)
            if value is not None:
                changes[key] = " ".join(value)
        print("\n".join(cmd_items.set_fields(config, arguments.item, changes, arguments.body)))
    elif command == "show":
        cmd_items.show(config, arguments.item, arguments.json)
    elif command == "list":
        cmd_items.list_items(config, arguments.kind, arguments.state, arguments.label, arguments.json)
    elif command == "ready":
        cmd_items.ready(config, arguments.json)
    elif command == "blocked":
        cmd_items.blocked(config, arguments.json)
    elif command == "graph":
        cmd_items.graph(config, arguments.json)
    elif command in ("start", "release", "complete", "reopen"):
        print(getattr(cmd_flow, command)(config, arguments.item))
    elif command == "evidence":
        print(cmd_flow.evidence(config, arguments.item, arguments.change, arguments.vcs))
    elif command == "close":
        print(cmd_flow.close(config, arguments.item, arguments.reason))
    elif command == "check":
        ledger = cmd_items.ledger_for(config)
        problems = ledger_check.check(ledger)
        if problems:
            print("\n".join(problems))
            raise QewError(f"{len(problems)} problem(s)", 1)
        print(f"ok {len(ledger.item_ids())} items")
    elif command == "draft":
        if arguments.draft_command == "list":
            cmd_draft.list_drafts(config, arguments.json)
        elif arguments.draft_command == "approve":
            print("\n".join(cmd_draft.approve(config, arguments.items, arguments.tree, arguments.quote)))
        else:
            print("\n".join(cmd_draft.revert(config, arguments.items, arguments.reason, arguments.quote)))


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _run(arguments)
    except QewError as error:
        print(f"qew: {error}", file=sys.stderr)
        return error.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
