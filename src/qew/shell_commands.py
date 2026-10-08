"""Recognize `gh` commands in shell text without ever executing it.

Command text comes from an agent and is data: it is tokenized the way a shell
would, split into simple commands at operators, and matched structurally. A
quoted example such as `echo "gh issue edit 3"` is one argument, never a command.
"""

from dataclasses import dataclass
import re
import shlex
from typing import List, Optional, Union


_ENVIRONMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_NUMBER = re.compile(r"[1-9][0-9]*")
_PULL_URL = re.compile(r"https?://[^/\s]+/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_MERGE_VALUE_FLAGS = frozenset(
    {
        "-R", "--repo", "-b", "--body", "-F", "--body-file", "-t", "--subject",
        "-A", "--author-email", "--match-head-commit",
    }
)
_ISSUE_ADVICE = {
    "create": "qew create",
    "edit": "qew set",
    "close": "qew complete (finished work) or qew close (abandoned, duplicate, delivered or superseded)",
    "reopen": "qew reopen",
}


@dataclass(frozen=True)
class Merge:
    """`gh pr merge N`: the merged pull request, and the repository if named."""

    pull_request: int
    repository: Optional[str]


@dataclass(frozen=True)
class RawWrite:
    """A `gh` command that changes issues or opens a pull request around Qew."""

    command: str
    advice: str


def recognize(command: str) -> List[Union[Merge, RawWrite]]:
    found: List[Union[Merge, RawWrite]] = []
    for segment in _segments(command):
        found.extend(_classify(segment))
    return found


def _segments(command: str) -> List[List[str]]:
    segments: List[List[str]] = []
    for line in command.splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        try:
            tokens = list(lexer)
        except ValueError:
            continue
        current: List[str] = []
        for token in tokens:
            if token and all(character in "();<>|&" for character in token):
                if current:
                    segments.append(current)
                current = []
            else:
                current.append(token)
        if current:
            segments.append(current)
    return segments


def _classify(tokens: List[str]) -> List[Union[Merge, RawWrite]]:
    index = 0
    while index < len(tokens) and _ENVIRONMENT.match(tokens[index]):
        index += 1
    words = tokens[index:]
    if len(words) < 3 or words[0].rsplit("/", 1)[-1] != "gh":
        return []
    area, action = words[1], words[2]
    if area == "issue" and action in _ISSUE_ADVICE:
        return [RawWrite(f"gh issue {action}", _ISSUE_ADVICE[action])]
    if area == "pr" and action == "create":
        return [RawWrite("gh pr create", "qew pr --for ISSUE")]
    if area == "pr" and action == "merge":
        if "--disable-auto" in words[3:]:
            return []
        merge = _merge(words[3:])
        raw = RawWrite("gh pr merge", "qew pr merge PR")
        return [raw] if merge is None else [raw, merge]
    return []


def _merge(arguments: List[str]) -> Optional[Merge]:
    repository: Optional[str] = None
    pull_request: Optional[int] = None
    explicit: Optional[str] = None
    skip = False
    for position, argument in enumerate(arguments):
        if skip:
            skip = False
            continue
        if argument in ("--auto", "--disable-auto"):
            return None
        if argument.startswith("-"):
            name, equals, value = argument.partition("=")
            if name in _MERGE_VALUE_FLAGS:
                if not equals:
                    skip = True
                    value = arguments[position + 1] if position + 1 < len(arguments) else ""
                if name in ("-R", "--repo"):
                    if not _REPOSITORY.fullmatch(value):
                        return None
                    explicit = value
            continue
        if pull_request is not None:
            continue
        url = _PULL_URL.fullmatch(argument)
        if _NUMBER.fullmatch(argument):
            pull_request = int(argument)
        elif url:
            repository, pull_request = url.group(1), int(url.group(2))
        else:
            return None
    if pull_request is None:
        return None
    return Merge(pull_request, explicit or repository)
