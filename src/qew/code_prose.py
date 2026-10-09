"""A high-precision detector for commands, flags and paths written into prose without code marking.

Agents that quote `qew start X --force` unmarked in an item body teach later readers to treat it as an
instruction. Inline code spans and fenced blocks (backtick or tilde, indented or not; an unclosed fence
runs to the end) are code; everything else is scanned. Findings are advisory and never block a write.
"""

from functools import lru_cache
import re
import sys
from collections.abc import Iterable

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_INLINE = re.compile(r"(`+).+?\1")
_FLAG = re.compile(r"(?<![\w-])--[A-Za-z][\w-]*")
_PATH = re.compile(r"(?<![\w:/.~-])(?:~|\.{1,2})?/[\w.@+-]+(?:/[\w.@+-]*)*")
_PYTHON = re.compile(r"\bpython3?\s+-m\b")
_GIT = ("add", "branch", "checkout", "clone", "commit", "diff", "fetch", "log", "merge", "pull", "push", "rebase", "reset", "stash", "status", "tag")
_GH = ("api", "auth", "issue", "label", "pr", "project", "release", "repo", "run")
_PIPX = ("install", "uninstall", "run", "upgrade", "inject")


@lru_cache(maxsize=1)
def _qew_words() -> tuple[str, ...]:
    from qew import cli  # late: cli imports this module

    return tuple(sorted(cli.top_level_commands()))  # built once: the parser is expensive and scanned bodies are many


def _invocation(words: Iterable[str]) -> re.Pattern[str]:
    return re.compile(r"\b(qew|git|gh|pipx)\s+(" + "|".join(re.escape(w) for w in words) + r")\b")


def _prose_lines(text: str) -> Iterable[str]:
    fence = ""
    for line in text.splitlines():
        opening = _FENCE.match(line)
        if fence:
            if opening and opening.group(1)[0] == fence[0] and len(opening.group(1)) >= len(fence):
                fence = ""
            continue
        if opening:
            fence = opening.group(1)
            continue
        yield _INLINE.sub(" ", line)


def scan(text: str) -> list[str]:
    """Code-shaped tokens in unmarked prose, in order of first appearance."""
    invocations = _invocation(sorted({*_qew_words(), *_GIT, *_GH, *_PIPX}))
    allowed = {"qew": set(_qew_words()), "git": set(_GIT), "gh": set(_GH), "pipx": set(_PIPX)}
    found: list[str] = []
    for line in _prose_lines(text):
        hits = []
        for match in invocations.finditer(line):
            if match.group(2) in allowed[match.group(1)]:
                hits.append((match.start(), f"{match.group(1)} {match.group(2)}"))
        hits += [(m.start(), re.sub(r"\s+", " ", m.group(0))) for m in _PYTHON.finditer(line)]
        hits += [(m.start(), m.group(0)) for pattern in (_FLAG, _PATH) for m in pattern.finditer(line)]
        found += [token for _, token in sorted(hits)]
    return list(dict.fromkeys(found))


def warn(bodies: Iterable[str]) -> None:
    """Print one stderr warning per distinct token across `bodies`."""
    tokens = list(dict.fromkeys(token for body in bodies for token in scan(body or "")))
    for token in tokens:
        print(f"warning: unmarked code-shaped text: {token}", file=sys.stderr)
