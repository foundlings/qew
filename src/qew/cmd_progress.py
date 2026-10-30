"""`qew progress ROOT...`: how far along an epic is, as a tree of percentages from one ledger read.

A leaf is an item with no children. A node's percent is its completed leaves over its leaves less those closed as not
planned; a blocked, gated or claimed leaf is not done.
"""

import argparse
from dataclasses import dataclass

from qew import board, ledger_config, ledger_graph, readfmt, untrusted_text
from qew.cmd_read import snapshot_for
from qew.jsontypes import JsonObject
from qew.ledger_store import Snapshot

BAR_WIDTH = 10
COUNTED = ("completed", "closed", "claimed", "ready", "blocked", "plan", "needs_decision")


@dataclass
class Node:
    id: str
    title: str
    raw_title: str
    kind: str
    work_state: str
    counts: dict[str, int]
    children: list[Node]
    stage: int | None = None  # a claimed leaf's progress; the totals still count it as not done

    @property
    def leaves(self) -> int:
        return sum(self.counts.values())

    @property
    def done(self) -> int:
        return self.counts["completed"]

    @property
    def total(self) -> int:
        return self.leaves - self.counts["closed"]

    @property
    def active(self) -> int:
        return self.counts["claimed"]

    @property
    def active_percent(self) -> int:
        return self.active * 100 // self.total if self.total else 0

    @property
    def percent(self) -> int | None:
        return self.done * 100 // self.total if self.total else None


def positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"`{value}` is not a whole number") from None
    if number < 1:
        raise argparse.ArgumentTypeError("a depth is one or more")
    return number


def register(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    progress = commands.add_parser("progress", help="percent done of an item's whole tree, from the leaves, in one read")
    progress.add_argument("root", nargs="+", help="item ids or aliases whose trees to total")
    progress.add_argument("--depth", type=positive, metavar="N", help="show the tree only N levels below each root (totals still cover every leaf)")
    progress.add_argument("--epics-only", action="store_true", help="omit leaves: only items that have children get a row")
    progress.add_argument("--tree", action="store_true", help="a terminal view: box-drawing guides, numbers in a fixed left column, titles cut to the terminal width")
    progress.add_argument("--color", nargs="?", const="always", default="never", choices=("always", "never"), help="with --tree: paint rows (green done, yellow under way, dim blocked); off unless asked, whatever the terminal")
    progress.add_argument("--open-only", action="store_true", help="omit finished (100%%) and empty (n/a) rows, keeping the parents of open work")
    progress.add_argument("--json", action="store_true", help="machine-readable output: one nested tree per root")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "progress":
        return False
    snapshot = snapshot_for(ledger_config.load())
    kids = ledger_graph.children(snapshot)
    roots = [snapshot.ledger.resolve(reference, list(snapshot.items)) for reference in arguments.root]
    for root in roots:
        tree = build(snapshot, kids, root, frozenset())
        if arguments.json:
            print(readfmt.dumps(document(tree, arguments.depth, arguments.epics_only, 0)))
        elif arguments.tree:
            print("\n".join(tree_lines(tree, arguments.depth, arguments.epics_only, arguments.open_only, board.terminal_width(), arguments.color == "always")))
        else:
            print("\n".join(lines(tree, arguments.depth, arguments.epics_only, 0, arguments.open_only)))
    return True


def build(snapshot: Snapshot, kids: dict[str, list[str]], item_id: str, path: frozenset[str]) -> Node:
    item = snapshot.items[item_id]
    below = [child for child in kids.get(item_id, []) if child not in path and child != item_id]  # a cycle is reported by `qew check`
    children = [build(snapshot, kids, child, path | {item_id}) for child in below]
    state = ledger_graph.work_state(snapshot, item_id)
    stage = claimed_stage(snapshot, item_id) if state == "claimed" and not children else None
    counts = {name: sum(c.counts[name] for c in children) for name in COUNTED}
    if not children:
        counts[state] += 1
    return Node(item_id, untrusted_text.show(item.title).text, item.title, item.kind.value, state, counts, children, stage)


def claimed_stage(snapshot: Snapshot, item_id: str) -> int:
    """How far a claimed leaf has come: 0 claimed, 25 draft pull request, 50 evidence recorded, 75 pull request ready, 90 merged."""
    stage = 50 if snapshot.folds[item_id].evidence else 0
    for record in snapshot.ledger.load_mirrors(item_id):
        if record.get("kind") != "pr":
            continue
        if record.get("state") == "merged":
            stage = max(stage, 90)
        elif record.get("state") == "open":
            stage = max(stage, 25 if record.get("draft") else 75)
    return stage


def shown(node: Node, depth: int | None, epics_only: bool, level: int, open_only: bool = False) -> list[Node]:
    if depth is not None and level >= depth:
        return []
    return [child for child in node.children if not (epics_only and not child.children) and not (open_only and child.done >= child.total)]


def document(node: Node, depth: int | None, epics_only: bool, level: int) -> JsonObject:
    return {
        "id": node.id, "kind": node.kind, "title": None if untrusted_text.findings(node.raw_title) else node.raw_title, "work_state": node.work_state,
        "counts": node.counts, "leaves": node.leaves, "done": node.done, "total": node.total, "percent": node.percent,
        "stage_percent": node.stage,
        "children": [document(child, depth, epics_only, level + 1) for child in shown(node, depth, epics_only, level)],
    }


def bar(percent: int | None) -> str:
    filled = 0 if percent is None else percent * BAR_WIDTH // 100
    return "[" + "#" * filled + "-" * (BAR_WIDTH - filled) + "]"


def lines(node: Node, depth: int | None, epics_only: bool, level: int, open_only: bool = False) -> list[str]:
    pad = "  " * level
    if not node.children:
        return [f"{pad}{node.id}  {node.title}  {node.work_state}"]
    percent = "n/a" if node.percent is None else f"{node.percent}%"
    rows = [f"{pad}{node.id}  {node.title}  {bar(node.percent)} {percent}  ({node.done}/{node.total})"]
    for child in shown(node, depth, epics_only, level, open_only):
        rows += lines(child, depth, epics_only, level + 1, open_only)
    return rows


COUNT_WIDTH = 7  # done/total is at least this wide; it grows to the widest in the tree, so the guides line up
LABELS_FROM = 90  # a pane this wide gets "done" and "in progress" spelled out beside the two percents
PAINT = {"done": "\x1b[32m", "going": "\x1b[33m", "stuck": "\x1b[2m"}


def counts_text(node: Node) -> str:
    return f"{node.done}/{node.total}" + (f" (+{node.active})" if node.active else "") if node.children else "0/0"


def count_width(node: Node) -> int:
    return max([COUNT_WIDTH, len(counts_text(node)), *(count_width(child) for child in node.children)])


def race(node: Node) -> str:
    """Done `#` and in progress `=` share one bar; a claimed leaf under way always shows at least one cell."""
    done = (node.percent or 0) * BAR_WIDTH // 100
    active = min(node.active_percent * BAR_WIDTH // 100 or (1 if node.active else 0), BAR_WIDTH - done)
    return "[" + "#" * done + "=" * active + "-" * (BAR_WIDTH - done - active) + "]"


def epic_numbers(node: Node, slot: int, labels: bool) -> str:
    percent = "n/a" if node.percent is None else f"{node.percent}%"
    if labels:
        return f"{race(node)} {percent:>4} done  {f'{node.active_percent}%':>3} in progress  {counts_text(node):>{slot}}"
    return f"{race(node)} {percent:>4} {f'+{node.active_percent}%':>4}  {counts_text(node):>{slot}}"


def numbers(node: Node, slot: int, labels: bool) -> str:
    if node.children or node.kind == "epic":
        return epic_numbers(node, slot, labels)
    span = len(f"{'':{BAR_WIDTH + 2}} {'':>4} done  {'':>3} in progress  {'':>{slot}}" if labels else f"{'':{BAR_WIDTH + 2}} {'':>4} {'':>4}  {'':>{slot}}")
    if node.stage is not None:
        return f"{bar(node.stage)} {f'{node.stage}%':>4}  {node.work_state}".ljust(span)
    return node.work_state.ljust(span)


def paint(node: Node) -> str | None:
    if node.work_state in ("blocked", "needs_decision") and not node.children or node.percent is None:
        return PAINT["stuck"]
    if node.percent == 100:
        return PAINT["done"]
    return PAINT["going"] if node.percent or node.counts["claimed"] else None


def tree_lines(node: Node, depth: int | None, epics_only: bool, open_only: bool, width: int, color: bool,
               prefix: str = "", connector: str = "", level: int = 0, slot: int = 0) -> list[str]:
    slot = slot or count_width(node)
    head = f"{numbers(node, slot, width >= LABELS_FROM)}  {prefix}{connector}{node.id}"
    room = width - len(head) - 2
    title = "" if room < 2 else "  " + (node.title if len(node.title) <= room else node.title[: room - 1] + "…")
    line = head + title
    shade = paint(node) if color else None
    rows = [f"{shade}{line}\x1b[0m" if shade else line]
    below = shown(node, depth, epics_only, level, open_only)
    for index, child in enumerate(below):
        last = index == len(below) - 1
        spine = prefix + ("" if not connector else "   " if connector.startswith("└") else "│  ")
        rows += tree_lines(child, depth, epics_only, open_only, width, color, spine, "└─ " if last else "├─ ", level + 1, slot)
    return rows
