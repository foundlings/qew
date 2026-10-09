"""Qew v1 command line."""

import argparse
import importlib
import json
import os
import sys
from typing import List, Optional, Sequence

from qew import __version__, body_sections, code_prose, editor, outcome, replication, cmd_draft, cmd_items, ledger_check, ledger_config, plan_apply, plan_file
from qew.errors import QewError


EPILOG = """Commands by workflow:
Read
  qew show        one item with derived edges and evidence
  qew list        items, filtered
  qew search      find approved work by words
  qew ready       approved, unclaimed, unblocked work
  qew prime       bounded session context
  qew blocked     open work with unmet needs
  qew graph       dependency edges
  qew board       the offline kanban: seven status columns
  qew triage      review drafts and items missing fields
  qew plan        status: roll up an epic's children
  qew check       validate the ledger
  qew inbox       one person's waiting drafts and assigned work
  qew export      write items as Markdown files or JSON
Work
  qew create      file an item (a draft unless a human's quote releases it)
  qew set         change header fields
  qew comment     add a comment to an item
  qew start       claim work
  qew release     give a claim up
  qew evidence    record the code changes that deliver an item
  qew complete    finish claimed work once the gate passes
  qew close       close work that will not be completed
  qew comment     discuss any item without changing it
  qew block       make an item wait for another
  qew unblock     stop an item waiting for another
  qew reopen      reopen completed or closed work
Drafts (only a human's quoted words release work)
  qew draft       list | approve | revert
Pull requests
  qew pr          open | check | ready | merge
  qew land        complete the items of a merged pull request
  qew branch      prune: delete merged branches no open pull request uses
Mirrors
  qew sync        push the ledger to every mirror target
  qew mirror      add | remove | list mirror targets
  qew forge       migrate the primary forge to another target
  qew project     ensure the board on each target
  qew status      replication state: uncommitted, unpushed, pending per target, divergences
  qew import      read a forge project, or an exported Markdown tree, into the ledger
  qew restore     recreate a forge project from the ledger alone
  qew accept      apply a forge-side change the ledger had not made
  qew reject      keep the ledger value; the next push restores the forge
  qew conflicts   list every open sync conflict with both sides and how to settle it
  qew resolve     settle a sync conflict by taking one side, with a recorded reason
  qew unlock      remove a stale repository lock a crashed git left, with a human's quoted approval
Setup
  qew init        create the config and the ledger
  qew ledger      fork the ledger into a new identity
  qew migrate-ids rewrite letter, counter and imported ids to time ids (old ids stay as aliases)
  qew hook        install | remove agent hooks; the events agents call

Exit codes: 0 ok, 1 invalid ledger or forge failure, 2 refusal or usage, 3 ownership conflict or environment,
4 indeterminate data (nothing written), 5 already yours, 6 claim cap reached.

Help for one command: qew COMMAND --help. Finishing work is `qew complete`, never a forge-side close."""

# Command modules that own their own subcommands. Each exposes
# `register(commands)` (add its subparsers) and `run(arguments) -> bool` (True when handled).
# A module that does not exist yet is skipped; any other import failure is loud.
_MODULES = ("cmd_read", "cmd_comment", "cmd_triage", "cmd_search", "cmd_prime", "cmd_hook", "cmd_sync", "cmd_project", "cmd_pr", "cmd_land", "cmd_status", "cmd_divergence", "cmd_forgeio", "cmd_inbox")
_MODULES += ("cmd_board",)
_MODULES += ("cmd_transfer",)
_MODULES += ("cmd_mirror", "cmd_ledger", "cmd_forge")
_MODULES += ("cmd_block", "cmd_plan", "cmd_claim", "cmd_finish")
_MODULES += ("cmd_branch", "cmd_migrate", "cmd_conflicts", "cmd_unlock")


def top_level_commands() -> list:
    """Names of the commands `qew --help` lists."""
    action = next(a for a in _parser()._actions if isinstance(a, argparse._SubParsersAction))
    return list(action.choices)


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

    create = commands.add_parser("create", help="file an item (a draft unless --quote releases it)")
    create.add_argument("--kind", choices=["epic", "issue", "task"], default="issue", help="item kind")
    create.add_argument("--title", help="short imperative title")
    create.add_argument("--parent", help="parent item")
    create.add_argument("--key", help="idempotency key (one word): a retry with the same key and content returns the first item")
    create.add_argument("--needs", nargs="*", default=[], help="items this one is blocked by")
    create.add_argument("--label", action="append", default=[], help="label, repeatable")
    create.add_argument("--priority", type=int, default=2, help="lower is sooner")
    create.add_argument("--body", help="Markdown body")
    create.add_argument("--body-file", help="read the body from a file, or - for stdin")
    create.add_argument("--edit", action="store_true", help="write the body in $VISUAL or $EDITOR")
    create.add_argument("--where", help="structured body: where the work happens")
    create.add_argument("--problem", help="structured body: the problem (not with --goal)")
    create.add_argument("--goal", help="structured body: the goal (not with --problem)")
    create.add_argument("--fix", help="structured body: the fix (not with --approach)")
    create.add_argument("--approach", help="structured body: the approach (not with --fix)")
    create.add_argument("--done-when", help="structured body: what done looks like")
    create.add_argument("--discovered-from", metavar="ITEM", help="the item this one was found while working on (not a dependency)")
    create.add_argument("--quote", help="a human's words, \"Name: 'their words'\"; releases the item")
    create.add_argument("--from-file", help="a plan: JSON Lines, one object or an array of items, or - for stdin")
    create.add_argument("--draft", action="store_true", help="with --from-file: file every item as a draft (refuses --quote and approved lines)")
    create.add_argument("--dry-run", action="store_true", help="validate and print the operations without writing")
    create.add_argument("--json", action="store_true", help="machine-readable output")
    create.add_argument("--import", dest="import_events", action="store_true", help="with --from-file: keep each item's own events (imported history)")

    set_ = commands.add_parser("set", help="change an item's header fields")
    set_.add_argument("items", nargs="+", help="item ids or aliases")
    set_.add_argument("--title", help="new title")
    set_.add_argument("--parent", help="new parent")
    set_.add_argument("--no-parent", action="store_true", help="remove the parent")
    set_.add_argument("--needs", nargs="*", help="replace the needs list")
    set_.add_argument("--tracks", help="ledger id of the tracking issue item (empty clears it)")
    set_.add_argument("--pr", help="ledger id of the pull request item (empty clears it)")
    set_.add_argument("--labels", nargs="*", help="replace the labels")
    set_.add_argument("--priority", type=int, help="new priority")
    set_.add_argument("--due", help="due date YYYY-MM-DD (an epic milestone's due date)")
    set_.add_argument("--body", help="replace the body")
    set_.add_argument("--body-file", help="replace the body from a file, or - for stdin")
    set_.add_argument("--kind", choices=["epic", "issue", "task"], help="new kind (children must still nest under it)")
    set_.add_argument("--add-label", action="append", default=[], help="add one label, repeatable")
    set_.add_argument("--remove-label", action="append", default=[], help="remove one label, repeatable")
    set_.add_argument("--json", action="store_true", help="machine-readable output")
    set_.add_argument("--edit", action="store_true", help="rewrite the body in $VISUAL or $EDITOR")
    set_.add_argument("--closed", action="store_true", help="allow editing completed or closed work")

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


def _plan(arguments: argparse.Namespace) -> plan_file.Plan:
    if arguments.key is not None and arguments.from_file:
        raise QewError("--key names one item; a plan file has its own `ref` per entry", 2)
    if arguments.from_file:
        return plan_file.parse(plan_file.read_bytes(arguments.from_file), os.path.basename(arguments.from_file))
    if not arguments.title:
        raise QewError("--title is required", 2)
    if not plan_file.title_is_one_line(arguments.title):
        raise QewError("--title: title must be one line", 2)
    body = arguments.body
    structured = body_sections.compose(arguments)
    sources = [bool(arguments.body is not None), bool(arguments.body_file), arguments.edit, structured is not None]
    if sum(sources) > 1:
        raise QewError("use only one of --body, --body-file, --edit and the structured flags", 2)
    if arguments.body_file:
        body = sys.stdin.read() if arguments.body_file == "-" else editor.read_body_file(arguments.body_file)
    if arguments.edit:
        body = editor.edit_body()
    if structured is not None:
        body = structured
    return plan_file.single(
        {
            "kind": arguments.kind,
            "title": arguments.title,
            "parent": arguments.parent,
            "needs": arguments.needs,
            "labels": arguments.label,
            "priority": arguments.priority,
            "body": body or "",
            **({"discovered_from": arguments.discovered_from} if arguments.discovered_from else {}),
        },
        arguments.key,
    )


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
        plan = _plan(arguments)
        code_prose.warn(str(entry.spec.get("body", "")) for entry in plan.entries)
        lines = plan_apply.apply(
            config, plan, arguments.quote, arguments.draft, arguments.import_events,
            mark=bool(arguments.from_file) or arguments.key is not None, as_json=arguments.json, dry_run=arguments.dry_run,
        )
        print("\n".join(lines))
    elif command == "set":
        changes = {}
        if arguments.no_parent and arguments.parent is not None:
            raise QewError("--no-parent and --parent contradict each other", 2)
        if arguments.no_parent:
            changes["parent"] = ""
        for key in ("title", "parent", "priority", "kind", "tracks", "pr", "due"):
            value = getattr(arguments, key)
            if value is not None:
                changes[key] = str(value)
        for key in ("needs", "labels"):
            value = getattr(arguments, key)
            if value is not None:
                changes[key] = " ".join(value)
        sources = [arguments.body is not None, bool(arguments.body_file), arguments.edit]
        if sum(sources) > 1:
            raise QewError("use only one of --body, --body-file and --edit", 2)
        body = arguments.body
        if arguments.body_file:
            body = sys.stdin.read() if arguments.body_file == "-" else editor.read_body_file(arguments.body_file)
        if arguments.edit:
            body = editor.edit_body()
        code_prose.warn([body or ""])
        changed = cmd_items.set_fields(
            config, arguments.items, changes, body, arguments.closed, arguments.add_label, arguments.remove_label
        )
        if arguments.json:
            print(json.dumps({"changed": [{"item": i, "key": k, "old": o, "new": n} for i, k, o, n in changed]}))
        else:
            print("\n".join(f"{i} {k}: {o} -> {n}" if k != "body" else f"{i} body changed" for i, k, o, n in changed)
                  or f"{' '.join(arguments.items)} unchanged")
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
