"""An item's author is a ledger fact: the actor of its first recorded event."""

from qew.ledger_event import Event
from qew.ledger_labels import MAX_LABEL
from qew.ledger_store import Snapshot


def _correction(snapshot: Snapshot, item_id: str) -> Event | None:
    found = [event for event in snapshot.events[item_id] if event.type == "author_corrected"]
    return max(found, key=lambda event: event.id) if found else None


def author(snapshot: Snapshot, item_id: str) -> str:
    """The newest correction's author, else the first event's actor, or "" when the item has no events (nothing is guessed)."""
    corrected = _correction(snapshot, item_id)
    if corrected is not None:
        return str(corrected.get("author"))
    events = snapshot.events[item_id]
    return min(events, key=lambda event: event.id).actor if events else ""


def via(snapshot: Snapshot, item_id: str) -> str:
    """The tool that filed the item for its author (`via` on the correction, else on the first event), or ""."""
    corrected = _correction(snapshot, item_id)
    events = snapshot.events[item_id]
    source = corrected if corrected is not None else (min(events, key=lambda event: event.id) if events else None)
    value = source.get("via") if source is not None else None
    return value if isinstance(value, str) else ""


def label_problem(actor: str) -> str:
    """Why `author:ACTOR` cannot be a forge label, or ''."""
    name = f"author:{actor}"
    if len(name) > MAX_LABEL:
        return f"`{name[:20]}...` is longer than {MAX_LABEL} characters"
    if any(c.isspace() or c == "," or not c.isprintable() for c in actor):
        return f"actor `{actor!r}` has whitespace, a comma or a control character"
    return ""
