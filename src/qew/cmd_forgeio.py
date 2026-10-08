"""`qew import` and `qew restore`: move a project from a forge into the ledger, or back."""

import time
from typing import Optional

from qew import forge_registry, ledger_config
from qew.cmd_items import ledger_for
from qew.cmd_sync import choose, mirrors_with_boards, render
from qew.errors import QewError
from qew.ledger_config import Config, Mirror
from qew.ledger_import import import_target
from qew.restore import restore_target
from qew.sync import Target


def register(commands) -> None:
    imported = commands.add_parser("import", help="read a forge project into the ledger (items, mirrors, drafts)")
    _target_flags(imported, "--from")
    imported.add_argument("--history", action="store_true", help="import closed foreign issues as closed work, not drafts")
    restore = commands.add_parser("restore", help="recreate a forge project from the ledger alone")
    _target_flags(restore, "--to")


def _target_flags(parser, name: str) -> None:
    parser.add_argument(name, dest="target", help="configured mirror target (slug or unique part of it)")
    parser.add_argument("--forge", help="ad hoc forge: github, forgejo, gitea or gitlab (with --repo)")
    parser.add_argument("--repo", help="ad hoc repository URL (with --forge)")
    parser.add_argument("--api", help="API root override for the ad hoc target")


def run(arguments) -> bool:
    if arguments.command not in ("import", "restore"):
        return False
    config = ledger_config.load()
    target = build_target(config, arguments)
    ledger = ledger_for(config)
    if arguments.command == "import":
        report = import_target(ledger, target, history=arguments.history)
        print(f"{target.slug}: imported new={report.new_items} applied={report.applied} comments={report.comments}"
              f" divergences={report.divergences}")
        failures = report.failures
    else:
        report = restore_target(ledger, target, config)
        print("\n".join(render(report)))
        failures = report.failures
    if failures:
        raise QewError("; ".join(failures[:3]), 1)
    return True


def build_target(config: Config, arguments) -> Target:
    ledger = ledger_for(config)
    if arguments.forge or arguments.repo:
        if not (arguments.forge and arguments.repo):
            raise QewError("an ad hoc target needs both --forge and --repo", 2)
        options = {"api": arguments.api} if arguments.api else {}
        mirror = Mirror(forge_registry.canonical(arguments.forge), arguments.repo.rstrip("/"), None, "", options)
    else:
        mirrors = choose(mirrors_with_boards(ledger, config), arguments.target)
        if len(mirrors) != 1:
            raise QewError("name one target with --from/--to, or pass --forge and --repo", 2)
        mirror = mirrors[0]
    return Target(mirror, ledger_config.mirror_slug(mirror), forge_registry.build(mirror))
