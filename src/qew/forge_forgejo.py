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
from typing import Any, override
from collections.abc import Mapping

from qew import label_style
from qew.forge import (
    is_dead_twin,
    CachedPage,
    LabelNotPersisted,
    PageListing,
    Milestone,
    MilestoneSpec,
    marker_alone,
    Forge,
    ForgeRejected,
    IssueSpec,
    PullRequestSpec,
    RemoteComment,
    RemoteIssue,
    RemoteBranch,
    RemotePullRequest,
    Transport,
)
from qew.forge_http import MAX_PAGES, NotModified, api_base, call_json, get_conditional, resolve_token, split_repo
from qew.forge_pages import list_pages
from qew.ledger_config import Mirror

PAGE_SIZE = 50
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


def _object(data: Any, what: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise _malformed(f"{what} is not an object")
    return data


def _described(label: dict[str, Any]) -> str:
    """A label's description: Forgejo sends a string, and an absent or null one reads as empty."""
    value = label.get("description")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _malformed("label.description")
    return value


def _typed[T](data: Mapping[str, Any], key: str, kind: type[T], what: str) -> T:
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
        milestone=0 if issue.get("milestone") is None else _typed(_object(issue["milestone"], "milestone"), "id", int, "milestone"),
    )


def _parse_milestone(data: Any) -> Milestone:
    found = _object(data, "milestone")
    state = _typed(found, "state", str, "milestone")
    if state not in ("open", "closed"):
        raise _malformed(f"milestone.state is `{state}`")
    due = found.get("due_on")
    return Milestone(_typed(found, "id", int, "milestone"), _typed(found, "title", str, "milestone"),
                     "" if found.get("description") is None else _typed(found, "description", str, "milestone"),
                     "" if due is None else _typed(found, "due_on", str, "milestone")[:10], state == "open")


def _milestone_payload(spec: MilestoneSpec) -> dict[str, Any]:
    return {"title": spec.title, "description": spec.description, "due_on": f"{spec.due}T00:00:00Z" if spec.due else None}


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


def _total_count(headers: Mapping[str, str]) -> int | None:
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

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"token {resolve_token(self._mirror, self._env)}"}

    def _call(self, method: str, path: str, payload: Any | None = None) -> tuple[Any, Mapping[str, str]]:
        return call_json(self._transport, method, f"{self._repo_url}{path}", self._headers(), payload)

    def _list(self, path: str, params: Mapping[str, str] | None = None) -> list[Any]:
        items: list[Any] = []
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

    def _label_ids(self, names: tuple[str, ...]) -> list[int]:
        known: dict[str, int] = {}
        for label in self._list("/labels"):
            label = _object(label, "label")
            name = _typed(label, "name", str, "label")
            known[name] = _typed(label, "id", int, "label")
            wanted = label_style.repaint(name, _typed(label, "color", str, "label"), _described(label))
            if wanted is not None:
                self._call("PATCH", f"/labels/{known[name]}", {"color": "#" + wanted.color, "description": wanted.description})
        ids: list[int] = []
        made: list[str] = []
        for name in dict.fromkeys(names):
            if name not in known:
                style = label_style.style_of(name)
                created, _ = self._call("POST", "/labels", {"name": name, "color": "#" + style.color, "description": style.description})
                known[name] = _typed(_object(created, "label"), "id", int, "label")
                made.append(name)
            ids.append(known[name])
        if made:
            present = {_typed(_object(label, "label"), "name", str, "label") for label in self._list("/labels")}
            absent = [name for name in made if name not in present]
            if absent:
                raise LabelNotPersisted(absent)
        return ids

    @override
    def list_repo_labels(self) -> list[str]:
        return sorted(_typed(_object(label, "label"), "name", str, "label") for label in self._list("/labels"))

    @override
    def labels_in_use(self) -> set[str]:
        used: set[str] = set()
        for entry in self._list("/issues", {"state": "all"}):  # pull requests are listed among issues
            issue = _object(entry, "issue")
            if is_dead_twin(issue.get("body") or "", issue.get("state") == "open"):
                continue
            for label in _typed(issue, "labels", list, "issue"):
                used.add(_typed(_object(label, "label"), "name", str, "label"))
        return used

    @override
    def delete_label(self, name: str) -> None:
        for label in self._list("/labels"):
            label = _object(label, "label")
            if _typed(label, "name", str, "label") == name:
                self._call("DELETE", f"/labels/{_typed(label, 'id', int, 'label')}")
                return

    # -- issues --

    @override
    def find_issue(self, marker: str, recent: bool = False) -> RemoteIssue | None:  # server-side search: recent changes nothing
        hits = self._list("/issues", {"q": marker, "type": "issues", "state": "all"})
        matches = [issue for issue in map(_parse_issue, hits) if marker_alone(issue.body, marker)]
        return min(matches, key=lambda issue: issue.number) if matches else None

    @override
    def find_milestone(self, title: str) -> Milestone | None:
        found = [_parse_milestone(entry) for entry in self._list("/milestones", {"state": "all"})]
        return next((m for m in found if m.title == title), None)

    @override
    def create_milestone(self, spec: MilestoneSpec) -> Milestone:
        data, _ = self._call("POST", "/milestones", _milestone_payload(spec))
        return _parse_milestone(data)

    @override
    def update_milestone(self, number: int, spec: MilestoneSpec) -> Milestone:
        data, _ = self._call("PATCH", f"/milestones/{number}", _milestone_payload(spec))
        return _parse_milestone(data)

    @override
    def read_milestone(self, number: int) -> Milestone:
        data, _ = self._call("GET", f"/milestones/{number}")
        return _parse_milestone(data)

    @override
    def set_milestone_open(self, number: int, open: bool) -> Milestone:
        data, _ = self._call("PATCH", f"/milestones/{number}", {"state": "open" if open else "closed"})
        return _parse_milestone(data)

    @override
    def assign_milestone(self, number: int, milestone: int) -> RemoteIssue:
        data, _ = self._call("PATCH", f"/issues/{number}", {"milestone": milestone})
        return _parse_issue(data)

    @override
    def blob_url(self, base: str, ref: str, path: str) -> str:
        return f"{base}/src/branch/{ref}/{path}"

    @override
    def create_issue(self, spec: IssueSpec) -> RemoteIssue:
        payload: dict[str, Any] = {"title": spec.title, "body": spec.body, "labels": self._label_ids(spec.labels)}
        if spec.assignee:
            payload["assignees"] = [spec.assignee]
        created, _ = self._call("POST", "/issues", payload)
        issue = _parse_issue(created)
        return issue if spec.open else self._set_state(issue.number, spec)

    @override
    def update_issue(self, number: int, spec: IssueSpec) -> RemoteIssue:
        label_ids = self._label_ids(spec.labels)
        payload = {
            "title": spec.title,
            "body": spec.body,
            "state": "open" if spec.open else "closed",
            "assignees": [spec.assignee] if spec.assignee else [],
        }
        if spec.keep_state:
            del payload["state"]
        self._call("PATCH", f"/issues/{number}", payload)
        self._call("PUT", f"/issues/{number}/labels", {"labels": label_ids})
        return self.read_issue(number)

    def _set_state(self, number: int, spec: IssueSpec) -> RemoteIssue:
        data, _ = self._call("PATCH", f"/issues/{number}", {"state": "open" if spec.open else "closed"})
        return _parse_issue(data)

    @override
    def read_issue(self, number: int) -> RemoteIssue:
        data, _ = self._call("GET", f"/issues/{number}")
        return _parse_issue(data)

    @override
    def list_issues(self, since: str = "") -> list[RemoteIssue]:
        # `type=issues` keeps pull requests out; the key check guards a server that ignores it.
        window = {"since": since} if since else {}
        listed = self._list("/issues", {"type": "issues", "state": "all", "sort": "oldest", **window})
        return [_parse_issue(entry) for entry in listed if "pull_request" not in _object(entry, "issue")]

    @override
    def list_issue_pages(self, cached: Mapping[int, CachedPage]) -> PageListing:
        def fetch(number: int, etag: str) -> NotModified | tuple[tuple[RemoteIssue, ...], int, Mapping[str, str], bool]:
            query = urllib.parse.urlencode({"type": "issues", "state": "all", "sort": "oldest", "page": number, "limit": PAGE_SIZE})
            answer = get_conditional(self._transport, f"{self._repo_url}/issues?{query}", self._headers(), etag)
            if isinstance(answer, NotModified):
                return answer
            data, headers = answer
            if not isinstance(data, list):
                raise _malformed("/issues is not a list")
            issues = tuple(_parse_issue(e) for e in data if "pull_request" not in _object(e, "issue"))
            return issues, len(data), headers, _has_next_link(headers)

        return list_pages(fetch, cached, PAGE_SIZE)

    @override
    def list_comments(self, number: int) -> list[RemoteComment]:
        return [_parse_comment(entry) for entry in self._list(f"/issues/{number}/comments")]

    @override
    def create_comment(self, number: int, body: str) -> RemoteComment:
        data, _ = self._call("POST", f"/issues/{number}/comments", {"body": body})
        return _parse_comment(data)

    @override
    def link_dependency(self, number: int, blocked_by: int) -> None:
        existing = self._list(f"/issues/{number}/dependencies")
        if any(_parse_issue(issue).number == blocked_by for issue in existing):
            return
        target = {"owner": self._namespace, "repo": self._repo, "index": blocked_by}
        self._call("POST", f"/issues/{number}/dependencies", target)

    # -- pull requests --

    @override
    def open_pull_request(self, spec: PullRequestSpec) -> RemotePullRequest:
        title = f"{DRAFT_PREFIX} {spec.title}" if spec.draft else spec.title
        payload: dict[str, Any] = {"head": spec.head, "base": spec.base, "title": title, "body": spec.body}
        if spec.assignee:
            payload["assignees"] = [spec.assignee]
        data, _ = self._call("POST", "/pulls", payload)
        return self._with_checks(data)

    @override
    def read_pull_request(self, number: int) -> RemotePullRequest:
        data, _ = self._call("GET", f"/pulls/{number}")
        return self._with_checks(data)

    @override
    def mark_pull_request_ready(self, number: int) -> RemotePullRequest:
        data, _ = self._call("GET", f"/pulls/{number}")
        current = _parse_pull_request(data, "none")
        if not current.draft:
            return self._with_checks(data)
        title = _DRAFT_TITLE.sub("", _typed(data, "title", str, "pull request"), count=1)
        updated, _ = self._call("PATCH", f"/pulls/{number}", {"title": title})
        return self._with_checks(updated)

    @override
    def merge_pull_request(self, number: int, expected_head_sha: str, bypass_checks: bool = False) -> RemotePullRequest:
        payload: dict[str, Any] = {"Do": "merge", "head_commit_id": expected_head_sha}
        if bypass_checks:
            payload["force_merge"] = True
        self._call("POST", f"/pulls/{number}/merge", payload)
        return self.read_pull_request(number)

    @override
    def find_open_pull_requests(self, head: str) -> list[RemotePullRequest]:
        found: list[RemotePullRequest] = []
        for entry in self._list("/pulls", {"state": "open"}):
            ref = _typed(_object(_object(entry, "pull request").get("head"), "pull request.head"), "ref", str, "pull request.head")
            if ref == head:
                found.append(self._with_checks(entry))
        return found

    @override
    def close_pull_request(self, number: int) -> RemotePullRequest:
        data, _ = self._call("GET", f"/pulls/{number}")
        if _typed(_object(data, "pull request"), "state", str, "pull request") == "open":
            self._call("PATCH", f"/pulls/{number}", {"state": "closed"})
        return self.read_pull_request(number)

    @override
    def update_pull_request_body(self, number: int, body: str) -> RemotePullRequest:
        self._call("PATCH", f"/pulls/{number}", {"body": body})
        return self.read_pull_request(number)

    @override
    def requires_status_checks(self, branch: str) -> bool:
        try:
            data, _ = self._call("GET", f"/branch_protections/{urllib.parse.quote(branch, safe='')}")
        except ForgeRejected as error:
            if error.status == 404:  # no protection record for the branch
                return False
            raise
        return _typed(_object(data, "branch protection"), "enable_status_check", bool, "branch protection")

    # -- branches --

    @override
    def list_branches(self) -> list[RemoteBranch]:
        repository, _ = self._call("GET", "")
        default = _typed(_object(repository, "repository"), "default_branch", str, "repository")
        found: list[RemoteBranch] = []
        for entry in self._list("/branches"):
            branch = _object(entry, "branch")
            name = _typed(branch, "name", str, "branch")
            found.append(RemoteBranch(name, name == default, _typed(branch, "protected", bool, "branch")))
        return found

    @override
    def branch_is_merged(self, name: str, base: str) -> bool:
        spec = f"{urllib.parse.quote(base, safe='/')}...{urllib.parse.quote(name, safe='/')}"
        data, _ = self._call("GET", f"/compare/{spec}")
        return _typed(_object(data, "comparison"), "total_commits", int, "comparison") == 0

    @override
    def delete_branch(self, name: str) -> None:
        try:
            self._call("DELETE", f"/branches/{urllib.parse.quote(name, safe='/')}")
        except ForgeRejected as error:
            if error.status != 404:  # a branch that is already gone is success
                raise

    @override
    def list_open_pull_heads(self) -> list[str]:
        heads: list[str] = []
        for entry in self._list("/pulls", {"state": "open"}):
            head = _object(_object(entry, "pull request").get("head"), "pull request.head")
            heads.append(_typed(head, "ref", str, "pull request.head"))
        return heads

    def _with_checks(self, data: Any) -> RemotePullRequest:
        sha = _typed(_object(_object(data, "pull request").get("head"), "pull request.head"), "sha", str, "head")
        if not _SHA.match(sha):
            raise _malformed("pull request.head.sha is not a commit id")
        status, _ = self._call("GET", f"/commits/{sha}/status")
        return _parse_pull_request(data, _parse_checks(status))
