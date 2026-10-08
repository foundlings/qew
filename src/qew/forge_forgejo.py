"""Forgejo and Gitea backend: the shared `/api/v1` REST API, no boards.

Differences from GitHub that shape this file: labels are referenced by id (so names are
resolved, and created when missing, before every write); there is no draft flag, so a draft
pull request is one whose title starts with the `WIP:` prefix; there is no close reason, so
`IssueSpec.close_reason` is accepted and not sent; and combined commit status is its own
endpoint. Every forge answer is untrusted: a shape that is not what the API documents raises
`ForgeRejected` instead of being defaulted.
"""

import re
import urllib.parse
from typing import Any, Dict, List, Mapping, Optional, Tuple

from qew.forge import (
    Forge,
    ForgeRejected,
    IssueSpec,
    PullRequestSpec,
    RemoteComment,
    RemoteIssue,
    RemotePullRequest,
    Transport,
)
from qew.forge_http import MAX_PAGES, api_base, call_json, resolve_token, split_repo
from qew.ledger_config import Mirror

PAGE_SIZE = 50
LABEL_COLOR = "#ededed"
DRAFT_PREFIX = "WIP:"
_DRAFT_TITLE = re.compile(r"^\s*(?:WIP:|\[WIP\])\s*", re.IGNORECASE)
_SHA = re.compile(r"^[0-9a-f]{40,64}$")
_CHECKS = {
    "success": "passing",
    "failure": "failing",
    "error": "failing",
    "warning": "failing",  # fail closed: a warning status is not a pass
    "pending": "pending",
}


def _malformed(what: str) -> ForgeRejected:
    return ForgeRejected(200, f"malformed forge answer: {what}")


def _object(data: Any, what: str) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise _malformed(f"{what} is not an object")
    return data


def _typed(data: Mapping[str, Any], key: str, kind: type, what: str) -> Any:
    value = data.get(key)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise _malformed(f"{what}.{key} is not {kind.__name__}")
    return value


def _parse_issue(data: Any) -> RemoteIssue:
    issue = _object(data, "issue")
    state = _typed(issue, "state", str, "issue")
    if state not in ("open", "closed"):
        raise _malformed(f"issue.state is `{state}`")
    labels = _typed(issue, "labels", list, "issue")
    names = tuple(_typed(_object(label, "label"), "name", str, "label") for label in labels)
    return RemoteIssue(
        number=_typed(issue, "number", int, "issue"),
        url=_typed(issue, "html_url", str, "issue"),
        node_id=str(_typed(issue, "id", int, "issue")),
        open=state == "open",
        title=_typed(issue, "title", str, "issue"),
        body="" if issue.get("body") is None else _typed(issue, "body", str, "issue"),  # null is an empty body
        labels=names,
        assignee=_parse_assignee(issue),
        comments=_typed(issue, "comments", int, "issue") if "comments" in issue else 0,
    )


def _parse_comment(data: Any) -> RemoteComment:
    comment = _object(data, "comment")
    return RemoteComment(
        id=str(_typed(comment, "id", int, "comment")),
        author=_typed(_object(comment.get("user"), "comment.user"), "login", str, "comment.user"),
        body="" if comment.get("body") is None else _typed(comment, "body", str, "comment"),
        created=_typed(comment, "created_at", str, "comment"),
    )


def _parse_assignee(issue: Mapping[str, Any]) -> str:
    # Gitea omits `assignee` (or sends null) for an unassigned issue.
    assignee = issue.get("assignee")
    if assignee is None:
        return ""
    return _typed(_object(assignee, "assignee"), "login", str, "assignee")


def _parse_pull_request(data: Any, checks: str) -> RemotePullRequest:
    pull = _object(data, "pull request")
    state = _typed(pull, "state", str, "pull request")
    if state not in ("open", "closed"):
        raise _malformed(f"pull request.state is `{state}`")
    merged = _typed(pull, "merged", bool, "pull request")
    head = _object(pull.get("head"), "pull request.head")
    base = _object(pull.get("base"), "pull request.base")
    if "mergeable" not in pull or not isinstance(pull["mergeable"], (bool, type(None))):
        raise _malformed("pull request.mergeable is not a boolean")
    return RemotePullRequest(
        body=_pull_body(pull),
        author=_typed(_object(pull.get("user"), "pull request.user"), "login", str, "pull request.user"),
        assignee=_pull_assignee(pull),
        merge_sha=_optional_str(pull, "merge_commit_sha", "pull request") if merged else "",
        number=_typed(pull, "number", int, "pull request"),
        url=_typed(pull, "html_url", str, "pull request"),
        state="merged" if merged else state,
        draft=bool(_DRAFT_TITLE.match(_typed(pull, "title", str, "pull request"))),
        head_sha=_typed(head, "sha", str, "pull request.head"),
        head_branch=_typed(head, "ref", str, "pull request.head"),
        base_branch=_typed(base, "ref", str, "pull request.base"),
        mergeable=pull["mergeable"],
        checks=checks,
    )


def _optional_str(data: Mapping[str, Any], key: str, what: str) -> str:
    """A string the forge may send as null; null reads as empty, any other type is malformed."""
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _malformed(f"{what}.{key} is not str")
    return value


def _pull_body(pull: Mapping[str, Any]) -> str:
    if "body" not in pull:
        raise _malformed("pull request.body is missing")
    return _optional_str(pull, "body", "pull request")


def _pull_assignee(pull: Mapping[str, Any]) -> str:
    assignees = pull.get("assignees")
    if assignees is None:
        return ""
    if not isinstance(assignees, list):
        raise _malformed("pull request.assignees is not list")
    if not assignees:
        return ""
    return _typed(_object(assignees[0], "assignee"), "login", str, "assignee")


def _parse_checks(data: Any) -> str:
    status = _object(data, "commit status")
    if _typed(status, "total_count", int, "commit status") == 0:
        return "none"
    state = _typed(status, "state", str, "commit status")
    if state not in _CHECKS:
        raise _malformed(f"commit status.state is `{state}`")
    return _CHECKS[state]


def _total_count(headers: Mapping[str, str]) -> Optional[int]:
    for key, value in headers.items():
        if key.lower() == "x-total-count":
            if not value.isdigit():
                raise _malformed("X-Total-Count is not a number")
            return int(value)
    return None


def _has_next_link(headers: Mapping[str, str]) -> bool:
    for key, value in headers.items():
        if key.lower() == "link":
            return 'rel="next"' in value
    return False


class Forgejo(Forge):
    name = "forgejo"

    def __init__(self, mirror: Mirror, transport: Transport, env: Mapping[str, str]) -> None:
        host, namespace, repo = split_repo(mirror)
        root = api_base(mirror, f"{host}/api/v1")
        self._mirror = mirror
        self._transport = transport
        self._env = env
        self._namespace = namespace
        self._repo = repo
        self._repo_url = f"{root}/repos/{urllib.parse.quote(namespace, safe='')}/{urllib.parse.quote(repo, safe='')}"

    # -- requests --

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"token {resolve_token(self._mirror, self._env)}"}

    def _call(self, method: str, path: str, payload: Optional[Any] = None) -> Tuple[Any, Mapping[str, str]]:
        return call_json(self._transport, method, f"{self._repo_url}{path}", self._headers(), payload)

    def _list(self, path: str, params: Optional[Mapping[str, str]] = None) -> List[Any]:
        items: List[Any] = []
        for page in range(1, MAX_PAGES + 1):
            query = urllib.parse.urlencode({**(params or {}), "page": page, "limit": PAGE_SIZE})
            data, headers = self._call("GET", f"{path}?{query}")
            if not isinstance(data, list):
                raise _malformed(f"{path} is not a list")
            items.extend(data)
            total = _total_count(headers)
            if not data or (total is not None and len(items) >= total):
                return items
            if total is None and not _has_next_link(headers) and len(data) < PAGE_SIZE:
                return items
        raise ForgeRejected(200, f"{path} has more than {MAX_PAGES} pages")

    # -- labels --

    def _label_ids(self, names: Tuple[str, ...]) -> List[int]:
        known: Dict[str, int] = {}
        for label in self._list("/labels"):
            label = _object(label, "label")
            known[_typed(label, "name", str, "label")] = _typed(label, "id", int, "label")
        ids: List[int] = []
        for name in dict.fromkeys(names):
            if name not in known:
                created, _ = self._call("POST", "/labels", {"name": name, "color": LABEL_COLOR})
                known[name] = _typed(_object(created, "label"), "id", int, "label")
            ids.append(known[name])
        return ids

    # -- issues --

    def find_issue(self, marker: str) -> Optional[RemoteIssue]:
        hits = self._list("/issues", {"q": marker, "type": "issues", "state": "all"})
        matches = [issue for issue in map(_parse_issue, hits) if marker in issue.body]
        return min(matches, key=lambda issue: issue.number) if matches else None

    def create_issue(self, spec: IssueSpec) -> RemoteIssue:
        payload: Dict[str, Any] = {"title": spec.title, "body": spec.body, "labels": self._label_ids(spec.labels)}
        if spec.assignee:
            payload["assignees"] = [spec.assignee]
        created, _ = self._call("POST", "/issues", payload)
        issue = _parse_issue(created)
        return issue if spec.open else self._set_state(issue.number, spec)

    def update_issue(self, number: int, spec: IssueSpec) -> RemoteIssue:
        label_ids = self._label_ids(spec.labels)
        payload = {
            "title": spec.title,
            "body": spec.body,
            "state": "open" if spec.open else "closed",
            "assignees": [spec.assignee] if spec.assignee else [],
        }
        self._call("PATCH", f"/issues/{number}", payload)
        self._call("PUT", f"/issues/{number}/labels", {"labels": label_ids})
        return self.read_issue(number)

    def _set_state(self, number: int, spec: IssueSpec) -> RemoteIssue:
        data, _ = self._call("PATCH", f"/issues/{number}", {"state": "open" if spec.open else "closed"})
        return _parse_issue(data)

    def read_issue(self, number: int) -> RemoteIssue:
        data, _ = self._call("GET", f"/issues/{number}")
        return _parse_issue(data)

    def list_issues(self) -> List[RemoteIssue]:
        # `type=issues` keeps pull requests out; the key check guards a server that ignores it.
        listed = self._list("/issues", {"type": "issues", "state": "all", "sort": "oldest"})
        return [_parse_issue(entry) for entry in listed if "pull_request" not in _object(entry, "issue")]

    def list_comments(self, number: int) -> List[RemoteComment]:
        return [_parse_comment(entry) for entry in self._list(f"/issues/{number}/comments")]

    def create_comment(self, number: int, body: str) -> RemoteComment:
        data, _ = self._call("POST", f"/issues/{number}/comments", {"body": body})
        return _parse_comment(data)

    def link_dependency(self, number: int, blocked_by: int) -> None:
        existing = self._list(f"/issues/{number}/dependencies")
        if any(_parse_issue(issue).number == blocked_by for issue in existing):
            return
        target = {"owner": self._namespace, "repo": self._repo, "index": blocked_by}
        self._call("POST", f"/issues/{number}/dependencies", target)

    # -- pull requests --

    def open_pull_request(self, spec: PullRequestSpec) -> RemotePullRequest:
        title = f"{DRAFT_PREFIX} {spec.title}" if spec.draft else spec.title
        payload: Dict[str, Any] = {"head": spec.head, "base": spec.base, "title": title, "body": spec.body}
        if spec.assignee:
            payload["assignees"] = [spec.assignee]
        data, _ = self._call("POST", "/pulls", payload)
        return self._with_checks(data)

    def read_pull_request(self, number: int) -> RemotePullRequest:
        data, _ = self._call("GET", f"/pulls/{number}")
        return self._with_checks(data)

    def mark_pull_request_ready(self, number: int) -> RemotePullRequest:
        data, _ = self._call("GET", f"/pulls/{number}")
        current = _parse_pull_request(data, "none")
        if not current.draft:
            return self._with_checks(data)
        title = _DRAFT_TITLE.sub("", _typed(data, "title", str, "pull request"), count=1)
        updated, _ = self._call("PATCH", f"/pulls/{number}", {"title": title})
        return self._with_checks(updated)

    def merge_pull_request(self, number: int, expected_head_sha: str, bypass_checks: bool = False) -> RemotePullRequest:
        payload: Dict[str, Any] = {"Do": "merge", "head_commit_id": expected_head_sha}
        if bypass_checks:
            payload["force_merge"] = True
        self._call("POST", f"/pulls/{number}/merge", payload)
        return self.read_pull_request(number)

    def update_pull_request_body(self, number: int, body: str) -> RemotePullRequest:
        self._call("PATCH", f"/pulls/{number}", {"body": body})
        return self.read_pull_request(number)

    def requires_status_checks(self, branch: str) -> bool:
        try:
            data, _ = self._call("GET", f"/branch_protections/{urllib.parse.quote(branch, safe='')}")
        except ForgeRejected as error:
            if error.status == 404:  # no protection record for the branch
                return False
            raise
        return _typed(_object(data, "branch protection"), "enable_status_check", bool, "branch protection")

    def _with_checks(self, data: Any) -> RemotePullRequest:
        sha = _typed(_object(_object(data, "pull request").get("head"), "pull request.head"), "sha", str, "head")
        if not _SHA.match(sha):
            raise _malformed("pull request.head.sha is not a commit id")
        status, _ = self._call("GET", f"/commits/{sha}/status")
        return _parse_pull_request(data, _parse_checks(status))
