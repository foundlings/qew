"""GitHub backend: issues and pull requests over REST, Projects v2 over GraphQL.

Everything the forge answers is untrusted data. Responses are parsed strictly: a
missing or mistyped field raises `ForgeRejected` rather than becoming a default.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple
from urllib.parse import quote

from qew.forge import (
    Board,
    Forge,
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
CLOSE_REASONS = ("", "completed", "not_planned")
_NEXT_LINK = re.compile(r'<([^>]+)>([^,<]*)')

_OWNER_PROJECTS = """
query OwnerProjects($owner: String!, $cursor: String) {
  repositoryOwner(login: $owner) {
    id
    ... on ProjectV2Owner {
      projectsV2(first: 100, after: $cursor) {
        nodes { id number title url fields(first: 50) { nodes { ... on ProjectV2SingleSelectField { id name options { id name } } } } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}"""
_CREATE_PROJECT = """
mutation CreateProject($ownerId: ID!, $title: String!) {
  createProjectV2(input: {ownerId: $ownerId, title: $title}) {
    projectV2 { id number title url fields(first: 50) { nodes { ... on ProjectV2SingleSelectField { id name options { id name } } } } }
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
    )


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
class _Project:
    id: str
    number: int
    title: str
    url: str
    status_field_id: Optional[str]
    status_options: Tuple[Tuple[str, str], ...]  # (option id, name)


def parse_project(data: Any) -> _Project:
    node = _obj(data, "project")
    field_id: Optional[str] = None
    options: Tuple[Tuple[str, str], ...] = ()
    for field in _array(_obj(node.get("fields"), "project.fields").get("nodes"), "project.fields.nodes"):
        field = _obj(field, "project field")
        if not field:  # a field that is not single-select matches no fragment and arrives as {}
            continue
        if _str(field, "name", "field") == STATUS_FIELD:
            field_id = _str(field, "id", "field")
            options = tuple(
                (_str(_obj(o, "option"), "id", "option"), _str(o, "name", "option"))
                for o in _array(field.get("options"), "field.options")
            )
    return _Project(
        id=_str(node, "id", "project"), number=_int(node, "number", "project"),
        title=_str(node, "title", "project"), url=_str(node, "url", "project"),
        status_field_id=field_id, status_options=options,
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
        return self._with_checks(parse_pull(self._wire.rest("POST", f"{self._repo_path}/pulls", payload)))

    def read_pull_request(self, number: int) -> RemotePullRequest:
        return self._with_checks(self._read_pull(number))

    def mark_pull_request_ready(self, number: int) -> RemotePullRequest:
        facts = self._read_pull(number)
        if facts.draft:
            self._wire.graphql(_MARK_READY, {"id": facts.node_id})
            facts = self._read_pull(number)
        return self._with_checks(facts)

    def merge_pull_request(self, number: int, expected_head_sha: str) -> RemotePullRequest:
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
            mergeable=facts.mergeable, checks=self._checks(facts.head_sha),
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

    def ensure_board(self, columns: Tuple[str, ...]) -> Board:
        project = self._find_or_create_project()
        if project.status_field_id is None:
            self._wire.graphql(_CREATE_STATUS_FIELD, {"projectId": project.id, "name": STATUS_FIELD, "options": _option_inputs(columns)})
        elif tuple(name for _, name in project.status_options) != tuple(columns):
            self._wire.graphql(_UPDATE_STATUS_FIELD, {"fieldId": project.status_field_id, "options": _option_inputs(columns)})
        return Board(id=str(project.number), url=project.url, columns=tuple(columns), title=project.title)

    def place_on_board(self, board: Board, number: int, column: str) -> None:
        issue = self.read_issue(number)
        number_wanted = _project_number(board.id)
        _, projects = self._owner_projects()
        project = next((p for p in projects if p.number == number_wanted), None)
        if project is None:
            raise ForgeRejected(404, f"project number {number_wanted} not found for {self._owner}")
        options = {name: option_id for option_id, name in project.status_options}
        if project.status_field_id is None or column not in options:
            raise ForgeRejected(400, f"board has no `{column}` column in its {STATUS_FIELD} field")
        added = self._wire.graphql(_ADD_ITEM, {"projectId": project.id, "contentId": issue.node_id})
        item = _obj(_obj(added.get("addProjectV2ItemById"), "addProjectV2ItemById").get("item"), "item")
        self._wire.graphql(_SET_STATUS, {
            "projectId": project.id, "itemId": _str(item, "id", "item"),
            "fieldId": project.status_field_id, "optionId": options[column],
        })

    def _find_or_create_project(self) -> _Project:
        owner_id, projects = self._owner_projects()
        if self.mirror.project is not None:
            number = _project_number(self.mirror.project)
            for project in projects:
                if project.number == number:
                    wanted = self.mirror.options.get("project_title")
                    if isinstance(wanted, str) and wanted and wanted != project.title:
                        raise ForgeRejected(409, f"project number {number} is titled {project.title!r}, not project_title {wanted!r}; "
                                                 "remove one of them (a different board is never reconciled)")
                    return project
            raise ForgeRejected(404, f"project number {number} not found for {self._owner}")
        title = self._project_title()
        for project in projects:
            if project.title == title:
                return project
        created = self._wire.graphql(_CREATE_PROJECT, {"ownerId": owner_id, "title": title})
        return parse_project(_obj(_obj(created.get("createProjectV2"), "createProjectV2").get("projectV2"), "projectV2"))

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


def _option_inputs(columns: Tuple[str, ...]) -> List[Dict[str, str]]:
    return [{"name": name, "color": "GRAY", "description": ""} for name in columns]


def _project_number(value: str) -> int:
    if not value.isdigit():
        raise ForgeRejected(400, f"mirror `project` must be a project number, got {value!r}")
    return int(value)
