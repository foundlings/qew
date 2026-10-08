"""An in-memory stand-in for GitHub's REST v3 and GraphQL wire format on 127.0.0.1.

Only the endpoints `qew.forge_github` uses. State lives in plain dicts, every
request is logged, and `fail_next` injects a status for the next matching call.
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

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
        self.failures: List[Dict[str, Any]] = []
        self.next_number = 1
        self.next_id = 1000
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

    def fail_next(self, status: int, path_part: str = "", count: int = 1, body: Optional[Any] = None) -> None:
        self.failures.append({"status": status, "part": path_part, "left": count, "body": body})

    def calls(self, method: str, path_part: str) -> List[Any]:
        return [body for m, path, body in self.requests if m == method and path_part in path]

    # -- test-side mutation of the world ------------------------------------

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
            "assignee": (payload.get("assignees") or [None])[0], "blocked_by": [],
        }
        self.issues[number] = issue
        return issue

    def issue_json(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": issue["id"], "node_id": issue["node_id"], "number": issue["number"],
            "html_url": f"{self.base}/{OWNER}/{REPO}/issues/{issue['number']}",
            "state": issue["state"], "state_reason": issue["state_reason"], "title": issue["title"],
            "body": issue["body"], "labels": [{"name": name} for name in issue["labels"]],
            "assignee": None if not issue["assignee"] else {"login": issue["assignee"]},
        }

    def pull_json(self, pull: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "number": pull["number"], "node_id": pull["node_id"],
            "html_url": f"{self.base}/{OWNER}/{REPO}/pull/{pull['number']}",
            "state": pull["state"], "merged": pull["merged"], "draft": pull["draft"],
            "mergeable": pull["mergeable"], "title": pull["title"], "body": pull["body"],
            "head": dict(pull["head"]), "base": {"ref": pull["base"]},
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
            "assignee": None, "blocked_by": [], "pull_request": {},
        }
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
                "options": [{"id": o["id"], "name": o["name"]} for o in field["options"]],
            })
        return {
            "id": project["id"], "number": project["number"], "title": project["title"],
            "url": f"{self.base}/orgs/{OWNER}/projects/{project['number']}",
            "fields": {"nodes": fields},
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
            "fields": [{
                "id": f"PVTSSF_{number}", "name": "Status",
                "options": [{"id": f"default-{n}", "name": n} for n in ("Todo", "In Progress", "Done")],
            }],
        }
        self.projects[project["id"]] = project
        return {"data": {"createProjectV2": {"projectV2": self.project_json(project)}}}

    def gql_ProjectFields(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        project = self.projects.get(variables["id"])
        return {"data": {"node": None if project is None else self.project_json(project)}}

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
                    project["items"] = {}  # real GitHub drops values of replaced options
                    return {"data": {"updateProjectV2Field": {"projectV2Field": {"id": field["id"]}}}}
        return {"errors": [{"type": "NOT_FOUND", "message": "field not found"}]}

    def _set_options(self, field: Dict[str, Any], options: List[Dict[str, Any]]) -> None:
        field["options"] = [{"id": f"opt-{field['id']}-{n}", "name": o["name"]} for n, o in enumerate(options)]

    def gql_AddItem(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        project = self.projects[variables["projectId"]]
        item = project["items"].setdefault(variables["contentId"], {"id": f"PVTI_{len(project['items']) + 1}"})
        return {"data": {"addProjectV2ItemById": {"item": {"id": item["id"]}}}}

    def gql_SetStatus(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        project = self.projects[variables["projectId"]]
        for item in project["items"].values():
            if item["id"] == variables["itemId"]:
                item["field"] = variables["fieldId"]
                item["option"] = variables["optionId"]
                return {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": item["id"]}}}}
        return {"errors": [{"type": "NOT_FOUND", "message": "item not found"}]}

    def gql_MarkReady(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        for pull in self.pulls.values():
            if pull["node_id"] == variables["id"]:
                pull["draft"] = False
                return {"data": {"markPullRequestReadyForReview": {"pullRequest": {"id": pull["node_id"]}}}}
        return {"errors": [{"type": "NOT_FOUND", "message": "pull request not found"}]}

    def item_status(self, project_id: str, issue_node_id: str) -> Optional[str]:
        """The option name the issue holds in the project's Status field, or None."""
        project = self.projects[project_id]
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

    def _serve(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        url = urlsplit(self.path)
        self.fake.requests.append((method, self.path, body))
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"message": "Bad credentials"})
        injected = self._injected()
        if injected is not None:
            return self._send(injected["status"], injected["body"] or {"message": "injected"})
        if url.path == GRAPHQL_PATH and method == "POST":
            return self._send(200, self.fake.graphql(body["query"], body.get("variables", {})))
        self._rest(method, url.path, parse_qs(url.query), body)

    def _injected(self) -> Optional[Dict[str, Any]]:
        for failure in self.fake.failures:
            if failure["left"] > 0 and failure["part"] in self.path:
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

    def _send(self, status: int, payload: Any, link: str = "") -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if link:
            self.send_header("Link", link)
        self.end_headers()
        self.wfile.write(data)

    def _page(self, items: List[Any], query: Dict[str, List[str]], path: str) -> Tuple[List[Any], str]:
        page = int(query.get("page", ["1"])[0])
        size = self.fake.page_size
        window = items[(page - 1) * size: page * size]
        link = ""
        if page * size < len(items):
            link = f'<{self.fake.base}{path}?per_page=100&page={page + 1}>; rel="next"'
        return window, link

    # -- issues ------------------------------------------------------------

    def _get_issues(self, query: Dict[str, List[str]], body: Any) -> None:
        issues = [self.fake.issue_json(i) | ({"pull_request": {}} if "pull_request" in i else {})
                  for i in self.fake.issues.values()]
        window, link = self._page(issues, query, f"{API_PREFIX}/repos/{OWNER}/{REPO}/issues")
        self._send(200, window, link)

    def _post_issues(self, query: Dict[str, List[str]], body: Any) -> None:
        self._send(201, self.fake.issue_json(self.fake.new_issue(body)))

    def _get_issue(self, number: str, query: Dict[str, List[str]], body: Any) -> None:
        issue = self.fake.issues.get(int(number))
        self._send(404, {"message": "Not Found"}) if issue is None else self._send(200, self.fake.issue_json(issue))

    def _patch_issue(self, number: str, query: Dict[str, List[str]], body: Any) -> None:
        issue = self.fake.issues.get(int(number))
        if issue is None:
            return self._send(404, {"message": "Not Found"})
        for key in ("title", "body", "labels", "state", "state_reason"):
            if key in body:
                issue[key] = body[key]
        if "assignees" in body:
            issue["assignee"] = body["assignees"][0] if body["assignees"] else None
        self._send(200, self.fake.issue_json(issue))

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
        self._send(200, {"sha": "f" * 40, "merged": True, "message": "Pull Request successfully merged"})

    def _get_status(self, sha: str, query: Dict[str, List[str]], body: Any) -> None:
        status = self.fake.statuses.get(sha, {"state": "pending", "total_count": 0})
        self._send(200, status)

    def _get_check_runs(self, sha: str, query: Dict[str, List[str]], body: Any) -> None:
        runs = self.fake.check_runs.get(sha, [])
        window, link = self._page(runs, query, f"{API_PREFIX}/repos/{OWNER}/{REPO}/commits/{sha}/check-runs")
        self._send(200, {"total_count": len(runs), "check_runs": window}, link)


_ROUTES = (
    (r"/issues", "issues"),
    (r"/issues/(\d+)", "issue"),
    (r"/issues/(\d+)/dependencies/blocked_by", "blocked_by"),
    (r"/pulls", "pulls"),
    (r"/pulls/(\d+)", "pull"),
    (r"/pulls/(\d+)/merge", "merge"),
    (r"/commits/([0-9a-f]+)/status", "status"),
    (r"/commits/([0-9a-f]+)/check-runs", "check_runs"),
)
