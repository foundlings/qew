"""Tick the checklist in a pull request body, in place.

An item is a Markdown list line `- [ ] text`. Its id is the `<!-- pr-progress:ID -->`
marker when it has one, else its 1-based position among the items (`@N` when a marker already uses N). Lines
inside fenced code blocks are not items; fences follow the CommonMark rules.
"""

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional

from qew.errors import QewError
from qew.github import edit_pull_request_body, fetch_pull_request_state
from qew.pr_owner import require_owner

_ITEM = re.compile(r"^(\s*[-*+]\s+\[)([ xX])(\].*)$")
_MARKER = re.compile(r"<!--\s*pr-progress:([A-Za-z0-9_.-]+)\s*-->")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


@dataclass(frozen=True)
class Item:
    id: str
    checked: bool
    text: str
    line: int


@dataclass(frozen=True)
class PullState:
    state: str
    author: str
    viewer: str
    body: str


def parse(body: str) -> List[Item]:
    found = []
    fence: Optional[str] = None
    for index, line in enumerate(body.split("\n")):
        opened = _FENCE.match(line.rstrip("\r"))
        if fence is not None:
            # Only the same character, at least as long, with nothing after it, closes.
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence) and not opened.group(2).strip():
                fence = None
            continue
        if opened and not (opened.group(1)[0] == "`" and "`" in opened.group(2)):
            fence = opened.group(1)
            continue
        match = _ITEM.match(line)
        if match is None:
            continue
        marker = _MARKER.search(line)
        text = _MARKER.sub("", match.group(3)[1:]).strip()
        found.append((marker.group(1) if marker else None, match.group(2) in "xX", text, index))
    marked = [value[0] for value in found if value[0] is not None]
    duplicates = sorted({value for value in marked if marked.count(value) > 1})
    if duplicates:
        raise QewError(f"duplicate checklist item id: {', '.join(duplicates)}", 2)
    items: List[Item] = []
    for position, (marker_id, checked, text, index) in enumerate(found, start=1):
        # A position that a marker already uses is written @N so ids never collide.
        identifier = marker_id or (str(position) if str(position) not in marked else f"@{position}")
        items.append(Item(id=identifier, checked=checked, text=text, line=index))
    return items


def open_items(body: str) -> List[Item]:
    return [item for item in parse(body) if not item.checked]


def tick(
    repository: str, number: int, ids: List[str], everything: bool
) -> Dict[str, Any]:
    """Tick the named items (or every open one) of the caller's open pull request."""
    if number <= 0:
        raise QewError("pull request number must be positive", 2)
    before = _pull(repository, number, own=True)
    items = parse(before.body)
    unknown = [value for value in ids if value not in {item.id for item in items}]
    if unknown:
        raise QewError(f"PR #{number} has no checklist item {', '.join(unknown)}", 2)
    wanted = {item.id for item in items if not item.checked} if everything else set(ids)
    todo = [item for item in items if item.id in wanted and not item.checked]
    if todo:
        updated = _ticked(before.body, todo)
        if _pull(repository, number, own=True).body != before.body:
            raise QewError(f"PR #{number} body changed while ticking; rerun", 3)
        edit_pull_request_body(repository, number, updated)
        if _pull(repository, number, own=True).body != updated:
            raise QewError(f"PR #{number} body is not as written after ticking", 3)
        final = updated
    else:
        final = before.body
    return {
        "changed": [item.id for item in todo],
        "open": [item.id for item in open_items(final)],
    }


def _ticked(body: str, items: List[Item]) -> str:
    lines = body.split("\n")
    for item in items:
        match = _ITEM.match(lines[item.line])
        lines[item.line] = f"{match.group(1)}x{match.group(3)}"
    return "\n".join(lines)


def read(repository: str, number: int) -> List[Item]:
    if number <= 0:
        raise QewError("pull request number must be positive", 2)
    return parse(_pull(repository, number, own=False).body)


def _pull(repository: str, number: int, *, own: bool) -> PullState:
    fetched = fetch_pull_request_state(repository, number)
    pull = fetched["pull_request"]
    author = pull.get("author")
    login: Optional[str] = author.get("login") if isinstance(author, dict) else None
    body = pull.get("body")
    viewer = fetched["viewer"]
    state = pull.get("state")
    if (
        pull.get("number") != number
        or state not in ("OPEN", "CLOSED", "MERGED")
        or not isinstance(login, str)
        or not isinstance(viewer, str)
        or not isinstance(body, str)
    ):
        raise QewError(f"indeterminate: invalid data for PR #{number}", 4)
    if own:
        if state != "OPEN":
            raise QewError(f"PR #{number} is {state.lower()}, not open", 2)
        require_owner(number, login, viewer, pull)
    return PullState(state, login, viewer, body)
