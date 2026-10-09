"""`qew block` and `qew unblock`: incremental, idempotent edits of one item's `needs`."""

import argparse
import json

from qew import ledger_config
from qew.cmd_items import ledger_for, set_fields
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_event import State


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    block = commands.add_parser("block", help="make an item wait for another (adds one need)")
    block.add_argument("item", help="item id or alias")
    block.add_argument("--on", required=True, help="the item it must wait for")
    block.add_argument("--closed", action="store_true", help="allow editing completed or closed work")
    block.add_argument("--json", action="store_true", help="machine-readable output")
    unblock = commands.add_parser("unblock", help="stop an item waiting for another (removes one need)")
    unblock.add_argument("item", help="item id or alias")
    unblock.add_argument("--from", dest="blocker", required=True, help="the item it no longer waits for")
    unblock.add_argument("--closed", action="store_true", help="allow editing completed or closed work")
    unblock.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command not in ("block", "unblock"):
        return False
    config = ledger_config.load()
    if arguments.command == "block":
        print(edit(config, arguments.item, arguments.on, True, arguments.closed, arguments.json))
    else:
        print(edit(config, arguments.item, arguments.blocker, False, arguments.closed, arguments.json))
    return True


def edit(config: Config, reference: str, other: str, add: bool, allow_closed: bool, as_json: bool) -> str:
    ledger = ledger_for(config)
    ids = ledger.item_ids()
    item_id = ledger.resolve(reference, ids)
    blocker = ledger.resolve(other, ids)
    state = ledger.snapshot().folds[item_id].state
    if state in (State.COMPLETED, State.CLOSED) and not allow_closed:
        raise QewError(f"{item_id} is {state.value}; pass --closed to edit finished work", 2)
    needs = list(ledger.snapshot().items[item_id].needs)  # the header list with every edge event applied
    wanted = ([*needs, blocker] if blocker not in needs else needs) if add else [n for n in needs if n != blocker]
    changed = wanted != needs
    if changed:
        set_fields(config, [item_id], {"needs": " ".join(wanted)}, None, allow_closed)
        if list(ledger.snapshot().items[item_id].needs) != wanted:
            raise QewError(f"{item_id}: the needs list did not read back as written", 3)
    if as_json:
        return json.dumps({"item": item_id, "needs": wanted, "changed": changed})
    if not changed:
        return f"{item_id} unchanged"
    return f"{item_id} blocked on {blocker}" if add else f"{item_id} no longer blocked on {blocker}"
