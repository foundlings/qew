"""`qew conflicts` and `qew resolve`: list every open sync conflict and settle each one with a recorded reason.

A ledger merge conflict is settled by taking one side whole (`ours` = this checkout, `theirs` = the remote); the
side not kept is recorded in a `conflict_resolved` event. A forge divergence or claim contest is settled by taking
the `forge` value (accept) or keeping the `ledger` value (reject). See docs/specs/commands.md.
"""

import argparse

from qew import cmd_divergence, ledger_config, readfmt
from qew.cmd_items import ledger_for
from qew.ledger_aliases import item_of_path
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_conflicts import LEDGER_REFERENCE, Side, clipped_discard, collect
from qew.ledger_repo import LedgerRepo

MERGE_SIDES = (Side.OURS, Side.THEIRS)
FORGE_SIDES = (Side.FORGE, Side.LEDGER)


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    conflicts = commands.add_parser("conflicts", help="list every open sync conflict with both sides and how to settle it")
    conflicts.add_argument("--json", action="store_true", help="machine-readable output")
    resolve = commands.add_parser("resolve", help="settle a sync conflict by taking one side, with a recorded reason")
    resolve.add_argument("conflict", help="`ledger` for the ledger merge conflict, else a divergence event id or an item with one")
    resolve.add_argument("--take", required=True, choices=[side.value for side in Side],
                         help="ours|theirs for the ledger merge; forge|ledger for a forge divergence or claim contest")
    resolve.add_argument("--reason", required=True, help="why this side; recorded in the ledger")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command == "conflicts":
        config = ledger_config.load()
        found = collect(config, ledger_for(config).snapshot())
        if arguments.json:
            print(readfmt.document({"conflicts": [c.to_dict() for c in found]}))
        else:
            print("\n".join(c.render() for c in found) if found else "no conflicts")
        return True
    if arguments.command == "resolve":
        print(resolve(ledger_config.load(), arguments.conflict, Side(arguments.take), arguments.reason))
        return True
    return False


def resolve(config: Config, reference: str, take: Side, reason: str) -> str:
    if not reason.strip():
        raise QewError("--reason must say why this side was taken", 2)
    if reference == LEDGER_REFERENCE:
        if take not in MERGE_SIDES:
            raise QewError("the ledger merge conflict is settled with --take ours or --take theirs", 2)
        return _resolve_merge(config, take, reason)
    if take not in FORGE_SIDES:
        raise QewError("a forge divergence or claim contest is settled with --take forge or --take ledger", 2)
    if take == Side.FORGE:
        return cmd_divergence.accept(config, reference, note=reason)
    return cmd_divergence.reject(config, reference, note=reason)


def _resolve_merge(config: Config, take: Side, reason: str) -> str:
    repo = LedgerRepo(config.ledger_path, config.replication.branch)
    resolution = repo.resolve_merge(take.value, config.replication.timeout_s)
    ledger = ledger_for(config)
    items = ledger.item_ids()
    recorded: dict[str, dict[str, str | None]] = {}
    for relative, text in resolution.discarded.items():
        owner = item_of_path(relative)
        if owner is not None and owner in items:
            recorded.setdefault(owner, {})[relative] = clipped_discard(text)
    for item_id, discarded in sorted(recorded.items()):
        ledger.append_event(item_id, "conflict_resolved", took=take.value, reason=reason, discarded=discarded)
    return f"resolved the ledger merge: took {take.value} for {len(resolution.discarded)} file(s)"
