"""The text of a pull request body: derived from the item, scanned for closing references.

Finishing work is `qew complete`, never a forge side effect, so no body may carry a keyword
that makes the forge close an issue when the pull request merges.
"""

import re

from qew.errors import QewError
from qew.forge import marker_for

_CLOSING = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s*"
    r"(?:[\w.-]+/[\w.-]+#\d+|GH-\d+|#\d+|https?://\S+)",
    re.IGNORECASE,
)
_SNIPPET_LIMIT = 60


def refuse_closing_references(text: str, where: str) -> None:
    """Exit 2 when `text` closes an issue on merge; a `Delivers`/`Part of` back-reference is fine."""
    found = _CLOSING.search(text)
    if found:
        snippet = found.group(0)[:_SNIPPET_LIMIT]
        raise QewError(
            f"{where} carries a closing reference ({snippet!r}); a pull request never closes issues, "
            "`qew complete` does",
            2,
        )


def build_body(
    item_id: str,
    issue_number: int,
    what: str,
    why: str | None,
    testing: str | None,
    extra: str,
    links: str = "",
) -> str:
    """Marker, the `Delivers` line, then the What / Why / Testing sections that have text, then the `links` block, then `extra`."""
    parts = [marker_for(item_id), f"Delivers {item_id} (issue #{issue_number})."]
    for heading, text in (("What", what), ("Why", why), ("Testing", testing)):
        if text and text.strip():
            parts.append(f"## {heading}\n\n{text.strip()}")
    if links:
        parts.append(links)
    if extra.strip():
        parts.append(extra.rstrip("\n"))
    return "\n\n".join(parts) + "\n"


def build_no_item_body(reason: str, what: str | None, why: str | None, testing: str | None, extra: str, links: str) -> str:
    """The body of a pull request that has no ledger item: the reason, then What / Why / Testing, the links block, and `extra`."""
    parts = [f"No ledger item: {' '.join(reason.split())}."]
    for heading, text in (("What", what), ("Why", why), ("Testing", testing)):
        if text and text.strip():
            parts.append(f"## {heading}\n\n{text.strip()}")
    parts.append(links)
    if extra.strip():
        parts.append(extra.rstrip("\n"))
    return "\n\n".join(parts) + "\n"
