"""An item's author is a ledger fact: the actor of its first recorded event."""

from qew.ledger_labels import MAX_LABEL
from qew.ledger_store import Snapshot


def author(snapshot: Snapshot, item_id: str) -> str:
    """The first event's actor, or "" when the item has no events (nothing is guessed)."""
    events = snapshot.events[item_id]
    return min(events, key=lambda event: event.id).actor if events else ""


def label_problem(actor: str) -> str:
    """Why `author:ACTOR` cannot be a forge label, or ''."""
    name = f"author:{actor}"
    if len(name) > MAX_LABEL:
        return f"`{name[:20]}...` is longer than {MAX_LABEL} characters"
    if any(c.isspace() or c == "," or not c.isprintable() for c in actor):
        return f"actor `{actor!r}` has whitespace, a comma or a control character"
    return ""
