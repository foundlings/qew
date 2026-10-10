"""Cleaning of text that came from a forge: untrusted data, stored only as plain printable text."""

import re
import unicodedata

TITLE_LIMIT = 200
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_line(text: str) -> str:
    """One printable line: controls, newlines and format characters become spaces, capped in length."""
    flat = "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in text)
    return " ".join(flat.split())[:TITLE_LIMIT]


def clean_body(text: str) -> str:
    return _CONTROL.sub("", text.replace("\r\n", "\n"))
