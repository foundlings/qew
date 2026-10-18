"""One snapshot for a whole batch: after the first full build, a later `snapshot()` re-reads only the items the batch wrote since.

Only `qew batch` turns this on, inside its write session, which holds the ledger lock: nobody else changes the files. What a
write touched is read from the session's journal (every ledger write is recorded there), so no write path has to cooperate.
Anything the journal does not explain (a new or removed item, a renamed id, a path outside items/ and events/) falls back to a
full rebuild; the result is always what a full build would return.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

type Held = dict[str | tuple[Path, str], tuple[object, int]]  # ledger path -> (snapshot, journal length); (path, "remote") -> (has a remote, 0)

_HELD: Held | None = None


@contextmanager
def reusing() -> Iterator[None]:
    global _HELD
    _HELD = {}
    try:
        yield
    finally:
        _HELD = None


def held() -> Held | None:
    return _HELD


def touched(root: Path, entries: list[tuple[Path, str | None]]) -> set[str] | None:
    """The item ids whose item or event files these journal entries wrote; None when an entry is not one a refresh can explain."""
    ids: set[str] = set()
    for path, _ in entries:
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            return None
        if parts and parts[0] in ("mirrors", "baselines", ".qew"):
            continue  # a snapshot reads neither
        if len(parts) < 2 or parts[0] not in ("items", "events"):
            return None
        ids.add(parts[1])
    return ids
