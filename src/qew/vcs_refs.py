"""Mechanical parser for `Refs: ID` trailers in a commit message.

A citation is a trailer in the last paragraph of the message (never the subject
paragraph), written exactly `Refs: ID[, ID...]` at column zero. Body prose,
other casings and indented lines are not citations. Only the leading ids of the
trailer are read: the list ends at the first token that cannot be a ledger id
(`#12`, a URL, prose with punctuation) and after an id that ends a sentence
(`Refs: 042h3tmc9. Generated only through ...` cites 042h3tmc9 alone).
"""

import re

from qew.ledger_ids import valid_id

_TRAILER_LINE = re.compile(r"[A-Za-z][A-Za-z0-9-]*:[ \t]+\S")
_REFS = re.compile(r"Refs:[ \t]+(.+)")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
_SEPARATORS = re.compile(r"[,\s]+")
_SENTENCE_END = ".;:"


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
            ended = token[-1:] in tuple(_SENTENCE_END)
            core = token.rstrip(_SENTENCE_END) if ended else token
            if not core or not valid_id(core):
                break  # prose or a lookalike: the ids are over
            if core not in found:
                found.append(core)
            if ended:
                break
    return found
