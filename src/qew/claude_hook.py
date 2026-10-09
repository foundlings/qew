"""Claude Code shell-command hook adapter."""

from pathlib import Path

from qew.hook_command import guarded
from qew.hook_events import install_json_hooks, remove_json_hooks
from qew.jsontypes import JsonObject


_SETTINGS_PATH = (".claude", "settings.json")


def _entry(event: str) -> JsonObject:
    return {"command": guarded(event, "claude"), "timeout": 10, "type": "command"}


def _exec_entry(event: str) -> JsonObject:
    """The earlier exec-form entry; install replaces it, remove deletes it."""
    return {"args": ["hook", event, "--agent", "claude"], "command": "qew", "timeout": 10, "type": "command"}


def _group(event: str, matcher: bool = True) -> JsonObject:
    group: JsonObject = {"hooks": [_entry(event)]}
    if matcher:
        group["matcher"] = "Bash"
    return group


_WANTED: dict[str, list[JsonObject]] = {
    "SessionStart": [_group("session-start", matcher=False)],
    "PreToolUse": [_group("pre-tool")],
    "PostToolUse": [_group("post-tool")],
}
# Qew's earlier entries (shell warnings, session-start prime): dropped on install.
_LEGACY: dict[str, list[JsonObject]] = {
    "SessionStart": [
        {"hooks": [{"args": ["prime"], "command": "qew", "timeout": 10, "type": "command"}]},
        {"hooks": [_exec_entry("session-start")]},
    ],
    "PreToolUse": [
        {"matcher": "Bash", "hooks": [_exec_entry("pre-tool")]},
        {
            "matcher": "Bash",
            "hooks": [{"args": ["hook", "pre-shell", "--agent", "claude"], "command": "qew", "timeout": 10, "type": "command"}],
        }
    ],
    "PostToolUse": [
        {"matcher": "Bash", "hooks": [_exec_entry("post-tool")]},
        {
            "matcher": "Bash",
            "hooks": [{"args": ["hook", "post-shell", "--agent", "claude"], "command": "qew", "timeout": 60, "type": "command"}],
        }
    ],
}


def install(root: Path) -> bool:
    return install_json_hooks(root, _SETTINGS_PATH, "Claude settings", _WANTED, _LEGACY)


def remove(root: Path) -> bool:
    return remove_json_hooks(root, _SETTINGS_PATH, "Claude settings", _WANTED, _LEGACY)
