"""`qew sync` and `qew project ensure` (skeleton)."""


def register(commands) -> None:
    sync = commands.add_parser("sync", help="push ledger state to every mirror target")
    sync.add_argument("--target", help="only this target (slug or unique part of it)")
    sync.add_argument("--item", help="only this item")
    sync.add_argument("--dry-run", action="store_true", help="show what would change without calling any forge")
    project = commands.add_parser("project", help="mirror boards")
    sub = project.add_subparsers(dest="project_command", required=True)
    ensure = sub.add_parser("ensure", help="create the Draft / Ready / In progress / In review / Done board on each target")
    ensure.add_argument("--target", help="only this target (slug or unique part of it)")


def run(arguments) -> bool:
    return False
