"""Claude Code shell-command hook adapter."""

from pathlib import Path

from qew.hook_events import install_json_hooks, remove_json_hooks


_SETTINGS_PATH = (".claude", "settings.json")


def _group(event: str, timeout: int) -> dict:
    return {
        "matcher": "Bash",
        "hooks": [
            {
                "args": ["hook", event, "--agent", "claude"],
                "command": "qew",
                "timeout": timeout,
                "type": "command",
            }
        ],
    }


_WANTED = {
    "PreToolUse": [_group("pre-shell", 10)],
    "PostToolUse": [_group("post-shell", 60)],
}
# Qew's earlier session-start prime: dropped on install, since it spends context
# in every session.
_LEGACY = {
    "SessionStart": [
        {"hooks": [{"args": ["prime"], "command": "qew", "timeout": 10, "type": "command"}]}
    ]
}


def install(root: Path) -> bool:
    return install_json_hooks(root, _SETTINGS_PATH, "Claude settings", _WANTED, _LEGACY)


def remove(root: Path) -> bool:
    return remove_json_hooks(root, _SETTINGS_PATH, "Claude settings", _WANTED, _LEGACY)
