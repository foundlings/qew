"""Dependency graph validation and native edge mutations."""

from typing import Any, Dict, FrozenSet, Set

from qew.errors import QewError
from qew.github import add_blocked_by, fetch_dependency_graph, remove_blocked_by


Graph = Dict[int, FrozenSet[int]]


def block(repository: str, issue: int, blocker: int, *, allow_closed: bool = False) -> None:
    _positive(issue)
    _positive(blocker)
    if issue == blocker:
        raise QewError("a work item cannot block itself", 2)
    before_payload = fetch_dependency_graph(repository)
    before = decode_graph(before_payload)
    _require_nodes(before, issue, blocker)
    _require_open_target(before_payload, issue, allow_closed)
    if blocker in before[issue]:
        return
    if _reaches(before, blocker, issue):
        raise QewError(f"adding #{blocker} as a blocker of #{issue} creates a cycle", 2)
    add_blocked_by(repository, issue, blocker)
    after = decode_graph(fetch_dependency_graph(repository))
    if blocker not in after.get(issue, frozenset()):
        raise QewError(f"dependency verification failed for #{issue} and #{blocker}", 3)


def unblock(repository: str, issue: int, blocker: int, *, allow_closed: bool = False) -> None:
    _positive(issue)
    _positive(blocker)
    before_payload = fetch_dependency_graph(repository)
    before = decode_graph(before_payload)
    _require_nodes(before, issue, blocker)
    _require_open_target(before_payload, issue, allow_closed)
    if blocker not in before[issue]:
        return
    remove_blocked_by(repository, issue, blocker)
    after = decode_graph(fetch_dependency_graph(repository))
    if blocker in after.get(issue, frozenset()):
        raise QewError(f"dependency removal verification failed for #{issue} and #{blocker}", 3)


def decode_graph(payload: Dict[str, Any]) -> Graph:
    data = _mapping(payload, "data", "response")
    repository = _mapping(data, "repository", "response data")
    issues = _mapping(repository, "issues", "repository")
    _complete(issues, "issues")
    nodes = _nodes(issues, "issues")
    graph = {}
    for issue in nodes:
        number = _integer(issue, "number", "issue")
        blocked_by = _mapping(issue, "blockedBy", f"issue #{number}")
        _complete(blocked_by, f"blockers for #{number}")
        graph[number] = frozenset(
            _integer(blocker, "number", "blocker")
            for blocker in _nodes(blocked_by, f"blockers for #{number}")
        )
    return graph


def _reaches(graph: Graph, start: int, destination: int) -> bool:
    pending = [start]
    visited: Set[int] = set()
    while pending:
        current = pending.pop()
        if current == destination:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.get(current, frozenset()))
    return False


def _require_nodes(graph: Graph, *numbers: int) -> None:
    missing = [number for number in numbers if number not in graph]
    if missing:
        rendered = ", ".join(f"#{number}" for number in missing)
        raise QewError(f"unknown work item: {rendered}", 2)


def _positive(number: int) -> None:
    if number <= 0:
        raise QewError("issue numbers must be positive", 2)


def _require_open_target(payload: Dict[str, Any], issue: int, allow_closed: bool) -> None:
    if allow_closed:
        return
    data = _mapping(payload, "data", "response")
    repository = _mapping(data, "repository", "response data")
    issues = _mapping(repository, "issues", "repository")
    for node in _nodes(issues, "issues"):
        if _integer(node, "number", "issue") != issue:
            continue
        state = node.get("state")
        if state == "CLOSED":
            raise QewError(f"#{issue} is closed; pass --closed to edit it", 2)
        if state != "OPEN":
            raise QewError(f"indeterminate: invalid state for #{issue}", 4)
        return
    raise QewError(f"unknown work item: #{issue}", 2)


def _mapping(value: Dict[str, Any], key: str, context: str) -> Dict[str, Any]:
    child = value.get(key)
    if not isinstance(child, dict):
        raise QewError(f"indeterminate: missing {key} in {context}", 4)
    return child


def _nodes(connection: Dict[str, Any], context: str):
    nodes = connection.get("nodes")
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        raise QewError(f"indeterminate: invalid nodes in {context}", 4)
    return nodes


def _complete(connection: Dict[str, Any], context: str) -> None:
    page_info = _mapping(connection, "pageInfo", context)
    if page_info.get("hasNextPage") is not False:
        raise QewError(f"indeterminate: GitHub truncated {context}", 4)


def _integer(value: Dict[str, Any], key: str, context: str) -> int:
    child = value.get(key)
    if not isinstance(child, int) or isinstance(child, bool):
        raise QewError(f"indeterminate: invalid {key} in {context}", 4)
    return child
