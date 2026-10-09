"""Where the last successful forge pull stopped, so the next one can ask only for what changed since.

The cursor is local state (`.qew/state/TARGET/cursor.json`, never committed). It records the START time of the
last successful pull and of the last successful FULL listing. A pull lists from `pulled_at` minus a safety margin
(clock skew between this machine and the forge, and edits racing the listing); a full listing is due when there is
no usable cursor, when asked for, or when the last one is older than a day. Only a full listing may conclude that
an issue no longer exists on the forge.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from qew import instants
from qew.jsontypes import JsonObject

MARGIN = timedelta(seconds=120)
FULL_EVERY = timedelta(hours=24)
MODES = ("conditional", "incremental", "full")


@dataclass(frozen=True)
class Cursor:
    pulled_at: datetime  # start of the last successful pull
    full_at: datetime  # start of the last pull that saw the complete issue set
    mode: str  # how that pull listed: conditional (ETag pages), incremental (since) or full

    def to_json(self) -> JsonObject:
        return {"pulled_at": instants.format_instant(self.pulled_at), "full_at": instants.format_instant(self.full_at), "mode": self.mode}


def parse(value: object) -> Cursor | None:
    """The cursor a file holds, or None when it is absent or damaged (the cost is one extra full listing)."""
    if not isinstance(value, dict):
        return None
    pulled, full = value.get("pulled_at"), value.get("full_at")
    if not isinstance(pulled, str) or not isinstance(full, str):
        return None
    pulled_at, full_at = instants.parse_instant(pulled), instants.parse_instant(full)
    mode = value.get("mode")
    if pulled_at is None or full_at is None or mode not in MODES:
        return None
    return Cursor(pulled_at, full_at, mode)


def full_due(cursor: Cursor | None, now: datetime) -> bool:
    return cursor is None or now - cursor.full_at >= FULL_EVERY


def since_of(cursor: Cursor | None, now: datetime, force_full: bool) -> str:
    """The `since` argument of the listing: empty means everything."""
    if force_full or cursor is None or full_due(cursor, now):
        return ""
    return instants.format_instant(cursor.pulled_at - MARGIN)


def after_pull(cursor: Cursor | None, started: datetime, mode: str) -> Cursor:
    """`mode` conditional or full saw the complete set; an incremental pull keeps the earlier full time."""
    return Cursor(started, cursor.full_at if mode == "incremental" and cursor else started, mode)


def next_mode(cursor: Cursor | None, now: datetime) -> str:
    """How the next pull will list, as `qew status` shows it."""
    if cursor is None or full_due(cursor, now):
        return "full"
    return cursor.mode
