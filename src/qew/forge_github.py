"""GitHub backend: issues and pull requests over REST, Projects v2 over GraphQL.

Everything the forge answers is untrusted data. Responses are parsed strictly: a
missing or mistyped field raises `ForgeRejected` rather than becoming a default.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple
from urllib.parse import quote

from qew.forge import (
    BOARD_VIEWS,
    LEGACY_STATUS,
    PRIORITY_OPTIONS,
    STATUS_OPTIONS,
    Board,
    Forge,
    Placement,
    ForgeAuth,
    ForgeRejected,
    ForgeUnavailable,
    IssueSpec,
    PullRequestSpec,
    RemoteComment,
    RemoteIssue,
    RemotePullRequest,
    Transport,
)
from qew.forge_http import MAX_PAGES, api_base, call_json, resolve_token, split_repo
from qew.ledger_config import Mirror

API_VERSION = "2022-11-28"
DOTCOM_API = "https://api.github.com"
STATUS_FIELD = "Status"
PRIORITY_FIELD = "Priority"
DEFAULT_VIEW = "View 1"
CLOSE_REASONS = ("", "completed", "not_planned")
_NEXT_LINK = re.compile(r'<([^>]+)>([^,<]*)')

_OWNER_PROJECTS = """
query OwnerProjects($owner: String!, $cursor: String) {
  repositoryOwner(login: $owner) {
    id
    ... on ProjectV2Owner {
      projectsV2(first: 100, after: $cursor) {
        nodes { id number title url fields(first: 50) { nodes { ... on ProjectV2SingleSelectField { id name options { id name color description } } } } views(first: 50) { nodes { id name layout filter } } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}"""
_ENABLE_AUTO_MERGE = """
mutation EnableAutoMerge($id: ID!) {
  enablePullRequestAutoMerge(input: {pullRequestId: $id, mergeMethod: MERGE}) { pullRequest { id } }
}"""
_CREATE_PROJECT = """
mutation CreateProject($ownerId: ID!, $title: String!) {
  createProjectV2(input: {ownerId: $ownerId, title: $title}) {
    projectV2 { id number title url fields(first: 50) { nodes { ... on ProjectV2SingleSelectField { id name options { id name color description } } } } views(first: 50) { nodes { id name layout filter } } }
  }
}"""
_CREATE_STATUS_FIELD = """
mutation CreateStatusField($projectId: ID!, $name: String!, $options: [ProjectV2SingleSelectFieldOptionInput!]) {
  createProjectV2Field(input: {projectId: $projectId, dataType: SINGLE_SELECT, name: $name, singleSelectOptions: $options}) {
    projectV2Field { ... on ProjectV2SingleSelectField { id } }
  }
}"""
_UPDATE_STATUS_FIELD = """
mutation UpdateStatusField($fieldId: ID!, $options: [ProjectV2SingleSelectFieldOptionInput!]) {
  updateProjectV2Field(input: {fieldId: $fieldId, singleSelectOptions: $options}) {
    projectV2Field { ... on ProjectV2SingleSelectField { id } }
  }
}"""
_CREATE_VIEW = """
mutation CreateView($projectId: ID!, $name: String!, $layout: ProjectV2ViewLayout!) {
  createProjectV2View(input: {projectId: $projectId, name: $name, layout: $layout}) { projectV2View { id } }
}"""
_UPDATE_VIEW = """
mutation UpdateView($viewId: ID!, $name: String, $layout: ProjectV2ViewLayout, $filter: String) {
  updateProjectV2View(input: {viewId: $viewId, name: $name, layout: $layout, filter: $filter}) { projectV2View { id } }
}"""
_ITEM_PLACEMENT = """
query ItemPlacement($contentId: ID!) {
  node(id: $contentId) {
    ... on Issue {
      projectItems(first: 50) {
        nodes {
          id
          project { number }
          fieldValues(first: 30) { nodes { ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2FieldCommon { name } } } } }
        }
      }
    }
  }
}"""
_DELETE_ITEM = """
mutation DeleteItem($projectId: ID!, $itemId: ID!) {
  deleteProjectV2Item(input: {projectId: $projectId, itemId: $itemId}) { deletedItemId }
}"""
_ADD_ITEM = """
mutation AddItem($projectId: ID!, $contentId: ID!) {
  addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) { item { id } }
}"""
_SET_STATUS = """
mutation SetStatus($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
  updateProjectV2ItemFieldValue(input: {projectId: $projectId, itemId: $itemId, fieldId: $fieldId, value: {singleSelectOptionId: $optionId}}) {
    projectV2Item { id }
  }
}"""
_MARK_READY = """
mutation MarkReady($id: ID!) {
  markPullRequestReadyForReview(input: {pullRequestId: $id}) { pullRequest { id } }
}"""


def api_roots(mirror: Mirror) -> Tuple[str, str]:
    """(REST root, GraphQL URL): github.com, a GHES host, or the mirror's `api` / `graphql` options."""
    host, _, _ = split_repo(mirror)
    default = DOTCOM_API if host.split("://", 1)[-1] in ("github.com", "www.github.com") else f"{host}/api/v3"
    rest = api_base(mirror, default)
    graphql = mirror.options.get("graphql")
    if isinstance(graphql, str):
        return rest, graphql.rstrip("/")
    root = rest[: -len("/v3")] if rest.endswith("/v3") else rest
    return rest, f"{root}/graphql"


# -- strict parsing ---------------------------------------------------------------

def _malformed(what: str) -> ForgeRejected:
    return ForgeRejected(200, f"malformed GitHub response: {what}")


def _obj(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise _malformed(what)
    return value


def _array(value: Any, what: str) -> List[Any]:
    if not isinstance(value, list):
        raise _malformed(what)
    return value


def _str(data: Dict[str, Any], key: str, what: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise _malformed(f"{what}.{key}")
    return value


def _int(data: Dict[str, Any], key: str, what: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise _malformed(f"{what}.{key}")
    return value


def _bool(data: Dict[str, Any], key: str, what: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise _malformed(f"{what}.{key}")
    return value


def _nullable_str(data: Dict[str, Any], key: str, what: str) -> str:
    """A key GitHub sends as a string or null (an empty body is null); null reads as empty."""
    if key not in data:
        raise _malformed(f"{what}.{key}")
    value = data[key]
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _malformed(f"{what}.{key}")
    return value


def parse_issue(data: Any) -> RemoteIssue:
    issue = _obj(data, "issue")
    state = _str(issue, "state", "issue")
    if state not in ("open", "closed"):
        raise _malformed("issue.state")
    labels = tuple(_str(_obj(label, "issue.labels[]"), "name", "label") for label in _array(issue.get("labels"), "issue.labels"))
    if "assignee" not in issue:
        raise _malformed("issue.assignee")
    assignee = "" if issue["assignee"] is None else _str(_obj(issue["assignee"], "issue.assignee"), "login", "assignee")
    return RemoteIssue(
        number=_int(issue, "number", "issue"),
        url=_str(issue, "html_url", "issue"),
        node_id=_str(issue, "node_id", "issue"),
        open=state == "open",
        title=_str(issue, "title", "issue"),
        body=_nullable_str(issue, "body", "issue"),
        labels=labels,
        assignee=assignee,
        comments=_int(issue, "comments", "issue") if "comments" in issue else 0,
    )


def parse_comment(data: Any) -> RemoteComment:
    comment = _obj(data, "comment")
    return RemoteComment(
        id=str(_int(comment, "id", "comment")),
        author=_str(_obj(comment.get("user"), "comment.user"), "login", "comment.user"),
        body=_nullable_str(comment, "body", "comment"),
        created=_str(comment, "created_at", "comment"),
    )


@dataclass(frozen=True)
class _PullFacts:
    """What one pull request GET says; `checks` needs further calls and is joined later."""

    number: int
    url: str
    node_id: str
    state: str
    draft: bool
    head_sha: str
    head_branch: str
    base_branch: str
    mergeable: Optional[bool]
    body: str
    author: str
    assignee: str
    auto_merge: bool
    merge_sha: str


def _login(data: Dict[str, Any], key: str, what: str, nullable: bool) -> str:
    """The `login` of a user object; a null user reads as "" only where GitHub may send one."""
    if key not in data or (data[key] is None and not nullable):
        raise _malformed(f"{what}.{key}")
    return "" if data[key] is None else _str(_obj(data[key], f"{what}.{key}"), "login", f"{what}.{key}")


def parse_pull(data: Any) -> _PullFacts:
    pull = _obj(data, "pull")
    state = _str(pull, "state", "pull")
    if state not in ("open", "closed"):
        raise _malformed("pull.state")
    head = _obj(pull.get("head"), "pull.head")
    base = _obj(pull.get("base"), "pull.base")
    if "mergeable" not in pull or not (pull["mergeable"] is None or isinstance(pull["mergeable"], bool)):
        raise _malformed("pull.mergeable")
    return _PullFacts(
        number=_int(pull, "number", "pull"),
        url=_str(pull, "html_url", "pull"),
        node_id=_str(pull, "node_id", "pull"),
        state="merged" if _bool(pull, "merged", "pull") else state,
        draft=_bool(pull, "draft", "pull"),
        head_sha=_str(head, "sha", "pull.head"),
        head_branch=_str(head, "ref", "pull.head"),
        base_branch=_str(base, "ref", "pull.base"),
        mergeable=pull["mergeable"],
        body=_nullable_str(pull, "body", "pull"),
        author=_login(pull, "user", "pull", nullable=False),
        assignee=_login(pull, "assignee", "pull", nullable=True),
        auto_merge=_auto_merge(pull),
        merge_sha=_nullable_str(pull, "merge_commit_sha", "pull") if _bool(pull, "merged", "pull") else "",
    )


def _auto_merge(pull: Dict[str, Any]) -> bool:
    if "auto_merge" not in pull or not (pull["auto_merge"] is None or isinstance(pull["auto_merge"], dict)):
        raise _malformed("pull.auto_merge")
    return pull["auto_merge"] is not None


def combine_checks(status_state: Optional[str], run_outcomes: List[str]) -> str:
    """Fold a commit status and check-run outcomes ("pass", "fail", "wait") into one verdict."""
    outcomes = list(run_outcomes)
    if status_state is not None:
        outcomes.append({"success": "pass", "pending": "wait"}.get(status_state, "fail"))
    if not outcomes:
        return "none"
    if "fail" in outcomes:
        return "failing"
    return "pending" if "wait" in outcomes else "passing"


def _run_outcome(run: Any) -> str:
    data = _obj(run, "check run")
    if _str(data, "status", "check run") != "completed":
        return "wait"
    conclusion = _str(data, "conclusion", "check run")
    return "pass" if conclusion in ("success", "neutral", "skipped") else "fail"


@dataclass(frozen=True)
class _Option:
    id: str
    name: str
    color: str
    description: str


@dataclass(frozen=True)
class _Field:
    id: str
    options: Tuple[_Option, ...]


@dataclass(frozen=True)
class _View:
    id: str
    name: str
    layout: str
    filter: str


@dataclass(frozen=True)
class _Project:
    id: str
    number: int
    title: str
    url: str
    fields: Mapping[str, _Field]
    views: Tuple[_View, ...]

    @property
    def status_field_id(self) -> Optional[str]:
        field = self.fields.get(STATUS_FIELD)
        return field.id if field else None

    @property
    def status_options(self) -> Tuple[Tuple[str, str], ...]:  # (option id, name)
        field = self.fields.get(STATUS_FIELD)
        return tuple((o.id, o.name) for o in field.options) if field else ()


def parse_project(data: Any) -> _Project:
    node = _obj(data, "project")
    fields: Dict[str, _Field] = {}
    for field in _array(_obj(node.get("fields"), "project.fields").get("nodes"), "project.fields.nodes"):
        field = _obj(field, "project field")
        if not field:  # a field that is not single-select matches no fragment and arrives as {}
            continue
        options = tuple(
            _Option(
                _str(_obj(o, "option"), "id", "option"), _str(o, "name", "option"),
                _nullable_str(o, "color", "option") or "GRAY", _nullable_str(o, "description", "option"),
            )
            for o in _array(field.get("options"), "field.options")
        )
        fields[_str(field, "name", "field")] = _Field(_str(field, "id", "field"), options)
    views = tuple(
        _View(
            _str(_obj(v, "view"), "id", "view"), _str(v, "name", "view"), _str(v, "layout", "view"),
            _nullable_str(v, "filter", "view"),
        )
        for v in _array(_obj(node.get("views"), "project.views").get("nodes"), "project.views.nodes")
    )
    return _Project(
        id=_str(node, "id", "project"), number=_int(node, "number", "project"),
        title=_str(node, "title", "project"), url=_str(node, "url", "project"),
        fields=fields, views=views,
    )


def _next_url(headers: Mapping[str, str]) -> Optional[str]:
    link = next((value for key, value in headers.items() if key.lower() == "link"), "")
    for url, params in _NEXT_LINK.findall(link):
        if re.search(r'rel="?next"?', params):
            return url
    return None


# -- the two wires ----------------------------------------------------------------

class _Wire:
    """Authenticated JSON calls against the REST root and the GraphQL endpoint."""

    def __init__(self, mirror: Mirror, transport: Transport, env: Mapping[str, str]) -> None:
        self._mirror = mirror
        self._transport = transport
        self._env = env
        self._token: Optional[str] = None
        self.rest_root, self.graphql_url = api_roots(mirror)

    def _headers(self) -> Dict[str, str]:
        if self._token is None:
            self._token = resolve_token(self._mirror, self._env)
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "qew",
        }

    def rest(self, method: str, path: str, payload: Optional[Any] = None) -> Any:
        data, _ = call_json(self._transport, method, self.rest_root + path, self._headers(), payload)
        return data

    def pages(self, path: str) -> Iterator[Any]:
        """Every page of a list endpoint, following Link rel=next on this API root only."""
        url: Optional[str] = self.rest_root + path
        for _ in range(MAX_PAGES):
            if url is None:
                return
            data, headers = call_json(self._transport, "GET", url, self._headers())
            yield data
            url = _next_url(headers)
            if url is not None and not url.startswith(self.rest_root + "/"):
                raise ForgeRejected(200, "pagination link leaves the API root; refusing to follow it")
        if url is not None:
            raise ForgeRejected(200, f"more than {MAX_PAGES} pages; refusing a truncated listing")

    def graphql(self, query: str, variables: Mapping[str, Any]) -> Dict[str, Any]:
        data, _ = call_json(self._transport, "POST", self.graphql_url, self._headers(), {"query": query, "variables": dict(variables)})
        answer = _obj(data, "graphql response")
        errors = answer.get("errors")
        if errors:
            raise _graphql_error(errors)
        return _obj(answer.get("data"), "graphql data")


def _graphql_error(errors: Any) -> Exception:
    entries = [e for e in errors if isinstance(e, dict)] if isinstance(errors, list) else []
    kinds = {e.get("type") for e in entries}
    detail = "; ".join(str(e.get("message", "")) for e in entries)[:300] or "unparseable GraphQL errors"
    if "RATE_LIMITED" in kinds:
        return ForgeUnavailable(f"GraphQL: {detail}")
    if kinds & {"FORBIDDEN", "INSUFFICIENT_SCOPES", "UNAUTHORIZED"}:
        return ForgeAuth(f"GraphQL: {detail}")
    return ForgeRejected(200, f"GraphQL: {detail}")


# -- the backend ------------------------------------------------------------------

class GitHub(Forge):
    name = "github"

    def __init__(self, mirror: Mirror, transport: Transport, env: Mapping[str, str]) -> None:
        self.mirror = mirror
        _, namespace, repo = split_repo(mirror)
        self._owner = namespace
        self._repo = repo
        self._repo_path = f"/repos/{quote(namespace, safe='')}/{quote(repo, safe='')}"
        self._wire = _Wire(mirror, transport, env)

    # issues

    def find_issue(self, marker: str) -> Optional[RemoteIssue]:
        for page in self._wire.pages(f"{self._repo_path}/issues?state=all&per_page=100"):
            for entry in _array(page, "issue list"):
                issue = _obj(entry, "issue list entry")
                if "pull_request" in issue:
                    continue
                if marker in _nullable_str(issue, "body", "issue"):
                    return parse_issue(issue)
        return None

    def create_issue(self, spec: IssueSpec) -> RemoteIssue:
        _check_reason(spec)
        payload: Dict[str, Any] = {"title": spec.title, "body": spec.body, "labels": list(spec.labels)}
        if spec.assignee:
            payload["assignees"] = [spec.assignee]
        created = parse_issue(self._wire.rest("POST", f"{self._repo_path}/issues", payload))
        if spec.open:
            return created
        return self.update_issue(created.number, spec)

    def update_issue(self, number: int, spec: IssueSpec) -> RemoteIssue:
        _check_reason(spec)
        payload: Dict[str, Any] = {
            "title": spec.title, "body": spec.body, "labels": list(spec.labels),
            "assignees": [spec.assignee] if spec.assignee else [],
            "state": "open" if spec.open else "closed",
        }
        if not spec.open and spec.close_reason:
            payload["state_reason"] = spec.close_reason
        return parse_issue(self._wire.rest("PATCH", f"{self._repo_path}/issues/{number}", payload))

    def read_issue(self, number: int) -> RemoteIssue:
        return parse_issue(self._wire.rest("GET", f"{self._repo_path}/issues/{number}"))

    def list_issues(self) -> List[RemoteIssue]:
        found: List[RemoteIssue] = []
        for page in self._wire.pages(f"{self._repo_path}/issues?state=all&sort=created&direction=asc&per_page=100"):
            for entry in _array(page, "issue list"):
                if "pull_request" not in _obj(entry, "issue list entry"):
                    found.append(parse_issue(entry))
        return found

    def list_comments(self, number: int) -> List[RemoteComment]:
        found: List[RemoteComment] = []
        for page in self._wire.pages(f"{self._repo_path}/issues/{number}/comments?per_page=100"):
            found.extend(parse_comment(entry) for entry in _array(page, "comment list"))
        return found

    def create_comment(self, number: int, body: str) -> RemoteComment:
        return parse_comment(self._wire.rest("POST", f"{self._repo_path}/issues/{number}/comments", {"body": body}))

    def link_dependency(self, number: int, blocked_by: int) -> None:
        path = f"{self._repo_path}/issues/{number}/dependencies/blocked_by"
        for page in self._wire.pages(f"{path}?per_page=100"):
            if any(_int(_obj(entry, "dependency"), "number", "dependency") == blocked_by for entry in _array(page, "dependencies")):
                return
        blocker = _obj(self._wire.rest("GET", f"{self._repo_path}/issues/{blocked_by}"), "issue")
        self._wire.rest("POST", path, {"issue_id": _int(blocker, "id", "issue")})

    # pull requests

    def open_pull_request(self, spec: PullRequestSpec) -> RemotePullRequest:
        payload = {"title": spec.title, "head": spec.head, "base": spec.base, "body": spec.body, "draft": spec.draft}
        created = parse_pull(self._wire.rest("POST", f"{self._repo_path}/pulls", payload))
        if spec.assignee:
            # A pull request is assigned through its issue; the create call has no such field. The pull
            # request already exists, so a login the forge will not assign leaves it unassigned (the caller
            # reads `assignee` back) instead of failing and orphaning it.
            try:
                self._wire.rest("PATCH", f"{self._repo_path}/issues/{created.number}", {"assignees": [spec.assignee]})
            except ForgeRejected:
                pass
            return self.read_pull_request(created.number)
        return self._with_checks(created)

    def update_pull_request_body(self, number: int, body: str) -> RemotePullRequest:
        self._wire.rest("PATCH", f"{self._repo_path}/pulls/{number}", {"body": body})
        return self.read_pull_request(number)

    def requires_status_checks(self, branch: str) -> bool:
        name = quote(branch, safe="")
        rules = _array(self._wire.rest("GET", f"{self._repo_path}/rules/branches/{name}"), "branch rules")
        if any(_str(_obj(rule, "branch rule"), "type", "branch rule") == "required_status_checks" for rule in rules):
            return True
        try:
            protection = _obj(self._wire.rest("GET", f"{self._repo_path}/branches/{name}/protection"), "branch protection")
        except ForgeRejected as error:
            if error.status == 404:  # the forge's "Branch not protected"
                return False
            raise
        return protection.get("required_status_checks") is not None

    def enable_auto_merge(self, number: int) -> RemotePullRequest:
        facts = self._read_pull(number)
        if not facts.auto_merge:
            self._wire.graphql(_ENABLE_AUTO_MERGE, {"id": facts.node_id})
        return self.read_pull_request(number)

    def read_pull_request(self, number: int) -> RemotePullRequest:
        return self._with_checks(self._read_pull(number))

    def mark_pull_request_ready(self, number: int) -> RemotePullRequest:
        facts = self._read_pull(number)
        if facts.draft:
            self._wire.graphql(_MARK_READY, {"id": facts.node_id})
            facts = self._read_pull(number)
        return self._with_checks(facts)

    def merge_pull_request(self, number: int, expected_head_sha: str, bypass_checks: bool = False) -> RemotePullRequest:
        # GitHub has no bypass flag: an administrator's token merges past protection with this same call.
        facts = self._read_pull(number)
        if facts.head_sha != expected_head_sha:
            raise ForgeRejected(409, f"head moved: expected {expected_head_sha}, found {facts.head_sha}")
        if facts.state != "merged":
            self._wire.rest("PUT", f"{self._repo_path}/pulls/{number}/merge", {"merge_method": "merge", "sha": expected_head_sha})
        return self.read_pull_request(number)

    def _read_pull(self, number: int) -> _PullFacts:
        return parse_pull(self._wire.rest("GET", f"{self._repo_path}/pulls/{number}"))

    def _with_checks(self, facts: _PullFacts) -> RemotePullRequest:
        return RemotePullRequest(
            number=facts.number, url=facts.url, state=facts.state, draft=facts.draft,
            head_sha=facts.head_sha, head_branch=facts.head_branch, base_branch=facts.base_branch,
            mergeable=facts.mergeable, checks=self._checks(facts.head_sha), body=facts.body,
            author=facts.author, assignee=facts.assignee, auto_merge=facts.auto_merge, merge_sha=facts.merge_sha,
        )

    def _checks(self, sha: str) -> str:
        base = f"{self._repo_path}/commits/{quote(sha, safe='')}"
        status = _obj(self._wire.rest("GET", f"{base}/status"), "combined status")
        state = _str(status, "state", "combined status") if _int(status, "total_count", "combined status") else None
        runs: List[str] = []
        for page in self._wire.pages(f"{base}/check-runs?per_page=100"):
            runs.extend(_run_outcome(run) for run in _array(_obj(page, "check runs").get("check_runs"), "check_runs"))
        return combine_checks(state, runs)

    # boards

    def ensure_board(self, columns: Tuple[str, ...], managed_filter: str = "") -> Board:
        project, fresh = self._find_or_create_project()
        notes: List[str] = []
        status = _status_options(columns)
        self._ensure_select(project, STATUS_FIELD, status, LEGACY_STATUS, notes, fresh)
        self._ensure_select(project, PRIORITY_FIELD, PRIORITY_OPTIONS, {}, notes, fresh)
        self._ensure_views(project, managed_filter, notes)
        return Board(id=str(project.number), url=project.url, columns=tuple(columns), title=project.title, notes=tuple(notes))

    def _ensure_select(self, project: _Project, name: str, desired, legacy: Mapping[str, str], notes: List[str], fresh: bool) -> None:
        field = project.fields.get(name)
        if field is None:
            options = [{"name": n, "color": c, "description": d} for n, c, d in desired]
            self._wire.graphql(_CREATE_STATUS_FIELD, {"projectId": project.id, "name": name, "options": options})
            return
        # a project created a moment ago holds only GitHub's stock options and no cards: replace them
        inputs, changed = reconcile_options(name, () if fresh else field.options, desired, legacy, notes)
        if changed:
            self._wire.graphql(_UPDATE_STATUS_FIELD, {"fieldId": field.id, "options": inputs})

    def _ensure_views(self, project: _Project, managed_filter: str, notes: List[str]) -> None:
        existing = {v.name: v for v in project.views}
        claimed = {name for name, _layout, _filter in BOARD_VIEWS}
        spare = [v for v in project.views if v.name not in claimed]
        for name, layout, text in BOARD_VIEWS:
            wanted = f"{managed_filter} {text}".strip()
            view = existing.get(name)
            if view is None and name == BOARD_VIEWS[0][0] and spare and spare[0].name == DEFAULT_VIEW:
                view = spare[0]  # the project's untouched default view becomes Kanban
                self._wire.graphql(_UPDATE_VIEW, {"viewId": view.id, "name": name, "layout": layout, "filter": wanted})
                continue
            if view is None:
                made = self._wire.graphql(_CREATE_VIEW, {"projectId": project.id, "name": name, "layout": layout})
                created = _obj(_obj(made.get("createProjectV2View"), "createProjectV2View").get("projectV2View"), "view")
                if wanted:
                    self._wire.graphql(_UPDATE_VIEW, {"viewId": _str(created, "id", "view"), "name": name, "layout": layout, "filter": wanted})
                continue
            if view.layout != layout or view.filter != wanted:
                self._wire.graphql(_UPDATE_VIEW, {"viewId": view.id, "name": name, "layout": layout, "filter": wanted})
                notes.append(f"view {name} corrected: was {view.layout} `{view.filter}`, now {layout} `{wanted}`")

    def _board_project(self, board: Board) -> _Project:
        wanted = _project_number(board.id)
        _, projects = self._owner_projects()
        project = next((p for p in projects if p.number == wanted), None)
        if project is None:
            raise ForgeRejected(404, f"project number {wanted} not found for {self._owner}")
        return project

    def place_on_board(self, board: Board, number: int, column: str, priority: str = "") -> None:
        issue = self.read_issue(number)
        project = self._board_project(board)
        status = project.fields.get(STATUS_FIELD)
        options = {o.name: o.id for o in status.options} if status else {}
        if status is None or column not in options:
            raise ForgeRejected(400, f"board has no `{column}` column in its {STATUS_FIELD} field; run `qew project ensure`")
        urgency = project.fields.get(PRIORITY_FIELD)
        urgent = {o.name: o.id for o in urgency.options} if urgency else {}
        if priority and priority not in urgent:
            raise ForgeRejected(400, f"board has no `{priority}` option in its {PRIORITY_FIELD} field; run `qew project ensure`")
        added = self._wire.graphql(_ADD_ITEM, {"projectId": project.id, "contentId": issue.node_id})
        item = _obj(_obj(added.get("addProjectV2ItemById"), "addProjectV2ItemById").get("item"), "item")
        item_id = _str(item, "id", "item")
        self._wire.graphql(_SET_STATUS, {
            "projectId": project.id, "itemId": item_id, "fieldId": status.id, "optionId": options[column],
        })
        if priority and urgency is not None:
            self._wire.graphql(_SET_STATUS, {
                "projectId": project.id, "itemId": item_id, "fieldId": urgency.id, "optionId": urgent[priority],
            })

    def _placement(self, board: Board, number: int) -> Optional[Tuple[str, Placement]]:
        issue = self.read_issue(number)
        data = self._wire.graphql(_ITEM_PLACEMENT, {"contentId": issue.node_id})
        node = _obj(data.get("node"), "node")
        wanted = _project_number(board.id)
        for entry in _array(_obj(node.get("projectItems"), "projectItems").get("nodes"), "projectItems.nodes"):
            entry = _obj(entry, "project item")
            if _int(_obj(entry.get("project"), "item.project"), "number", "item.project") != wanted:
                continue
            values: Dict[str, str] = {}
            for value in _array(_obj(entry.get("fieldValues"), "fieldValues").get("nodes"), "fieldValues.nodes"):
                value = _obj(value, "field value")
                if value:  # other field kinds match no fragment and arrive as {}
                    values[_str(_obj(value.get("field"), "value.field"), "name", "value.field")] = _str(value, "name", "value")
            return _str(entry, "id", "item"), Placement(values.get(STATUS_FIELD, ""), values.get(PRIORITY_FIELD, ""))
        return None

    def read_placement(self, board: Board, number: int) -> Optional[Placement]:
        found = self._placement(board, number)
        return found[1] if found else None

    def remove_from_board(self, board: Board, number: int) -> None:
        found = self._placement(board, number)
        if found is not None:
            self._wire.graphql(_DELETE_ITEM, {"projectId": self._board_project(board).id, "itemId": found[0]})

    def _find_or_create_project(self) -> Tuple[_Project, bool]:
        """The project and whether this call created it."""
        owner_id, projects = self._owner_projects()
        if self.mirror.project is not None:
            number = _project_number(self.mirror.project)
            for project in projects:
                if project.number == number:
                    wanted = self.mirror.options.get("project_title")
                    if isinstance(wanted, str) and wanted and wanted != project.title:
                        raise ForgeRejected(409, f"project number {number} is titled {project.title!r}, not project_title {wanted!r}; "
                                                 "remove one of them (a different board is never reconciled)")
                    return project, False
            raise ForgeRejected(404, f"project number {number} not found for {self._owner}")
        title = self._project_title()
        for project in projects:
            if project.title == title:
                return project, False
        created = self._wire.graphql(_CREATE_PROJECT, {"ownerId": owner_id, "title": title})
        return parse_project(_obj(_obj(created.get("createProjectV2"), "createProjectV2").get("projectV2"), "projectV2")), True

    def _owner_projects(self) -> Tuple[str, List[_Project]]:
        projects: List[_Project] = []
        cursor: Optional[str] = None
        for _ in range(MAX_PAGES):
            data = self._wire.graphql(_OWNER_PROJECTS, {"owner": self._owner, "cursor": cursor})
            if data.get("repositoryOwner") is None:
                raise ForgeRejected(404, f"owner {self._owner} not found")
            owner = _obj(data["repositoryOwner"], "repositoryOwner")
            listing = _obj(owner.get("projectsV2"), "projectsV2")
            projects.extend(parse_project(node) for node in _array(listing.get("nodes"), "projectsV2.nodes"))
            info = _obj(listing.get("pageInfo"), "pageInfo")
            if not _bool(info, "hasNextPage", "pageInfo"):
                return _str(owner, "id", "repositoryOwner"), projects
            cursor = _str(info, "endCursor", "pageInfo")
        raise ForgeRejected(200, f"more than {MAX_PAGES} pages of projects; refusing a truncated listing")

    def _project_title(self) -> str:
        title = self.mirror.options.get("project_title")
        return title if isinstance(title, str) and title else self._repo


def _check_reason(spec: IssueSpec) -> None:
    if spec.close_reason not in CLOSE_REASONS:
        raise ForgeRejected(422, f"close_reason must be one of {CLOSE_REASONS}, got {spec.close_reason!r}")


def _status_options(columns: Tuple[str, ...]) -> Tuple[Tuple[str, str, str], ...]:
    known = {name: (name, color, description) for name, color, description in STATUS_OPTIONS}
    return tuple(known.get(name, (name, "GRAY", "")) for name in columns)


def reconcile_options(field: str, existing: Tuple[_Option, ...], desired, legacy: Mapping[str, str], notes: List[str]):
    """The option list to send and whether it differs from `existing`.

    A wanted option keeps its id (so cards stay) and takes the wanted color and description. A legacy name is
    renamed in place to the option it became, or dropped when that option already exists. Anything else is a
    foreign option: kept after ours, untouched, and reported.
    """
    by_name = {o.name: o for o in existing}
    wanted_names = {name for name, _c, _d in desired}
    inputs: List[Dict[str, str]] = []
    used = set()
    for name, color, description in desired:
        found = by_name.get(name)
        if found is None:
            old = next((o for o, target in legacy.items() if target == name and o in by_name and o not in wanted_names), None)
            found = by_name.get(old) if old else None
            if found is not None:
                notes.append(f"{field} option {found.name} renamed to {name}")
        entry = {"name": name, "color": color, "description": description}
        if found is not None:
            used.add(found.name)
            entry["id"] = found.id
        inputs.append(entry)
    for option in existing:
        if option.name in used:
            continue
        if legacy.get(option.name) in wanted_names:
            notes.append(f"{field} option {option.name} dropped: {legacy[option.name]} already exists")
            continue
        notes.append(f"{field} option {option.name} ({option.color}) is not part of the model; kept")
        inputs.append({"id": option.id, "name": option.name, "color": option.color, "description": option.description})
    current = [(o.id, o.name, o.color, o.description) for o in existing]
    proposed = [(i.get("id"), i["name"], i["color"], i["description"]) for i in inputs]
    return inputs, current != proposed


def _project_number(value: str) -> int:
    if not value.isdigit():
        raise ForgeRejected(400, f"mirror `project` must be a project number, got {value!r}")
    return int(value)
