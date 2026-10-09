"""`qew repair-closes`: restore items an older pull closed as not planned although the forge closed them as completed."""

import argparse

from qew import close_repair, cmd_sync, forge_registry, ledger_config
from qew.cmd_items import ledger_for


def register(commands) -> None:
    repair = commands.add_parser("repair-closes", help="restore items a pull wrongly closed as not planned; the forge's first close decides")
    repair.add_argument("--dry-run", action="store_true", help="list the items that would be restored and write nothing")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "repair-closes":
        return False
    config = ledger_config.load()
    repaired = close_repair.repair(config, ledger_for(config), forge_registry.build, arguments.dry_run)
    if not repaired:
        print("nothing to repair")
        return True
    verb = "would restore" if arguments.dry_run else "restored"
    for found in repaired:
        print(f"{verb} {found.item_id} (#{found.number}) as completed")
    if not arguments.dry_run:
        print(cmd_sync.sync_command(config, None, [found.item_id for found in repaired], False))
    return True
