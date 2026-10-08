"""Normalize explicitly scoped GitHub reads into stable command output."""

from typing import Any, Dict, List, Optional, Sequence

from qew.errors import QewError
from qew.model import AUTHOR_LABEL_PREFIX, NormalizedLabels, ascii_fold, normalize_labels, text_findings
from qew.ready import evaluate_work


def list_items(
    payload: Dict[str, Any],
    *,
    state: str,
    labels: Sequence[str] = (),
    author: Optional[str] = None,
    assignee: Optional[str] = None,
    epic: Optional[int],
    bodies: bool,
) -> List[Dict[str, Any]]:
    rendered = []
    for issue in _issue_nodes(payload):
        normalized, issue_labels = _normalized(issue)
        if not _routine_work(normalized) and not (normalized.approved and normalized.epic and not normalized.draft):
            continue
        if _state(issue) != state and state != "all":
            continue
        folded_labels = {ascii_fold(name) for name in issue_labels}
        if any(ascii_fold(wanted) not in folded_labels for wanted in labels):
            continue
        if author is not None and ascii_fold(f"{AUTHOR_LABEL_PREFIX}{author}") not in folded_labels:
            continue
        logins = [
            _text(node, "login", "assignee")
            for node in _relation_nodes(issue, "assignees", _integer(issue, "number", "issue"))
        ]
        if assignee is not None and ascii_fold(assignee) not in {ascii_fold(login) for login in logins}:
            continue
        parent = issue.get("parent")
        parent_number = None if parent is None else _integer(_dict(parent, "parent"), "number", "parent")
        if epic is not None and parent_number != epic:
            continue
        if normalized.epic:
            item = _plan_summary(issue, normalized)
        else:
            item = _summary(issue, normalized)
            item["work_state"] = evaluate_work(issue).status
        item["labels"] = sorted(issue_labels)
        item["assignees"] = sorted(logins)
        if bodies:
            item["body"] = _text(issue, "body", f"issue #{item['number']}")
        rendered.append(item)
    order = {"ready": 0, "claimed": 1, "blocked": 2, "needs_approval": 3, "plan": 4, "closed": 5}
    return sorted(rendered, key=lambda item: (order[item["work_state"]], item["number"]))


def draft_items(payload: Dict[str, Any], parent: Optional[int]) -> List[Dict[str, Any]]:
    """Open drafts (plans included), optionally only the direct children of `parent`."""
    rendered = []
    for issue in _issue_nodes(payload):
        normalized, _ = _normalized(issue)
        if not normalized.draft or _state(issue) != "open":
            continue
        value = issue.get("parent")
        parent_number = None if value is None else _integer(_dict(value, "parent"), "number", "parent")
        if parent is not None and parent_number != parent:
            continue
        number = _integer(issue, "number", "issue")
        rendered.append(
            {
                "number": number,
                "parent": parent_number,
                "priority": None if normalized.priority is None else normalized.priority.value,
                "schema_version": 1,
                "title": _text(issue, "title", f"issue #{number}"),
                "type": "epic"
                if normalized.epic
                else None if normalized.work_type is None else normalized.work_type.value,
                "url": _text(issue, "url", f"issue #{number}"),
            }
        )
    return sorted(rendered, key=lambda item: item["number"])


def search_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    search = _mapping(_mapping(payload, "data", "response"), "search", "response data")
    rendered = []
    for issue in _nodes(search, "search"):
        normalized, _ = _normalized(issue)
        if _routine_work(normalized):
            rendered.append(_summary(issue, normalized))
    return rendered


def search_is_capped(payload: Dict[str, Any]) -> bool:
    search = _mapping(_mapping(payload, "data", "response"), "search", "response data")
    page_info = _mapping(search, "pageInfo", "search")
    capped = page_info.get("hasNextPage")
    if not isinstance(capped, bool):
        raise QewError("indeterminate: invalid pagination in search", 4)
    return capped


def triage_items(payload: Dict[str, Any], terms: Optional[str]) -> List[Dict[str, Any]]:
    needle = terms.casefold() if terms is not None else None
    rendered = []
    for issue in _issue_nodes(payload):
        normalized, _ = _normalized(issue)
        missing = []
        if len(normalized.priorities) != 1:
            missing.append("priority")
        if len(normalized.work_types) != 1 and not normalized.epic:
            missing.append("type")
        if not missing:
            continue
        title = _text(issue, "title", "issue")
        body = _text(issue, "body", "issue")
        if needle is not None and needle not in f"{title}\n{body}".casefold():
            continue
        item = {
            "body": body,
            "missing": missing,
            "number": _integer(issue, "number", "issue"),
            "schema_version": 1,
            "state": _state(issue),
            "title": title,
            "url": _text(issue, "url", "issue"),
        }
        findings = _sourced_findings((("title", title, None), ("body", body, None)))
        if findings:
            item["text_findings"] = findings
        rendered.append(item)
    return sorted(rendered, key=lambda item: item["number"])


def blocked_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rendered = []
    for issue in _issue_nodes(payload):
        normalized, _ = _normalized(issue)
        if not _routine_work(normalized):
            continue
        number = _integer(issue, "number", "issue")
        blockers = _relation_nodes(issue, "blockedBy", number)
        open_blockers = [
            _relation(blocker)
            for blocker in blockers
            if _state(blocker) == "open"
        ]
        if not open_blockers:
            continue
        item = _summary(issue, normalized)
        del item["state"]
        item["blocked_by"] = open_blockers
        rendered.append(item)
    return sorted(
        rendered,
        key=lambda item: (_priority_rank(item["priority"]), item["number"]),
    )


def show_item(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = _mapping(payload, "data", "response")
    repository = _mapping(data, "repository", "response data")
    issue = _mapping(repository, "issue", "repository")
    normalized, labels = _normalized(issue)
    number = _integer(issue, "number", "issue")
    parent_value = issue.get("parent")
    parent = None if parent_value is None else _relation(_dict(parent_value, "parent"))
    comments = []
    comments_connection = _mapping(issue, "comments", f"issue #{number}")
    for comment in _nodes(comments_connection, "comments"):
        author_value = comment.get("author")
        author = None
        if author_value is not None:
            author = _text(_dict(author_value, "comment author"), "login", "comment author")
        comments.append(
            {
                "author": author,
                "body": _text(comment, "body", "comment"),
                "created_at": _text(comment, "createdAt", "comment"),
            }
        )
    title = _text(issue, "title", f"issue #{number}")
    body = _text(issue, "body", f"issue #{number}")
    child_nodes = _relation_nodes(issue, "subIssues", number)
    next_item = None
    if normalized.epic:
        ready = []
        for child in child_nodes:
            evaluation = evaluate_work(child)
            if evaluation.item is not None:
                ready.append(evaluation.item)
        ready.sort(key=lambda value: (value.priority.rank, value.number))
        if ready:
            selected = ready[0]
            next_item = {
                "number": selected.number,
                "priority": selected.priority.value,
                "title": selected.title,
                "type": selected.work_type.value,
                "url": selected.url,
            }
    item = {
        "approved": normalized.approved,
        "assignees": [
            _text(assignee, "login", "assignee")
            for assignee in _relation_nodes(issue, "assignees", number)
        ],
        "blocked_by": [
            _relation(item) for item in _relation_nodes(issue, "blockedBy", number)
        ],
        "blocking": [
            _relation(item) for item in _relation_nodes(issue, "blocking", number)
        ],
        "body": body,
        "children": [
            _relation(item) for item in child_nodes
        ],
        "comments": comments,
        "draft": normalized.draft,
        "labels": labels,
        "number": number,
        "next": next_item,
        "parent": parent,
        "priority": None if normalized.priority is None else normalized.priority.value,
        "schema_version": 1,
        "state": _state(issue),
        "title": title,
        "type": None if normalized.work_type is None else normalized.work_type.value,
        "url": _text(issue, "url", f"issue #{number}"),
    }
    fields = [("title", title, None), ("body", body, None)]
    fields.extend(
        ("comment", comment["body"], index)
        for index, comment in enumerate(comments)
    )
    findings = _sourced_findings(fields)
    if findings:
        item["text_findings"] = findings
    return item


def _sourced_findings(fields) -> List[Dict[str, Any]]:
    rendered = []
    for source, text, comment_index in fields:
        findings = text_findings(text)
        if findings:
            item = {"source": source, **findings}
            if comment_index is not None:
                item["comment_index"] = comment_index
            rendered.append(item)
    return rendered


def _summary(issue: Dict[str, Any], normalized: NormalizedLabels) -> Dict[str, Any]:
    if normalized.priority is None or normalized.work_type is None:
        raise AssertionError("routine work must have normalized vocabulary")
    number = _integer(issue, "number", "issue")
    return {
        "number": number,
        "priority": normalized.priority.value,
        "schema_version": 1,
        "state": _state(issue),
        "title": _text(issue, "title", f"issue #{number}"),
        "type": normalized.work_type.value,
        "url": _text(issue, "url", f"issue #{number}"),
    }


def _plan_summary(issue: Dict[str, Any], normalized: NormalizedLabels) -> Dict[str, Any]:
    if normalized.priority is None:
        raise QewError("indeterminate: plan missing priority", 4)
    number = _integer(issue, "number", "plan")
    return {
        "number": number,
        "priority": normalized.priority.value,
        "schema_version": 1,
        "state": _state(issue),
        "title": _text(issue, "title", f"issue #{number}"),
        "type": None,
        "url": _text(issue, "url", f"issue #{number}"),
        "work_state": "plan",
    }


def _routine_work(normalized: NormalizedLabels) -> bool:
    return normalized.workable and normalized.triaged


def _normalized(issue: Dict[str, Any]):
    number = _integer(issue, "number", "issue")
    label_nodes = _relation_nodes(issue, "labels", number)
    labels = [_text(label, "name", "label") for label in label_nodes]
    return normalize_labels(labels), labels


def _issue_nodes(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = _mapping(payload, "data", "response")
    repository = _mapping(data, "repository", "response data")
    return _nodes(_connection(repository, "issues", "repository"), "issues")


def _relation_nodes(issue: Dict[str, Any], key: str, number: int) -> List[Dict[str, Any]]:
    return _nodes(_connection(issue, key, f"issue #{number}"), key)


def _relation(issue: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "number": _integer(issue, "number", "related issue"),
        "state": _state(issue),
        "title": _text(issue, "title", "related issue"),
    }


def _state(issue: Dict[str, Any]) -> str:
    value = _text(issue, "state", "issue")
    if value not in ("OPEN", "CLOSED"):
        raise QewError("indeterminate: invalid issue state", 4)
    return value.lower()


def _priority_rank(priority: str) -> int:
    return int(priority[1:])


def _mapping(value: Dict[str, Any], key: str, context: str) -> Dict[str, Any]:
    return _dict(value.get(key), f"{key} in {context}")


def _connection(value: Dict[str, Any], key: str, context: str) -> Dict[str, Any]:
    connection = _mapping(value, key, context)
    page_info = _mapping(connection, "pageInfo", key)
    has_next = page_info.get("hasNextPage")
    if has_next is not False:
        raise QewError(f"indeterminate: incomplete pagination in {key}", 4)
    return connection


def _nodes(connection: Dict[str, Any], context: str) -> List[Dict[str, Any]]:
    nodes = connection.get("nodes")
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        raise QewError(f"indeterminate: invalid nodes in {context}", 4)
    return nodes


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
