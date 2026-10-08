"""`qew board`: the kanban of the whole ledger, offline, plus its named board files."""

from dataclasses import replace

from qew import board, board_config, ledger_config
from qew.cmd_items import ledger_for
from qew.errors import QewError


def _view_options(parser) -> None:
    parser.add_argument("--columns", dest="cfg_columns", help="status columns to show, comma separated, in order")
    parser.add_argument("--sort", dest="cfg_sort", help="default sort: priority, updated, created or title, optionally :desc")
    parser.add_argument("--group-by", dest="cfg_group_by", help="default grouping: column, owner, label, epic or parent")
    parser.add_argument("--filter", dest="cfg_filter", help="default filter terms")


def register(commands) -> None:
    parser = commands.add_parser("board", help="show the offline kanban board (seven status columns)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--compact", action="store_true", help="fixed-format lines for agents")
    parser.add_argument("--name", help="a named board file (default: the board named default, else all columns)")
    parser.add_argument("--sort", help="priority, updated, created or title, optionally :desc")
    parser.add_argument("--group-by", help="column, owner, label, epic or parent")
    parser.add_argument("--filter", help="terms label: kind: state: priority: owner: epic: parent: status: (space = and, comma = or)")
    sub = parser.add_subparsers(dest="board_command", required=False)
    sub.add_parser("columns", help="only the column names and card counts")
    sub.add_parser("list", help="the named board files")
    create = sub.add_parser("create", help="create a named board file")
    create.add_argument("board_name", help="lowercase letters, digits and dashes")
    _view_options(create)
    change = sub.add_parser("set", help="change the defaults of a named board file")
    change.add_argument("board_name", help="the board to change")
    _view_options(change)


def run(arguments) -> bool:
    if arguments.command != "board":
        return False
    ledger = ledger_for(ledger_config.load())
    if arguments.board_command == "list":
        print(_listing(ledger))
    elif arguments.board_command == "create":
        board_config.create(ledger, _from_options(board_config.View(arguments.board_name), arguments))
        print(f"board {arguments.board_name} created")
    elif arguments.board_command == "set":
        board_config.update(ledger, arguments.board_name, _changes(arguments))
        print(f"board {arguments.board_name} updated")
    else:
        _show(ledger, arguments)
    return True


def _changes(arguments) -> dict:
    changes = {}
    if arguments.cfg_columns is not None:
        changes["columns"] = tuple(c.strip() for c in arguments.cfg_columns.split(",") if c.strip())
    for option, key in (("cfg_sort", "sort"), ("cfg_group_by", "group_by"), ("cfg_filter", "filter")):
        if getattr(arguments, option) is not None:
            changes[key] = getattr(arguments, option)
    return changes


def _from_options(view, arguments):
    return replace(view, **_changes(arguments))


def _listing(ledger) -> str:
    lines = []
    for name in board_config.names(ledger):
        view = board_config.load(ledger, name)
        lines.append(f"{name} columns={','.join(view.columns)} sort={view.sort} group={view.group_by} filter={view.filter or '-'}")
    return "\n".join(lines) or "no board files"


def _show(ledger, arguments) -> None:
    name = arguments.name or board_config.DEFAULT_NAME
    view = board_config.load(ledger, name)
    if view is None:
        if arguments.name:
            raise QewError(f"no board named `{name}`", 2)
        view = board_config.View(name)
    snapshot = ledger.snapshot()
    expression = view.filter if arguments.filter is None else arguments.filter
    keep = board.parse_filter(snapshot, expression)
    shown = board.sort_cards([card for card in board.cards(snapshot) if keep(card)], arguments.sort or view.sort)
    groups = board.group_cards(snapshot, shown, arguments.group_by or view.group_by, view.columns)
    if arguments.board_command == "columns":
        print(board.render_columns(groups))
    elif arguments.json:
        print(board.render_json(name, groups))
    elif arguments.compact:
        print(board.render_compact(groups))
    else:
        print(board.render_text(groups, color=board.use_color()))
