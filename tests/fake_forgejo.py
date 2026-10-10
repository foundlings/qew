"""An in-memory fake of the Gitea/Forgejo /api/v1 wire format, for exactly the endpoints qew uses."""

from datetime import datetime
import hashlib
import json
import re
import sys
import threading
import time
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from collections.abc import Callable

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0] + "/src")
from qew import instants  # noqa: E402

OWNER = "acme"
REPO = "widgets"
TOKEN = "fake-token"
_BASE = rf"^/api/v1/repos/{OWNER}/{REPO}"


class FakeForgejo:
    """Started on 127.0.0.1 in a thread. State is plain attributes the walks may inspect."""

    def __init__(self) -> None:
        self.issues: dict[int, dict[str, Any]] = {}
        self.labels: dict[int, str] = {}
        self.milestones: dict[int, dict[str, Any]] = {}
        self.label_meta: dict[str, dict[str, str]] = {}  # name -> {color, description}
        self.drop_label_creates = False  # accept a label create yet store nothing
        self.pulls: dict[int, dict[str, Any]] = {}
        self.branches: dict[str, str] = {}  # branch -> head sha
        self.default_branch = "main"
        self.contained_in: dict[str, list[str]] = {}  # branch -> branches whose history already holds its tip
        self.protections: dict[str, dict[str, Any]] = {}  # branch -> branch protection record
        self.statuses: dict[str, tuple[str, int]] = {}  # sha -> (combined state, total_count)
        self.dependencies: dict[int, list[int]] = {}  # issue -> issues blocking it
        self.requests: list[tuple[str, str, Any | None]] = []
        self.max_limit = 50
        self.link_base = ""  # when set, pagination Link headers point here instead of at this API
        self.endless = False  # lists answer a full page forever, with a next link and no total header
        self.delay = 0.0  # seconds every request waits before it is served
        self.down = False  # every request answers 503
        self.fail: list[tuple[int, str]] = []  # (status, body) consumed one per request
        self.raw_issue_body: bytes | None = None  # when set, GET issue answers this verbatim
        self.token = TOKEN
        self.etags = True  # answer issue-list pages with an ETag and honor If-None-Match
        self.clock: Callable[[], datetime] = instants.now  # the forge's clock; walks move it to place edits in time
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

    def start(self) -> FakeForgejo:
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

    def add_branch(self, name: str, sha: str = "", contained_in: tuple[str, ...] = ()) -> None:
        """A branch on the forge; `contained_in` names the branches that already hold its tip (it is merged there)."""
        self.branches[name] = sha or f"{len(self.branches) + 1:040x}"
        self.contained_in[name] = list(contained_in)

    def touch(self, number: int) -> None:
        """Record a modification: edits, comments and label changes all bump updated_at."""
        self.issues[number]["updated_at"] = instants.format_instant(self.clock())

    def delete_issue(self, number: int) -> None:
        """An admin deletes the issue; later issues keep their numbers."""
        del self.issues[number]

    def issue_labels(self, number: int) -> list[str]:
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

    def _milestone_json(self, number):
        if not number:
            return None
        found = self.milestones[number]
        return {"id": number, "title": found["title"], "description": found["description"],
                "due_on": found["due_on"], "state": found["state"]}

    def _issue_json(self, number: int) -> dict[str, Any]:
        issue = self.issues[number]
        assignee = issue["assignee"]
        shaped: dict[str, Any] = {
            "id": 1000 + number,
            "number": number,
            "html_url": f"{self.repo_url}/issues/{number}",
            "state": issue["state"],
            "title": issue["title"],
            "body": issue["body"],
            "labels": [{"id": i, "name": self.labels[i]} for i in issue["label_ids"]],
            "comments": len(issue["discussion"]),
            "milestone": self._milestone_json(issue.get("milestone")),
        }
        if assignee:  # Gitea omits the key when nobody is assigned
            shaped["assignee"] = {"id": 7, "login": assignee}
            shaped["assignees"] = [{"id": 7, "login": assignee}]
        return shaped

    def add_comment(self, number: int, login: str, body: str | None) -> dict[str, Any]:
        """A comment made on the forge by someone else; created_at rises with each comment."""
        comment = {"id": 5000 + sum(len(i["discussion"]) for i in self.issues.values()) + 1, "login": login, "body": body}
        discussion = self.issues[number]["discussion"]
        comment["created_at"] = f"2026-01-01T00:{len(discussion):02d}:00Z"
        discussion.append(comment)
        self.touch(number)
        return comment

    def _comment_json(self, comment: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": comment["id"], "user": {"id": 7, "login": comment["login"]}, "body": comment["body"],
            "created_at": comment["created_at"],
        }

    def _pull_as_issue_json(self, number: int) -> dict[str, Any]:
        """Gitea lists pull requests among issues unless `type=issues` is asked for."""
        pull = self.pulls[number]
        return {
            "id": 2000 + number, "number": number, "html_url": f"{self.repo_url}/pulls/{number}",
            "state": pull["state"], "title": pull["title"], "body": "", "labels": [], "comments": 0,
            "pull_request": {"merged": pull["merged"]},
        }

    def _pull_json(self, number: int) -> dict[str, Any]:
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
            "body": pull.get("body", ""),
            "user": {"login": "opener"},
            "assignees": [{"login": a} for a in pull.get("assignees", [])] or None,
            "merge_commit_sha": pull.get("merge_sha"),
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

        def do_DELETE(self) -> None:
            self._serve("DELETE")

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
            if fake.etags and method == "GET" and status == 200 and re.fullmatch(_BASE + r"/issues", path) and isinstance(body, list):
                etag = '"' + hashlib.sha1(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest() + '"'
                if self.headers.get("If-None-Match") == etag:
                    return self._send(304, b"", {**headers, "ETag": etag})
                headers = {**headers, "ETag": etag}
            if isinstance(body, bytes):
                encoded = body
            else:
                encoded = b"" if body is None else json.dumps(body).encode("utf-8")
            self._send(status, encoded, headers)

        def _send(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
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


def _unquote(params: dict[str, str]) -> dict[str, str]:
    from urllib.parse import unquote_plus

    return {key: unquote_plus(value) for key, value in params.items()}


def _page(fake: FakeForgejo, items: list[Any], params: dict[str, str]) -> tuple[int, Any, dict[str, str]]:
    page = int(params.get("page", "1"))
    limit = min(int(params.get("limit", "30")), fake.max_limit)
    if fake.endless:
        return 200, items[:limit], {"Link": f'<{fake.link_base or fake.api}?page={page + 1}>; rel="next"'}
    chunk = items[(page - 1) * limit : page * limit]
    headers = {"X-Total-Count": str(len(items))}
    if page * limit < len(items):
        headers["Link"] = f'<{fake.link_base or fake.api}?page={page + 1}>; rel="next"'
    return 200, chunk, headers


def _route(
    fake: FakeForgejo, method: str, path: str, params: dict[str, str], payload: Any
) -> tuple[int, Any, dict[str, str]]:
    def ok(body: Any, status: int = 200) -> tuple[int, Any, dict[str, str]]:
        return status, body, {}

    if re.match(_BASE + r"/milestones$", path):
        if method == "GET":
            return _page(fake, [fake._milestone_json(n) for n in sorted(fake.milestones)], params)
        number = len(fake.milestones) + 1
        fake.milestones[number] = {"title": payload["title"], "description": payload.get("description", ""),
                                   "due_on": payload.get("due_on"), "state": "open"}
        return ok(fake._milestone_json(number), 201)
    found = re.match(_BASE + r"/milestones/(\d+)$", path)
    if found:
        number = int(found.group(1))
        if number not in fake.milestones:
            raise _Reply(404, "milestone not found")
        if method == "PATCH":
            for key in ("title", "description", "due_on", "state"):
                if key in payload:
                    fake.milestones[number][key] = payload[key]
        return ok(fake._milestone_json(number))
    if method == "GET" and re.match(_BASE + r"/labels$", path):
        items = [{"id": i, "name": n, **{"color": "ededed", "description": "", **fake.label_meta.get(n, {})}} for i, n in fake.labels.items()]
        return _page(fake, items, params)
    match = re.match(_BASE + r"/labels/(\d+)$", path)
    if match and method == "PATCH":
        label_id = int(match.group(1))
        if label_id not in fake.labels:
            raise _Reply(404, "label not found")
        meta = fake.label_meta.setdefault(fake.labels[label_id], {"color": "ededed", "description": ""})
        meta.update({key: payload[key] for key in ("color", "description") if key in payload})
        return ok({"id": label_id, "name": fake.labels[label_id], **meta})
    if match and method == "DELETE":
        label_id = int(match.group(1))
        if label_id not in fake.labels:
            raise _Reply(404, "label not found")
        name = fake.labels.pop(label_id)
        fake.label_meta.pop(name, None)
        for issue in fake.issues.values():
            issue["label_ids"] = [i for i in issue["label_ids"] if i != label_id]
        return ok(None, 204)
    if method == "POST" and re.match(_BASE + r"/labels$", path):
        if any(name == payload["name"] for name in fake.labels.values()):
            raise _Reply(409, "label exists")
        if fake.drop_label_creates:
            return ok({"id": 999, "name": payload["name"]}, 201)
        label_id = fake._label_id(payload["name"])
        fake.label_meta[payload["name"]] = {"color": payload.get("color", ""), "description": payload.get("description", "")}
        return ok({"id": label_id, "name": payload["name"]}, 201)
    if method == "GET" and re.match(_BASE + r"/issues$", path):
        query = params.get("q", "")
        state = params.get("state", "open")
        since = params.get("since", "")
        found = [n for n, i in fake.issues.items()
                 if (query in (i["body"] or "") or query in i["title"]) and state in ("all", i["state"])
                 and i.get("updated_at", "") >= since]
        shaped = {n: fake._issue_json(n) for n in found}
        if params.get("type") != "issues":
            shaped.update({n: fake._pull_as_issue_json(n) for n, p in fake.pulls.items() if state in ("all", p["state"])})
        # Like the real API: newest first unless sort=oldest.
        ordered = sorted(shaped, reverse=params.get("sort") != "oldest")
        return _page(fake, [shaped[n] for n in ordered], params)
    if method == "POST" and re.match(_BASE + r"/issues$", path):
        number = fake._index()
        assignees = payload.get("assignees") or []
        fake.issues[number] = {
            "title": payload["title"],
            "body": payload["body"],
            "state": "open",
            "label_ids": list(payload.get("labels") or []),
            "assignee": assignees[0] if assignees else "",
            "discussion": [],
        }
        fake.touch(number)
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
            for field in ("title", "body", "state", "milestone"):
                if field in payload:
                    issue[field] = payload[field]
            if "assignees" in payload:
                issue["assignee"] = payload["assignees"][0] if payload["assignees"] else ""
            fake.touch(number)
            return ok(fake._issue_json(number))
    match = re.match(_BASE + r"/issues/(\d+)/comments$", path)
    if match:
        number = int(match.group(1))
        if number not in fake.issues:
            raise _Reply(404, "issue not found")
        if method == "GET":
            return _page(fake, [fake._comment_json(c) for c in fake.issues[number]["discussion"]], params)
        if method == "POST":
            return ok(fake._comment_json(fake.add_comment(number, "qew-bot", payload["body"])), 201)
    match = re.match(_BASE + r"/issues/(\d+)/labels$", path)
    if match and method == "PUT":
        number = int(match.group(1))
        if number not in fake.issues:
            raise _Reply(404, "issue not found")
        unknown = [i for i in payload["labels"] if i not in fake.labels]
        if unknown:
            raise _Reply(422, "label does not exist")
        fake.issues[number]["label_ids"] = list(payload["labels"])
        fake.touch(number)
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
            "body": payload.get("body", ""),
            "assignees": list(payload.get("assignees") or []),
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
            for field in ("title", "body"):
                if field in payload:
                    fake.pulls[number][field] = payload[field]
            if payload.get("state") in ("open", "closed") and not fake.pulls[number]["merged"]:
                fake.pulls[number]["state"] = payload["state"]
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
        pull["merge_sha"] = "e" * 40
        return ok(None, 200)
    if method == "GET" and re.match(_BASE + r"$", path):
        return ok({"name": REPO, "default_branch": fake.default_branch})
    if method == "GET" and re.match(_BASE + r"/branches$", path):
        listed = [{"name": name, "protected": name in fake.protections, "commit": {"id": sha}} for name, sha in fake.branches.items()]
        return _page(fake, listed, params)
    match = re.match(_BASE + r"/branches/(.+)$", path)
    if match and method == "DELETE":
        name = unquote(match.group(1))
        if name not in fake.branches:
            raise _Reply(404, "branch does not exist")
        if name in fake.protections or name == fake.default_branch:
            raise _Reply(403, "branch is protected")
        del fake.branches[name]
        return ok(None, 204)
    match = re.match(_BASE + r"/compare/(.+)$", path)
    if match and method == "GET":
        base, _, head = unquote(match.group(1)).partition("...")
        if base not in fake.branches or head not in fake.branches:
            raise _Reply(404, "branch does not exist")
        merged = head == base or base in fake.contained_in.get(head, [])
        return ok({"total_commits": 0 if merged else 1, "commits": [] if merged else [{"sha": fake.branches[head]}]})
    if method == "GET" and re.match(_BASE + r"/pulls$", path):
        wanted = params.get("state", "open")
        opened = [fake._pull_json(n) for n, p in sorted(fake.pulls.items()) if wanted in ("all", p["state"])]
        return _page(fake, opened, params)
    match = re.match(_BASE + r"/branch_protections/([\w.-]+)$", path)
    if match and method == "GET":
        record = fake.protections.get(match.group(1))
        if record is None:
            raise _Reply(404, "branch protection does not exist")
        return ok(record)
    match = re.match(_BASE + r"/commits/([0-9a-zA-Z]+)/status$", path)
    if match and method == "GET":
        state, total = fake.statuses.get(match.group(1), ("pending", 0))
        return ok({"state": state, "total_count": total, "sha": match.group(1), "statuses": []})
    raise _Reply(404, f"no route {method} {path}")
