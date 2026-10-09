"""Item attachments: names, limits and the immutable files that hold the bytes.

An item owns a directory `items/ID/` beside `items/ID.md`. Attachment bytes are immutable files
`items/ID/attachments/HASH.EXT` (content hash, original extension kept so a forge web view can preview images); the
item names them through `attached` and `detached` events (a relative path with subdirectories, mapped to a hash; the
last event by id wins). Two machines can therefore never write the same path with different bytes, and a merge has
nothing to conflict on. The named tree is derived: `qew show` lists it and `qew extract` writes it out.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re

from qew.errors import QewError

SOFT_FILE_BYTES = 25 * 1024 * 1024  # above this a warning is printed
HARD_FILE_BYTES = 100 * 1024 * 1024  # above this a file is refused
ITEM_BYTES = 500 * 1024 * 1024  # the most distinct bytes one item may hold
MAX_PATH_BYTES = 1024
MAX_SEGMENT_BYTES = 255
DIRECTORY = "attachments"
REFERENCE = re.compile(r"\]\(attachment:([^)\s]+)\)")  # [text](attachment:docs/plan.md) in an item body
_DIGEST = re.compile(r"[0-9a-f]{64}")
_EXTENSION = re.compile(r"\.[a-z0-9]{1,10}")


def hard_limit() -> int:
    """The per-file refusal size; `QEW_ATTACH_MAX_BYTES` lowers or raises it (tests use it)."""
    value = os.environ.get("QEW_ATTACH_MAX_BYTES", "")
    return int(value) if value.isdigit() and int(value) > 0 else HARD_FILE_BYTES


def path_error(name: str) -> str | None:
    """Why `name` cannot be an attachment path, or None: relative, `/`-separated, no empty, `.` or `..` segment."""
    if not name:
        return "the name is empty"
    if len(name.encode("utf-8", "replace")) > MAX_PATH_BYTES:
        return f"the name is longer than {MAX_PATH_BYTES} bytes"
    if name.startswith("/") or name.endswith("/"):
        return "the name must be relative and must not end with `/`"
    for segment in name.split("/"):
        if segment in ("", ".", ".."):
            return f"the name has an empty, `.` or `..` segment: {name!r}"
        if len(segment.encode("utf-8", "replace")) > MAX_SEGMENT_BYTES:
            return f"a segment is longer than {MAX_SEGMENT_BYTES} bytes"
        if "\\" in segment or any(not character.isprintable() for character in segment):
            return f"the name has a backslash or a control character: {name!r}"
    return None


def checked_path(name: str) -> str:
    error = path_error(name)
    if error:
        raise QewError(f"attachment name refused: {error}", 2)
    return name


def digest_error(digest: object) -> str | None:
    return None if isinstance(digest, str) and _DIGEST.fullmatch(digest) else "the hash must be 64 lowercase hex characters"


def extension_of(filename: str) -> str:
    """The original extension, lowercased, when it is short and plain; otherwise none."""
    suffix = Path(filename).suffix.lower()
    return suffix if _EXTENSION.fullmatch(suffix) else ""


def blob_name(digest: str, extension: str) -> str:
    return f"{digest}{extension}"


def blob_error(digest: str, blob: object) -> str | None:
    if not isinstance(blob, str) or not blob.startswith(digest) or (blob[len(digest):] and not _EXTENSION.fullmatch(blob[len(digest):])):
        return "the blob file name must be the hash plus an optional short extension"
    return None


def hash_file(path: Path) -> tuple[str, int]:
    """(sha256 hex, size) of a file read in chunks."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def clashes(existing: set[str], name: str) -> list[str]:
    """Names in `existing` that cannot coexist with the file `name`: a file where `name` has a directory, or the
    other way round."""
    found = [other for other in existing if other != name and (name.startswith(other + "/") or other.startswith(name + "/"))]
    return sorted(found)


@dataclass(frozen=True)
class Attachment:
    path: str
    digest: str
    size: int
    blob: str
    event: str
