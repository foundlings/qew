"""GitLab backend: REST /api/v4, the project addressed by its URL-encoded full path.

Issues, issue links, an issue board whose lists are the scoped labels `board::<Column>`,
and merge requests as the pull-request methods. See docs/specs/forges.md ("GitLab notes").
"""

import re
from typing import Any, Dict, Iterator, List, Mapping, Optional, Set, Tuple
from urllib.parse import quote, urlencode

from qew import forge_http
from qew.forge import (
    LABEL_COLOR,
    LABEL_DESCRIPTION,
    LabelNotPersisted,
    Milestone,
    MilestoneSpec,
    marker_alone,
    Board,
    Forge,
    Placement,
    ForgeRejected,
    IssueSpec,
    PullRequestSpec,
    RemoteComment,
    RemoteIssue,
    RemotePullRequest,
    Transport,
    Unsupported,
)
from qew.ledger_config import Mirror

PRIORITY_PREFIX = "priority::"
BOARD_PREFIX = "board::"
DRAFT_TITLE = "Draft: "
_DRAFT_MARKER = re.compile(r"^\s*(?:\[draft\]|\(draft\)|draft:)\s*", re.IGNORECASE)
_LABEL_COLOR = "#6699cc"
_PER_PAGE = 100

_PIPELINE_CHECKS = {
    "success": "passing",
    "failed": "failing",
    "canceled": "failing",
    "canceling": "failing",
    "created": "pending",
    "waiting_for_resource": "pending",
    "preparing": "pending",
    "pending": "pending",
    "running": "pending",
    "scheduled": "pending",
    "manual": "pending",
    "skipped": "none",
}
# detailed_merge_status values that mean "the forge has not decided yet".
_MERGE_UNDECIDED = frozenset({"checking", "unchecked", "preparing", "approvals_syncing", "ci_still_running"})
_MR_STATES = {"opened": "open", "locked": "open", "closed": "closed", "merged": "merged"}


# ----- parsing: malformed forge JSON is ForgeRejected, never a defaulted value -----


def _malformed(what: str) -> ForgeRejected:
    return ForgeRejected(200, f"malformed GitLab answer: {what}")


def _object(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise _malformed(f"{what} is not an object")
    return value


def _list(value: Any, what: str) -> List[Any]:
    if not isinstance(value, list):
        raise _malformed(f"{what} is not a list")
    return value


def _field(data: Mapping[str, Any], key: str, kind: type) -> Any:
    value = data.get(key, None)
    if type(value) is not kind:
        raise _malformed(f"`{key}` is missing or not {kind.__name__}")
    return value


def _description(data: Mapping[str, Any]) -> str:
    if "description" in data and data["description"] is None:
        return ""  # GitLab answers null for an empty description
    return _field(data, "description", str)


def _parse_issue(raw: Any) -> RemoteIssue:
    data = _object(raw, "issue")
    state = _field(data, "state", str)
    if state not in ("opened", "closed"):
        raise _malformed(f"issue state `{state}`")
    labels = tuple(_field({"label": item}, "label", str) for item in _list(data.get("labels"), "labels"))
    assignees = _list(data.get("assignees"), "assignees")
    assignee = _field(_object(assignees[0], "assignee"), "username", str) if assignees else ""
    return RemoteIssue(
        number=_field(data, "iid", int),
        url=_field(data, "web_url", str),
        node_id=str(_field(data, "id", int)),
        open=state == "opened",
        title=_field(data, "title", str),
        body=_description(data),
        labels=labels,
        assignee=assignee,
        comments=_field(data, "user_notes_count", int) if "user_notes_count" in data else 0,
        milestone=0 if data.get("milestone") is None else _field(_object(data["milestone"], "milestone"), "id", int),
    )


def _parse_milestone(raw: Any) -> Milestone:
    data = _object(raw, "milestone")
    state = _field(data, "state", str)
    if state not in ("active", "closed"):
        raise _malformed(f"milestone state `{state}`")
    due = data.get("due_date")
    return Milestone(_field(data, "id", int), _field(data, "title", str), _description(data),
                     "" if due is None else _field(data, "due_date", str), state == "active")


def _milestone_payload(spec: MilestoneSpec) -> Dict[str, Any]:
    return {"title": spec.title, "description": spec.description, "due_date": spec.due or None}


def _parse_note(raw: Any) -> RemoteComment:
    data = _object(raw, "note")
    body = "" if data.get("body") is None else _field(data, "body", str)
    return RemoteComment(
        id=str(_field(data, "id", int)),
        author=_field(_object(data.get("author"), "note author"), "username", str),
        body=body,
        created=_field(data, "created_at", str),
    )


def _parse_checks(raw: Any) -> str:
    if raw is None:
        return "none"
    status = _field(_object(raw, "head_pipeline"), "status", str)
    if status not in _PIPELINE_CHECKS:
        raise _malformed(f"pipeline status `{status}`")
    return _PIPELINE_CHECKS[status]


def _parse_mergeable(status: str) -> Optional[bool]:
    if status == "mergeable":
        return True
    # Every other settled status is a blocker; an unknown future value fails closed.
    return None if status in _MERGE_UNDECIDED else False


def _assignee_name(data: Mapping[str, Any]) -> str:
    assignees = _list(data.get("assignees"), "assignees")
    return _field(_object(assignees[0], "assignee"), "username", str) if assignees else ""


def _merge_sha(data: Mapping[str, Any], state: str) -> str:
    value = data.get("merge_commit_sha")
    if value is None or state != "merged":
        return ""
    if not isinstance(value, str):
        raise _malformed("merge request.merge_commit_sha")
    return value


def _parse_merge_request(raw: Any) -> RemotePullRequest:
    data = _object(raw, "merge request")
    state = _field(data, "state", str)
    if state not in _MR_STATES:
        raise _malformed(f"merge request state `{state}`")
    return RemotePullRequest(
        body=_description(data),
        author=_field(_object(data.get("author"), "author"), "username", str),
        assignee=_assignee_name(data),
        auto_merge=_field(data, "merge_when_pipeline_succeeds", bool),
        merge_sha=_merge_sha(data, state),
        number=_field(data, "iid", int),
        url=_field(data, "web_url", str),
        state=_MR_STATES[state],
        draft=_field(data, "draft", bool),
        head_sha=_field(data, "sha", str),
        head_branch=_field(data, "source_branch", str),
        base_branch=_field(data, "target_branch", str),
        mergeable=_parse_mergeable(_field(data, "detailed_merge_status", str)),
        checks=_parse_checks(data.get("head_pipeline")),
    )


def _next_page(headers: Mapping[str, str]) -> bool:
    for key, value in headers.items():
        if key.lower() == "x-next-page":
            return bool(value.strip())
    return False


def _csv(labels: Tuple[str, ...]) -> str:
    for label in labels:
        if "," in label:
            raise ForgeRejected(400, f"label `{label}` contains a comma, which GitLab cannot carry")
    return ",".join(labels)


def _strip_draft(title: str) -> str:
    while _DRAFT_MARKER.match(title):
        title = _DRAFT_MARKER.sub("", title, count=1)
    return title


class GitLab(Forge):
    name = "gitlab"

    def __init__(self, mirror: Mirror, transport: Transport, env: Mapping[str, str]) -> None:
        self._mirror = mirror
        self._transport = transport
        self._env = env
        host, namespace, repo = forge_http.split_repo(mirror)
        self._host = host
        self._full_path = f"{namespace}/{repo}" if namespace else repo
        self._repo = repo
        self._project = f"/projects/{quote(self._full_path, safe='')}"
        self._api = forge_http.api_base(mirror, f"{host}/api/v4")

    # ----- requests -----

    def _call(self, method: str, path: str, query: Optional[Mapping[str, Any]] = None,
              payload: Optional[Any] = None) -> Tuple[Any, Mapping[str, str]]:
        token = forge_http.resolve_token(self._mirror, self._env)
        url = self._api + path + (f"?{urlencode(query)}" if query else "")
        return forge_http.call_json(self._transport, method, url, {"PRIVATE-TOKEN": token}, payload)

    def _pages(self, path: str, query: Mapping[str, Any]) -> Iterator[Any]:
        """Items of every page, lazily; a walk longer than MAX_PAGES is an error, not a truncation."""
        for page in range(1, forge_http.MAX_PAGES + 1):
            data, headers = self._call("GET", path, {**query, "page": page, "per_page": _PER_PAGE})
            yield from _list(data, "page")
            if not _next_page(headers):
                return
        raise ForgeRejected(200, f"{path} has more than {forge_http.MAX_PAGES} pages")

    # ----- issues -----

    def find_issue(self, marker: str) -> Optional[RemoteIssue]:
        query = {"search": marker, "in": "description", "state": "all"}
        matches = [issue for issue in map(_parse_issue, self._pages(f"{self._project}/issues", query))
                   if marker_alone(issue.body, marker)]
        return min(matches, key=lambda found: found.number) if matches else None

    def list_issues(self, since: str = "") -> List[RemoteIssue]:
        query = {"state": "all", "order_by": "created_at", "sort": "asc", **({"updated_after": since} if since else {})}
        return [_parse_issue(raw) for raw in self._pages(f"{self._project}/issues", query)]

    def list_comments(self, number: int) -> List[RemoteComment]:
        query = {"order_by": "created_at", "sort": "asc"}
        notes = [_object(raw, "note") for raw in self._pages(f"{self._project}/issues/{number}/notes", query)]
        # System notes are GitLab's own activity lines (label changes, state changes), not comments.
        return [_parse_note(note) for note in notes if not _field(note, "system", bool)]

    def create_comment(self, number: int, body: str) -> RemoteComment:
        data, _ = self._call("POST", f"{self._project}/issues/{number}/notes", payload={"body": body})
        return _parse_note(data)

    def read_issue(self, number: int) -> RemoteIssue:
        data, _ = self._call("GET", f"{self._project}/issues/{number}")
        return _parse_issue(data)

    def find_milestone(self, title: str) -> Optional[Milestone]:
        found = [_parse_milestone(raw) for raw in self._pages(f"{self._project}/milestones", {"state": "all"})]
        return next((m for m in found if m.title == title), None)

    def create_milestone(self, spec: MilestoneSpec) -> Milestone:
        data, _ = self._call("POST", f"{self._project}/milestones", payload=_milestone_payload(spec))
        return _parse_milestone(data)

    def update_milestone(self, number: int, spec: MilestoneSpec) -> Milestone:
        data, _ = self._call("PUT", f"{self._project}/milestones/{number}", payload=_milestone_payload(spec))
        return _parse_milestone(data)

    def read_milestone(self, number: int) -> Milestone:
        found = next((m for m in map(_parse_milestone, self._pages(f"{self._project}/milestones", {"state": "all"}))
                      if m.number == number), None)
        if found is None:
            raise ForgeRejected(404, f"milestone {number} not found")
        return found

    def set_milestone_open(self, number: int, open: bool) -> Milestone:
        data, _ = self._call("PUT", f"{self._project}/milestones/{number}", payload={"state_event": "activate" if open else "close"})
        return _parse_milestone(data)

    def assign_milestone(self, number: int, milestone: int) -> RemoteIssue:
        return self._write_issue(number, {"milestone_id": milestone})

    def _ensure_issue_labels(self, wanted: Tuple[str, ...]) -> None:
        """Create the project labels that are missing (neutral, never touching an existing one) and confirm them."""
        _csv(wanted)  # refuses an unusable label name before any request
        known = {name for name, _ in self._label_ids()}
        missing = [name for name in dict.fromkeys(wanted) if name not in known]
        for name in missing:
            self._create_label(name, "#" + LABEL_COLOR)
        if missing:
            present = {name for name, _ in self._label_ids()}
            absent = [name for name in missing if name not in present]
            if absent:
                raise LabelNotPersisted(absent)

    def create_issue(self, spec: IssueSpec) -> RemoteIssue:
        self._ensure_issue_labels(spec.labels)
        payload: Dict[str, Any] = {
            "title": spec.title, "description": spec.body, "labels": _csv(spec.labels),
            "assignee_ids": self._assignee_ids(spec.assignee),
        }
        data, _ = self._call("POST", f"{self._project}/issues", payload=payload)
        created = _parse_issue(data)
        if spec.open:
            return created
        return self._write_issue(created.number, {"state_event": "close"})

    def update_issue(self, number: int, spec: IssueSpec) -> RemoteIssue:
        self._ensure_issue_labels(spec.labels)
        current = self.read_issue(number)
        kept = tuple(label for label in current.labels if label.startswith((BOARD_PREFIX, PRIORITY_PREFIX)) and label not in spec.labels)
        payload: Dict[str, Any] = {
            "title": spec.title, "description": spec.body, "labels": _csv((*spec.labels, *kept)),
            "assignee_ids": self._assignee_ids(spec.assignee),
        }
        if current.open != spec.open:
            payload["state_event"] = "reopen" if spec.open else "close"
        return self._write_issue(number, payload)

    def _write_issue(self, number: int, payload: Mapping[str, Any]) -> RemoteIssue:
        data, _ = self._call("PUT", f"{self._project}/issues/{number}", payload=payload)
        return _parse_issue(data)

    def _assignee_ids(self, username: str) -> List[int]:
        if not username:
            return []
        data, _ = self._call("GET", "/users", {"username": username})
        for raw in _list(data, "users"):
            user = _object(raw, "user")
            if _field(user, "username", str) == username:
                return [_field(user, "id", int)]
        raise ForgeRejected(404, f"no GitLab user `{username}`")

    def link_dependency(self, number: int, blocked_by: int) -> None:
        payload = {"target_project_id": self._full_path, "target_issue_iid": blocked_by, "link_type": "is_blocked_by"}
        try:
            self._call("POST", f"{self._project}/issues/{number}/links", payload=payload)
        except ForgeRejected as error:
            if error.status != 409:  # 409 is GitLab's documented "already linked"
                raise

    # ----- boards -----

    def ensure_board(self, columns: Tuple[str, ...], managed_filter: str = "") -> Board:
        name = self._mirror.project or self._repo
        board = self._find_board(name)  # `project` may be a board id this method returned earlier
        if board is None:
            data, _ = self._call("POST", f"{self._project}/boards", payload={"name": name})
            board = _object(data, "board")
        board_id = _field(board, "id", int)
        present = {
            _field(_object(_object(item, "board list").get("label"), "list label"), "name", str)
            for item in _list(board.get("lists"), "board lists")
        }
        label_ids = {name: label_id for name, label_id in self._label_ids()}
        for column in columns:
            label = BOARD_PREFIX + column
            if label in present:
                continue
            if label not in label_ids:
                label_ids[label] = self._create_label(label)
            self._call("POST", f"{self._project}/boards/{board_id}/lists", payload={"label_id": label_ids[label]})
        return Board(str(board_id), f"{self._host}/{self._full_path}/-/boards/{board_id}", tuple(columns))

    def _find_board(self, name: str) -> Optional[Dict[str, Any]]:
        for raw in self._pages(f"{self._project}/boards", {}):
            board = _object(raw, "board")
            if str(_field(board, "id", int)) == name or _field(board, "name", str) == name:
                return board
        return None

    def _label_ids(self) -> List[Tuple[str, int]]:
        found = []
        for raw in self._pages(f"{self._project}/labels", {}):
            label = _object(raw, "label")
            found.append((_field(label, "name", str), _field(label, "id", int)))
        return found

    def list_repo_labels(self) -> List[str]:
        return sorted(name for name, _ in self._label_ids())

    def labels_in_use(self) -> Set[str]:
        used: Set[str] = set()
        for resource in ("issues", "merge_requests"):
            for raw in self._pages(f"{self._project}/{resource}", {"state": "all"}):
                used.update(_field({"label": item}, "label", str) for item in _list(_object(raw, resource).get("labels"), "labels"))
        return used

    def delete_label(self, name: str) -> None:
        for label_name, label_id in self._label_ids():
            if label_name == name:
                self._call("DELETE", f"{self._project}/labels/{label_id}")
                return

    def _create_label(self, name: str, color: str = _LABEL_COLOR) -> int:
        data, _ = self._call("POST", f"{self._project}/labels",
                             payload={"name": name, "color": color, "description": LABEL_DESCRIPTION})
        return _field(_object(data, "label"), "id", int)

    def place_on_board(self, board: Board, number: int, column: str, priority: str = "") -> None:
        if column not in board.columns:
            raise ForgeRejected(400, f"board has no column `{column}`")
        wanted = [BOARD_PREFIX + column] + ([PRIORITY_PREFIX + priority] if priority else [])
        labels = self.read_issue(number).labels
        owned = (BOARD_PREFIX, PRIORITY_PREFIX) if priority else (BOARD_PREFIX,)
        stale = [label for label in labels if label.startswith(owned) and label not in wanted]
        payload: Dict[str, Any] = {}
        missing = [label for label in wanted if label not in labels]
        if missing:
            payload["add_labels"] = _csv(tuple(missing))
        if stale:
            payload["remove_labels"] = _csv(tuple(stale))
        if payload:
            self._call("PUT", f"{self._project}/issues/{number}", payload=payload)

    def read_placement(self, board: Board, number: int) -> Optional[Placement]:
        labels = self.read_issue(number).labels
        columns = [label[len(BOARD_PREFIX):] for label in labels if label.startswith(BOARD_PREFIX)]
        if not columns:
            return None
        priorities = [label[len(PRIORITY_PREFIX):] for label in labels if label.startswith(PRIORITY_PREFIX)]
        return Placement(columns[0], priorities[0] if priorities else "")

    def remove_from_board(self, board: Board, number: int) -> None:
        stale = [label for label in self.read_issue(number).labels if label.startswith((BOARD_PREFIX, PRIORITY_PREFIX))]
        if stale:
            self._call("PUT", f"{self._project}/issues/{number}", payload={"remove_labels": _csv(tuple(stale))})

    # ----- merge requests -----

    def open_pull_request(self, spec: PullRequestSpec) -> RemotePullRequest:
        title = _strip_draft(spec.title)
        payload = {
            "source_branch": spec.head, "target_branch": spec.base,
            "title": DRAFT_TITLE + title if spec.draft else title,
            "description": spec.body, "remove_source_branch": False,
        }
        if spec.assignee:
            payload["assignee_ids"] = self._assignee_ids(spec.assignee)
        data, _ = self._call("POST", f"{self._project}/merge_requests", payload=payload)
        return _parse_merge_request(data)

    def read_pull_request(self, number: int) -> RemotePullRequest:
        data, _ = self._call("GET", f"{self._project}/merge_requests/{number}")
        return _parse_merge_request(data)

    def mark_pull_request_ready(self, number: int) -> RemotePullRequest:
        data, _ = self._call("GET", f"{self._project}/merge_requests/{number}")
        current = _parse_merge_request(data)
        if not current.draft:
            return current
        title = _strip_draft(_field(_object(data, "merge request"), "title", str))
        data, _ = self._call("PUT", f"{self._project}/merge_requests/{number}", payload={"title": title})
        return _parse_merge_request(data)

    def update_pull_request_body(self, number: int, body: str) -> RemotePullRequest:
        data, _ = self._call("PUT", f"{self._project}/merge_requests/{number}", payload={"description": body})
        return _parse_merge_request(data)

    def requires_status_checks(self, branch: str) -> bool:
        project, _ = self._call("GET", self._project)
        if not _field(_object(project, "project"), "only_allow_merge_if_pipeline_succeeds", bool):
            return False
        try:
            self._call("GET", f"{self._project}/protected_branches/{quote(branch, safe='')}")
        except ForgeRejected as error:
            if error.status == 404:  # the branch is not protected
                return False
            raise
        return True

    def enable_auto_merge(self, number: int) -> RemotePullRequest:
        payload = {"merge_when_pipeline_succeeds": True, "should_remove_source_branch": False, "squash": False}
        self._call("PUT", f"{self._project}/merge_requests/{number}/merge", payload=payload)
        return self.read_pull_request(number)

    def merge_pull_request(self, number: int, expected_head_sha: str, bypass_checks: bool = False) -> RemotePullRequest:
        if bypass_checks:
            raise Unsupported("merge_pull_request with bypass_checks")
        payload = {"sha": expected_head_sha, "should_remove_source_branch": False, "squash": False}
        data, _ = self._call("PUT", f"{self._project}/merge_requests/{number}/merge", payload=payload)
        merged = _parse_merge_request(data)
        if merged.state != "merged":
            raise ForgeRejected(200, f"merge request {number} is {merged.state} after the merge call")
        return merged
