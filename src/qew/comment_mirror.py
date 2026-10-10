"""Comments on a forge issue: how a ledger comment is written there, and how to read ours back.

A pushed comment starts with a marker naming its ledger comment id, so a rerun, another checkout or an echo of our own
comment is recognised, never imported again and never pushed twice. Threading degrades to a first line, since forge issue
comments have no parents. Forge text is untrusted data: it is only ever stored as text.
"""

import hashlib
import re

from qew.comments import Comment

_MARKER = re.compile(r"\A<!-- qew-comment:([0-9a-zA-Z.-]+) -->\n")
_REPLY = re.compile(r"\A\*In reply to [^\n]*\*\n\n")
_FOOTER = re.compile(r"\n\n---\n\*[^\n]*\*\s*\Z")
QUOTE_LIMIT = 80


def digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def render(comment: Comment, parent: Comment | None) -> str:
    """The forge body for a ledger comment: marker, an optional reply line, the text, a footer naming author and time."""
    lines = [f"<!-- qew-comment:{comment.id} -->"]
    if parent is not None:
        quoted = " ".join(parent.text.split())[:QUOTE_LIMIT]
        lines.append(f"*In reply to @{parent.author}: {quoted}*\n")
    lines.append(comment.text.rstrip("\n"))
    return "\n".join(lines) + f"\n\n---\n*{comment.author} · {comment.at} · via qew*\n"


def parse(body: str) -> tuple[str | None, str]:
    """(ledger comment id or None, the text) of a forge comment body; text of a marked comment excludes the generated lines."""
    found = _MARKER.match(body)
    if not found:
        return None, body
    rest = body[found.end():]
    rest = _REPLY.sub("", rest, count=1)
    rest = _FOOTER.sub("", rest, count=1)
    return found.group(1), rest.strip("\n")
