"""`qew pr link|unlink|links`: read and change a pull request's link declaration in the ledger (see `pr_links`).

The ledger is the source of truth, so these never call a forge: they work offline and with no token.
"""

import argparse
import json
from collections.abc import Iterable

from qew import pr_links, pr_record
from qew.cmd_items import ledger_for
from qew.errors import NotFound, QewError
from qew.ledger_config import Config
from qew.ledger_store import Ledger, Snapshot, actor
from qew.pr_links import Role

SCHEMA_VERSION = 1


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser], target_help: str) -> None:
    link = sub.add_parser("link", help="declare which items a pull request closes, leaves open (remains), or only relates to; idempotent, re-roles an item")
    link.add_argument("number", type=int, help="pull request number")
    link.add_argument("--closes", nargs="+", default=[], metavar="ITEM", help="items this pull request completes when it merges (commas or spaces)")
    link.add_argument("--remains", nargs="+", default=[], metavar="ITEM", help="items it touches or partly delivers; they stay open")
    link.add_argument("--related", nargs="+", default=[], metavar="ITEM", help="items for context only")
    link.add_argument("--no-item", metavar="REASON", help="the pull request relates to no ledger item (the reason is recorded); excludes the item flags")
    link.add_argument("--target", help=target_help)
    link.add_argument("--json", action="store_true", help="machine-readable output")
    unlink = sub.add_parser("unlink", help="remove items from a pull request's declaration; idempotent")
    unlink.add_argument("number", type=int, help="pull request number")
    unlink.add_argument("items", nargs="+", metavar="ITEM", help="items to remove (commas or spaces)")
    unlink.add_argument("--target", help=target_help)
    unlink.add_argument("--json", action="store_true", help="machine-readable output")
    links = sub.add_parser("links", help="show a pull request's link declaration")
    links.add_argument("number", type=int, help="pull request number")
    links.add_argument("--target", help=target_help)
    links.add_argument("--json", action="store_true", help="machine-readable output")


def _split(values: Iterable[str]) -> list[str]:
    return [part for value in values for part in value.replace(",", " ").split()]


def _items(ledger: Ledger, snapshot: Snapshot, values: Iterable[str]) -> list[str]:
    found: list[str] = []
    for reference in _split(values):
        try:
            item_id = ledger.resolve(reference, list(snapshot.items))
        except NotFound as error:
            raise QewError(str(error), 2) from None
        if item_id not in found:
            found.append(item_id)
    return found


def _record(config: Config, number: int, target: str | None) -> tuple[Ledger, Snapshot, pr_record.PrRecord]:
    pr_record.positive_number(number)
    ledger = ledger_for(config)
    snapshot = ledger.snapshot()
    return ledger, snapshot, pr_record.find(ledger, number, target)


def _allowed(snapshot: Snapshot, record: pr_record.PrRecord) -> None:
    states = pr_record.known_states(snapshot, record)
    pr_record.require_owner(snapshot, record, states, actor(), "", merging=False)


def _wanted(ledger: Ledger, snapshot: Snapshot, arguments: argparse.Namespace) -> dict[str, Role]:
    wanted: dict[str, Role] = {}
    for role, values in (("closes", arguments.closes), ("remains", arguments.remains), ("related", arguments.related)):
        for item_id in _items(ledger, snapshot, values):
            if item_id in wanted:
                raise QewError(f"{item_id} is named under two roles ({wanted[item_id]} and {role}); an item has one role per pull request", 2)
            wanted[item_id] = role  # type: ignore[assignment]  # the loop's role names are the Role literals
    return wanted


def describe(declaration: pr_links.Declaration) -> str:
    if declaration.no_item is not None:
        return f"no item: {declaration.no_item}"
    parts = [f"{role} {' '.join(declaration.items(role))}" for role in pr_links.ROLES if declaration.items(role)]
    return "; ".join(parts) or "no declaration"


def link(config: Config, arguments: argparse.Namespace) -> str:
    ledger, snapshot, record = _record(config, arguments.number, arguments.target)
    pr_links.require_schema(ledger)
    _allowed(snapshot, record)
    wanted = _wanted(ledger, snapshot, arguments)
    if arguments.no_item is not None:
        if wanted:
            raise QewError("--no-item excludes --closes, --remains and --related: a pull request relates to no item or to some", 2)
        changed = [] if not pr_links.declare_no_item(ledger, record.mirror_file, arguments.no_item) else ["no-item"]
    else:
        if not wanted:
            raise QewError("name what the pull request does: --closes, --remains, --related or --no-item REASON", 2)
        finished = [i for i, role in wanted.items() if role == "closes" and snapshot.folds[i].state in pr_record.CLOSED_STATES]
        if finished:
            raise QewError(f"{' '.join(finished)} is already finished; a pull request cannot close it (use --related, or --remains to note the delivery)", 2)
        changed = pr_links.link(ledger, record.mirror_file, wanted)
    declaration = pr_links.load(ledger, record.mirror_file)
    if arguments.json:
        return json.dumps({"number": record.number, "changed": changed, **_fields(declaration), "schema_version": SCHEMA_VERSION}, sort_keys=True)
    return f"pr #{record.number} links: {describe(declaration)}" + ("" if changed else " (unchanged)")


def unlink(config: Config, arguments: argparse.Namespace) -> str:
    ledger, snapshot, record = _record(config, arguments.number, arguments.target)
    pr_links.require_schema(ledger)
    _allowed(snapshot, record)
    removed = pr_links.unlink(ledger, record.mirror_file, _items(ledger, snapshot, arguments.items))
    declaration = pr_links.load(ledger, record.mirror_file)
    if arguments.json:
        return json.dumps({"number": record.number, "removed": removed, **_fields(declaration), "schema_version": SCHEMA_VERSION}, sort_keys=True)
    return f"pr #{record.number} links: {describe(declaration)}" + ("" if removed else " (unchanged)")


def _fields(declaration: pr_links.Declaration) -> dict[str, object]:
    return {"closes": declaration.items("closes"), "remains": declaration.items("remains"), "related": declaration.items("related"),
            "no_item": declaration.no_item, "declared": declaration.declared}


def show(config: Config, arguments: argparse.Namespace) -> str:
    ledger, _, record = _record(config, arguments.number, arguments.target)
    declaration = pr_links.load(ledger, record.mirror_file) if ledger.schema >= pr_links.LINKS_SCHEMA else pr_links.Declaration()
    if arguments.json:
        return json.dumps({"number": record.number, **_fields(declaration), "schema_version": SCHEMA_VERSION}, sort_keys=True)
    return f"pr #{record.number}: {describe(declaration)}"


def run(config: Config, arguments: argparse.Namespace) -> str | None:
    """The output of `pr link|unlink|links`, or None when the command is another one."""
    return {"link": link, "unlink": unlink, "links": show}[arguments.pr_command](config, arguments) if arguments.pr_command in ("link", "unlink", "links") else None
