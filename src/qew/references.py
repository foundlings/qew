"""References to other repositories' issues (`owner/repo#123`) found in an item's text.

They are shown by `qew show` as plain text: never followed, never an edge, never a path. The pattern is strict (an owner
name, a repository name, a number) and refuses anything that continues a path, so hostile text cannot smuggle structure in.
"""

import re

_REFERENCE = re.compile(r"(?<![\w./-])([A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9._-]{0,99})#(\d{1,9})\b")


def external_references(*texts: str) -> list[str]:
    """Every distinct `owner/repo#N` in the texts, in order of first appearance."""
    found: list[str] = []
    for text in texts:
        for repo, number in _REFERENCE.findall(text):
            reference = f"{repo}#{number}"
            if reference not in found:
                found.append(reference)
    return found
