"""An in-memory fake of the Gitea/Forgejo /api/v1 wire format, for exactly the endpoints qew uses."""

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

OWNER = "acme"
REPO = "widgets"
TOKEN = "fake-token"
_BASE = rf"^/api/v1/repos/{OWNER}/{REPO}"


class FakeForgejo:
    """Started on 127.0.0.1 in a thread. State is plain attributes the walks may inspect."""

    def __init__(self) -> None:
        self.issues: Dict[int, Dict[str, Any]] = {}
        self.labels: Dict[int, str] = {}
        self.pulls: Dict[int, Dict[str, Any]] = {}
        self.branches: Dict[str, str] = {}  # branch -> head sha
        self.statuses: Dict[str, Tuple[str, int]] = {}  # sha -> (combined state, total_count)
        self.dependencies: Dict[int, List[int]] = {}  # issue -> issues blocking it
        self.requests: List[Tuple[str, str, Optional[Any]]] = []
        self.max_limit = 50
        self.endless = False  # lists answer a full page forever, with a next link and no total header
        self.delay = 0.0  # seconds every request waits before it is served
        self.down = False  # every request answers 503
        self.fail: List[Tuple[int, str]] = []  # (status, body) consumed one per request
        self.raw_issue_body: Optional[bytes] = None  # when set, GET issue answers this verbatim
        self.token = TOKEN
        self._next_index = 1
        self._next_label = 1
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        self._thread = threading.Thread(target=lambda: self._server.serve_forever(poll_interval=0.01), daemon=True)

    @property
    def api(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/api/v1"

    @property
    def repo_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/{OWNER}/{REPO}"

    def start(self) -> "FakeForgejo":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def set_head(self, branch: str, sha: str) -> None:
        self.branches[branch] = sha
        for pull in self.pulls.values():
            if pull["head"]["ref"] == branch:
                pull["head"]["sha"] = sha

    def issue_labels(self, number: int) -> List[str]:
        return [self.labels[label_id] for label_id in self.issues[number]["label_ids"]]

    # -- state helpers used by the handler (called with the lock held) --

    def _index(self) -> int:
        number = self._next_index
        self._next_index += 1
        return number

    def _label_id(self, name: str) -> int:
        for label_id, existing in self.labels.items():
            if existing == name:
                return label_id
        label_id = self._next_label
        self._next_label += 1
        self.labels[label_id] = name
        return label_id

    def _issue_json(self, number: int) -> Dict[str, Any]:
        issue = self.issues[number]
        assignee = issue["assignee"]
        shaped: Dict[str, Any] = {
            "id": 1000 + number,
            "number": number,
            "html_url": f"{self.repo_url}/issues/{number}",
            "state": issue["state"],
            "title": issue["title"],
            "body": issue["body"],
            "labels": [{"id": i, "name": self.labels[i]} for i in issue["label_ids"]],
        }
        if assignee:  # Gitea omits the key when nobody is assigned
            shaped["assignee"] = {"id": 7, "login": assignee}
            shaped["assignees"] = [{"id": 7, "login": assignee}]
        return shaped

    def _pull_json(self, number: int) -> Dict[str, Any]:
        pull = self.pulls[number]
        return {
            "number": number,
            "html_url": f"{self.repo_url}/pulls/{number}",
            "state": pull["state"],
            "merged": pull["merged"],
            "title": pull["title"],
            "mergeable": pull["mergeable"],
            "head": dict(pull["head"]),
            "base": {"ref": pull["base"]},
        }


def _make_handler(fake: FakeForgejo) -> type:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            return

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
            if fake.delay:
                time.sleep(fake.delay)
            if fake.down:
                return self._send(503, b'{"message":"down"}')
            payload = json.loads(raw) if raw else None
            path, _, query = self.path.partition("?")
            params = dict(pair.split("=", 1) for pair in query.split("&") if "=" in pair)
            with fake._lock:
                fake.requests.append((method, self.path, payload))
                if fake.fail:
                    status, body = fake.fail.pop(0)
                    return self._send(status, body.encode("utf-8"))
                if self.headers.get("Authorization") != f"token {fake.token}":
                    return self._send(401, b'{"message":"token is required"}')
                try:
                    status, body, headers = _route(fake, method, path, _unquote(params), payload)
                except _Reply as reply:
                    status, body, headers = reply.status, reply.body, {}
            if isinstance(body, bytes):
                encoded = body
            else:
                encoded = b"" if body is None else json.dumps(body).encode("utf-8")
            self._send(status, encoded, headers)

        def _send(self, status: int, body: bytes, headers: Optional[Dict[str, str]] = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

    return Handler


class _Reply(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.body = json.dumps({"message": message}).encode("utf-8")


def _unquote(params: Dict[str, str]) -> Dict[str, str]:
    from urllib.parse import unquote_plus

    return {key: unquote_plus(value) for key, value in params.items()}


def _page(fake: FakeForgejo, items: List[Any], params: Dict[str, str]) -> Tuple[int, Any, Dict[str, str]]:
    page = int(params.get("page", "1"))
    limit = min(int(params.get("limit", "30")), fake.max_limit)
    if fake.endless:
        return 200, items[:limit], {"Link": f'<{fake.api}?page={page + 1}>; rel="next"'}
    chunk = items[(page - 1) * limit : page * limit]
    headers = {"X-Total-Count": str(len(items))}
    if page * limit < len(items):
        headers["Link"] = f'<{fake.api}?page={page + 1}>; rel="next"'
    return 200, chunk, headers


def _route(
    fake: FakeForgejo, method: str, path: str, params: Dict[str, str], payload: Any
) -> Tuple[int, Any, Dict[str, str]]:
    def ok(body: Any, status: int = 200) -> Tuple[int, Any, Dict[str, str]]:
        return status, body, {}

    if method == "GET" and re.match(_BASE + r"/labels$", path):
        items = [{"id": i, "name": n, "color": "ededed"} for i, n in fake.labels.items()]
        return _page(fake, items, params)
    if method == "POST" and re.match(_BASE + r"/labels$", path):
        if any(name == payload["name"] for name in fake.labels.values()):
            raise _Reply(409, "label exists")
        label_id = fake._label_id(payload["name"])
        return ok({"id": label_id, "name": payload["name"]}, 201)
    if method == "GET" and re.match(_BASE + r"/issues$", path):
        query = params.get("q", "")
        found = [n for n, i in sorted(fake.issues.items()) if query in i["body"] or query in i["title"]]
        return _page(fake, [fake._issue_json(n) for n in found], params)
    if method == "POST" and re.match(_BASE + r"/issues$", path):
        number = fake._index()
        assignees = payload.get("assignees") or []
        fake.issues[number] = {
            "title": payload["title"],
            "body": payload["body"],
            "state": "open",
            "label_ids": list(payload.get("labels") or []),
            "assignee": assignees[0] if assignees else "",
        }
        return ok(fake._issue_json(number), 201)
    match = re.match(_BASE + r"/issues/(\d+)$", path)
    if match:
        number = int(match.group(1))
        if number not in fake.issues:
            raise _Reply(404, "issue not found")
        if method == "GET":
            if fake.raw_issue_body is not None:
                return ok(fake.raw_issue_body)
            return ok(fake._issue_json(number))
        if method == "PATCH":
            issue = fake.issues[number]
            for field in ("title", "body", "state"):
                if field in payload:
                    issue[field] = payload[field]
            if "assignees" in payload:
                issue["assignee"] = payload["assignees"][0] if payload["assignees"] else ""
            return ok(fake._issue_json(number))
    match = re.match(_BASE + r"/issues/(\d+)/labels$", path)
    if match and method == "PUT":
        number = int(match.group(1))
        if number not in fake.issues:
            raise _Reply(404, "issue not found")
        unknown = [i for i in payload["labels"] if i not in fake.labels]
        if unknown:
            raise _Reply(422, "label does not exist")
        fake.issues[number]["label_ids"] = list(payload["labels"])
        return ok([{"id": i, "name": fake.labels[i]} for i in payload["labels"]])
    match = re.match(_BASE + r"/issues/(\d+)/dependencies$", path)
    if match:
        number = int(match.group(1))
        if number not in fake.issues:
            raise _Reply(404, "issue not found")
        blockers = fake.dependencies.setdefault(number, [])
        if method == "GET":
            return _page(fake, [fake._issue_json(b) for b in blockers], params)
        if method == "POST":
            index = payload["index"]
            if payload["owner"] != OWNER or payload["repo"] != REPO or index not in fake.issues:
                raise _Reply(404, "dependency target not found")
            if index in blockers:
                raise _Reply(409, "dependency already exists")
            blockers.append(index)
            return ok(fake._issue_json(number), 201)
    if method == "POST" and re.match(_BASE + r"/pulls$", path):
        for field in ("head", "base", "title"):
            if not payload.get(field):
                raise _Reply(422, f"{field} is required")
        if payload["head"] not in fake.branches:
            raise _Reply(404, "head branch does not exist")
        number = fake._index()
        fake.pulls[number] = {
            "title": payload["title"],
            "state": "open",
            "merged": False,
            "mergeable": True,
            "head": {"ref": payload["head"], "sha": fake.branches[payload["head"]]},
            "base": payload["base"],
        }
        return ok(fake._pull_json(number), 201)
    match = re.match(_BASE + r"/pulls/(\d+)$", path)
    if match:
        number = int(match.group(1))
        if number not in fake.pulls:
            raise _Reply(404, "pull request not found")
        if method == "GET":
            return ok(fake._pull_json(number))
        if method == "PATCH":
            fake.pulls[number]["title"] = payload["title"]
            return ok(fake._pull_json(number))
    match = re.match(_BASE + r"/pulls/(\d+)/merge$", path)
    if match and method == "POST":
        number = int(match.group(1))
        pull = fake.pulls.get(number)
        if pull is None:
            raise _Reply(404, "pull request not found")
        if payload.get("Do") != "merge":
            raise _Reply(422, "only Do=merge is supported by this fake")
        if payload.get("head_commit_id") != pull["head"]["sha"]:
            raise _Reply(409, "head commit changed")
        if pull["state"] != "open":
            raise _Reply(405, "pull request is not open")
        pull["state"] = "closed"
        pull["merged"] = True
        return ok(None, 200)
    match = re.match(_BASE + r"/commits/([0-9a-zA-Z]+)/status$", path)
    if match and method == "GET":
        state, total = fake.statuses.get(match.group(1), ("pending", 0))
        return ok({"state": state, "total_count": total, "sha": match.group(1), "statuses": []})
    raise _Reply(404, f"no route {method} {path}")
