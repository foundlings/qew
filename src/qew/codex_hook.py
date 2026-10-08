"""Codex shell-command hook adapter."""

from pathlib import Path

from qew.hook_events import install_json_hooks, remove_json_hooks


_HOOKS_PATH = (".codex", "hooks.json")


def _group(event: str, timeout: int) -> dict:
    return {
        "matcher": "Bash",
        "hooks": [
            {"command": f"qew hook {event} --agent codex", "timeout": timeout, "type": "command"}
        ],
    }


_WANTED = {
    "PreToolUse": [_group("pre-shell", 10)],
    "PostToolUse": [_group("post-shell", 60)],
}
_LEGACY = {
    "SessionStart": [
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
    ]
}


def install(root: Path) -> bool:
    return install_json_hooks(root, _HOOKS_PATH, "Codex", _WANTED, _LEGACY)


def remove(root: Path) -> bool:
    return remove_json_hooks(root, _HOOKS_PATH, "Codex", _WANTED, _LEGACY)
