"""`qew repair-closes`: restore items an older pull closed as not planned although the forge closed them as completed."""

import argparse

from qew import close_repair, cmd_sync, evidence_backfill, evidence_resolve, forge_registry, ledger_config
from qew.cmd_items import ledger_for


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    repair = commands.add_parser("repair-closes", help="restore items a pull wrongly closed as not planned; the forge's first close decides")
    repair.add_argument("--dry-run", action="store_true", help="list the items that would be restored and write nothing")
    rebuild = commands.add_parser("repair-evidence", help="rebuild the missing evidence header of completed items from their events")
    rebuild.add_argument("--dry-run", action="store_true", help="say how many headers would be rebuilt and write nothing")


def repair_evidence(config: ledger_config.Config, dry_run: bool) -> str:
    """Fill the header of completed items that lack one: ids their events cite that did not visibly fail to land, else the
    typed absence; the same rule as the schema 4 migration."""
    ledger = ledger_for(config)
    filled = evidence_backfill.plan(ledger.snapshot(), evidence_resolve.resolver(config))
    if not filled:
        return "nothing to repair"
    if not dry_run:
        evidence_backfill.apply(ledger, filled)
    return f"{'would repair' if dry_run else 'repaired'} {len(filled)} evidence header(s): {evidence_backfill.summary(filled)}"


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command == "repair-evidence":
        print(repair_evidence(ledger_config.load(), arguments.dry_run))
        return True
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
