"""Mechanical parser for `Refs: ID` trailers in a commit message.

A citation is a trailer in the last paragraph of the message (never the subject
paragraph), written exactly `Refs: ID[, ID...]` at column zero. Body prose,
other casings and indented lines are not citations. Tokens that cannot be a
ledger id (`#12`, URLs, anything with punctuation) are ignored.
"""

import re

from qew.ledger_ids import valid_id

_TRAILER_LINE = re.compile(r"[A-Za-z][A-Za-z0-9-]*:[ \t]+\S")
_REFS = re.compile(r"Refs:[ \t]+(.+)")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
_SEPARATORS = re.compile(r"[,\s]+")


def cited_ids(message: str) -> list[str]:
    """Candidate ledger ids the message cites, in order, without duplicates."""
    paragraphs = _PARAGRAPH_BREAK.split(message.strip("\n"))
    if len(paragraphs) < 2:
        return []
    lines = paragraphs[-1].split("\n")
    if not all(_TRAILER_LINE.match(line) for line in lines):
        return []
    found: list[str] = []
    for line in lines:
        match = _REFS.fullmatch(line)
        if match is None:
            continue
        for token in _SEPARATORS.split(match.group(1).strip()):
            if token and valid_id(token) and token not in found:
                found.append(token)
    return found
