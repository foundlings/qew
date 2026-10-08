"""The shared logic behind `qew hook`: what a shell-command hook says or does.

Agents call `qew hook pre-shell|post-shell --agent NAME` around every shell
command. The hook never blocks and never fails: the worst outcome is silence or
a message naming the Qew command to run by hand.
"""

import json
from typing import Callable, Iterable, List, Optional

from qew.shell_commands import Merge, RawWrite, recognize


LandPullRequest = Callable[[Optional[str], int], str]

_WARNING = (
    "This repository is managed with Qew. Use `{advice}` instead of `{command}`; "
    "do not work around Qew with gh."
)


def shell_command(raw: str) -> Optional[str]:
    """The command text of an agent's hook payload, or None when it has none."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else payload.get("command")
    return command if isinstance(command, str) else None


def message(event: str, raw: str, land: LandPullRequest) -> Optional[str]:
    command = shell_command(raw)
    if not command:
        return None
    found = recognize(command)
    if event == "pre-shell":
        return _warnings(item for item in found if isinstance(item, RawWrite))
    lines = [
        land(item.repository, item.pull_request) for item in found if isinstance(item, Merge)
    ]
    return "\n".join(lines) or None


def render(agent: str, event: str, text: Optional[str]) -> str:
    """The agent's non-blocking output shape; empty when there is nothing to say."""
    if agent in ("claude", "codex"):
        if text is None:
            return ""
        name = "PreToolUse" if event == "pre-shell" else "PostToolUse"
        return json.dumps(
            {"hookSpecificOutput": {"additionalContext": text, "hookEventName": name}},
            sort_keys=True,
        )
    if agent == "cursor":
        if event != "pre-shell":
            return ""
        decision = {"permission": "allow"}
        if text is not None:
            decision["agent_message"] = text
        return json.dumps(decision, sort_keys=True)
    return text or ""


def _warnings(writes: Iterable[RawWrite]) -> Optional[str]:
    lines: List[str] = []
    for write in writes:
        line = _WARNING.format(advice=write.advice, command=write.command)
        if line not in lines:
            lines.append(line)
    return "\n".join(lines) or None
