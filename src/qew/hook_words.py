"""The words a shell command must contain before any pre-tool guard rule can act on it, and the cheap test built on them.

Kept free of qew imports: the `qew` entry uses it to answer an inert hook event without loading the CLI. A guard rule for a new
binary needs its name here (`tests/test_hook_fast_walk.py` fails when the guard knows a binary this list lacks).
"""

import json

# Every simple-command name `hook_guard.refusals` acts on, plus `push` (`jj git push` of unevidenced work). A substring test, so a
# path (`/usr/bin/gh`), a `sh -c '...'` string and a word split by quotes all still match; a false hit only costs the slow path.
GUARDED: tuple[str, ...] = ("gh", "glab", "tea", "fj", "curl", "wget", "http", "xh", "push")
PAYLOAD_LIMIT = 1_000_000
AGENTS = ("claude", "codex", "cursor", "opencode")


def command_of(raw: str) -> str | None:
    """The shell command in an agent's hook payload, or None when there is none or the payload is not JSON."""
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else payload.get("command")
    return command if isinstance(command, str) else None


def inert(command: str | None) -> bool:
    """True when no pre-tool rule can refuse `command`: it is absent, or names none of the guarded binaries."""
    if not command:
        return True
    plain = command.translate({ord(c): None for c in "'\"\\"})  # the guard tokenizes like a shell, so g"h" is gh
    return not any(word in plain for word in GUARDED)
