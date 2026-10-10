"""The local copy of a target's issue listing pages (`.qew/state/TARGET/pages/N.json`), kept for conditional requests.

Each file holds a page's ETag and the issues parsed from it. The files are a cache of forge text: a damaged
or odd-shaped file is dropped (the page is then fetched without a condition), never trusted.
"""

from collections.abc import Iterable, Mapping

from qew.jsontypes import JsonObject
from qew.forge import CachedPage, RemoteIssue, close_reason_of

_STRINGS = ("url", "node_id", "title", "body", "assignee")


def to_json(page: CachedPage) -> JsonObject:
    return {"etag": page.etag, "more": page.more, "raw": page.raw, "issues": [_issue_json(i) for i in page.issues]}


def _issue_json(issue: RemoteIssue) -> JsonObject:
    return {"number": issue.number, "url": issue.url, "node_id": issue.node_id, "open": issue.open, "title": issue.title,
            "body": issue.body, "labels": list(issue.labels), "assignee": issue.assignee, "assignees": list(issue.assignees), "comments": issue.comments,
            "milestone": issue.milestone, "close_reason": issue.close_reason, "issue_id": issue.issue_id, "parent": issue.parent}


def from_json(value: object) -> CachedPage | None:
    if not isinstance(value, dict) or not isinstance(value.get("etag"), str) or not value["etag"]:
        return None
    if not isinstance(value.get("more"), bool) or not isinstance(value.get("raw"), int) or not isinstance(value.get("issues"), list):
        return None
    issues = [_issue(entry) for entry in value["issues"]]
    if any(issue is None for issue in issues):
        return None
    return CachedPage(value["etag"], tuple(i for i in issues if i is not None), value["more"], value["raw"])


def _issue(entry: object) -> RemoteIssue | None:
    if not isinstance(entry, dict):
        return None
    ints = ("number", "comments", "milestone")
    if not all(type(entry.get(k)) is int for k in ints) or not isinstance(entry.get("open"), bool):
        return None
    if not all(isinstance(entry.get(k), str) for k in _STRINGS):
        return None
    labels = entry.get("labels")
    if not isinstance(labels, list) or not all(isinstance(l, str) for l in labels):
        return None
    close_reason = entry.get("close_reason")
    return RemoteIssue(entry["number"], entry["url"], entry["node_id"], entry["open"], entry["title"], entry["body"],
                       tuple(labels), entry["assignee"], entry["comments"], entry["milestone"],
                       close_reason_of(close_reason),
                       assignees=tuple(x for x in entry.get("assignees", []) if isinstance(x, str)) if isinstance(entry.get("assignees"), list) else (),
                       issue_id=entry["issue_id"] if type(entry.get("issue_id")) is int else 0,
                       parent=entry["parent"] if type(entry.get("parent")) is int else 0)


def load(files: Mapping[int, object]) -> dict[int, CachedPage]:
    found = {number: from_json(value) for number, value in files.items()}
    return {number: page for number, page in found.items() if page is not None}


def dump(pages: Iterable[CachedPage]) -> dict[int, JsonObject]:
    return {number: to_json(page) for number, page in enumerate(pages, start=1)}
