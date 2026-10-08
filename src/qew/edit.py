"""Guarded, verified edits to canonical issue fields."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional

from qew.errors import QewError
from qew.github import edit_issue, fetch_mutation_target
from qew.model import APPROVAL_LABEL, Priority, WorkType, normalize_labels, reject_reserved_areas


@dataclass(frozen=True)
class SetRequest:
    repository: str
    issue: int
    priority: Optional[str]
    work_type: Optional[str]
    add_areas: List[str]
    remove_areas: List[str]
    parent: Optional[int]
    no_parent: bool
    title: Optional[str]
    body_file: Optional[str]
    allow_closed: bool
    needs_approval: Optional[bool] = None


@dataclass(frozen=True)
class Target:
    number: int
    title: str
    body: str
    state: str
    labels: FrozenSet[str]
    parent: Optional[int]


def set_issue(request: SetRequest) -> bool:
    body = _validate(request)
    before = _target(fetch_mutation_target(request.repository, request.issue))
    if before.state == "CLOSED" and not request.allow_closed:
        raise QewError(f"#{request.issue} is closed; pass --closed to edit it", 2)
    arguments = _arguments(request, before, body)
    if not arguments:
        return False
    edit_issue(request.repository, request.issue, arguments)
    after = _target(fetch_mutation_target(request.repository, request.issue))
    _verify(request, after, body)
    return True


def _validate(request: SetRequest) -> Optional[str]:
    if request.issue <= 0 or (request.parent is not None and request.parent <= 0):
        raise QewError("issue numbers must be positive", 2)
    if request.title is not None and not request.title.strip():
        raise QewError("title must not be empty", 2)
    areas = [*request.add_areas, *request.remove_areas]
    if any(not area.strip() for area in areas):
        raise QewError("area labels must not be empty", 2)
    reject_reserved_areas(areas)
    overlap = set(request.add_areas) & set(request.remove_areas)
    if overlap:
        raise QewError(f"cannot add and remove the same area: {', '.join(sorted(overlap))}", 2)
    if not any(
        (
            request.priority,
            request.work_type,
            request.add_areas,
            request.remove_areas,
            request.parent,
            request.no_parent,
            request.title,
            request.body_file,
            request.needs_approval is not None,
        )
    ):
        raise QewError("set requires at least one change", 2)
    if request.body_file is None:
        return None
    path = Path(request.body_file)
    if path.is_symlink():
        raise QewError("body-file must not be a symlink", 2)
    try:
        body = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise QewError(f"cannot read body-file: {error}", 2) from error
    if not body.strip():
        raise QewError("body-file must not be empty", 2)
    return body


def _arguments(request: SetRequest, before: Target, body: Optional[str]) -> List[str]:
    arguments = []
    if request.title is not None and request.title != before.title:
        arguments.extend(("--title", request.title))
    if body is not None and body != before.body:
        arguments.extend(("--body", body))
    labels_to_add = set(request.add_areas) - before.labels
    labels_to_remove = set(request.remove_areas) & before.labels
    if request.priority is not None:
        priorities = {value.value for value in Priority} & before.labels
        labels_to_remove.update(priorities - {request.priority})
        if request.priority not in before.labels:
            labels_to_add.add(request.priority)
    if request.work_type is not None:
        work_types = {value.value for value in WorkType} & before.labels
        labels_to_remove.update(work_types - {request.work_type})
        if request.work_type not in before.labels:
            labels_to_add.add(request.work_type)
    if request.needs_approval is True and APPROVAL_LABEL not in before.labels:
        labels_to_add.add(APPROVAL_LABEL)
    if request.needs_approval is False and APPROVAL_LABEL in before.labels:
        labels_to_remove.add(APPROVAL_LABEL)
    if labels_to_add:
        arguments.extend(("--add-label", ",".join(sorted(labels_to_add))))
    if labels_to_remove:
        arguments.extend(("--remove-label", ",".join(sorted(labels_to_remove))))
    if request.parent is not None and request.parent != before.parent:
        arguments.extend(("--parent", str(request.parent)))
    if request.no_parent and before.parent is not None:
        arguments.append("--remove-parent")
    return arguments


def _verify(request: SetRequest, after: Target, body: Optional[str]) -> None:
    failures = []
    if request.title is not None and after.title != request.title:
        failures.append("title")
    if body is not None and after.body != body:
        failures.append("body")
    normalized = normalize_labels(list(after.labels))
    if request.priority is not None and (
        normalized.priority is None
        or normalized.priority.value != request.priority
        or len(normalized.priorities) != 1
    ):
        failures.append("priority")
    if request.work_type is not None and (
        normalized.work_type is None
        or normalized.work_type.value != request.work_type
        or len(normalized.work_types) != 1
    ):
        failures.append("type")
    if any(area not in after.labels for area in request.add_areas):
        failures.append("added areas")
    if any(area in after.labels for area in request.remove_areas):
        failures.append("removed areas")
    if request.needs_approval is not None and (APPROVAL_LABEL in after.labels) != request.needs_approval:
        failures.append("needs-approval")
    if request.parent is not None and after.parent != request.parent:
        failures.append("parent")
    if request.no_parent and after.parent is not None:
        failures.append("parent removal")
    if failures:
        raise QewError(f"issue edit verification failed: {', '.join(failures)}", 3)


def _target(payload: Dict[str, Any]) -> Target:
    data = _mapping(payload, "data", "response")
    repository = _mapping(data, "repository", "response data")
    issue = _mapping(repository, "issue", "repository")
    number = _integer(issue, "number", "issue")
    labels = _connection(issue, "labels", number)
    parent_value = issue.get("parent")
    parent = None
    if parent_value is not None:
        parent = _integer(_dict(parent_value, "parent"), "number", "parent")
    state = _text(issue, "state", f"issue #{number}")
    if state not in ("OPEN", "CLOSED"):
        raise QewError(f"indeterminate: invalid state for #{number}", 4)
    return Target(
        number=number,
        title=_text(issue, "title", f"issue #{number}"),
        body=_text(issue, "body", f"issue #{number}"),
        state=state,
        labels=frozenset(_text(label, "name", "label") for label in labels),
        parent=parent,
    )


def _connection(issue: Dict[str, Any], key: str, number: int) -> List[Dict[str, Any]]:
    connection = _mapping(issue, key, f"issue #{number}")
    page_info = _mapping(connection, "pageInfo", key)
    if page_info.get("hasNextPage") is not False:
        raise QewError(f"indeterminate: incomplete pagination in {key}", 4)
    nodes = connection.get("nodes")
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        raise QewError(f"indeterminate: invalid nodes in {key}", 4)
    return nodes


def _mapping(value: Dict[str, Any], key: str, context: str) -> Dict[str, Any]:
    return _dict(value.get(key), f"{key} in {context}")


def _dict(value: Any, context: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise QewError(f"indeterminate: invalid {context}", 4)
    return value


def _text(value: Dict[str, Any], key: str, context: str) -> str:
    child = value.get(key)
    if not isinstance(child, str):
        raise QewError(f"indeterminate: invalid {key} in {context}", 4)
    return child


def _integer(value: Dict[str, Any], key: str, context: str) -> int:
    child = value.get(key)
    if not isinstance(child, int) or isinstance(child, bool):
        raise QewError(f"indeterminate: invalid {key} in {context}", 4)
    return child
