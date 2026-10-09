"""Codex shell-command hook adapter."""

from pathlib import Path

from qew.hook_command import guarded
from qew.hook_events import install_json_hooks, remove_json_hooks


_HOOKS_PATH = (".codex", "hooks.json")


def _group(event: str, timeout: int, matcher: bool = True) -> dict:
    group = {"hooks": [{"command": guarded(event, "codex"), "timeout": timeout, "type": "command"}]}
    if matcher:
        group["matcher"] = "Bash"
    return group


_WANTED = {
    "SessionStart": [_group("session-start", 10, matcher=False)],
    "PreToolUse": [_group("pre-tool", 10)],
    "PostToolUse": [_group("post-tool", 10)],
}
# Qew's earlier entries (shell warnings, session-start prime): dropped on install.
def _plain(event: str, timeout: int = 10, matcher: bool = True) -> dict:
    """The earlier unguarded entry; install replaces it, remove deletes it."""
    group = {"hooks": [{"command": f"qew hook {event} --agent codex", "timeout": timeout, "type": "command"}]}
    if matcher:
        group["matcher"] = "Bash"
    return group


_LEGACY = {
    "SessionStart": [
        _plain("session-start", matcher=False),
        {
            "hooks": [
                {
                    "command": "qew prime",
                    "statusMessage": "Loading Qew work context",
                    "timeout": 10,
                    "type": "command",
                }
            ]
        }
    ],
    "PreToolUse": [_plain("pre-shell"), _plain("pre-tool")],
    "PostToolUse": [_plain("post-shell", 60), _plain("post-tool")],
}


def install(root: Path) -> bool:
    return install_json_hooks(root, _HOOKS_PATH, "Codex", _WANTED, _LEGACY)


def remove(root: Path) -> bool:
    return remove_json_hooks(root, _HOOKS_PATH, "Codex", _WANTED, _LEGACY)
