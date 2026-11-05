"""`qew progress ROOT...`: how far along an epic is, as a tree of percentages from one ledger read.

A leaf is an item with no children. A node's percent is its completed leaves over its leaves less those closed as not
planned; a blocked, gated or claimed leaf is not done.
"""

import argparse
from dataclasses import dataclass

from qew import graph_scope, graph_view, board, ledger_config, ledger_graph, readfmt, untrusted_text
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
    def units(self) -> int:
        return sum(self.counts.values())

    @property
    def done(self) -> int:
        return self.counts["completed"]

    @property
    def total(self) -> int:
        return self.units - self.counts["closed"]

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
    progress = commands.add_parser("progress", help="percent done from selected roots and relationships, in one read")
    progress.add_argument("root", nargs="+", help="item ids or aliases whose trees to total")
    graph_scope.register(progress)
    progress.add_argument("--depth", type=positive, metavar="N", help="show the tree only N levels below each root (totals still cover every leaf)")
    progress.add_argument("--epics-only", action="store_true", help="omit leaves: only items that have children get a row")
    progress.add_argument("--tree", action="store_true", help="a terminal view: box-drawing guides, numbers in a fixed left column, titles cut to the terminal width")
    progress.add_argument("--color", nargs="?", const="always", default="never", choices=("always", "never"), help="with --tree: paint rows (green done, yellow under way, dim blocked); off unless asked, whatever the terminal")
    progress.add_argument("--open-only", action="store_true", help="omit finished (100%%) and empty (n/a) rows, keeping the parents of open work")
    progress.add_argument("--json", action="store_true", help="machine-readable output: one nested view per root, shared nodes as references")


def run(arguments: argparse.Namespace) -> bool:
    if arguments.command != "progress":
        return False
    snapshot = snapshot_for(ledger_config.load())
    roots = list(dict.fromkeys(snapshot.ledger.resolve(reference, list(snapshot.items)) for reference in arguments.root))
    selected = graph_scope.select(snapshot, roots, arguments.edges or [graph_scope.Walk.CHILDREN], unmet_only=arguments.unmet_only)
    view = selected if arguments.edges else None
    # Build all requested roots before printing: a bad later root/cycle must not leave a partial answer.
    kids = ledger_graph.children(snapshot)
    trees = [build_selected(snapshot, view, root) if view else build(snapshot, kids, root, frozenset()) for root in roots]
    for root, tree in zip(roots, trees, strict=True):
        if arguments.json:
            data = document(tree, arguments.depth, arguments.epics_only, 0, arguments.open_only, view=view)
            if view:
                reachable = set(graph_scope.postorder(view, root))
                data["edges"] = [edge for edge in graph_view.document(snapshot, view)["edges"] if edge["source"] in reachable and edge["target"] in reachable]
                data["traversal"] = [walk.value for walk in arguments.edges]
            print(readfmt.dumps(data))
        elif arguments.tree:
            print("\n".join(tree_lines(tree, arguments.depth, arguments.epics_only, arguments.open_only, board.terminal_width(), arguments.color == "always", view=view)))
        else:
            print("\n".join(lines(tree, arguments.depth, arguments.epics_only, 0, arguments.open_only, view=view)))
    return True


def build_selected(snapshot: Snapshot, view: graph_scope.Selection, root: str) -> Node:
    """A DAG of shared Node objects with unique reachable work-unit counts, computed without recursive expansion."""
    memo: dict[str, Node] = {}
    units: dict[str, set[str]] = {}
    containers = {target for _, relation, target in view.edges if relation == "parent"}
    for item_id in graph_scope.postorder(view, root):
        below = list(dict.fromkeys(child for _, child in view.steps.get(item_id, ())))
        units[item_id] = set().union(*(units[child] for child in below))
        if item_id not in containers:
            units[item_id].add(item_id)
        counts = dict.fromkeys((*COUNTED, "missing"), 0)
        for leaf in units[item_id]:
            state = ledger_graph.work_state(snapshot, leaf) if leaf in snapshot.items else "missing"
            counts[state] += 1
        if item_id not in snapshot.items:
            memo[item_id] = Node(item_id, "[missing item]", "[missing item]", "missing", "missing", counts, [])
            continue
        item = snapshot.items[item_id]
        state = ledger_graph.work_state(snapshot, item_id)
        stage = claimed_stage(snapshot, item_id) if state == "claimed" and not below else None
        memo[item_id] = Node(item_id, untrusted_text.show(item.title).text, item.title, item.kind.value, state,
                             counts, [memo[child] for child in below], stage)
    return memo[root]


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


def document(node: Node, depth: int | None, epics_only: bool, level: int, open_only: bool = False,
             seen: set[str] | None = None, view: graph_scope.Selection | None = None, via: str = "") -> JsonObject:
    seen = set() if seen is None else seen
    if node.id in seen:
        return {"id": node.id, "reference": True, **({"via": via} if via else {})}
    seen.add(node.id)
    return {
        **({"via": via} if via else {}),
        "id": node.id, "kind": node.kind, "title": None if untrusted_text.findings(node.raw_title) else node.raw_title, "work_state": node.work_state,
        "counts": node.counts, ("units" if view else "leaves"): node.units, "done": node.done, "total": node.total, "percent": node.percent,
        "stage_percent": node.stage,
        "children": [document(child, depth, epics_only, level + 1, open_only, seen, view, via_text(view, node.id, child.id)) for child in shown(node, depth, epics_only, level, open_only)],
    }


def via_text(view: graph_scope.Selection | None, source: str, target: str) -> str:
    return ", ".join(walk.value for walk, child in view.steps[source] if child == target) if view else ""


def bar(percent: int | None) -> str:
    filled = 0 if percent is None else percent * BAR_WIDTH // 100
    return "[" + "#" * filled + "-" * (BAR_WIDTH - filled) + "]"


def lines(node: Node, depth: int | None, epics_only: bool, level: int, open_only: bool = False,
          seen: set[str] | None = None, view: graph_scope.Selection | None = None, via: str = "") -> list[str]:
    seen = set() if seen is None else seen
    pad = "  " * level
    identity = node.id + (f" [{via}]" if via else "")
    if node.id in seen:
        return [f"{pad}{identity}  ↩ reference"]
    seen.add(node.id)
    if not node.children:
        return [f"{pad}{identity}  {node.title}  {node.work_state}"]
    percent = "n/a" if node.percent is None else f"{node.percent}%"
    rows = [f"{pad}{identity}  {node.title}  {bar(node.percent)} {percent}  ({node.done}/{node.total})"]
    for child in shown(node, depth, epics_only, level, open_only):
        rows += lines(child, depth, epics_only, level + 1, open_only, seen, view, via_text(view, node.id, child.id))
    return rows


COUNT_WIDTH = 7  # done/total is at least this wide; it grows to the widest in the tree, so the guides line up
LABELS_FROM = 90  # a pane this wide gets "done" and "in progress" spelled out beside the two percents
PAINT = {"done": "\x1b[32m", "going": "\x1b[33m", "stuck": "\x1b[2m"}


def counts_text(node: Node) -> str:
    return f"{node.done}/{node.total}" + (f" (+{node.active})" if node.active else "") if node.children else "0/0"


def count_width(node: Node) -> int:
    seen: set[str] = set()
    stack = [node]
    width = COUNT_WIDTH
    while stack:
        current = stack.pop()
        if current.id not in seen:
            seen.add(current.id)
            width = max(width, len(counts_text(current)))
            stack.extend(current.children)
    return width


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
               prefix: str = "", connector: str = "", level: int = 0, slot: int = 0, seen: set[str] | None = None, view: graph_scope.Selection | None = None, via: str = "") -> list[str]:
    seen = set() if seen is None else seen
    slot = slot or count_width(node)
    identity = node.id + (f" [{via}]" if via else "")
    if node.id in seen:
        return [f"{' ' * slot}  {prefix}{connector}{identity}  ↩ reference"]
    seen.add(node.id)
    head = f"{numbers(node, slot, width >= LABELS_FROM)}  {prefix}{connector}{identity}"
    room = width - len(head) - 2
    title = "" if room < 2 else "  " + (node.title if len(node.title) <= room else node.title[: room - 1] + "…")
    line = head + title
    shade = paint(node) if color else None
    rows = [f"{shade}{line}\x1b[0m" if shade else line]
    below = shown(node, depth, epics_only, level, open_only)
    for index, child in enumerate(below):
        last = index == len(below) - 1
        spine = prefix + ("" if not connector else "   " if connector.startswith("└") else "│  ")
        rows += tree_lines(child, depth, epics_only, open_only, width, color, spine, "└─ " if last else "├─ ", level + 1, slot, seen, view, via_text(view, node.id, child.id))
    return rows
