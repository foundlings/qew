"""Cursor shell-command hook adapter."""

from pathlib import Path
from typing import Any

from qew.errors import QewError
from qew.hook_events import Events, install_json_hooks, remove_json_hooks


_HOOKS_PATH = (".cursor", "hooks.json")
_WANTED: Events = {
    "beforeShellExecution": [{"command": "qew hook pre-tool --agent cursor", "timeout": 10}],
    "afterShellExecution": [{"command": "qew hook post-tool --agent cursor", "timeout": 10}],
    "sessionStart": [{"command": "qew hook session-start --agent cursor", "timeout": 10}],
}
# Qew's earlier entries (shell warnings, session-start prime): dropped on install.
_LEGACY: Events = {
    "beforeShellExecution": [{"command": "qew hook pre-shell --agent cursor", "timeout": 10}],
    "afterShellExecution": [{"command": "qew hook post-shell --agent cursor", "timeout": 60}],
    "sessionStart": [{"command": "qew prime --json"}],
}


def install(root: Path) -> bool:
    return install_json_hooks(root, _HOOKS_PATH, "Cursor", _WANTED, _LEGACY, _validate_version)


def remove(root: Path) -> bool:
    return remove_json_hooks(root, _HOOKS_PATH, "Cursor", _WANTED, _LEGACY, _validate_version)


def _validate_version(value: dict[str, Any], create: bool) -> None:
    if "version" not in value:
        if create:
            value["version"] = 1
        return
    version = value["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise QewError(f"unsupported Cursor hooks version: {version!r}", 2)
