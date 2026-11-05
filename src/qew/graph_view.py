"""Terminal and machine views of a selected graph; indentation never discards shared links."""

from typing import TypedDict

from qew import graph_scope, ledger_graph, untrusted_text
from qew.ledger_store import Snapshot


class NodeDocument(TypedDict):
    id: str
    title: str | None
    kind: str
    state: str


class EdgeDocument(TypedDict):
    source: str
    relation: graph_scope.Relation
    target: str


class Document(TypedDict):
    schema_version: int
    roots: list[str]
    nodes: list[NodeDocument]
    edges: list[EdgeDocument]


def document(snapshot: Snapshot, view: graph_scope.Selection) -> Document:
    nodes: list[NodeDocument] = []
    for node in view.nodes:
        if node not in snapshot.items:
            nodes.append({"id": node, "title": None, "kind": "missing", "state": "missing"})
        else:
            item = snapshot.items[node]
            nodes.append({"id": node, "title": None if untrusted_text.findings(item.title) else item.title,
                          "kind": item.kind.value, "state": ledger_graph.work_state(snapshot, node)})
    return {"schema_version": 1, "roots": list(view.roots), "nodes": nodes,
            "edges": [{"source": a, "relation": kind, "target": b} for a, kind, b in view.edges]}


def tree(snapshot: Snapshot, view: graph_scope.Selection, depth: int | None = None) -> str:
    rows: list[str] = []
    seen: set[str] = set()
    for root in view.roots or view.nodes:
        if root in seen and not view.roots:
            continue
        stack = [(root, "", "", "", 0)]
        while stack:
            node, prefix, connector, edge, level = stack.pop()
            label = untrusted_text.show(snapshot.items[node].title).text if node in snapshot.items else "[missing item]"
            state = ledger_graph.work_state(snapshot, node) if node in snapshot.items else "missing"
            repeated = node in seen
            rows.append(f"{prefix}{connector}{edge}{node}  {label}  {state}" + ("  ↩ reference" if repeated else ""))
            if repeated or depth is not None and level >= depth:
                continue
            seen.add(node)
            below = view.steps.get(node, ())
            spine = prefix + ("" if not connector else "   " if connector.startswith("└") else "│  ")
            for index in range(len(below) - 1, -1, -1):
                walk, child = below[index]
                stack.append((child, spine, "└─ " if index == len(below) - 1 else "├─ ", f"{walk.value}: ", level + 1))
    return "\n".join(rows) + ("\n" if rows else "")
