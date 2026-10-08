"""`qew project ensure`: create the seven-status board per target."""

from typing import Callable

from qew import forge_registry, ledger_config, project_status, sync_plan, untrusted_text
from qew.cmd_items import ledger_for
from qew.cmd_sync import choose, mirrors_with_boards
from qew.errors import QewError
from qew.forge import COLUMNS, Forge, ForgeError, Unsupported
from qew.ledger_config import Config, Mirror


def register(commands) -> None:
    project = commands.add_parser("project", help="mirror boards")
    sub = project.add_subparsers(dest="project_command", required=True)
    ensure = sub.add_parser("ensure", help="create the seven-status board on each target")
    ensure.add_argument("--target", help="only this target (slug or unique part of it)")
    report = sub.add_parser("status", help="report board drift from the ledger")
    report.add_argument("--target", help="only this target (slug or unique part of it)")
    report.add_argument("--json", action="store_true", help="machine-readable output")


def run(arguments) -> bool:
    if arguments.command != "project":
        return False
    if arguments.project_command == "status":
        text, code = project_status.status(ledger_config.load(), arguments.target, arguments.json)
        print(text)
        if code:
            raise QewError("the board is not in sync" if code == 1 else "no board to compare with", code)
        return True
    print(ensure(ledger_config.load(), arguments.target))
    return True


def ensure(config: Config, wanted, build: Callable[[Mirror], Forge] = forge_registry.build) -> str:
    ledger = ledger_for(config)
    lines = []
    failed = False
    for mirror in choose(mirrors_with_boards(ledger, config), wanted):
        slug = ledger_config.mirror_slug(mirror)
        try:
            board = build(mirror).ensure_board(COLUMNS, sync_plan.managed_filter(mirror.select))
        except Unsupported:
            lines.append(f"{slug}: boards unsupported")
            continue
        except ForgeError as error:
            failed = True
            lines.append(f"{slug}: failed {error.detail}")
            continue
        ledger.write_board(slug, {"id": board.id, "url": board.url, "columns": list(board.columns)})
        lines.append(f"{slug}: board {board.id} {board.url}" + (f" {untrusted_text.show(board.title).text}" if board.title else ""))
        lines.extend(f"{slug}: {untrusted_text.show(note).text}" for note in board.notes)
    if failed:
        print("\n".join(lines))
        raise QewError("project ensure finished with failures", 1)
    return "\n".join(lines) or "no mirror targets configured"
