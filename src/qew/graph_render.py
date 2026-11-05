"""Render the item graph as text, Graphviz dot or Mermaid. Pure functions over a snapshot.

Titles are untrusted data: every label is escaped for its syntax so a title cannot add nodes,
edges or directives. Order is by id (shorter first, then lexical), identical on every run.
"""

from collections.abc import Callable

from qew import untrusted_text, graph_scope
from qew.ledger_ids import sort_key
from qew.ledger_store import Snapshot

FORMATS = ("text", "dot", "mermaid")
type Edge = graph_scope.Edge  # canonical source, relation, target


def edges(snapshot: Snapshot, view: graph_scope.Selection | None = None) -> list[Edge]:
    """Dependency edges first, then parent edges, each sorted."""
    if view is not None:
        return list(view.edges)
    ordered = sorted(snapshot.items.values(), key=lambda item: sort_key(item.id))
    needs: list[Edge] = [(item.id, "needs", need) for item in ordered for need in sorted(item.needs, key=sort_key)]
    parents: list[Edge] = [(item.id, "parent", item.parent) for item in ordered if item.parent]
    return needs + parents


def _title(title: str) -> str:
    """The title as is, except that one hiding characters a reader cannot see is withheld."""
    return untrusted_text.show(title).text if untrusted_text.findings(title) else title


def _nodes(snapshot: Snapshot, found: list[Edge], view: graph_scope.Selection | None = None) -> list[tuple[str, str]]:
    """(id, label) for every item, then every referenced id that no item file backs."""
    visible = set(view.nodes) if view else set(snapshot.items)
    nodes = [(i, f"{i}: {_title(snapshot.items[i].title)}") for i in sorted(snapshot.items, key=sort_key) if i in visible]
    missing = {t for _, _, t in found if t not in snapshot.items} | ({n for n in view.nodes if n not in snapshot.items} if view else set())
    return nodes + [(i, i) for i in sorted(missing, key=sort_key)]


def text(snapshot: Snapshot, view: graph_scope.Selection | None = None) -> str:
    incident = {node for a, _, b in view.edges for node in (a, b)} if view else set()
    isolated = [n for n in view.nodes if n not in incident] if view else []
    return "".join(f"{n}\n" for n in isolated) + "".join(f"{a} -> {b}\n" if kind == "needs" else f"{a} {kind} {b}\n" for a, kind, b in edges(snapshot, view))


def _dot_escape(value: str) -> str:
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    return value.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")


def dot(snapshot: Snapshot, view: graph_scope.Selection | None = None) -> str:
    found = edges(snapshot, view)
    lines = ["digraph qew {"]
    for node, label in _nodes(snapshot, found, view):
        dashed = ", style=dashed" if node not in snapshot.items else ""
        lines.append(f'  "{_dot_escape(node)}" [label="{_dot_escape(label)}"{dashed}];')
    for a, kind, b in found:
        style = "" if kind == "needs" else f' [style=dashed, label="{kind}"]'
        lines.append(f'  "{_dot_escape(a)}" -> "{_dot_escape(b)}"{style};')
    return "\n".join(lines + ["}"]) + "\n"


def _mermaid_escape(value: str) -> str:
    for raw, entity in (("#", "#35;"), ("&", "#amp;"), ('"', "#quot;"), ("<", "#lt;"), (">", "#gt;")):
        value = value.replace(raw, entity)
    return value.replace("\r\n", "<br/>").replace("\r", "<br/>").replace("\n", "<br/>")


def mermaid(snapshot: Snapshot, view: graph_scope.Selection | None = None) -> str:
    found = edges(snapshot, view)
    nodes = _nodes(snapshot, found, view)
    names: dict[str, str] = {node: f"n{index}" for index, (node, _) in enumerate(nodes)}
    lines = ["graph LR"]
    lines += [f'  {names[node]}["{_mermaid_escape(label)}"]' for node, label in nodes]
    for a, kind, b in found:
        arrow = "-->" if kind == "needs" else f"-.->|{kind}|"
        lines.append(f"  {names[a]} {arrow} {names[b]}")
    return "\n".join(lines) + "\n"


RENDERERS: dict[str, Callable[[Snapshot, graph_scope.Selection | None], str]] = {"text": text, "dot": dot, "mermaid": mermaid}
