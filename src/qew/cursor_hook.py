"""Cursor shell-command hook adapter."""

from pathlib import Path
from typing import Any, Dict

from qew.errors import QewError
from qew.hook_events import install_json_hooks, remove_json_hooks


_HOOKS_PATH = (".cursor", "hooks.json")
_WANTED = {
    "beforeShellExecution": [{"command": "qew hook pre-shell --agent cursor", "timeout": 10}],
    "afterShellExecution": [{"command": "qew hook post-shell --agent cursor", "timeout": 60}],
}
_LEGACY = {"sessionStart": [{"command": "qew prime --json"}]}


def install(root: Path) -> bool:
    return install_json_hooks(root, _HOOKS_PATH, "Cursor", _WANTED, _LEGACY, _validate_version)


def remove(root: Path) -> bool:
    return remove_json_hooks(root, _HOOKS_PATH, "Cursor", _WANTED, _LEGACY, _validate_version)


def _validate_version(value: Dict[str, Any], create: bool) -> None:
    if "version" not in value:
        if create:
            value["version"] = 1
        return
    version = value["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise QewError(f"unsupported Cursor hooks version: {version!r}", 2)
