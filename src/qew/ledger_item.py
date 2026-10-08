"""The item file: a fixed `key: value` header ended by a `---` line, then a free Markdown body."""

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, List, Tuple

from qew import ledger_labels
from qew.errors import QewError
from qew.ledger_ids import valid_id

MAX_HEADER_BYTES = 8192
RULE = "---"


class Kind(str, Enum):
    EPIC = "epic"
    ISSUE = "issue"
    TASK = "task"


# The schema: header keys in file order. Unknown keys are refused, never ignored.
KEYS = ("id", "kind", "title", "parent", "needs", "tracks", "pr", "labels", "owner", "priority", "due", "created", "discovered_from")
LINK_KEYS = ("tracks", "pr", "due", "discovered_from")  # tracking issue / pull request item ids and a YYYY-MM-DD due date; optional, omitted when empty
REQUIRED = ("id", "kind", "title", "created")
LIST_KEYS = ("needs", "labels")
DEFAULT_PRIORITY = 2
_DUE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Item:
    id: str
    kind: Kind
    title: str
    created: str
    parent: str = ""
    needs: Tuple[str, ...] = ()
    labels: Tuple[str, ...] = ()
    owner: str = ""
    priority: int = DEFAULT_PRIORITY
    body: str = field(default="", compare=False)
    tracks: str = ""
    pr: str = ""
    due: str = ""
    discovered_from: str = ""  # the item this one was found while working on; never a dependency

    def header(self) -> Dict[str, str]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "title": self.title,
            "parent": self.parent,
            "needs": " ".join(self.needs),
            "tracks": self.tracks,
            "pr": self.pr,
            "labels": " ".join(self.labels),
            "owner": self.owner,
            "priority": str(self.priority),
            "due": self.due,
            "created": self.created,
            "discovered_from": self.discovered_from,
        }

    def with_value(self, key: str, value: str) -> "Item":
        header = self.header()
        header[key] = value
        return from_header(header, self.body)


def parse_item(text: str, where: str = "item") -> Item:
    """Parse the header only, stopping at the first `---` line within bounded bytes."""
    header: Dict[str, str] = {}
    body_start = None
    consumed = 0
    lines = text.split("\n")
    for index, line in enumerate(lines):
        consumed += len(line.encode("utf-8")) + 1
        if consumed > MAX_HEADER_BYTES:
            raise QewError(f"{where}: header exceeds {MAX_HEADER_BYTES} bytes or has no `---` line", 1)
        if line == RULE:
            body_start = index + 1
            break
        key, colon, value = line.partition(":")
        if not colon or not key or key != key.strip():
            raise QewError(f"{where}: malformed header line {index + 1}", 1)
        if key not in KEYS:
            raise QewError(f"{where}: unknown header key `{key}`", 1)
        if key in header:
            raise QewError(f"{where}: duplicate header key `{key}`", 1)
        header[key] = value.strip()
    if body_start is None:
        raise QewError(f"{where}: header is not terminated by a `---` line", 1)
    body = "\n".join(lines[body_start:])
    return from_header(header, body, where)


def from_header(header: Dict[str, str], body: str = "", where: str = "item") -> Item:
    for key in REQUIRED:
        if not header.get(key):
            raise QewError(f"{where}: missing required header key `{key}`", 1)
    try:
        kind = Kind(header["kind"])
    except ValueError:
        raise QewError(f"{where}: kind must be epic, issue or task, not `{header['kind']}`", 1) from None
    if not valid_id(header["id"]):
        raise QewError(f"{where}: `{header['id']}` is not a valid id", 1)
    priority = header.get("priority", str(DEFAULT_PRIORITY)) or str(DEFAULT_PRIORITY)
    if not priority.isdigit():
        raise QewError(f"{where}: priority must be a non-negative integer", 1)
    if header.get("due") and not _DUE.match(header["due"]):
        raise QewError(f"{where}: due must be a date like 2026-12-31", 1)
    for key in ("parent", "tracks", "pr", "discovered_from"):
        if header.get(key) and not valid_id(header[key]):
            raise QewError(f"{where}: `{key}` must be an item id (a ledger link, never a forge number or URL)", 1)
    needs = tuple(header.get("needs", "").split())
    labels = tuple(header.get("labels", "").split())
    for need in needs:
        if not valid_id(need):
            raise QewError(f"{where}: `{need}` in needs is not an item id", 1)
    if "\n" in header["title"]:
        raise QewError(f"{where}: title must be one line", 1)
    return Item(
        id=header["id"],
        kind=kind,
        title=header["title"],
        created=header["created"],
        parent=header.get("parent", ""),
        needs=needs,
        labels=labels,
        owner=header.get("owner", ""),
        priority=int(priority),
        body=body,
        tracks=header.get("tracks", ""),
        pr=header.get("pr", ""),
        due=header.get("due", ""),
        discovered_from=header.get("discovered_from", ""),
    )


def render_item(item: Item) -> str:
    lines = [f"{key}: {value}" for key, value in item.header().items() if value or key not in LINK_KEYS]
    body = item.body if item.body.endswith("\n") or not item.body else item.body + "\n"
    return "\n".join(lines) + f"\n{RULE}\n" + body


def check_header_values(item: Item) -> List[str]:
    """Problems with a parsed item that the parser itself tolerates."""
    problems = []
    if item.owner:
        problems.append("owner is derived from claim events; the header value must stay empty")
    if item.parent == item.id:
        problems.append("an item cannot be its own parent")
    if item.id in item.needs:
        problems.append("an item cannot need itself")
    return problems
