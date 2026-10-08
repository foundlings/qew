"""High-precision advisory detection for code-shaped Markdown prose."""

import re
from typing import List, Tuple


LONG_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
PATH = re.compile(r"(?<![:\w])(?:\.{1,2}/|/)[A-Za-z0-9._~/-]+")
COMMANDS = (
    re.compile(r"(?<![\w-])qew\s+(?:apply|block|blocked|campaign|close|complete|create|graph|hooks|init|list|plan|pr|prime|project|ready|reopen|search|set|show|start|triage|unblock)\b"),
    re.compile(r"(?<![\w-])gh\s+(?:api|issue|label|pr)\b"),
    re.compile(r"(?<![\w-])git\s+(?:branch|commit|diff|log|pull|push|show|status|switch)\b"),
    re.compile(r"(?<![\w-])python3?\s+-m\b"),
    re.compile(r"(?<![\w-])pipx\s+(?:install|run|uninstall|upgrade)\b"),
)


def unmarked_code_tokens(markdown: str) -> List[str]:
    visible = _without_marked_code(markdown)
    matches: List[Tuple[int, str]] = []
    for pattern in (*COMMANDS, LONG_FLAG, PATH):
        for match in pattern.finditer(visible):
            token = match.group(0).rstrip(".,;:!?)]}")
            matches.append((match.start(), token))
    tokens = []
    for _, token in sorted(matches):
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _without_marked_code(markdown: str) -> str:
    output = []
    fence = None
    for line in markdown.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = next((value for value in ("```", "~~~") if stripped.startswith(value)), None)
        if marker is not None and (fence is None or marker == fence):
            fence = marker if fence is None else None
            output.append(_spaces(line))
        elif fence is not None:
            output.append(_spaces(line))
        else:
            output.append(re.sub(r"`[^`\n]*`", lambda match: " " * len(match.group(0)), line))
    return "".join(output)


def _spaces(value: str) -> str:
    return "".join("\n" if character == "\n" else " " for character in value)
