"""`qew branch prune`: delete the forge branches that are already merged and that nothing uses."""

import argparse
from collections.abc import Callable

from qew import branch_cleanup, forge_registry, ledger_config, untrusted_text
from qew.cmd_items import ledger_for
from qew.cmd_sync import choose, mirrors_with_boards
from qew.errors import QewError
from qew.forge import Forge, ForgeError, Unsupported
from qew.ledger_config import Config, Mirror


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    branch = commands.add_parser("branch", help="forge branches")
    sub = branch.add_subparsers(dest="branch_command", required=True)
    prune = sub.add_parser("prune", help="delete merged branches that no open pull request uses")
    prune.add_argument("--target", help="only this target (slug or unique part of it)")
    prune.add_argument("--base", help="the branch a branch must already be merged into; default is the configured target branch")
    prune.add_argument("--dry-run", action="store_true", help="list what would be deleted and delete nothing")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "branch":
        return False
    print_lines = lambda text: print(text, flush=True)
    prune(ledger_config.load(), arguments.target, arguments.base, arguments.dry_run, forge_registry.build, print_lines)
    return True


def prune(
    config: Config, wanted: str | None, base: str | None, dry_run: bool,
    build: Callable[[Mirror], Forge], emit: Callable[[str], None],
) -> None:
    """Prune each chosen target, emitting one line per branch as it is handled; a forge failure stops with exit 1."""
    base = base or config.target
    mirrors = choose(mirrors_with_boards(ledger_for(config), config), wanted)
    if not mirrors:
        emit("no mirror targets configured")
        return
    for mirror in mirrors:
        slug = ledger_config.mirror_slug(mirror)
        if ledger_config.is_read_only(config, mirror):
            emit(f"{slug}: read-only target, skipped")
            continue
        forge = build(mirror)
        try:
            names = branch_cleanup.removable(forge, base, config.target)
            for name in names:
                shown = untrusted_text.show(name).text
                if dry_run:
                    emit(f"{slug}: would delete {shown}")
                    continue
                forge.delete_branch(name)
                emit(f"{slug}: deleted {shown}")
        except Unsupported:
            emit(f"{slug}: branches unsupported")
            continue
        except ForgeError as error:
            raise QewError(f"{slug}: {error.detail}", 1) from None
        emit(f"{slug}: {len(names)} branch(es) {'would be ' if dry_run else ''}deleted, merged into {untrusted_text.show(base).text}")
