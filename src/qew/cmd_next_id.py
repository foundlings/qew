"""`qew next-id`: the next free id or ids of an item kind, from the kind's id_mode."""

import argparse

from qew import cmd_items, ledger_config
from qew.errors import QewError
from qew.ledger_ids import next_id


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = commands.add_parser("next-id", help="print the next free id (or ids) for an item kind; nothing is written")
    command.add_argument("--kind", choices=["epic", "issue", "task"], default="issue", help="the item kind (default issue)")
    command.add_argument("--count", type=int, default=1, help="how many consecutive ids to print (default 1)")
    command.add_argument("--after", help="letters kinds: treat this letter id as taken too (for ids reserved outside the ledger, such as a phase document)")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "next-id":
        return False
    if not 1 <= arguments.count <= 10_000:
        raise QewError("--count must be between 1 and 10000", 2)
    ledger = cmd_items.ledger_for(ledger_config.load())
    taken = {*ledger.item_ids(), *ledger.aliases().by_alias}
    mode = ledger.id_modes[arguments.kind]
    if arguments.after:
        if mode != "letters":
            raise QewError(f"--after applies to letters kinds; {arguments.kind} ids are {mode}", 2)
        taken.add(arguments.after)
    found: list[str] = []
    for _ in range(arguments.count):
        found.append(next_id(mode, taken))
        taken.add(found[-1])
    print("\n".join(found))
    return True
