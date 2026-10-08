"""Graph-wide invariant and trust-boundary diagnostics."""

from typing import Any, Dict, List, Set, Tuple

from qew.errors import QewError
from qew.graph import Graph, decode_graph
from qew.model import normalize_labels, text_findings


def graph_report(payload: Dict[str, Any]) -> Dict[str, Any]:
    graph = decode_graph(payload)
    issues = _issues(payload)
    contradictions = []
    findings = []
    for issue in sorted(issues, key=lambda value: _integer(value, "number", "issue")):
        number = _integer(issue, "number", "issue")
        labels = sorted(
            _text(label, "name", "label")
            for label in _nodes(_mapping(issue, "labels", f"issue #{number}"), "labels")
        )
        normalized = normalize_labels(labels)
        if len(normalized.priorities) > 1:
            contradictions.append(
                {
                    "issue": number,
                    "kind": "multiple_priorities",
                    "labels": sorted(priority.value for priority in normalized.priorities),
                }
            )
        if len(normalized.work_types) > 1:
            contradictions.append(
                {
                    "issue": number,
                    "kind": "multiple_types",
                    "labels": sorted(work_type.value for work_type in normalized.work_types),
                }
            )
        if normalized.epic and normalized.in_progress:
            contradictions.append(
                {
                    "issue": number,
                    "kind": "in_progress_epic",
                    "labels": ["epic", "in-progress"],
                }
            )
        if normalized.approved:
            issue_findings = text_findings(_text(issue, "title", f"issue #{number}"))
            if issue_findings:
                findings.append({"issue": number, **issue_findings})
    cycles = find_cycles(graph)
    return {
        "contradictions": contradictions,
        "cycles": cycles,
        "schema_version": 1,
        "text_findings": findings,
        "valid": not contradictions and not cycles,
    }


def find_cycles(graph: Graph) -> List[List[int]]:
    cycles: Set[Tuple[int, ...]] = set()
    done: Set[int] = set()
    for root in sorted(graph):
        if root in done:
            continue
        path = [root]
        positions = {root: 0}
        stack = [iter(sorted(graph.get(root, frozenset())))]
        while stack:
            blocker = next(stack[-1], None)
            if blocker is None:
                stack.pop()
                finished = path.pop()
                del positions[finished]
                done.add(finished)
            elif blocker in positions:
                cycles.add(_canonical_cycle(path[positions[blocker] :] + [blocker]))
            elif blocker not in done:
                positions[blocker] = len(path)
                path.append(blocker)
                stack.append(iter(sorted(graph.get(blocker, frozenset()))))
    return [list(cycle) for cycle in sorted(cycles)]


def _canonical_cycle(cycle: List[int]) -> Tuple[int, ...]:
    body = cycle[:-1]
    smallest = min(range(len(body)), key=body.__getitem__)
    rotated = body[smallest:] + body[:smallest]
    return tuple(rotated + [rotated[0]])


def _issues(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = _mapping(payload, "data", "response")
    repository = _mapping(data, "repository", "response data")
    return _nodes(_mapping(repository, "issues", "repository"), "issues")


def _mapping(value: Dict[str, Any], key: str, context: str) -> Dict[str, Any]:
    child = value.get(key)
    if not isinstance(child, dict):
        raise QewError(f"indeterminate: missing {key} in {context}", 4)
    return child


def _nodes(connection: Dict[str, Any], context: str) -> List[Dict[str, Any]]:
    nodes = connection.get("nodes")
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        raise QewError(f"indeterminate: invalid nodes in {context}", 4)
    return nodes


def _integer(value: Dict[str, Any], key: str, context: str) -> int:
    child = value.get(key)
    if not isinstance(child, int) or isinstance(child, bool):
        raise QewError(f"indeterminate: invalid {key} in {context}", 4)
    return child


def _text(value: Dict[str, Any], key: str, context: str) -> str:
    child = value.get(key)
    if not isinstance(child, str):
        raise QewError(f"indeterminate: invalid {key} in {context}", 4)
    return child
