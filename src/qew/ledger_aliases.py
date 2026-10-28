"""Old ids of migrated items (`aliases:` in the item header) and how they keep resolving.

`qew migrate-ids` renames an item file and records the old id in its header. Until every reference,
event directory and mirror directory has followed (a migration may stop midway), the ledger is read
through the alias map: a reference or directory named by an alias means the item that carries it, and
a leftover file under an old id is superseded by the renamed copy. The map is derived from the item
headers; nothing else stores it.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from qew import ledger_layout
from qew.ledger_item import MAX_HEADER_BYTES, RULE, Item


@dataclass(frozen=True)
class Aliases:
    by_alias: dict[str, str]  # old id -> the id of the item that carries it
    superseded: frozenset[str]  # file stems of leftover copies under an old id
    owned: dict[str, tuple[str, ...]] = field(default_factory=dict)  # item id -> its aliases

    def canonical(self, reference: str) -> str:
        return self.by_alias.get(reference, reference)

    def of(self, item_id: str) -> tuple[str, ...]:
        return self.owned.get(item_id, ())


ITEM_FILE = "item.md"  # the item file inside items/<ID>/; only the directory carries the id


def item_of_path(relative: str, sharded: bool = False) -> str | None:
    """The item id of a ledger-relative path `items/<ID>/item.md` (`items/<xx>/<ID>/item.md` when sharded); None for any other path."""
    parts = relative.split("/")
    length = 4 if sharded else 3
    return parts[length - 2] if len(parts) == length and parts[0] == "items" and parts[length - 1] == ITEM_FILE else None


def _header_values(path: Path) -> dict[str, str]:
    try:
        with path.open("rb") as stream:
            text = stream.read(MAX_HEADER_BYTES).decode("utf-8", "replace").replace("\r\n", "\n")
    except OSError:
        return {}
    return header_values_of(text)


def header_values_of(text: str) -> dict[str, str]:
    """The `id` and `aliases` values of an item file's text, read like the file itself: header only, bounded bytes."""
    text = text[:MAX_HEADER_BYTES].replace("\r\n", "\n")
    found: dict[str, str] = {}
    for line in text.split("\n"):
        if line == RULE:
            break
        key, colon, value = line.partition(":")
        if colon and key in ("id", "aliases") and key not in found:
            found[key] = value.strip()
    return found


class _Index:
    """What the item headers of one ledger said: stem -> its aliases, and the map built from them."""

    def __init__(self, stamp: int, headers: dict[str, tuple[str, ...]]) -> None:
        self.stamp = stamp
        self.headers = headers
        self.aliased = {stem: found for stem, found in headers.items() if found}
        self.aliases = _build(self.aliased, headers)

    def refresh(self, stamp: int, changed: set[str], items_dir: Path, sharded: bool = False) -> None:
        for stem in changed:
            found = _read(ledger_layout.home(items_dir, stem, sharded) / ITEM_FILE)
            if found is None:
                self.headers.pop(stem, None)
            else:
                self.headers[stem] = found
            if found:
                self.aliased[stem] = found
            else:
                self.aliased.pop(stem, None)
        self.stamp = stamp
        self.aliases = _build(self.aliased, self.headers)


_indexes: dict[str, _Index] = {}
_changed: dict[str, set[str]] = {}  # item stems this process wrote or removed since the last scan


def note_change(path: Path) -> None:
    """This process wrote or removed `path`: an item file is re-read on the next scan, not the whole directory."""
    if path.name != ITEM_FILE:
        return
    if path.parent.parent.name == "items":
        _changed.setdefault(str(path.parent.parent), set()).add(path.parent.name)
    elif path.parent.parent.parent.name == "items":  # sharded: items/<xx>/<ID>/item.md
        _changed.setdefault(str(path.parent.parent.parent), set()).add(path.parent.name)


def _read(path: Path) -> tuple[str, ...] | None:
    header = _header_values(path)
    if header.get("id") != path.parent.name:
        return None  # a mismatch is `qew check`'s to report
    return tuple(header.get("aliases", "").split())


def _build(aliased: dict[str, tuple[str, ...]], headers: dict[str, tuple[str, ...]]) -> Aliases:
    by_alias: dict[str, str] = {}
    owned: dict[str, tuple[str, ...]] = {}
    for stem in sorted(aliased):
        owned[stem] = tuple(sorted(aliased[stem]))
        for alias in aliased[stem]:
            by_alias.setdefault(alias, stem)
    superseded = frozenset(alias for alias, owner in by_alias.items() if alias != owner and alias in headers)
    return Aliases(by_alias, superseded, owned)


def _aliases_in(stem: str, text: str) -> tuple[str, ...] | None:
    header = header_values_of(text)
    return tuple(header.get("aliases", "").split()) if header.get("id") == stem else None


def scan(items_dir: Path, texts: Callable[[], Mapping[str, str]] | None = None, sharded: bool = False) -> Aliases:
    """The alias map. Headers are read once; later this process re-reads only the files it wrote, and a directory
    that changed behind its back (another process) is read again in full. `texts` gives every item file's text by
    directory name (the snapshot cache): the full build then reads no file."""
    try:
        stamp = ledger_layout.stamp(items_dir, sharded)
    except OSError:
        return Aliases({}, frozenset())
    key = str(items_dir)
    changed = _changed.pop(key, set())
    index = _indexes.get(key)
    if index is None or (not changed and index.stamp != stamp):
        headers = {}
        if texts is not None:
            for stem, text in sorted(texts().items()):
                found = _aliases_in(stem, text)
                if found is not None:
                    headers[stem] = found
        else:
            for directory in ledger_layout.homes(items_dir, sharded):
                found = _read(directory / ITEM_FILE)
                if found is not None:
                    headers[directory.name] = found
        index = _indexes[key] = _Index(stamp, headers)
    elif changed:
        index.refresh(stamp, changed, items_dir, sharded)
    return index.aliases


def canonical_item(item: Item, aliases: Aliases) -> Item:
    """The item with every link it holds named by its current id."""
    if not aliases.by_alias:
        return item
    return replace(
        item,
        parent=aliases.canonical(item.parent),
        needs=tuple(aliases.canonical(n) for n in item.needs),
        tracks=aliases.canonical(item.tracks),
        pr=aliases.canonical(item.pr),
        discovered_from=aliases.canonical(item.discovered_from),
    )
