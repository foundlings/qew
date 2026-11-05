"""Select a directed traversal without changing canonical ledger edges or owning the snapshot."""

import argparse
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from qew import ledger_graph
from qew.errors import QewError
from qew.ledger_event import State
from qew.ledger_ids import sort_key
from qew.ledger_store import Snapshot


type Relation = Literal["needs", "parent", "tracks", "pr", "discovered_from"]
type Edge = tuple[str, Relation, str]


class Walk(StrEnum):
    CHILDREN = "children"
    PARENT = "parent"
    NEEDS = "needs"
    BLOCKS = "blocks"
    TRACKS = "tracks"
    TRACKED_BY = "tracked-by"
    PR = "pr"
    PR_FOR = "pr-for"
    DISCOVERED_FROM = "discovered-from"
    DISCOVERIES = "discoveries"


DIRECTIONS: dict[Walk, tuple[Relation, bool]] = {
    Walk.CHILDREN: ("parent", True), Walk.PARENT: ("parent", False),
    Walk.NEEDS: ("needs", False), Walk.BLOCKS: ("needs", True),
    Walk.TRACKS: ("tracks", False), Walk.TRACKED_BY: ("tracks", True),
    Walk.PR: ("pr", False), Walk.PR_FOR: ("pr", True),
    Walk.DISCOVERED_FROM: ("discovered_from", False), Walk.DISCOVERIES: ("discovered_from", True),
}


@dataclass(frozen=True)
class Selection:
    roots: tuple[str, ...]
    nodes: tuple[str, ...]
    edges: tuple[Edge, ...]
    steps: dict[str, tuple[tuple[Walk, str], ...]]


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--edges", nargs="+", action="extend", type=Walk, choices=list(Walk), metavar="EDGE",
                        help="relationships to follow: children, parent, needs, blocks, tracks, tracked-by, pr, pr-for, discovered-from, discoveries")
    parser.add_argument("--unmet-only", action="store_true", help="follow only unsatisfied needs/blocks edges (completed prerequisites are omitted)")


def nonnegative(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("depth must be a whole number") from None
    if number < 0:
        raise argparse.ArgumentTypeError("depth must be zero or greater")
    return number


def all_edges(snapshot: Snapshot) -> list[Edge]:
    items = sorted(snapshot.items.values(), key=lambda item: sort_key(item.id))
    found: list[Edge] = [(item.id, "needs", target) for item in items for target in sorted(item.needs, key=sort_key)]
    columns: dict[Relation, list[Edge]] = {"parent": [], "tracks": [], "pr": [], "discovered_from": []}
    for item in items:
        references: tuple[tuple[Relation, str], ...] = (("parent", item.parent), ("tracks", item.tracks),
                                                       ("pr", item.pr), ("discovered_from", item.discovered_from))
        for relation, target in references:
            if target:
                columns[relation].append((item.id, relation, target))
    for column in columns.values():
        found.extend(column)
    return found


def select(snapshot: Snapshot, roots: list[str], walks: list[Walk], depth: int | None = None,
           open_only: bool = False, unmet_only: bool = False) -> Selection:
    if unmet_only and not any(w in (Walk.NEEDS, Walk.BLOCKS) for w in walks):
        raise QewError("--unmet-only needs --edges needs or blocks", 2)
    edges = [edge for edge in all_edges(snapshot) if not (unmet_only and edge[1] == "needs"
             and edge[2] in snapshot.folds and snapshot.folds[edge[2]].state == State.COMPLETED)]
    adjacency: dict[str, list[tuple[Walk, str]]] = {}
    chosen: set[Edge] = set()
    for walk in dict.fromkeys(walks):
        relation, reverse = DIRECTIONS[walk]
        for edge in edges:
            source, kind, target = edge
            if kind == relation:
                a, b = (target, source) if reverse else (source, target)
                adjacency.setdefault(a, []).append((walk, b))
                chosen.add(edge)
    seeds = roots or sorted(snapshot.items, key=sort_key)
    distance = dict.fromkeys(seeds, 0)
    queue = deque(seeds)
    while queue:
        source = queue.popleft()
        if depth is not None and distance[source] >= depth:
            continue
        for _, target in adjacency.get(source, []):
            if target not in distance:
                distance[target] = distance[source] + 1
                queue.append(target)
    nodes = set(distance)
    if open_only:
        keep = {n for n in nodes if n not in snapshot.folds or snapshot.folds[n].state not in (State.COMPLETED, State.CLOSED)} | set(roots)
        required: dict[str, list[str]] = {}
        for a, kind, b in chosen:
            if kind == "needs" and a in nodes and b in nodes and (b not in snapshot.folds or snapshot.folds[b].state != State.COMPLETED):
                required.setdefault(a, []).append(b)
        reverse_steps: dict[str, list[str]] = {}
        for a in nodes:
            for _, b in adjacency.get(a, []):
                if b in nodes:
                    reverse_steps.setdefault(b, []).append(a)
        queue = deque(keep)
        while queue:
            node = queue.popleft()
            for related in [*reverse_steps.get(node, []), *required.get(node, [])]:
                if related not in keep:
                    keep.add(related)
                    queue.append(related)
        nodes &= keep
    found = tuple(edge for edge in edges if edge in chosen and edge[0] in nodes and edge[2] in nodes)
    for relation in ("needs", "parent"):
        links: dict[str, list[str]] = {}
        for a, kind, b in found:
            if kind == relation:
                links.setdefault(a, []).append(b)
        cycles = ledger_graph.find_cycles(links)
        if cycles:
            raise QewError(f"{relation} cycle: {' -> '.join(cycles[0])}; run `qew check`", 1)
    steps = {a: tuple(sorted({(walk, b) for walk, b in adjacency.get(a, []) if b in nodes}, key=lambda step: (sort_key(step[1]), step[0]))) for a in nodes}
    return Selection(tuple(roots), tuple(sorted(nodes, key=sort_key)), found, steps)


def postorder(view: Selection, root: str) -> list[str]:
    """Reachable nodes once, children first. Progress cannot assign terminal totals to a cyclic traversal."""
    done: set[str] = set()
    active = {root}
    result: list[str] = []
    stack = [(root, iter(view.steps.get(root, ())))]
    while stack:
        node, rest = stack[-1]
        for _, child in rest:
            if child in active:
                raise QewError(f"selected progress traversal has a cycle through {child}; use `qew graph` to draw the links", 1)
            if child not in done:
                active.add(child)
                stack.append((child, iter(view.steps.get(child, ()))))
                break
        else:
            stack.pop()
            active.remove(node)
            done.add(node)
            result.append(node)
    return result
