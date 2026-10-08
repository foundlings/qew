"""Label names a ledger accepts: one word, bounded, and outside the namespaces sync owns."""

from typing import Iterable, Optional

from qew.errors import QewError

MAX_LABEL = 50
# Sync writes `qew:STATE`, `kind:KIND`, `priority:N` and `author:LOGIN` itself.
RESERVED_PREFIXES = ("qew:", "kind:", "priority:", "author:")


def problem(label: str) -> Optional[str]:
    """Why `label` cannot be a ledger label, or None."""
    if not label.strip():
        return "label must not be empty"
    if len(label) > MAX_LABEL:
        return f"label `{label[:20]}...` is longer than {MAX_LABEL} characters"
    if label != label.strip() or any(c.isspace() or c == "," or not c.isprintable() for c in label):
        return f"label `{label!r}` must be one word without commas or control characters"
    if label.casefold().startswith(RESERVED_PREFIXES):
        return f"label `{label}` is in a namespace reserved for qew (qew:, kind:, priority:, author:)"
    return None


def require_valid(labels: Iterable[str]) -> None:
    for label in labels:
        found = problem(label)
        if found:
            raise QewError(found, 2)
