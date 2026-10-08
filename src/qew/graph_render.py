"""Render the item graph as text, Graphviz dot or Mermaid. Pure functions over a snapshot.

Titles are untrusted data: every label is escaped for its syntax so a title cannot add nodes,
edges or directives. Order is by id (shorter first, then lexical), identical on every run.
"""

from typing import Callable, Dict, List, Tuple

from qew import untrusted_text
from qew.ledger_ids import sort_key
from qew.ledger_store import Snapshot

FORMATS = ("text", "dot", "mermaid")
Edge = Tuple[str, str, str]  # (item, "needs" | "parent", target)


def edges(snapshot: Snapshot) -> List[Edge]:
    """Dependency edges first, then parent edges, each sorted."""
    ordered = sorted(snapshot.items.values(), key=lambda item: sort_key(item.id))
    needs = [(item.id, "needs", need) for item in ordered for need in sorted(item.needs, key=sort_key)]
    parents = [(item.id, "parent", item.parent) for item in ordered if item.parent]
    return needs + parents


def _title(title: str) -> str:
    """The title as is, except that one hiding characters a reader cannot see is withheld."""
    return untrusted_text.show(title).text if untrusted_text.findings(title) else title


def _nodes(snapshot: Snapshot, found: List[Edge]) -> List[Tuple[str, str]]:
    """(id, label) for every item, then every referenced id that no item file backs."""
    nodes = [(i, f"{i}: {_title(snapshot.items[i].title)}") for i in sorted(snapshot.items, key=sort_key)]
    missing = {t for _, _, t in found if t not in snapshot.items}
    return nodes + [(i, i) for i in sorted(missing, key=sort_key)]


def text(snapshot: Snapshot) -> str:
    return "".join(f"{a} -> {b}\n" if kind == "needs" else f"{a} parent {b}\n" for a, kind, b in edges(snapshot))


def _dot_escape(value: str) -> str:
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    return value.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")


def dot(snapshot: Snapshot) -> str:
    found = edges(snapshot)
    lines = ["digraph qew {"]
    for node, label in _nodes(snapshot, found):
        dashed = ", style=dashed" if node not in snapshot.items else ""
        lines.append(f'  "{_dot_escape(node)}" [label="{_dot_escape(label)}"{dashed}];')
    for a, kind, b in found:
        style = "" if kind == "needs" else ' [style=dashed, label="parent"]'
        lines.append(f'  "{_dot_escape(a)}" -> "{_dot_escape(b)}"{style};')
    return "\n".join(lines + ["}"]) + "\n"


def _mermaid_escape(value: str) -> str:
    for raw, entity in (("#", "#35;"), ("&", "#amp;"), ('"', "#quot;"), ("<", "#lt;"), (">", "#gt;")):
        value = value.replace(raw, entity)
    return value.replace("\r\n", "<br/>").replace("\r", "<br/>").replace("\n", "<br/>")


def mermaid(snapshot: Snapshot) -> str:
    found = edges(snapshot)
    nodes = _nodes(snapshot, found)
    names: Dict[str, str] = {node: f"n{index}" for index, (node, _) in enumerate(nodes)}
    lines = ["graph LR"]
    lines += [f'  {names[node]}["{_mermaid_escape(label)}"]' for node, label in nodes]
    for a, kind, b in found:
        arrow = "-->" if kind == "needs" else "-.->|parent|"
        lines.append(f"  {names[a]} {arrow} {names[b]}")
    return "\n".join(lines) + "\n"


RENDERERS: Dict[str, Callable[[Snapshot], str]] = {"text": text, "dot": dot, "mermaid": mermaid}
