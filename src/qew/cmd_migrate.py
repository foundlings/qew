"""`qew migrate-ids --to base35-time` (new ids, old ones kept as aliases) and `qew migrate-layout` (move to the latest schema)."""

import argparse
import os
import re
from pathlib import Path

from qew import evidence_backfill, evidence_resolve, id_migration, ledger_check, ledger_config, ledger_layout, ledger_repo, ledger_store
from qew.cmd_items import ledger_for
from qew.errors import QewError
from qew.ledger_aliases import ITEM_FILE
from qew.ledger_store import Ledger, actor


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    migrate = commands.add_parser("migrate-ids", help="rewrite letter, counter and imported ids to base35-time ids; old ids stay as aliases")
    migrate.add_argument("--to", required=True, choices=["base35-time"], help="the id mode to migrate to (only base35-time)")
    migrate.add_argument("--dry-run", action="store_true", help="list the items that would be migrated and write nothing")
    layout = commands.add_parser("migrate-layout", help="move an older ledger straight to the latest layout: each item file into its own directory (items/ID/item.md)")
    layout.add_argument("--dry-run", action="store_true", help="report what would change and write nothing")


_VERSION_LINE = re.compile(r"^(schema_version\s*=\s*)\d+", re.M)


def _old_item_files(ledger: Ledger) -> list[Path]:
    """The item files of an older layout: `items/<ID>.md` directly in the items directory."""
    if not ledger.items_dir.is_dir():
        return []
    return sorted(p for p in ledger.items_dir.iterdir() if p.is_file() and p.suffix == ".md" and not p.name.startswith("."))


SHARDED_ROOTS = ("items", "events", "mirrors", "baselines")


def _flat_items(ledger: Ledger) -> int:
    """How many item directories sit directly in `items/` (the flat layout)."""
    root = ledger.path / "items"
    return sum(1 for path in root.iterdir() if path.is_dir()) if root.is_dir() else 0


def _shard(ledger: Ledger) -> int:
    """Move every item directory of `items`, `events`, `mirrors` and `baselines` under its shard (`ledger_layout`).

    Two steps per root, through a staging directory beside it: an id of one or two characters names a directory that is also
    the shard of other ids (and, on a case-insensitive file system, `AA` and `aa` are one directory), so the root is emptied
    first. Each moved file is journaled, so the commit holds both paths and a failure moves them back."""
    count = 0
    for kind in SHARDED_ROOTS:
        root = ledger.path / kind
        names = sorted(path.name for path in root.iterdir() if path.is_dir()) if root.is_dir() else []
        if not names:
            continue
        staging = ledger.path / f".{kind}.sharding"
        if staging.exists():
            raise QewError(f"{staging} is left over from an interrupted migration; remove it (its directories belong in {kind}/) and run again", 1)
        staging.mkdir()
        placed: list[str] = []
        try:
            for name in names:
                os.replace(root / name, staging / name)
            for name in names:
                target = ledger_layout.home(root, name, True)
                target.parent.mkdir(exist_ok=True)
                os.replace(staging / name, target)
                placed.append(name)
        except BaseException:
            _unshard(root, staging, names, placed)
            raise
        staging.rmdir()
        for name in names:
            home = ledger_layout.home(root, name, True)
            for path in sorted(home.rglob("*")):
                if path.is_file():
                    ledger_store.moved(root / name / path.relative_to(home), path)
        count += len(names)
    return count


def _unshard(root: Path, staging: Path, names: list[str], placed: list[str]) -> None:
    """Undo a half-done `_shard` of one root: back through the staging directory, shards removed, then into place."""
    for name in placed:
        os.replace(ledger_layout.home(root, name, True), staging / name)
    for shard in sorted(path for path in root.iterdir() if path.is_dir()):
        try:
            shard.rmdir()
        except OSError:
            pass  # a directory not ours
    for name in names:
        if (staging / name).exists():
            os.replace(staging / name, root / name)
    staging.rmdir()


def migrate_layout(config: ledger_config.Config, dry_run: bool) -> str:
    """Move the ledger straight to the latest layout: each items/<ID>.md becomes items/<ID>/item.md (same bytes), every
    completed item gets its evidence header from what its events recorded (re-resolved against the target), the item
    directories of items, events, mirrors and baselines move under two-character shards, and schema_version becomes the
    latest, in one commit. The result must pass `qew check` and no other login may hold a claim; any failure rolls the whole
    step back."""
    ledger = ledger_for(config)
    current = ledger_config.ledger_schema(ledger.path)
    latest = ledger_config.CURRENT_SCHEMA
    old = _old_item_files(ledger)
    if current >= latest and not old:
        return f"already schema_version {current}: nothing to migrate"
    for path in old:
        target = ledger.item_path(path.stem)
        if target.exists():
            raise QewError(f"{path.stem}: both items/{path.name} and items/{path.stem}/{ITEM_FILE} exist; settle it by hand, then migrate", 1)
    resolve = evidence_resolve.resolver(config)
    if dry_run:
        text = f"would change schema_version {current} -> {latest}"
        if old:
            text += f": move {len(old)} item files to items/ID/{ITEM_FILE} (contents unchanged)"
        elif current < 4:
            text += f": evidence headers for completed items: {evidence_backfill.summary(evidence_backfill.plan(ledger.snapshot(), resolve))}"
        else:
            text += ": no item file changes"
        if current < ledger_layout.SHARDED_SCHEMA:
            text += f"; move {_flat_items(ledger)} item directories of items, events, mirrors and baselines under two-character shards of the id"
        if (ledger.path / ".git").exists():
            text += "; sets core.untrackedCache in the ledger's git config (git status of a large ledger gets about 4 times faster)"
        return text + f"{'' if resolve or current >= 4 else ' (no code repository here: cited ids are kept unverified)'}; `qew check` and the claim check run on the result and roll everything back on a problem"
    for path in old:
        text = ledger_store.read_text_exact(path)
        ledger_store.write_new(ledger.item_path(path.stem), text)
        ledger_store.discard(path)
    path = ledger.path / ledger_config.LEDGER_FILE
    text = path.read_text(encoding="utf-8")
    declared = f"schema_version = {latest}"
    fixed = _VERSION_LINE.sub(lambda m: m.group(1) + str(latest), text, count=1) if _VERSION_LINE.search(text) else f"{declared}\n{text}"
    ledger_store.write_replacing(path, fixed)
    snapshot = ledger.snapshot()
    if current < 4:  # a step that moves files or fills headers waits for other logins' claims; 4 to 5 changes one line and keeps them all
        id_migration.refuse_foreign_claims(snapshot, actor(), "the layout is")
    filled = evidence_backfill.plan(snapshot, resolve) if current < 4 else []
    evidence_backfill.apply(ledger, filled)
    sharded = _shard(ledger) if current < ledger_layout.SHARDED_SCHEMA else 0
    ledger = ledger_for(config)  # the declared schema is the latest now: check reads the layout it wrote
    problems = ledger_check.check(ledger)
    if problems:
        raise QewError("qew check fails on the migrated ledger; nothing was changed:\n" + "\n".join(problems), 1)
    moved = f"moved {len(old)} item files to items/ID/{ITEM_FILE}; " if old else ""
    moved += f"moved {sharded} item directories under shards; " if sharded else ""
    cached = "core.untrackedCache set; " if ledger_repo.enable_untracked_cache(ledger.path) else ""
    return f"schema_version {current} -> {latest}; {moved}{cached}evidence headers: {evidence_backfill.summary(filled)}"


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
