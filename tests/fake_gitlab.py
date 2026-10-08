"""A stdlib fake of GitLab's REST /api/v4 wire format, for exactly the endpoints the backend uses.

In-memory state, one thread on 127.0.0.1, a request log and failure injection. It mimics
GitLab's real shapes: `state` is "opened"/"closed", an empty description is `null`, scoped
labels are created on first use, pagination answers `X-Next-Page`, and a duplicate issue
link answers 409.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

TOKEN = "glpat-fake"


class _Project:
    def __init__(self) -> None:
        self.issues: List[Dict[str, Any]] = []
        self.links: List[Tuple[int, int, str]] = []
        self.labels: List[Dict[str, Any]] = []
        self.boards: List[Dict[str, Any]] = []
        self.merge_requests: List[Dict[str, Any]] = []


class FakeGitLab:
    def __init__(self, token: str = TOKEN, page_size: int = 100) -> None:
        self.token = token
        self.page_size = page_size
        self.endless_pages = False
        self.search_returns_all = False
        self.malformed_issue_reads = False
        self.users = {"alice": 11, "bob": 12}
        self.log: List[Dict[str, Any]] = []
        self.projects: Dict[str, _Project] = {}
        self._failures: List[Tuple[int, str]] = []
        self._ids = 1000
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self._thread = threading.Thread(target=lambda: self._server.serve_forever(poll_interval=0.02), daemon=True)

    def start(self) -> "FakeGitLab":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def host(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def api(self) -> str:
        return f"{self.host}/api/v4"

    def project(self, path: str) -> _Project:
        return self.projects.setdefault(path, _Project())

    def fail_next(self, status: int, match: str = "", count: int = 1) -> None:
        self._failures.extend([(status, match)] * count)

    def requests(self, method: str, suffix: str) -> List[Dict[str, Any]]:
        return [r for r in self.log if r["method"] == method and r["path"].endswith(suffix)]

    def push_head(self, path: str, iid: int) -> None:
        mr = self._merge_request(path, iid)
        mr["sha"] = self._sha()

    def set_pipeline(self, path: str, iid: int, status: Optional[str]) -> None:
        self._merge_request(path, iid)["head_pipeline"] = None if status is None else {"status": status}

    def set_merge_status(self, path: str, iid: int, status: str) -> None:
        self._merge_request(path, iid)["detailed_merge_status"] = status

    def _merge_request(self, path: str, iid: int) -> Dict[str, Any]:
        return next(m for m in self.project(path).merge_requests if m["iid"] == iid)

    def _next_id(self) -> int:
        with self._lock:
            self._ids += 1
            return self._ids

    def _sha(self) -> str:
        return f"{self._next_id():040x}"

    # ----- dispatch -----

    def handle(self, method: str, raw_path: str, headers: Dict[str, str], raw_body: bytes) -> Tuple[int, Any, Dict[str, str]]:
        split = urlsplit(raw_path)
        parts = [unquote(p) for p in split.path.split("/") if p]
        query = {k: v[0] for k, v in parse_qs(split.query).items()}
        body = json.loads(raw_body) if raw_body else None
        self.log.append({
            "method": method, "path": "/" + "/".join(parts), "raw_path": split.path,
            "query": query, "body": body, "headers": headers,
        })
        if headers.get("private-token") != self.token:
            return 401, {"message": "401 Unauthorized"}, {}
        for index, (status, match) in enumerate(self._failures):
            if match in raw_path:
                del self._failures[index]
                return status, {"message": f"injected {status}"}, {}
        if parts[:2] != ["api", "v4"]:
            return 404, {"message": "404 Not Found"}, {}
        rest = parts[2:]
        if rest[:1] == ["users"] and method == "GET":
            return self._users(query)
        if rest[:1] == ["projects"] and len(rest) >= 3:
            return self._project(method, self.project(rest[1]), rest[1], rest[2:], query, body)
        return 404, {"message": "404 Not Found"}, {}

    def _project(self, method, project, path, rest, query, body):
        resource, tail = rest[0], rest[1:]
        if resource == "issues":
            return self._issues(method, project, path, tail, query, body)
        if resource == "labels":
            return self._labels(method, project, query, body)
        if resource == "boards":
            return self._boards(method, project, tail, query, body)
        if resource == "merge_requests":
            return self._merge_requests(method, project, path, tail, body)
        return 404, {"message": "404 Not Found"}, {}

    def _users(self, query):
        name = query.get("username", "")
        return 200, [{"id": i, "username": n} for n, i in self.users.items() if n == name], {}

    # ----- pagination -----

    def _page(self, items: List[Any], query: Dict[str, str]):
        size = min(int(query.get("per_page", "20")), self.page_size)
        page = int(query.get("page", "1"))
        chunk = items[(page - 1) * size : page * size]
        more = page * size < len(items) or self.endless_pages
        return 200, chunk, {"X-Next-Page": str(page + 1) if more else ""}

    # ----- issues -----

    def _issue_json(self, path: str, issue: Dict[str, Any]) -> Dict[str, Any]:
        out = {
            "id": issue["id"], "iid": issue["iid"], "title": issue["title"],
            "description": issue["description"] or None,
            "state": issue["state"], "labels": list(issue["labels"]),
            "assignees": [{"id": a, "username": n} for n, a in self.users.items() if a in issue["assignees"]],
            "web_url": f"{self.host}/{path}/-/issues/{issue['iid']}",
        }
        if self.malformed_issue_reads:
            del out["iid"]
        return out

    def _issues(self, method, project, path, tail, query, body):
        if not tail:
            if method == "GET":
                return self._issue_list(project, path, query)
            if method == "POST":
                issue = {"id": self._next_id(), "iid": len(project.issues) + 1, "state": "opened",
                         "title": body["title"], "description": body.get("description", ""),
                         "labels": [], "assignees": body.get("assignee_ids", [])}
                self._set_labels(project, issue, body.get("labels", ""))
                project.issues.append(issue)
                return 201, self._issue_json(path, issue), {}
        issue = next((i for i in project.issues if str(i["iid"]) == tail[0]), None)
        if issue is None:
            return 404, {"message": "404 Not found"}, {}
        if len(tail) == 1 and method == "GET":
            return 200, self._issue_json(path, issue), {}
        if len(tail) == 1 and method == "PUT":
            self._apply_issue_update(project, issue, body)
            return 200, self._issue_json(path, issue), {}
        if tail[1:] == ["links"] and method == "POST":
            return self._link(project, issue, body)
        return 404, {"message": "404 Not Found"}, {}

    def _issue_list(self, project, path, query):
        issues = sorted(project.issues, key=lambda i: -i["iid"])
        if query.get("state", "all") != "all":
            issues = [i for i in issues if i["state"] == query["state"]]
        needle = query.get("search")
        if needle is not None and not self.search_returns_all:
            fields = query.get("in", "title,description").split(",")
            issues = [i for i in issues if any(needle in (i[f] or "") for f in fields)]
        status, chunk, headers = self._page(issues, query)
        return status, [self._issue_json(path, i) for i in chunk], headers

    def _apply_issue_update(self, project, issue, body):
        for key in ("title", "description"):
            if key in body:
                issue[key] = body[key]
        if "labels" in body:
            self._set_labels(project, issue, body["labels"])
        if "add_labels" in body:
            self._set_labels(project, issue, ",".join([*issue["labels"], *body["add_labels"].split(",")]))
        if "remove_labels" in body:
            drop = set(body["remove_labels"].split(","))
            issue["labels"] = [x for x in issue["labels"] if x not in drop]
        if "assignee_ids" in body:
            issue["assignees"] = list(body["assignee_ids"])
        if body.get("state_event") == "close":
            issue["state"] = "closed"
        if body.get("state_event") == "reopen":
            issue["state"] = "opened"

    def _set_labels(self, project, issue, csv: str) -> None:
        names = list(dict.fromkeys(n for n in csv.split(",") if n))
        for name in names:
            self._ensure_label(project, name)
        issue["labels"] = names

    def _ensure_label(self, project, name: str) -> Dict[str, Any]:
        found = next((x for x in project.labels if x["name"] == name), None)
        if found is None:
            found = {"id": self._next_id(), "name": name, "color": "#428BCA"}
            project.labels.append(found)
        return found

    def _link(self, project, issue, body):
        target = next((i for i in project.issues if i["iid"] == body["target_issue_iid"]), None)
        if target is None:
            return 404, {"message": "404 Not found"}, {}
        entry = (issue["iid"], target["iid"], body["link_type"])
        if entry in project.links:
            return 409, {"message": "Issue(s) already assigned"}, {}
        project.links.append(entry)
        return 201, {"link_type": body["link_type"]}, {}

    # ----- labels and boards -----

    def _labels(self, method, project, query, body):
        if method == "GET":
            return self._page(sorted(project.labels, key=lambda x: x["id"]), query)
        if any(x["name"] == body["name"] for x in project.labels):
            return 409, {"message": "Label already exists"}, {}
        return 201, self._ensure_label(project, body["name"]), {}

    def _board_json(self, board):
        return {"id": board["id"], "name": board["name"], "lists": board["lists"]}

    def _boards(self, method, project, tail, query, body):
        if not tail and method == "GET":
            return self._page([self._board_json(b) for b in project.boards], query)
        if not tail and method == "POST":
            board = {"id": self._next_id(), "name": body["name"], "lists": []}
            project.boards.append(board)
            return 201, self._board_json(board), {}
        board = next((b for b in project.boards if str(b["id"]) == tail[0]), None)
        if board is None or tail[1:] != ["lists"] or method != "POST":
            return 404, {"message": "404 Not Found"}, {}
        label = next((x for x in project.labels if x["id"] == body["label_id"]), None)
        if label is None:
            return 400, {"message": "400 Bad request - label not found"}, {}
        entry = {"id": self._next_id(), "label": label, "position": len(board["lists"])}
        board["lists"].append(entry)
        return 201, entry, {}

    # ----- merge requests -----

    def _mr_json(self, path: str, mr: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": mr["id"], "iid": mr["iid"], "state": mr["state"], "title": mr["title"],
            "draft": mr["title"].lower().startswith("draft:"), "sha": mr["sha"],
            "source_branch": mr["source_branch"], "target_branch": mr["target_branch"],
            "detailed_merge_status": mr["detailed_merge_status"], "head_pipeline": mr["head_pipeline"],
            "web_url": f"{self.host}/{path}/-/merge_requests/{mr['iid']}",
        }

    def _merge_requests(self, method, project, path, tail, body):
        if not tail and method == "POST":
            mr = {"id": self._next_id(), "iid": len(project.merge_requests) + 1, "state": "opened",
                  "title": body["title"], "source_branch": body["source_branch"],
                  "target_branch": body["target_branch"], "sha": self._sha(),
                  "detailed_merge_status": "mergeable", "head_pipeline": None}
            project.merge_requests.append(mr)
            return 201, self._mr_json(path, mr), {}
        mr = next((m for m in project.merge_requests if tail and str(m["iid"]) == tail[0]), None)
        if mr is None:
            return 404, {"message": "404 Not found"}, {}
        if len(tail) == 1 and method == "GET":
            return 200, self._mr_json(path, mr), {}
        if len(tail) == 1 and method == "PUT":
            mr["title"] = body.get("title", mr["title"])
            return 200, self._mr_json(path, mr), {}
        if tail[1:] == ["merge"] and method == "PUT":
            return self._merge(path, mr, body)
        return 404, {"message": "404 Not Found"}, {}

    def _merge(self, path, mr, body):
        if mr["state"] != "opened":
            return 406, {"message": "Branch cannot be merged"}, {}
        if mr["title"].lower().startswith("draft:") or mr["detailed_merge_status"] != "mergeable":
            return 405, {"message": "405 Method Not Allowed"}, {}
        if body.get("sha") != mr["sha"]:
            return 409, {"message": f"SHA does not match HEAD of source branch: {mr['sha']}"}, {}
        mr["state"] = "merged"
        return 200, self._mr_json(path, mr), {}


def _handler(fake: FakeGitLab):
    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            headers = {k.lower(): v for k, v in self.headers.items()}
            status, payload, extra = fake.handle(self.command, self.path, headers, self.rfile.read(length))
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for key, value in extra.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = _serve

        def log_message(self, *args: Any) -> None:
            return None

    return Handler
