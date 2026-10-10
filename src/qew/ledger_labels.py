"""Label names a ledger accepts: one word, bounded, and outside the namespaces sync owns."""

from collections.abc import Iterable

from qew.errors import QewError

MAX_LABEL = 50
# Sync writes `qew:STATE`, `qew:managed`, `status:STATUS`, `epic`/`task`, `P0`..`P4`, `author:LOGIN` and the status labels `ready` and `in-progress` (and `gated` while an item is gated) itself.
RESERVED_PREFIXES = ("qew:", "kind:", "priority:", "status:", "author:")
RESERVED_NAMES = ("in-progress", "ready", "epic", "task", "p0", "p1", "p2", "p3", "p4")  # the reference board's claim label, the kind labels and the priority labels


def problem(label: str) -> str | None:
    """Why `label` cannot be a ledger label, or None."""
    if not label.strip():
        return "label must not be empty"
    if len(label) > MAX_LABEL:
        return f"label `{label[:20]}...` is longer than {MAX_LABEL} characters"
    if label != label.strip() or any(c.isspace() or c == "," or not c.isprintable() for c in label):
        return f"label `{label!r}` must be one word without commas or control characters"
    if label.casefold().startswith(RESERVED_PREFIXES) or label.casefold() in RESERVED_NAMES:
        return f"label `{label}` is in a namespace reserved for qew (qew:, kind:, priority:, status:, author:, epic, task, P0-P4)"
    return None


def require_valid(labels: Iterable[str]) -> None:
    for label in labels:
        found = problem(label)
        if found:
            raise QewError(found, 2)
