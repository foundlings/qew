"""`qew progress ROOT...`: how far along an epic is, as a tree of percentages from one ledger read.

A leaf is an item with no children. A node's percent is its completed leaves over its leaves less those closed as not
planned; a blocked, gated or claimed leaf is not done.
"""

import argparse
from dataclasses import dataclass

from qew import ledger_config, ledger_graph, readfmt, untrusted_text
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
        else:
            print("\n".join(lines(tree, arguments.depth, arguments.epics_only, 0)))
    return True


def build(snapshot: Snapshot, kids: dict[str, list[str]], item_id: str, path: frozenset[str]) -> Node:
    item = snapshot.items[item_id]
    below = [child for child in kids.get(item_id, []) if child not in path and child != item_id]  # a cycle is reported by `qew check`
    children = [build(snapshot, kids, child, path | {item_id}) for child in below]
    state = ledger_graph.work_state(snapshot, item_id)
    counts = {name: sum(c.counts[name] for c in children) for name in COUNTED}
    if not children:
        counts[state] += 1
    return Node(item_id, untrusted_text.show(item.title).text, item.title, item.kind.value, state, counts, children)


def shown(node: Node, depth: int | None, epics_only: bool, level: int) -> list[Node]:
    if depth is not None and level >= depth:
        return []
    return [child for child in node.children if not (epics_only and not child.children)]


def document(node: Node, depth: int | None, epics_only: bool, level: int) -> JsonObject:
    return {
        "id": node.id, "kind": node.kind, "title": None if untrusted_text.findings(node.raw_title) else node.raw_title, "work_state": node.work_state,
        "counts": node.counts, "leaves": node.leaves, "done": node.done, "total": node.total, "percent": node.percent,
        "children": [document(child, depth, epics_only, level + 1) for child in shown(node, depth, epics_only, level)],
    }


def bar(percent: int | None) -> str:
    filled = 0 if percent is None else percent * BAR_WIDTH // 100
    return "[" + "#" * filled + "-" * (BAR_WIDTH - filled) + "]"


def lines(node: Node, depth: int | None, epics_only: bool, level: int) -> list[str]:
    pad = "  " * level
    if not node.children:
        return [f"{pad}{node.id}  {node.title}  {node.work_state}"]
    percent = "n/a" if node.percent is None else f"{node.percent}%"
    rows = [f"{pad}{node.id}  {node.title}  {bar(node.percent)} {percent}  ({node.done}/{node.total})"]
    for child in shown(node, depth, epics_only, level):
        rows += lines(child, depth, epics_only, level + 1)
    return rows
