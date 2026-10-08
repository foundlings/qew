"""The release and discussion history of one item, as `qew show` prints it."""

from typing import List

from qew import untrusted_text
from qew.ledger_event import Event


def comments(events: List[Event]) -> List[dict]:
    return [{"at": e.at, "actor": e.actor, "body": e.get("body")} for e in sorted(events, key=lambda e: e.id) if e.type == "comment"]


def comment_lines(events: List[Event]) -> List[str]:
    lines: List[str] = []
    for entry in comments(events):
        lines.append(f"comment: {entry['actor']} {entry['at']}")
        lines.extend(f"  {line}" for line in untrusted_text.show(entry["body"].rstrip()).text.splitlines())
    return lines
