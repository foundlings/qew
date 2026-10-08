"""`qew migrate-ids --to time`: give every item a time-mode id, keeping the old id as an alias."""

import argparse

from qew import id_migration, ledger_config
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.ledger_store import actor


def register(commands) -> None:
    migrate = commands.add_parser("migrate-ids", help="rewrite letter, counter and imported ids to time ids; old ids stay as aliases")
    migrate.add_argument("--to", required=True, choices=["time"], help="the id mode to migrate to (only time)")
    migrate.add_argument("--dry-run", action="store_true", help="list the items that would be migrated and write nothing")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "migrate-ids":
        return False
    report = id_migration.migrate(ledger_for(ledger_config.load()), actor(), arguments.dry_run)
    if report.nothing:
        print("nothing to migrate")
        return True
    for old, new in report.renames:
        print(f"would migrate {old}" if arguments.dry_run else f"{old} -> {new}")
    verb = "would update" if arguments.dry_run else "updated"
    print(f"{'would migrate' if arguments.dry_run else 'migrated'} {len(report.renames)} items; {verb} {report.rewritten} files")
    return True
