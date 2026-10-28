"""Where an item's directories live: flat (`events/<ID>/`) up to schema 7, sharded (`events/_<xx>/<ID>/`) from schema 8.

A commit rewrites every directory on the path of what it changes, so one directory of 25,000 item directories made each
write rewrite a 25,000-entry tree. The shard is the last two characters of the id, lower-cased: ids are time-ordered, so a
leading prefix would put everything in one shard, and the trailing characters spread evenly (about 1,200 shards). Lower case
keeps two spellings from naming one directory on a case-insensitive file system. The shard directory starts with an underscore,
which an id never does: `items/AA` (an item) and `items/_aa` (a shard) can never be one directory, in a half-moved ledger or on a
case-insensitive disk, and a directory directly under `items` that does not start with one is an item directory in the wrong place.
The shard is not stored anywhere: it is computed from the id, so a rename moves the directory and nothing else records it.
"""

from pathlib import Path

SHARDED_SCHEMA = 8
SHARDED_SINCE = "6.46.0"  # the first qew that reads a schema 8 ledger: every agent on the ledger must run it before the migration


PREFIX = "_"


def shard(name: str) -> str:
    return PREFIX + name[-2:].lower()


def is_sharded(schema: int) -> bool:
    return schema >= SHARDED_SCHEMA


def home(root: Path, name: str, sharded: bool) -> Path:
    """The directory of one item (or of an id it had before a migration) under `items`, `events`, `mirrors` or `baselines`."""
    return root / shard(name) / name if sharded else root / name


def homes(root: Path, sharded: bool) -> list[Path]:
    """Every item directory under `root`, sorted by name."""
    if not root.is_dir():
        return []
    if not sharded:
        return sorted(path for path in root.iterdir() if path.is_dir())
    found = [item for group in root.iterdir() if group.is_dir() and group.name.startswith(PREFIX) for item in group.iterdir() if item.is_dir()]
    return sorted(found, key=lambda path: path.name)


def stamp(root: Path, sharded: bool) -> int:
    """A number that grows whenever an item directory is added to or removed from `root`: the directory's own modification time,
    or in a sharded layout that of the root plus every shard (a new item changes its shard, not the root)."""
    total = root.stat().st_mtime_ns
    if sharded:
        total += sum(group.stat().st_mtime_ns for group in root.iterdir() if group.is_dir())
    return total


def id_of(parts: tuple[str, ...] | list[str], sharded: bool) -> str | None:
    """The item id in the parts of a ledger-relative path under `items`, `events`, `mirrors` or `baselines`
    (`events/<ID>/…` flat, `events/<xx>/<ID>/…` sharded); None when the path is too short to name one."""
    index = 2 if sharded else 1
    return parts[index] if len(parts) > index else None


def notice(schema: int) -> str:
    """What to tell the user about a ledger still on the flat layout, or "" once it is sharded (schema 8). Older than schema 4 is
    refused outright with its own message, so only a working flat ledger gets this."""
    if schema >= SHARDED_SCHEMA:
        return ""
    return (f"this ledger is on schema {schema}: item directories are not sharded, so every write rewrites one huge directory and slows with the "
            f"ledger. Tell your user to run `qew migrate-layout --dry-run`, then `qew migrate-layout` once, after every agent on the ledger "
            f"runs qew {SHARDED_SINCE} or newer (older ones refuse a schema {SHARDED_SCHEMA} ledger).")


def flat_children(root: Path) -> list[Path]:
    """The item directories sitting directly under `root` (not in a shard): everything to move on the way to the sharded layout."""
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith(PREFIX))
