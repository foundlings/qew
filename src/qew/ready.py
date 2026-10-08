"""Derive actionable work from a complete GitHub issue graph."""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from qew.errors import QewError
from qew.model import Priority, WorkType, normalize_labels


@dataclass(frozen=True)
class ReadyItem:
    number: int
    priority: Priority
    title: str
    work_type: WorkType
    url: str


@dataclass(frozen=True)
class WorkEvaluation:
    status: str
    item: Optional[ReadyItem]


def derive_ready(payload: Dict[str, Any]) -> List[ReadyItem]:
    issues = _issues_connection(payload)
    if _has_next_page(issues, "issues"):
        raise QewError("indeterminate: GitHub truncated the issue list", 4)

    ready = []
    for issue in _nodes(issues, "issues"):
        evaluation = evaluate_work(issue)
        if evaluation.status == "ready":
            if evaluation.item is None:
                raise AssertionError("ready work must carry a ready item")
            ready.append(evaluation.item)
    return sorted(ready, key=lambda item: (item.priority.rank, item.number))


def evaluate_work(issue: Dict[str, Any]) -> WorkEvaluation:
    """Classify one issue after validating every field that affects readiness."""
    number = _integer(issue, "number", "issue")
    state = issue.get("state")
    if state is not None:
        if state not in ("OPEN", "CLOSED"):
            raise QewError(f"indeterminate: invalid state for #{number}", 4)
        if state == "CLOSED":
            return WorkEvaluation("closed", None)
    blockers = _mapping(issue, "blockedBy", f"issue #{number}")
    if _has_next_page(blockers, f"blockers for #{number}"):
        raise QewError(f"indeterminate: GitHub truncated blockers for #{number}", 4)
    assignees = _mapping(issue, "assignees", f"issue #{number}")
    claimed = _integer(assignees, "totalCount", f"assignees for #{number}") != 0
    blocked = any(
        _text(blocker, "state", "blocker") != "CLOSED"
        for blocker in _nodes(blockers, "blockers")
    )
    labels = _mapping(issue, "labels", f"issue #{number}")
    if _has_next_page(labels, f"labels for #{number}"):
        raise QewError(f"indeterminate: GitHub truncated labels for #{number}", 4)
    normalized = normalize_labels(
        [_text(label, "name", "label") for label in _nodes(labels, "labels")]
    )
    # A draft is held back before every other rule, even if it also carries
    # qew:approved (a human may add that label in the GitHub UI).
    if normalized.draft:
        return WorkEvaluation("draft", None)
    eligible = normalized.workable and normalized.triaged
    # A gate a human must open outranks claim and blockers: stuck work that is
    # then gated must show that a decision is owed, not that it is held.
    if eligible and normalized.needs_approval:
        return WorkEvaluation("needs_approval", None)
    if claimed:
        return WorkEvaluation("claimed", None)
    if blocked:
        return WorkEvaluation("blocked", None)
    if not eligible:
        return WorkEvaluation("untriaged", None)
    if normalized.priority is None or normalized.work_type is None:
        raise AssertionError("triaged work must have normalized vocabulary")
    return WorkEvaluation(
        "ready",
        ReadyItem(
            number=number,
            priority=normalized.priority,
            title=_text(issue, "title", f"issue #{number}"),
            work_type=normalized.work_type,
            url=_text(issue, "url", f"issue #{number}"),
        )
    )


def ready_json(items: List[ReadyItem], total: int) -> Dict[str, Any]:
    rendered = []
    for item in items:
        value = asdict(item)
        value["priority"] = item.priority.value
        value["type"] = item.work_type.value
        del value["work_type"]
        rendered.append(value)
    return {
        "items": rendered,
        "schema_version": 3,
        "total": total,
        "truncated": len(items) < total,
    }


def _issues_connection(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = _mapping(payload, "data", "response")
    repository = _mapping(data, "repository", "response data")
    return _mapping(repository, "issues", "repository")


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


def _has_next_page(connection: Dict[str, Any], context: str) -> bool:
    page_info = _mapping(connection, "pageInfo", context)
    value = page_info.get("hasNextPage")
    if not isinstance(value, bool):
        raise QewError(f"indeterminate: invalid pagination in {context}", 4)
    return value


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
