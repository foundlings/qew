"""`qew board`: the kanban of the whole ledger, offline."""

from qew import board, ledger_config
from qew.cmd_items import ledger_for


def register(commands) -> None:
    parser = commands.add_parser("board", help="show the offline kanban board (seven status columns)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--compact", action="store_true", help="fixed-format lines for agents")
    sub = parser.add_subparsers(dest="board_command", required=False)
    sub.add_parser("columns", help="only the column names and card counts")


def run(arguments) -> bool:
    if arguments.command != "board":
        return False
    snapshot = ledger_for(ledger_config.load()).snapshot()
    groups = board.by_column(board.ordered(board.cards(snapshot)))
    if arguments.board_command == "columns":
        print(board.render_columns(groups))
    elif arguments.json:
        print(board.render_json("default", groups))
    elif arguments.compact:
        print(board.render_compact(groups))
    else:
        print(board.render_text(groups, color=board.use_color()))
    return True
