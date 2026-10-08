"""An in-memory stand-in for GitHub's REST v3 and GraphQL wire format on 127.0.0.1.

Only the endpoints `qew.forge_github` uses. State lives in plain dicts, every
request is logged, and `fail_next` injects a status for the next matching call.
"""

from datetime import datetime
import hashlib
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0] + "/src")
from qew import instants  # noqa: E402

TOKEN = "test-token"
OWNER = "acme"
REPO = "widgets"
API_PREFIX = "/api/v3"
GRAPHQL_PATH = "/api/graphql"


class FakeGitHub:
    def __init__(self, page_size: int = 100) -> None:
        self.page_size = page_size
        self.issues: Dict[int, Dict[str, Any]] = {}
        self.pulls: Dict[int, Dict[str, Any]] = {}
        self.statuses: Dict[str, Dict[str, Any]] = {}
        self.check_runs: Dict[str, List[Dict[str, Any]]] = {}
        self.projects: Dict[str, Dict[str, Any]] = {}
        self.requests: List[Tuple[str, str, Any]] = []
        self.request_times: List[float] = []  # time.time() of each request, parallel to `requests`
        self.endless_check_runs = False  # check-run pages never end
        self.auto_merge_ignored = False  # accept the enable call yet change nothing
        self.rules: Dict[str, List[Dict[str, Any]]] = {}  # branch -> active ruleset rules
        self.protection: Dict[str, Dict[str, Any]] = {}  # branch -> classic protection record
        self.failures: List[Dict[str, Any]] = []
        self.branches: Dict[str, str] = {}  # branch -> head sha
        self.default_branch = "main"
        self.contained_in: Dict[str, List[str]] = {}  # branch -> branches whose history already holds its tip
        self.next_number = 1
        self.strict_add = False  # answer a repeated AddItem with GitHub's "Content already exists" refusal
        self.lag = 0  # number of ItemPlacement reads that still show nothing (eventual consistency)
        self.next_id = 1000
        self.milestones: Dict[int, Dict[str, Any]] = {}
        self.cards_page_size = 100  # cards per ProjectCards page
        self.cards_overlap = False  # each later page repeats the previous page's last card (cards added meanwhile)
        self.cards_endless = False  # every ProjectCards page says another follows
        self.label_store: Dict[str, Dict[str, str]] = {}  # name -> {color, description}
        self.drop_label_creates = False  # accept a label create yet store nothing
        self.etags = True  # answer issue-list pages with an ETag and honor If-None-Match
        self.clock: Callable[[], datetime] = instants.now  # the forge's clock; walks move it to place edits in time
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.fake = self  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    @property
    def api(self) -> str:
        return self.base + API_PREFIX

    @property
    def repo_url(self) -> str:
        return f"{self.base}/{OWNER}/{REPO}"

    def start(self) -> "FakeGitHub":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def fail_next(self, status: int, path_part: str = "", count: int = 1, body: Optional[Any] = None,
                  headers: Optional[Dict[str, str]] = None, skip: int = 0, after: bool = False) -> None:
        """Answer `status` (with `headers`) to the next `count` matching requests, after letting `skip` through.

        `after` performs the request first and loses only the response: the crash window of a create that was applied."""
        self.failures.append({"status": status, "part": path_part, "left": count, "body": body,
                              "headers": headers or {}, "skip": skip, "after": after})

    def add_branch(self, name: str, sha: str = "", contained_in: Tuple[str, ...] = ()) -> None:
        """A branch on the forge; `contained_in` names the branches that already hold its tip (it is merged there)."""
        self.branches[name] = sha or f"{len(self.branches) + 1:040x}"
        self.contained_in[name] = list(contained_in)

    def graphql_operations(self) -> List[str]:
        """Operation names of every GraphQL request so far, in order."""
        found = []
        for _method, path, body in self.requests:
            if path.endswith(GRAPHQL_PATH) and body:
                match = re.search(r"\b(?:query|mutation)\s+(\w+)", body["query"])
                found.append(match.group(1) if match else "")
        return found

    def gql_BoardItems(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        project = next((p for p in self.projects.values() if p["number"] == variables["number"]), None)
        if project is None:
            return {"data": {"repositoryOwner": {"projectV2": None}}}
        by_node = {issue["node_id"]: issue for issue in self.issues.values()}
        nodes = []
        for node_id, item in project["items"].items():
            issue = by_node.get(node_id)
            if issue is None:
                continue
            values = []
            for field in project["fields"]:
                chosen = item.get("values", {}).get(field["id"])
                for option in field["options"]:
                    if option["id"] == chosen:
                        values.append({"name": option["name"], "field": {"name": field["name"]}})
            nodes.append({
                "id": item["id"],
                "content": {"number": issue["number"], "repository": {"nameWithOwner": f"{OWNER}/{REPO}"}},
                "fieldValues": {"nodes": values},
            })
        return {"data": {"repositoryOwner": {"projectV2": {"items": {
            "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes,
        }}}}}

    def calls(self, method: str, path_part: str) -> List[Any]:
        return [body for m, path, body in self.requests if m == method and path_part in path]

    # -- test-side mutation of the world ------------------------------------

    def touch(self, issue: Dict[str, Any]) -> None:
        """Record a modification: edits, comments and label changes all bump updated_at, as on GitHub."""
        issue["updated_at"] = instants.format_instant(self.clock())

    def delete_issue(self, number: int) -> None:
        """An admin deletes the issue; later issues keep their numbers."""
        del self.issues[number]

    def push_to_pull(self, number: int, sha: str) -> None:
        self.pulls[number]["head"]["sha"] = sha

    def set_check_run(self, sha: str, status: str, conclusion: Optional[str]) -> None:
        self.check_runs.setdefault(sha, []).append({"status": status, "conclusion": conclusion})

    def set_status(self, sha: str, state: str, total: int = 1) -> None:
        self.statuses[sha] = {"state": state, "total_count": total}

    # -- REST ----------------------------------------------------------------

    def new_issue(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        number, self.next_number = self.next_number, self.next_number + 1
        self.next_id += 1
        issue = {
            "id": self.next_id, "node_id": f"I_{number}", "number": number,
            "title": payload["title"], "body": payload.get("body"), "state": "open",
            "state_reason": None, "labels": list(payload.get("labels", [])),
            "assignee": (payload.get("assignees") or [None])[0], "blocked_by": [], "discussion": [],
        }
        self.touch(issue)
        self.issues[number] = issue
        return issue

    def milestone_json(self, number):
        if not number:
            return None
        found = self.milestones[number]
        return {"number": number, "title": found["title"], "description": found["description"] or None,
                "due_on": found["due_on"], "state": found["state"]}

    def issue_json(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": issue["id"], "node_id": issue["node_id"], "number": issue["number"],
            "html_url": f"{self.base}/{OWNER}/{REPO}/issues/{issue['number']}",
            "milestone": self.milestone_json(issue.get("milestone")),
            "state": issue["state"], "state_reason": issue["state_reason"], "title": issue["title"],
            "body": issue["body"], "labels": [{"name": name} for name in issue["labels"]],
            "assignee": None if not issue["assignee"] else {"login": issue["assignee"]},
            "comments": len(issue["discussion"]),
        }

    def add_comment(self, number: int, login: str, body: Optional[str]) -> Dict[str, Any]:
        """A comment made on the forge by someone else; ids rise and timestamps are in creation order."""
        self.next_id += 1
        comment = {"id": self.next_id, "login": login, "body": body}
        discussion = self.issues[number]["discussion"]
        comment["created_at"] = f"2026-01-01T00:{len(discussion):02d}:00Z"
        discussion.append(comment)
        self.touch(self.issues[number])
        return comment

    def comment_json(self, comment: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": comment["id"], "user": {"login": comment["login"]}, "body": comment["body"],
            "created_at": comment["created_at"],
        }

    def pull_json(self, pull: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "number": pull["number"], "node_id": pull["node_id"],
            "html_url": f"{self.base}/{OWNER}/{REPO}/pull/{pull['number']}",
            "state": pull["state"], "merged": pull["merged"], "draft": pull["draft"],
            "mergeable": pull["mergeable"], "title": pull["title"], "body": pull["body"],
            "head": dict(pull["head"]), "base": {"ref": pull["base"]},
            "user": {"login": pull.get("author", "opener")},
            "assignee": None if not self.issues[pull["number"]]["assignee"] else {"login": self.issues[pull["number"]]["assignee"]},
            "auto_merge": {"merge_method": "merge"} if pull.get("auto_merge") else None,
            "merge_commit_sha": pull.get("merge_sha"),
        }

    def new_pull(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        number, self.next_number = self.next_number, self.next_number + 1
        pull = {
            "number": number, "node_id": f"PR_{number}", "state": "open", "merged": False,
            "draft": bool(payload.get("draft", False)), "mergeable": True, "title": payload["title"],
            "body": payload.get("body"), "head": {"sha": f"{number:040x}", "ref": payload["head"]},
            "base": payload["base"],
        }
        self.pulls[number] = pull
        # Real GitHub lists pull requests in the issues endpoint too.
        self.issues[number] = {
            "id": number + 5000, "node_id": f"PRI_{number}", "number": number, "title": pull["title"],
            "body": pull["body"], "state": "open", "state_reason": None, "labels": [],
            "assignee": None, "blocked_by": [], "pull_request": {}, "discussion": [],
        }
        self.touch(self.issues[number])
        return pull

    # -- GraphQL ---------------------------------------------------------------

    def graphql(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        found = re.search(r"\b(?:query|mutation)\s+(\w+)", query)
        name = found.group(1) if found else ""
        handler = getattr(self, f"gql_{name}", None)
        if handler is None:
            return {"errors": [{"type": "UNKNOWN", "message": f"unknown operation {name!r}"}]}
        return handler(variables)

    def project_json(self, project: Dict[str, Any]) -> Dict[str, Any]:
        fields = []
        for field in project["fields"]:
            fields.append({
                "id": field["id"], "name": field["name"],
                "options": [
                    {"id": o.get("id"), "name": o["name"], "color": o.get("color", "GRAY"), "description": o.get("description", "")}
                    for o in field["options"]
                ],
            })
        return {
            "id": project["id"], "number": project["number"], "title": project["title"],
            "url": f"{self.base}/orgs/{OWNER}/projects/{project['number']}",
            "fields": {"nodes": fields},
            "views": {"nodes": [dict(v) for v in project.get("views", [])]},
        }

    def gql_OwnerProjects(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        if variables["owner"] != OWNER:
            return {"data": {"repositoryOwner": None}}
        projects = [self.project_json(p) for p in self.projects.values()]
        return {"data": {"repositoryOwner": {
            "id": "O_owner", "projectsV2": {"nodes": projects, "pageInfo": {"hasNextPage": False, "endCursor": None}},
        }}}

    def gql_CreateProject(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        number = len(self.projects) + 1
        project = {
            "id": f"PVT_{number}", "number": number, "title": variables["title"], "items": {},
            "views": [{"id": f"PVTV_{number}_1", "name": "View 1", "layout": "TABLE_LAYOUT", "filter": None}],
            "fields": [{
                "id": f"PVTSSF_{number}", "name": "Status",
                "options": [{"id": f"default-{n}", "name": n} for n in ("Todo", "In Progress", "Done")],
            }],
        }
        self.projects[project["id"]] = project
        return {"data": {"createProjectV2": {"projectV2": self.project_json(project)}}}

    def gql_CreateStatusField(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        project = self.projects[variables["projectId"]]
        field = {"id": f"PVTSSF_new{len(project['fields'])}", "name": variables["name"], "options": []}
        self._set_options(field, variables["options"])
        project["fields"].append(field)
        return {"data": {"createProjectV2Field": {"projectV2Field": {"id": field["id"]}}}}

    def gql_UpdateStatusField(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        for project in self.projects.values():
            for field in project["fields"]:
                if field["id"] == variables["fieldId"]:
                    self._set_options(field, variables["options"])
                    kept = {o["id"] for o in field["options"]}
                    for item in project["items"].values():  # real GitHub drops values of removed options only
                        if item.get("option") not in kept and item.get("field") == field["id"]:
                            item.pop("option", None)
                        item["values"] = {f: o for f, o in item.get("values", {}).items() if f != field["id"] or o in kept}
                    return {"data": {"updateProjectV2Field": {"projectV2Field": {"id": field["id"]}}}}
        return {"errors": [{"type": "NOT_FOUND", "message": "field not found"}]}

    def _set_options(self, field: Dict[str, Any], options: List[Dict[str, Any]]) -> None:
        """An option input that carries an existing `id` keeps that id; others get fresh ids."""
        self.option_serial = getattr(self, "option_serial", 0)
        stored = []
        for o in options:
            self.option_serial += 1
            stored.append({
                "id": o.get("id") or f"opt-{field['id']}-{self.option_serial}",
                "name": o["name"], "color": o.get("color", "GRAY"), "description": o.get("description", ""),
            })
        field["options"] = stored

    def gql_CreateView(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        project = self.projects[variables["projectId"]]
        view = {
            "id": f"PVTV_{project['number']}_{len(project['views']) + 1}", "name": variables["name"],
            "layout": variables["layout"], "filter": None,
        }
        project["views"].append(view)
        return {"data": {"createProjectV2View": {"projectV2View": {"id": view["id"]}}}}

    def gql_UpdateView(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        for project in self.projects.values():
            for view in project["views"]:
                if view["id"] == variables["viewId"]:
                    for key in ("name", "layout", "filter"):
                        if variables.get(key) is not None:
                            view[key] = variables[key]
                    return {"data": {"updateProjectV2View": {"projectV2View": {"id": view["id"]}}}}
        return {"errors": [{"type": "NOT_FOUND", "message": "view not found"}]}

    def gql_ItemPlacement(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        """The issue's project items with the single-select values they hold (post-lag view)."""
        nodes = []
        for project in self.projects.values():
            item = project["items"].get(variables["contentId"])
            if item is None:
                continue
            values = []
            for field in project["fields"]:
                chosen = item.get("values", {}).get(field["id"])
                for option in field["options"]:
                    if option["id"] == chosen:
                        values.append({"name": option["name"], "field": {"name": field["name"]}})
            nodes.append({
                "id": item["id"], "project": {"number": project["number"]}, "fieldValues": {"nodes": values},
            })
        if self.lag > 0:
            self.lag -= 1
            nodes = []
        return {"data": {"node": {"projectItems": {"nodes": nodes}}}}

    def gql_ProjectCards(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        """The project's cards: items on issues of any repository, with their single-select values."""
        project = self.projects[variables["projectId"]]
        cards = []
        for content_id, item in project["items"].items():
            issue = next((i for i in self.issues.values() if i["node_id"] == content_id), None)
            number = item.get("number", issue["number"] if issue else 0)
            values = []
            for field in project["fields"]:
                chosen = item.get("values", {}).get(field["id"])
                for option in field["options"]:
                    if option["id"] == chosen:
                        values.append({"name": option["name"], "field": {"name": field["name"]}})
            cards.append({
                "id": item["id"],
                "content": {"number": number, "repository": {"nameWithOwner": item.get("repo", f"{OWNER}/{REPO}")}},
                "fieldValues": {"nodes": values},
            })
        start = int(variables["cursor"] or 0)
        size = self.cards_page_size
        window = cards[start:start + size]
        more = self.cards_endless or start + size < len(cards)
        next_start = start + size - (1 if self.cards_overlap and start + size < len(cards) else 0)
        return {"data": {"node": {"items": {
            "nodes": window, "pageInfo": {"hasNextPage": more, "endCursor": str(next_start)},
        }}}}

    def gql_DeleteItem(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        project = self.projects[variables["projectId"]]
        for key, item in list(project["items"].items()):
            if item["id"] == variables["itemId"]:
                del project["items"][key]
        return {"data": {"deleteProjectV2Item": {"deletedItemId": variables["itemId"]}}}

    def gql_AddItem(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        project = self.projects[variables["projectId"]]
        if self.strict_add and variables["contentId"] in project["items"]:
            return {"errors": [{"type": "UNPROCESSABLE", "message": "Content already exists in this project"}]}
        item = project["items"].setdefault(variables["contentId"], {"id": f"PVTI_{len(project['items']) + 1}"})
        return {"data": {"addProjectV2ItemById": {"item": {"id": item["id"]}}}}

    def gql_SetStatus(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        project = self.projects.get(variables["projectId"])
        if project is None:
            return {"errors": [{"type": "NOT_FOUND", "message": "Could not resolve to a node with the global id"}]}
        field = next((f for f in project["fields"] if f["id"] == variables["fieldId"]), None)
        if field is None or variables["optionId"] not in {o.get("id") for o in field["options"]}:
            return {"errors": [{"type": "NOT_FOUND", "message": "The field or option does not exist on this project"}]}
        for item in project["items"].values():
            if item["id"] == variables["itemId"]:
                item["field"] = variables["fieldId"]
                item["option"] = variables["optionId"]
                item.setdefault("values", {})[variables["fieldId"]] = variables["optionId"]
                return {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": item["id"]}}}}
        return {"errors": [{"type": "NOT_FOUND", "message": "item not found"}]}

    def gql_SetCard(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        """Status and Priority of one card in a single request (two aliased updateProjectV2ItemFieldValue fields)."""
        for field, option in (("statusField", "statusOption"), ("priorityField", "priorityOption")):
            answer = self.gql_SetStatus({"projectId": variables["projectId"], "itemId": variables["itemId"],
                                         "fieldId": variables[field], "optionId": variables[option]})
            if "errors" in answer:
                return answer
        return {"data": {"status": {"projectV2Item": {"id": variables["itemId"]}}, "priority": {"projectV2Item": {"id": variables["itemId"]}}}}

    def gql_EnableAutoMerge(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        for pull in self.pulls.values():
            if pull["node_id"] == variables["id"]:
                if self.auto_merge_ignored:
                    return {"data": {"enablePullRequestAutoMerge": {"pullRequest": {"id": pull["node_id"]}}}}
                pull["auto_merge"] = True
                return {"data": {"enablePullRequestAutoMerge": {"pullRequest": {"id": pull["node_id"]}}}}
        return {"errors": [{"type": "NOT_FOUND", "message": "pull request not found"}]}

    def gql_MarkReady(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        for pull in self.pulls.values():
            if pull["node_id"] == variables["id"]:
                pull["draft"] = False
                return {"data": {"markPullRequestReadyForReview": {"pullRequest": {"id": pull["node_id"]}}}}
        return {"errors": [{"type": "NOT_FOUND", "message": "pull request not found"}]}

    def project(self, number: str) -> Dict[str, Any]:
        return next(p for p in self.projects.values() if str(p["number"]) == number)

    def item_value(self, number: str, issue_node_id: str, field_name: str) -> Optional[str]:
        """The option name the issue holds in the named single-select field, or None."""
        project = self.project(number)
        item = project["items"].get(issue_node_id)
        if item is None:
            return None
        for field in project["fields"]:
            if field["name"] != field_name:
                continue
            chosen = item.get("values", {}).get(field["id"])
            return next((o["name"] for o in field["options"] if o["id"] == chosen), None)
        return None

    def item_status(self, number: str, issue_node_id: str) -> Optional[str]:
        """The option name the issue holds in the project's Status field, or None."""
        project = self.project(number)
        item = project["items"].get(issue_node_id)
        if item is None or "option" not in item:
            return None
        for field in project["fields"]:
            for option in field["options"]:
                if option["id"] == item["option"]:
                    return option["name"]
        return None


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeGitHub"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass

    @property
    def fake(self) -> FakeGitHub:
        return self.server.fake  # type: ignore[attr-defined,no-any-return]

    def do_GET(self) -> None:
        self._serve("GET")

    def do_POST(self) -> None:
        self._serve("POST")

    def do_PATCH(self) -> None:
        self._serve("PATCH")

    def do_PUT(self) -> None:
        self._serve("PUT")

    def do_DELETE(self) -> None:
        self._serve("DELETE")

    def _serve(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        url = urlsplit(self.path)
        self.fake.requests.append((method, self.path, body))
        self.fake.request_times.append(time.time())
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"message": "Bad credentials"})
        injected = self._injected()
        if injected is not None:
            if injected.get("after"):
                real, self._send = self._send, lambda *a, **k: None
                try:
                    self._rest(method, url.path, parse_qs(url.query), body)
                finally:
                    self._send = real
            return self._send(injected["status"], injected["body"] or {"message": "injected"},
                              headers=injected.get("headers", {}))
        if url.path == GRAPHQL_PATH and method == "POST":
            return self._send(200, self.fake.graphql(body["query"], body.get("variables", {})))
        self._rest(method, url.path, parse_qs(url.query), body)

    def _injected(self) -> Optional[Dict[str, Any]]:
        for failure in self.fake.failures:
            if failure["left"] > 0 and failure["part"] in self.path:
                if failure.get("skip", 0) > 0:
                    failure["skip"] -= 1
                    return None
                failure["left"] -= 1
                return failure
        return None

    def _rest(self, method: str, path: str, query: Dict[str, List[str]], body: Any) -> None:
        prefix = f"{API_PREFIX}/repos/{OWNER}/{REPO}"
        if not path.startswith(prefix):
            return self._send(404, {"message": "Not Found"})
        route = path[len(prefix):]
        for pattern, name in _ROUTES:
            found = re.fullmatch(pattern, route)
            if found:
                handler = getattr(self, f"_{method.lower()}_{name}", None)
                if handler is None:
                    return self._send(405, {"message": "Method Not Allowed"})
                return handler(*found.groups(), query=query, body=body)
        self._send(404, {"message": "Not Found"})

    def _send(self, status: int, payload: Any, link: str = "", headers: Optional[Dict[str, str]] = None) -> None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if link:
            self.send_header("Link", link)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _page(self, items: List[Any], query: Dict[str, List[str]], path: str) -> Tuple[List[Any], str]:
        page = int(query.get("page", ["1"])[0])
        size = self.fake.page_size
        window = items[(page - 1) * size: page * size]
        link = ""
        if page * size < len(items):
            kept = {k: v[0] for k, v in query.items() if k != "page"}
            link = f'<{self.fake.base}{path}?{urlencode({**kept, "page": page + 1})}>; rel="next"'
        return window, link

    # -- issues ------------------------------------------------------------

    def _get_issues(self, query: Dict[str, List[str]], body: Any) -> None:
        # Like the real API: newest first unless direction=asc, and `state` filters (default open).
        state = query.get("state", ["open"])[0]
        ordered = sorted(self.fake.issues.values(), key=lambda i: i["number"], reverse=query.get("direction", ["desc"])[0] != "asc")
        since = query.get("since", [""])[0]
        issues = [self.fake.issue_json(i) | ({"pull_request": {}} if "pull_request" in i else {})
                  for i in ordered if (state == "all" or i["state"] == state) and i.get("updated_at", "") >= since]
        window, link = self._page(issues, query, f"{API_PREFIX}/repos/{OWNER}/{REPO}/issues")
        if not self.fake.etags:
            return self._send(200, window, link)
        etag = '"' + hashlib.sha1(json.dumps(window, sort_keys=True).encode("utf-8")).hexdigest() + '"'
        if self.headers.get("If-None-Match") == etag:
            return self._send(304, b"", link, {"ETag": etag})
        self._send(200, window, link, {"ETag": etag})

    def _get_labels(self, query: Dict[str, List[str]], body: Any) -> None:
        listed = [{"name": n, **v} for n, v in self.fake.label_store.items()]
        window, link = self._page(listed, query, f"{API_PREFIX}/repos/{OWNER}/{REPO}/labels")
        self._send(200, window, link)

    def _delete_label(self, name: str, query: Dict[str, List[str]], body: Any) -> None:
        if self.fake.label_store.pop(unquote(name), None) is None:
            return self._send(404, {"message": "Not Found"})
        for issue in self.fake.issues.values():
            issue["labels"] = [x for x in issue["labels"] if x != unquote(name)]
        self._send(204, b"")

    def _patch_label(self, name: str, query: Dict[str, List[str]], body: Any) -> None:
        stored = self.fake.label_store.get(unquote(name))
        if stored is None:
            return self._send(404, {"message": "Not Found"})
        stored.update({key: body[key] for key in ("color", "description") if key in body})
        self._send(200, {"name": unquote(name), **stored})

    def _post_labels(self, query: Dict[str, List[str]], body: Any) -> None:
        if body["name"] in self.fake.label_store:
            return self._send(422, {"message": "already_exists"})
        if not self.fake.drop_label_creates:
            self.fake.label_store[body["name"]] = {"color": body.get("color", ""), "description": body.get("description", "")}
        self._send(201, {"name": body["name"]})

    def _get_milestones(self, query: Dict[str, List[str]], body: Any) -> None:
        listed = [self.fake.milestone_json(n) for n in sorted(self.fake.milestones)]
        window, link = self._page(listed, query, f"{API_PREFIX}/repos/{OWNER}/{REPO}/milestones")
        self._send(200, window, link)

    def _post_milestones(self, query: Dict[str, List[str]], body: Any) -> None:
        number = len(self.fake.milestones) + 1
        self.fake.milestones[number] = {"title": body["title"], "description": body.get("description", ""),
                                        "due_on": body.get("due_on"), "state": "open"}
        self._send(201, self.fake.milestone_json(number))

    def _get_milestone(self, number: str, query: Dict[str, List[str]], body: Any) -> None:
        if int(number) not in self.fake.milestones:
            return self._send(404, {"message": "Not Found"})
        self._send(200, self.fake.milestone_json(int(number)))

    def _patch_milestone(self, number: str, query: Dict[str, List[str]], body: Any) -> None:
        found = self.fake.milestones.get(int(number))
        if found is None:
            return self._send(404, {"message": "Not Found"})
        for key in ("title", "description", "due_on", "state"):
            if key in body:
                found[key] = body[key]
        self._send(200, self.fake.milestone_json(int(number)))

    def _post_issues(self, query: Dict[str, List[str]], body: Any) -> None:
        self._send(201, self.fake.issue_json(self.fake.new_issue(body)))

    def _get_issue(self, number: str, query: Dict[str, List[str]], body: Any) -> None:
        issue = self.fake.issues.get(int(number))
        self._send(404, {"message": "Not Found"}) if issue is None else self._send(200, self.fake.issue_json(issue))

    def _patch_issue(self, number: str, query: Dict[str, List[str]], body: Any) -> None:
        issue = self.fake.issues.get(int(number))
        if issue is None:
            return self._send(404, {"message": "Not Found"})
        if (issue["state"] == "closed" and body.get("state") == "closed" and body.get("state_reason")
                and body["state_reason"] != issue["state_reason"]):  # real GitHub refuses to switch the reason of a closed issue
            return self._send(422, {"message": "Validation Failed", "errors": [
                {"resource": "Issue", "code": "custom", "field": "state_reason", "message": "state_reason Cannot transition to this state"}]})
        for key in ("title", "body", "labels", "state", "state_reason", "milestone"):
            if key in body:
                issue[key] = body[key]
        if "assignees" in body:
            issue["assignee"] = body["assignees"][0] if body["assignees"] else None
        self.fake.touch(issue)
        self._send(200, self.fake.issue_json(issue))

    def _get_comments(self, number: str, query: Dict[str, List[str]], body: Any) -> None:
        issue = self.fake.issues.get(int(number))
        if issue is None:
            return self._send(404, {"message": "Not Found"})
        comments = [self.fake.comment_json(c) for c in issue["discussion"]]
        window, link = self._page(comments, query, f"{API_PREFIX}/repos/{OWNER}/{REPO}/issues/{number}/comments")
        self._send(200, window, link)

    def _post_comments(self, number: str, query: Dict[str, List[str]], body: Any) -> None:
        if int(number) not in self.fake.issues:
            return self._send(404, {"message": "Not Found"})
        self._send(201, self.fake.comment_json(self.fake.add_comment(int(number), "qew-bot", body["body"])))

    def _get_blocked_by(self, number: str, query: Dict[str, List[str]], body: Any) -> None:
        issue = self.fake.issues.get(int(number))
        if issue is None:
            return self._send(404, {"message": "Not Found"})
        blockers = [self.fake.issue_json(self.fake.issues[n]) for n in issue["blocked_by"]]
        window, link = self._page(blockers, query, f"{API_PREFIX}/repos/{OWNER}/{REPO}/issues/{number}/dependencies/blocked_by")
        self._send(200, window, link)

    def _post_blocked_by(self, number: str, query: Dict[str, List[str]], body: Any) -> None:
        issue = self.fake.issues.get(int(number))
        blocker = next((i for i in self.fake.issues.values() if i["id"] == body.get("issue_id")), None)
        if issue is None or blocker is None:
            return self._send(404, {"message": "Not Found"})
        if blocker["number"] in issue["blocked_by"]:
            return self._send(422, {"message": "Validation Failed: dependency already exists"})
        issue["blocked_by"].append(blocker["number"])
        self._send(201, self.fake.issue_json(blocker))

    # -- pull requests -----------------------------------------------------------

    def _get_repo(self, query: Dict[str, List[str]], body: Any) -> None:
        self._send(200, {"name": REPO, "default_branch": self.fake.default_branch})

    def _get_branches(self, query: Dict[str, List[str]], body: Any) -> None:
        listed = [{"name": n, "protected": n in self.fake.protection, "commit": {"sha": sha}} for n, sha in self.fake.branches.items()]
        window, link = self._page(listed, query, f"{API_PREFIX}/repos/{OWNER}/{REPO}/branches")
        self._send(200, window, link)

    def _delete_ref(self, name: str, query: Dict[str, List[str]], body: Any) -> None:
        name = unquote(name)
        if name not in self.fake.branches:
            return self._send(422, {"message": "Reference does not exist"})
        if name in self.fake.protection or name == self.fake.default_branch:
            return self._send(403, {"message": "Cannot delete a protected branch"})
        del self.fake.branches[name]
        self._send(204, b"")

    def _get_compare(self, spec: str, query: Dict[str, List[str]], body: Any) -> None:
        base, _, head = unquote(spec).partition("...")
        if base not in self.fake.branches or head not in self.fake.branches:
            return self._send(404, {"message": "Not Found"})
        merged = head == base or base in self.fake.contained_in.get(head, [])
        self._send(200, {"status": "behind" if merged else "ahead", "ahead_by": 0 if merged else 1, "behind_by": 0})

    def _get_pulls(self, query: Dict[str, List[str]], body: Any) -> None:
        wanted = query.get("state", ["open"])[0]
        listed = [self.fake.pull_json(p) for _n, p in sorted(self.fake.pulls.items()) if wanted == "all" or p["state"] == wanted]
        window, link = self._page(listed, query, f"{API_PREFIX}/repos/{OWNER}/{REPO}/pulls")
        self._send(200, window, link)

    def _post_pulls(self, query: Dict[str, List[str]], body: Any) -> None:
        self._send(201, self.fake.pull_json(self.fake.new_pull(body)))

    def _get_pull(self, number: str, query: Dict[str, List[str]], body: Any) -> None:
        pull = self.fake.pulls.get(int(number))
        self._send(404, {"message": "Not Found"}) if pull is None else self._send(200, self.fake.pull_json(pull))

    def _put_merge(self, number: str, query: Dict[str, List[str]], body: Any) -> None:
        pull = self.fake.pulls.get(int(number))
        if pull is None:
            return self._send(404, {"message": "Not Found"})
        if pull["draft"]:
            return self._send(405, {"message": "Pull Request is still a draft"})
        if body.get("sha") != pull["head"]["sha"]:
            return self._send(409, {"message": "Head branch was modified. Review and try the merge again."})
        pull["merged"] = True
        pull["state"] = "closed"
        pull["merge_sha"] = "f" * 40
        self._send(200, {"sha": "f" * 40, "merged": True, "message": "Pull Request successfully merged"})

    def _patch_pull(self, number: str, query: Dict[str, List[str]], body: Any) -> None:
        pull = self.fake.pulls.get(int(number))
        if pull is None:
            return self._send(404, {"message": "Not Found"})
        for key in ("title", "body"):
            if key in body:
                pull[key] = body[key]
                self.fake.issues[pull["number"]][key] = body[key]
                self.fake.touch(self.fake.issues[pull["number"]])
        if body.get("state") in ("open", "closed") and not pull["merged"]:
            pull["state"] = body["state"]
            self.fake.issues[pull["number"]]["state"] = body["state"]
            self.fake.touch(self.fake.issues[pull["number"]])
        self._send(200, self.fake.pull_json(pull))

    def _get_rules(self, branch: str, query: Dict[str, List[str]], body: Any) -> None:
        self._send(200, self.fake.rules.get(branch, []))

    def _get_protection(self, branch: str, query: Dict[str, List[str]], body: Any) -> None:
        record = self.fake.protection.get(branch)
        if record is None:
            return self._send(404, {"message": "Branch not protected"})
        self._send(200, record)

    def _get_status(self, sha: str, query: Dict[str, List[str]], body: Any) -> None:
        status = self.fake.statuses.get(sha, {"state": "pending", "total_count": 0})
        self._send(200, status)

    def _get_check_runs(self, sha: str, query: Dict[str, List[str]], body: Any) -> None:
        runs = self.fake.check_runs.get(sha, [])
        if self.fake.endless_check_runs:
            page = int(query.get("page", ["1"])[0])
            link = f'<{self.fake.base}{API_PREFIX}/repos/{OWNER}/{REPO}/commits/{sha}/check-runs?per_page=100&page={page + 1}>; rel="next"'
            return self._send(200, {"total_count": 1, "check_runs": [{"status": "completed", "conclusion": "success"}]}, link)
        window, link = self._page(runs, query, f"{API_PREFIX}/repos/{OWNER}/{REPO}/commits/{sha}/check-runs")
        self._send(200, {"total_count": len(runs), "check_runs": window}, link)


_ROUTES = (
    (r"", "repo"),
    (r"/branches", "branches"),
    (r"/git/refs/heads/(.+)", "ref"),
    (r"/compare/(.+)", "compare"),
    (r"/labels", "labels"),
    (r"/labels/(.+)", "label"),
    (r"/milestones", "milestones"),
    (r"/milestones/(\d+)", "milestone"),
    (r"/issues", "issues"),
    (r"/issues/(\d+)", "issue"),
    (r"/issues/(\d+)/comments", "comments"),
    (r"/issues/(\d+)/dependencies/blocked_by", "blocked_by"),
    (r"/pulls", "pulls"),
    (r"/pulls/(\d+)", "pull"),
    (r"/pulls/(\d+)/merge", "merge"),
    (r"/rules/branches/([\w.-]+)", "rules"),
    (r"/branches/([\w.-]+)/protection", "protection"),
    (r"/commits/([0-9a-f]+)/status", "status"),
    (r"/commits/([0-9a-f]+)/check-runs", "check_runs"),
)
