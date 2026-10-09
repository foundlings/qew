"""Code-shaped text in a body or comment that is not marked as code: a hint, never a gate."""

import re
from typing import List

_FENCE = re.compile(r"```.*?(```|\Z)", re.DOTALL)
_SPAN = re.compile(r"`[^`\n]*`")
_COMMAND = re.compile(r"(?<![\w`-])qew\s+[a-z][a-z-]*")
_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
_PATH = re.compile(r"(?<![\w/.])(?:\.{1,2}/|~/|/)[\w./-]+|(?<![\w/.])[\w-]+(?:/[\w-]+)*/[\w-]+\.[A-Za-z0-9]+")


def unmarked(text: str) -> List[str]:
    """Command, flag and path shaped tokens outside backticks and fences, once each, in order."""
    bare = _SPAN.sub(" ", _FENCE.sub(" ", text))
    found: List[str] = []
    for pattern in (_COMMAND, _FLAG, _PATH):
        for match in pattern.finditer(bare):
            token = " ".join(match.group(0).split())
            if token not in found:
                found.append(token)
    return found


def warnings(text: str) -> List[str]:
    return [f"warning: unmarked code-shaped text: {token}" for token in unmarked(text)]
