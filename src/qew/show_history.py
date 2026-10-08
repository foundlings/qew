"""The release and discussion history of one item, as `qew show` prints it."""

from typing import List

from qew import untrusted_text
from qew.ledger_event import Event


def _safe(text: str, keep_lines: bool = False) -> str:
    """Full text, unless it hides characters a reader cannot see; whitespace flattened unless lines are kept."""
    hidden = untrusted_text.findings(text)
    if hidden:
        return f"[text withheld: {', '.join(hidden)}]"
    return text.rstrip() if keep_lines else " ".join(text.split())


def comments(events: List[Event]) -> List[dict]:
    return [{"at": e.at, "actor": e.actor, "body": e.get("body")} for e in sorted(events, key=lambda e: e.id) if e.type == "comment"]


def comment_lines(events: List[Event]) -> List[str]:
    lines: List[str] = []
    for entry in comments(events):
        lines.append(f"comment: {entry['actor']} {entry['at']}")
        lines.extend(f"  {line}" for line in _safe(entry["body"], True).splitlines())
    return lines


def approvals(events: List[Event]) -> List[dict]:
    """Every release and withdrawal in order: {type, at, actor, quote, reason}."""
    return [
        {"type": e.type, "at": e.at, "actor": e.actor, "quote": e.get("quote"), "reason": e.get("reason", None)}
        for e in sorted(events, key=lambda e: e.id)
        if e.type in ("approved", "reverted")
    ]


def release_lines(events: List[Event]) -> List[str]:
    """The latest release or withdrawal, readable without opening event files."""
    history = approvals(events)
    if not history:
        return []
    last = history[-1]
    quote = _safe(last["quote"])
    where = f"(by {last['actor']} at {last['at']})"
    if last["type"] == "approved":
        return [f"released: {quote} {where}"]
    return [f"withdrawn: {_safe(last['reason'])} - {quote} {where}"]
