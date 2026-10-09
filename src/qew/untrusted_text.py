"""One helper for forge, import and merge text that reaches an agent through command output.

A title is data. It is flattened to one line, bounded, quoted once it was
rewritten, and withheld entirely when it hides characters (zero-width,
bidirectional override, control) that a reader cannot see.
"""

from dataclasses import dataclass
import json
import unicodedata

from qew import textscan

MAX_TITLE_BYTES = 96
ELLIPSIS = "…"


def findings(text: str) -> tuple[str, ...]:
    """Sorted, unique names of the hidden-character classes found in `text`.

    The classes are textscan's (zero_width, bidi_control, unicode_tags, confusable) plus `control`: control,
    format, private-use and unassigned characters that textscan's vocabulary does not already cover.
    """
    found = set(textscan.scan(text))
    for char in text:
        if not textscan.covers(ord(char)) and not char.isspace() and unicodedata.category(char) in ("Cc", "Cf", "Co", "Cs", "Cn"):
            found.add("control")
            break
    return tuple(sorted(found))


@dataclass(frozen=True)
class Shown:
    """How a title may be printed. `withheld` lists the findings that forbid printing it at all."""

    text: str
    withheld: tuple[str, ...]
    rewritten: bool

    @property
    def safe_for_command(self) -> bool:
        """False when a runnable command must not be printed beside this title."""
        return not self.withheld and not self.rewritten


def show(title: str) -> Shown:
    hidden = findings(title)
    if hidden:
        return Shown(f"[title withheld: {', '.join(hidden)}]", hidden, True)
    flat = " ".join(title.split())
    bounded = flat
    if len(flat.encode("utf-8")) > MAX_TITLE_BYTES:
        bounded = flat.encode("utf-8")[:MAX_TITLE_BYTES].decode("utf-8", errors="ignore") + ELLIPSIS
    rewritten = bounded != title
    return Shown(json.dumps(bounded, ensure_ascii=False) if rewritten else bounded, (), rewritten)
