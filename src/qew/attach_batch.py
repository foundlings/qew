"""Attach many files to many items in one ledger write: planned and checked whole before the first byte is stored.

Used by `qew item attach` (one request), `qew item attach --manifest` and the `attachments` of a create plan. A refusal
anywhere writes nothing anywhere; the same bytes under the same name again are unchanged.
"""

from dataclasses import dataclass
from pathlib import Path
import sys

from qew import attachments
from qew.errors import QewError
from qew.ledger_event import attachments_after
from qew.ledger_store import Ledger


@dataclass(frozen=True)
class Request:
    item_id: str
    source: Path
    name: str | None  # the name in the tree (a directory's files go under it); None: the file name, or the root for a directory
    where: str = ""  # "line 3": what a refusal names


@dataclass(frozen=True)
class Row:
    item_id: str
    path: str
    file: Path
    digest: str
    size: int
    blob: str


@dataclass(frozen=True)
class Batch:
    rows: tuple[Row, ...]  # what changes
    unchanged: int  # rows that already hold exactly these bytes
    items: int  # how many items the changed rows belong to


def files(source: Path, name: str | None) -> list[tuple[str, Path]]:
    """(attachment name, file) for a file or for every file under a directory; symlinks and special files are refused."""
    if name is not None:
        attachments.checked_path(name)
    if source.is_symlink():
        raise QewError(f"{source} is a symlink; name the real file", 2)
    if source.is_file():
        return [(attachments.checked_path(source.name if name is None else name), source)]
    if not source.is_dir():
        raise QewError(f"{source} is not a file or a directory", 2)
    found: list[tuple[str, Path]] = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise QewError(f"{path} is a symlink; attachments are regular files only", 2)
        if path.is_dir():
            continue
        if not path.is_file():
            raise QewError(f"{path} is not a regular file", 2)
        relative = path.relative_to(source).as_posix()
        found.append((attachments.checked_path(f"{name}/{relative}" if name is not None else relative), path))
    if not found:
        raise QewError(f"{source} holds no files", 2)
    return found


def plan(ledger: Ledger, requests: list[Request]) -> Batch:
    """Check every request against the limits, the names already attached and each other; nothing is written."""
    limit = attachments.hard_limit()
    wanted: dict[str, dict[str, Row]] = {}
    hashes: dict[Path, tuple[str, int]] = {}
    for request in requests:
        try:
            listed = files(request.source, request.name)
        except QewError as error:
            raise QewError(f"{request.where + ': ' if request.where else ''}{error}", error.exit_code) from None
        for path, file in listed:
            if file not in hashes:
                try:
                    hashes[file] = attachments.hash_file(file)
                except OSError as error:
                    raise QewError(f"{request.where + ': ' if request.where else ''}cannot read {file}: {error}", 2) from None
            digest, size = hashes[file]
            if size > limit:
                raise QewError(f"{file} is {size} bytes; the limit is {limit} (QEW_ATTACH_MAX_BYTES)", 2)
            if size > attachments.SOFT_FILE_BYTES:
                print(f"warning: {file} is {size // (1024 * 1024)} MiB; large files bloat the ledger repository", file=sys.stderr)
            row = Row(request.item_id, path, file, digest, size, attachments.blob_name(digest, attachments.extension_of(file.name)))
            seen = wanted.setdefault(request.item_id, {})
            if path in seen and seen[path].digest != digest:
                raise QewError(f"{request.where + ': ' if request.where else ''}`{path}` on {request.item_id} is given two different files", 2)
            seen[path] = row
    changed: list[Row] = []
    unchanged = 0
    for item_id, by_name in wanted.items():
        current = attachments_after(ledger.load_events(item_id)) if (ledger.items_dir / f"{item_id}.md").exists() else {}
        new = [row for path, row in by_name.items() if current.get(path) is None or current[path].digest != row.digest]
        unchanged += len(by_name) - len(new)
        names = set(by_name)
        for row in new:
            clash = attachments.clashes((set(current) - names) | names, row.path)
            if clash:
                raise QewError(f"`{row.path}` on {item_id} cannot be both a file and a directory (clashes with {', '.join(clash)}); detach one first", 2)
        stored = {value.digest: value.size for value in current.values()}
        stored.update({row.digest: row.size for row in new})
        if sum(stored.values()) > attachments.ITEM_BYTES:
            raise QewError(f"{item_id} would hold {sum(stored.values())} bytes of attachments; the limit is {attachments.ITEM_BYTES}", 2)
        changed.extend(new)
    return Batch(tuple(changed), unchanged, len({row.item_id for row in changed}))


def apply(ledger: Ledger, batch: Batch) -> None:
    """Store the bytes and name them, row by row; the caller's write session commits it all at once."""
    for row in batch.rows:
        ledger.store_blob(row.item_id, row.file, row.blob)
        ledger.append_event(row.item_id, "attached", path=row.path, hash=row.digest, size=row.size, blob=row.blob)


def summary(batch: Batch, verb: str = "attached") -> str:
    """One line for the whole batch."""
    if not batch.rows:
        return f"attachments unchanged ({batch.unchanged} already attached)" if batch.unchanged else "nothing to attach"
    same = f" ({batch.unchanged} unchanged)" if batch.unchanged else ""
    return f"{verb} {len(batch.rows)} file(s) to {batch.items} item(s){same}"
