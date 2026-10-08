"""Create linked pull requests from claimed Qew work."""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable, Dict, Optional

from qew.errors import QewError
from qew.github import (
    create_pr,
    fetch_issue,
    fetch_pull_request_state,
    fetch_show_issue,
    mark_pull_request_ready,
)
from qew.model import WorkType, normalize_labels, text_findings
from qew.pr_owner import require_owner
from qew.read_suite import show_item
from qew.vcs import synced_branch


_CLOSING = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s*"
    r"(https?://\S+|[\w.-]+/[\w.-]+#\d+|#\d+)"
)
_PART_OF = re.compile(r"(?im)^\s*Part of\s+#(\d+)\s*$")
_SECTION = re.compile(r"(?m)^### (Where|Problem|Goal|Fix|Approach|Done when)\s*$")
_PREFIX = {
    WorkType.BUG.value: "fix",
    WorkType.ENHANCEMENT.value: "feat",
    WorkType.TASK.value: "chore",
}


@dataclass(frozen=True)
class PullRequest:
    repository: str
    issue: Optional[int]
    title: Optional[str]
    what: Optional[str]
    why: Optional[str]
    testing: Optional[str]
    body_file: Optional[str]
    base: Optional[str]
    ready: bool
    head: Optional[str] = None


def create(
    request: PullRequest,
    advisory: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    branch = synced_branch(request.head)
    issue = _resolve_issue(branch, request.issue)
    draft_issue = _require_claim(fetch_issue(request.repository, issue), issue)
    if draft_issue and request.ready:
        raise QewError(
            f"a pull request for the draft #{issue} is always a draft; omit --ready", 2
        )
    work = show_item(fetch_show_issue(request.repository, issue))
    if work["number"] != issue or work["state"] != "open":
        raise QewError(f"#{issue} is not open", 2)
    if (not draft_issue and not work["approved"]) or work["type"] not in _PREFIX:
        raise QewError(f"#{issue} is not approved typed work", 2)
    if text_findings(work["title"]):
        raise QewError(f"#{issue} has suspicious title text", 2)
    title = _title(request.title, work["type"], work["title"])
    body = _body(request, work)
    if advisory is not None:
        advisory(body)
    created = create_pr(
        request.repository,
        branch=branch,
        title=title,
        body=body,
        base=request.base,
        draft=not request.ready,
        labels=["qew:draft"] if draft_issue else None,
    )
    return {**created, "draft": not request.ready, "issue": issue}


def _issue_from_branch(branch: str) -> Optional[int]:
    match = re.match(r"^[^/]+/(\d+)(?:[-_/]|$)", branch)
    return None if match is None else int(match.group(1))


def _resolve_issue(branch: str, explicit: Optional[int]) -> int:
    inferred = _issue_from_branch(branch)
    if explicit is None:
        if inferred is None:
            raise QewError("cannot infer issue from branch; pass --for ISSUE", 2)
        return inferred
    # A branch naming no issue carries no claim; only a different number contradicts.
    if inferred is not None and inferred != explicit:
        raise QewError(
            f"branch {branch} is unrelated to #{explicit}: it names #{inferred}", 3
        )
    return explicit


def _require_claim(payload: Dict[str, Any], expected: int) -> bool:
    """Require claimed approved work; a draft issue needs no claim (it cannot have one).

    Returns True for a draft issue, whose pull request is always a draft and carries
    qew:draft until the issue is approved.
    """
    data = _mapping(payload, "data", "response")
    viewer = _text(_mapping(data, "viewer", "response data"), "login", "viewer")
    issue = _mapping(_mapping(data, "repository", "response data"), "issue", "repository")
    number = _integer(issue, "number", "issue")
    if number != expected or _text(issue, "state", f"issue #{number}") != "OPEN":
        raise QewError(f"#{expected} is not open", 2)
    assignees = {
        _text(node, "login", "assignee")
        for node in _complete_nodes(issue, "assignees", number)
    }
    labels = {
        _text(node, "name", "label") for node in _complete_nodes(issue, "labels", number)
    }
    normalized = normalize_labels(list(labels))
    if normalized.draft:
        if normalized.epic or not normalized.triaged:
            raise QewError(f"#{expected} is not typed work", 2)
        return True
    if not normalized.approved or not normalized.triaged or normalized.epic:
        raise QewError(f"#{expected} is not approved typed work", 2)
    if viewer not in assignees or "in-progress" not in labels:
        raise QewError(f"#{expected} is not claimed by {viewer}", 3)
    competing = assignees - {viewer}
    if competing:
        raise QewError(f"claim conflict on #{expected} with {', '.join(sorted(competing))}", 3)
    return False


def _title(override: Optional[str], work_type: str, issue_title: str) -> str:
    if override is not None:
        if not override.strip():
            raise QewError("pull request title must not be empty", 2)
        return override
    return f"{_PREFIX[work_type]}: {issue_title}"


def _body(request: PullRequest, work: Dict[str, Any]) -> str:
    structured = (request.what, request.why, request.testing)
    if request.body_file is not None and any(value is not None for value in structured):
        raise QewError("body-file and structured PR fields are mutually exclusive", 2)
    if request.body_file is not None:
        path = Path(request.body_file)
        if path.is_symlink():
            raise QewError("body-file must not be a symlink", 2)
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise QewError(f"cannot read body-file: {error}", 2) from error
        if not body.strip():
            raise QewError("body-file must not be empty", 2)
    else:
        sections = _sections(work["body"])
        what = _value(request.what, sections.get("Fix") or sections.get("Approach") or f"Implement #{work['number']}.", "what")
        why = _value(request.why, sections.get("Problem") or sections.get("Goal") or f"Complete #{work['number']}.", "why")
        testing = _value(request.testing, "Not run (not provided).", "testing")
        body = f"## What\n\n{what}\n\n## Why\n\n{why}\n\n## Testing\n\n{testing}"
    return _trailers(body, work["number"], work["parent"])


def _sections(body: str) -> Dict[str, str]:
    matches = list(_SECTION.finditer(body))
    sections = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        value = body[match.end() : end].strip()
        if value:
            sections[match.group(1)] = value
    return sections


def _value(override: Optional[str], default: str, name: str) -> str:
    value = default if override is None else override
    if not value.strip():
        raise QewError(f"{name} must not be empty", 2)
    return value


def _trailers(body: str, issue: int, parent: Optional[Dict[str, Any]]) -> str:
    closing = _CLOSING.findall(body)
    if any(reference != f"#{issue}" for reference in closing):
        raise QewError("PR body contains a closing reference to unrelated work", 2)
    if len(closing) > 1:
        raise QewError(f"PR body contains duplicate closing references for #{issue}", 2)
    trailers = []
    if not closing:
        trailers.append(f"Fixes #{issue}")
    if parent is not None:
        parent_number = parent["number"]
        part_of = [int(number) for number in _PART_OF.findall(body)]
        if part_of.count(parent_number) > 1:
            raise QewError(f"PR body contains duplicate Part of #{parent_number} trailers", 2)
        if parent_number not in part_of:
            trailers.append(f"Part of #{parent_number}")
    if trailers:
        body = f"{body.rstrip()}\n\n" + "\n".join(trailers)
    return body


def _complete_nodes(value: Dict[str, Any], key: str, number: int):
    connection = _mapping(value, key, f"issue #{number}")
    page_info = _mapping(connection, "pageInfo", f"{key} for #{number}")
    if page_info.get("hasNextPage") is not False:
        raise QewError(f"indeterminate: GitHub truncated {key} for #{number}", 4)
    nodes = connection.get("nodes")
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        raise QewError(f"indeterminate: invalid {key} for #{number}", 4)
    return nodes


def _mapping(value: Dict[str, Any], key: str, context: str) -> Dict[str, Any]:
    child = value.get(key)
    if not isinstance(child, dict):
        raise QewError(f"indeterminate: missing {key} in {context}", 4)
    return child


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


def mark_ready(repository: str, number: int) -> bool:
    """Mark the caller's draft pull request ready; True when it was a draft.

    A merged or closed pull request, or one another login opened, is refused. An
    already-ready one is unchanged and nothing is written.
    """
    if number <= 0:
        raise QewError("pull request number must be positive", 2)
    before = _state(repository, number)
    state, draft, author, viewer, labels, pull = before
    if state != "OPEN":
        raise QewError(f"PR #{number} is {state.lower()}, not open", 2)
    require_owner(number, author, viewer, pull)
    if "qew:draft" in labels:
        raise QewError(
            f"PR #{number} is labelled qew:draft: approve the issues it closes first "
            "(qew draft approve ISSUE --tree), which removes the label",
            2,
        )
    if not draft:
        return False
    mark_pull_request_ready(repository, number)
    if _state(repository, number)[1]:
        raise QewError(f"indeterminate: PR #{number} is still a draft after marking it ready", 4)
    return True


def _state(repository: str, number: int):
    fetched = fetch_pull_request_state(repository, number)
    pull = fetched["pull_request"]
    state, draft = pull.get("state"), pull.get("isDraft")
    author = pull.get("author")
    login = author.get("login") if isinstance(author, dict) else None
    if (
        pull.get("number") != number
        or state not in ("OPEN", "CLOSED", "MERGED")
        or not isinstance(draft, bool)
        or not isinstance(login, str)
        or not isinstance(fetched["viewer"], str)
    ):
        raise QewError(f"indeterminate: invalid data for PR #{number}", 4)
    names = [node.get("name") for node in pull.get("labels", {}).get("nodes", []) if isinstance(node, dict)]
    return state, draft, login, fetched["viewer"], names, pull
