"""`qew migrate-ids --to base35-time` (new ids, old ones kept as aliases) and `qew migrate-layout` (declare schema 2)."""

import argparse
import re

from qew import id_migration, ledger_check, ledger_config
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.ledger_store import _write, actor


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    migrate = commands.add_parser("migrate-ids", help="rewrite letter, counter and imported ids to base35-time ids; old ids stay as aliases")
    migrate.add_argument("--to", required=True, choices=["base35-time"], help="the id mode to migrate to (only base35-time)")
    migrate.add_argument("--dry-run", action="store_true", help="list the items that would be migrated and write nothing")
    layout = commands.add_parser("migrate-layout", help="declare the attachment layout (schema_version 2); rewrites no item, event or mirror")
    layout.add_argument("--dry-run", action="store_true", help="report what would change and write nothing")


_VERSION_LINE = re.compile(r"^(schema_version\s*=\s*)\d+", re.M)


def migrate_layout(config: ledger_config.Config, dry_run: bool) -> str:
    """Move the ledger from schema_version 1 to 2: check it, wait out other logins' claims, change one line."""
    ledger = ledger_for(config)
    current = ledger_config.ledger_schema(ledger.path)
    if current >= ledger_config.CURRENT_SCHEMA:
        return f"already schema_version {current}: nothing to migrate"
    problems = ledger_check.check(ledger)
    if problems:
        raise QewError("qew check must be green before the migration:\n" + "\n".join(problems), 1)
    id_migration.refuse_foreign_claims(ledger.snapshot(), actor(), "the layout is")
    if dry_run:
        return f"would change schema_version {current} -> {ledger_config.CURRENT_SCHEMA} in {ledger_config.LEDGER_FILE}; no item, event or mirror is rewritten"
    path = ledger.path / ledger_config.LEDGER_FILE
    text = path.read_text(encoding="utf-8")
    declared = f"schema_version = {ledger_config.CURRENT_SCHEMA}"
    fixed = _VERSION_LINE.sub(lambda m: m.group(1) + str(ledger_config.CURRENT_SCHEMA), text, count=1) if _VERSION_LINE.search(text) else f"{declared}\n{text}"
    _write(path, fixed, exclusive=False)
    problems = ledger_check.check(ledger)
    if problems:
        raise QewError("qew check failed after the migration:\n" + "\n".join(problems), 1)
    return f"schema_version {current} -> {ledger_config.CURRENT_SCHEMA}; no item, event or mirror was rewritten"


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command == "migrate-layout":
        print(migrate_layout(ledger_config.load(), arguments.dry_run))
        return True
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
