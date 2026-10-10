"""Work-plan files: read, parse and validate a whole plan before anything is written.

A plan is JSON Lines (one object per line), one JSON object, or one JSON array of objects. Every rule
here is checked for every entry before the first write; a refusal names the line (or entry) it is about.
"""

from dataclasses import dataclass
import hashlib
import json
import os
import stat
import sys

from qew import ledger_fields
from qew.errors import QewError
from qew.jsontypes import JsonObject
from qew.ledger_item import title_is_one_line

KINDS = ("epic", "issue", "task")
# `events` is validated by the importer, `quote` and `approved` by the release rule.
FIELDS = ("kind", "title", "parent", "needs", "labels", "fields", "priority", "body", "ref", "id", "created", "events", "quote", "approved", "discovered_from", "attachments", "comments")


@dataclass(frozen=True)
class Entry:
    number: int  # the line in a JSON Lines plan, the position in an array
    label: str  # `line N` or `entry N`, for messages
    key: str  # the entry's `ref`, else `line-N`
    spec: JsonObject


@dataclass(frozen=True)
class Plan:
    entries: tuple[Entry, ...]
    digest: str  # sha256 of the raw bytes
    source: str  # file base name, or `-` for standard input


def read_bytes(path: str) -> bytes:
    """The plan's bytes: standard input, or a regular file that is not a symlink."""
    if path == "-":
        return sys.stdin.buffer.read()
    try:
        mode = os.lstat(path).st_mode
    except OSError as error:
        raise QewError(f"cannot read {path}: {error.strerror}", 2) from None
    if stat.S_ISLNK(mode):
        raise QewError(f"{path} is a symlink; name the reviewed file itself", 2)
    if not stat.S_ISREG(mode):
        raise QewError(f"{path} is not a regular file", 2)
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as error:
        raise QewError(f"cannot read {path}: {error.strerror}", 2) from None


def parse(data: bytes, source: str = "-") -> Plan:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise QewError(f"plan is not UTF-8 text (byte {error.start})", 2) from None
    if not text.strip():
        raise QewError("plan is empty: it names no work items", 2)
    objects = _objects(text)
    if not objects:
        raise QewError("plan is empty: it names no work items", 2)
    entries: list[Entry] = []
    used: dict[str, int] = {}
    for number, label, value in objects:
        if not isinstance(value, dict):
            raise QewError(f"{label}: not a JSON object", 2)
        spec = _checked(value, label)
        key = spec.get("ref") or f"line-{number}"
        if key in used:
            raise QewError(f"{label}: ref `{key}` is already used by entry {used[key]}", 2)
        used[key] = number
        entries.append(Entry(number, label, key, spec))
    for entry in entries:
        for value in _references(entry.spec):
            if value.startswith("@") and value[1:] not in used:
                raise QewError(f"{entry.label}: `{value}` names no entry of this plan", 2)
    return Plan(tuple(entries), hashlib.sha256(data).hexdigest(), source)


def _objects(text: str) -> list[tuple[int, str, object]]:
    """(number, label, value) per entry: a whole-file array or object, else one JSON value per line."""
    try:
        whole = json.loads(text)
    except ValueError:
        whole = None
    else:
        if isinstance(whole, list):
            return [(n, f"entry {n}", value) for n, value in enumerate(whole, 1)]
        return [(1, "entry 1" if "\n" in text.strip() else "line 1", whole)]
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            found.append((number, f"line {number}", json.loads(line)))
        except ValueError as error:
            raise QewError(f"line {number}: invalid JSON ({error})", 2) from None
    return found


def _checked(value: JsonObject, label: str) -> JsonObject:
    unknown = [key for key in value if key not in FIELDS]
    if unknown:
        raise QewError(f"{label}: unknown field `{unknown[0]}` (fields: {', '.join(FIELDS)})", 2)
    title = value.get("title")
    if not isinstance(title, str) or not title.strip():
        raise QewError(f"{label}: title must be a non-blank string", 2)
    if not title_is_one_line(title):
        raise QewError(f"{label}: title must be one line", 2)
    kind = value.get("kind", "issue")
    if kind not in KINDS:
        raise QewError(f"{label}: kind must be epic, issue or task, not `{kind}`", 2)
    priority = value.get("priority", 2)
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0:
        raise QewError(f"{label}: priority must be a non-negative integer, not `{priority}`", 2)
    for key in ("needs", "labels"):
        items = value.get(key, [])
        if not isinstance(items, list) or not all(isinstance(item, str) and item for item in items):
            raise QewError(f"{label}: {key} must be a list of non-empty strings", 2)
    if "fields" in value:
        if not isinstance(value["fields"], dict):
            raise QewError(f"{label}: fields must be an object of NAME: value-or-list", 2)
        ledger_fields.normalise(value["fields"], f"{label}: fields")
    for key in ("parent", "ref", "id", "created", "body", "quote", "discovered_from"):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            raise QewError(f"{label}: {key} must be a string", 2)
    ref = value.get("ref")
    if ref is not None and (not ref.strip() or any(c.isspace() for c in ref) or ref.startswith("@")):
        raise QewError(f"{label}: ref must be one word without a leading @", 2)
    if "approved" in value and not isinstance(value["approved"], bool):
        raise QewError(f"{label}: approved must be true or false", 2)
    if "events" in value and not isinstance(value["events"], list):
        raise QewError(f"{label}: events must be a list", 2)
    if "comments" in value and (not isinstance(value["comments"], list) or not all(isinstance(row, dict) for row in value["comments"])):
        raise QewError(f"{label}: comments must be a list of {{\"key\", \"text\", optional \"author\", \"at\", \"reply_to\"}}", 2)
    if "attachments" in value:
        rows = value["attachments"]
        if not isinstance(rows, list) or not all(isinstance(row, dict) and isinstance(row.get("source"), str) and set(row) <= {"source", "name"}
                                                 and isinstance(row.get("name", ""), str) for row in rows):
            raise QewError(f"{label}: attachments must be a list of {{\"source\": path, \"name\": optional name}} (sources relative to the current directory)", 2)
    return value


def _references(spec: JsonObject) -> list[str]:
    parent = spec.get("parent")
    return list(spec.get("needs", [])) + ([parent] if parent else [])


KEY_SOURCE = "--key"


def single(spec: JsonObject, key: str | None = None) -> Plan:
    """A one-entry plan for an item given on the command line; with a key, a retry of the same intent adopts the first run."""
    checked = _checked(spec, "entry 1")
    digest = hashlib.sha256(json.dumps(checked, sort_keys=True).encode()).hexdigest()
    if key is None:
        return Plan((Entry(1, "entry 1", "line-1", checked),), digest, "-")
    if not key.strip() or any(c.isspace() for c in key) or key.startswith("@"):
        raise QewError("--key must be one word without a leading @", 2)
    return Plan((Entry(1, "entry 1", key, checked),), digest, KEY_SOURCE)
