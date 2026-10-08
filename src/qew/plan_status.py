"""Derive deterministic plan rollups from complete child graphs."""

from typing import Any, Dict, List, Optional

from qew.errors import QewError
from qew.model import normalize_labels
from qew.ready import ReadyItem, evaluate_work


def derive_plan_statuses(
    payload: Dict[str, Any], requested: Optional[int]
) -> List[Dict[str, Any]]:
    issues = _connection(_mapping(_mapping(payload, "data", "response"), "repository", "data"), "issues", "repository")
    plans = []
    for plan in _nodes(issues, "issues"):
        number = _integer(plan, "number", "plan")
        labels = _connection(plan, "labels", f"plan #{number}")
        normalized = normalize_labels(
            [_text(label, "name", "label") for label in _nodes(labels, "labels")]
        )
        if not normalized.epic or len(normalized.priorities) != 1:
            continue
        plans.append(_rollup(plan))
    plans.sort(key=lambda item: item["number"])
    if requested is None:
        return plans
    selected = [plan for plan in plans if plan["number"] == requested]
    if not selected:
        raise QewError(f"#{requested} is not an open triaged plan", 2)
    return selected


def _rollup(plan: Dict[str, Any]) -> Dict[str, Any]:
    number = _integer(plan, "number", "plan")
    state = _text(plan, "state", f"plan #{number}")
    if state != "OPEN":
        raise QewError(f"indeterminate: non-open plan #{number} in open plan query", 4)
    children = _nodes(_connection(plan, "subIssues", f"plan #{number}"), "subIssues")
    counts = {
        "blocked": 0,
        "claimed": 0,
        "closed": 0,
        "draft": 0,
        "needs_approval": 0,
        "ready": 0,
        "total": len(children),
        "untriaged": 0,
    }
    ready: List[ReadyItem] = []
    for child in children:
        evaluation = evaluate_work(child)
        counts[evaluation.status] += 1
        if evaluation.item is not None:
            ready.append(evaluation.item)
    ready.sort(key=lambda item: (item.priority.rank, item.number))
    next_item = None
    if ready:
        item = ready[0]
        next_item = {
            "number": item.number,
            "priority": item.priority.value,
            "reason": "highest-priority ready child",
            "title": item.title,
            "type": item.work_type.value,
            "url": item.url,
        }
    draft = normalize_labels(
        [_text(label, "name", "label") for label in _nodes(_connection(plan, "labels", f"plan #{number}"), "labels")]
    ).draft
    completable = not draft and counts["total"] > 0 and counts["closed"] == counts["total"]
    return {
        "children": counts,
        "completable": completable,
        "draft": draft,
        "complete_command": (
            f"qew plan complete {number} --reason TEXT" if completable else None
        ),
        "next": next_item,
        "number": number,
        "schema_version": 1,
        "state": "open",
        "title": _text(plan, "title", f"plan #{number}"),
        "url": _text(plan, "url", f"plan #{number}"),
    }


def _mapping(value: Dict[str, Any], key: str, context: str) -> Dict[str, Any]:
    child = value.get(key)
    if not isinstance(child, dict):
        raise QewError(f"indeterminate: missing {key} in {context}", 4)
    return child


def _connection(value: Dict[str, Any], key: str, context: str) -> Dict[str, Any]:
    connection = _mapping(value, key, context)
    page_info = _mapping(connection, "pageInfo", key)
    if page_info.get("hasNextPage") is not False:
        raise QewError(f"indeterminate: incomplete pagination in {key}", 4)
    return connection


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
