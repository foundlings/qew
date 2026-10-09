"""The quoted human requester every created item must carry."""

import re

from qew.errors import QewError

HEADING = "## Requested by"
FORM = "Name: 'their words'"
MINIMUM_WORDS = 12
_QUOTE = re.compile(
    r"([^\W\d_][\w .'-]{0,40}?)\s*:\s*(['\"“‘])([^\r\n]+)(['\"”’])"
)
_PLACEHOLDER = re.compile(
    r"^[.…\s]*$|<[^>]*>|\b(todo|tbd|placeholder|his words|her words|their words)\b",
    re.IGNORECASE,
)


def require_quote(quote: str | None, context: str = "--quote") -> str:
    """Return the validated quote or refuse (exit 2) before any GitHub call."""
    if quote is None or not quote.strip():
        raise QewError(f"a human requester quote is required: {context} \"{FORM}\"", 2)
    text = quote.strip()
    match = _QUOTE.fullmatch(text)
    if match is None:
        raise QewError(f"requester quote must have the form {FORM} on one line ({context})", 2)
    words = match.group(3).strip()
    if len(words) < MINIMUM_WORDS:
        raise QewError(
            f"requester quote is too short; quote at least {MINIMUM_WORDS} characters of "
            f"the human's words ({context})",
            2,
        )
    if _PLACEHOLDER.search(words):
        raise QewError(f"requester quote is a placeholder, not a human's words ({context})", 2)
    return text


def requested_by(quote: str) -> str:
    return f"{HEADING}\n\n> {quote}"


def with_requested_by(text: str | None, quote: str) -> str:
    section = requested_by(quote)
    body = (text or "").rstrip()
    return f"{body}\n\n{section}" if body else section
