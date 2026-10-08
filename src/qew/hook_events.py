"""Shared structural operations for JSON hook adapters."""

import copy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from qew.errors import QewError
from qew.hook_config import update_json_config


Events = Dict[str, List[Dict[str, Any]]]
Prepare = Callable[[Dict[str, Any], bool], None]


def install_json_hooks(
    root: Path,
    relative: Sequence[str],
    config_name: str,
    wanted: Events,
    legacy: Events,
    prepare: Optional[Prepare] = None,
) -> bool:
    """Add Qew's hook entries and drop Qew's own earlier ones, keeping all others."""

    def install(value: Dict[str, Any]) -> Dict[str, Any]:
        if prepare is not None:
            prepare(value, True)
        hooks = _hooks(value, config_name, create=True)
        for event, entries in wanted.items():
            current = _entries(hooks, event, config_name, create=True)
            for entry in entries:
                if entry not in current:
                    current.append(copy.deepcopy(entry))
        _drop(hooks, legacy, config_name)
        return value

    return update_json_config(root, relative, install)


def remove_json_hooks(
    root: Path,
    relative: Sequence[str],
    config_name: str,
    wanted: Events,
    legacy: Events,
    prepare: Optional[Prepare] = None,
) -> bool:
    """Remove exactly Qew's entries, current and earlier, pruning containers it emptied."""

    def remove(value: Dict[str, Any]) -> Dict[str, Any]:
        if prepare is not None:
            prepare(value, False)
        hooks = _hooks(value, config_name, create=False)
        if hooks is None:
            return value
        _drop(hooks, wanted, config_name)
        _drop(hooks, legacy, config_name)
        if not hooks:
            value.pop("hooks")
        return value

    return update_json_config(root, relative, remove, create=False)


def _drop(hooks: Dict[str, Any], unwanted: Events, config_name: str) -> None:
    for event, entries in unwanted.items():
        current = _entries(hooks, event, config_name, create=False)
        if current is None:
            continue
        retained = [candidate for candidate in current if candidate not in entries]
        if len(retained) == len(current):
            continue
        if retained:
            hooks[event] = retained
        else:
            hooks.pop(event)


def _hooks(value: Dict[str, Any], config_name: str, *, create: bool):
    if "hooks" not in value:
        if not create:
            return None
        value["hooks"] = {}
    hooks = value["hooks"]
    if not isinstance(hooks, dict):
        raise QewError(f"{config_name} hooks must be an object", 2)
    return hooks


def _entries(hooks: Dict[str, Any], event: str, config_name: str, *, create: bool):
    if event not in hooks:
        if not create:
            return None
        hooks[event] = []
    entries = hooks[event]
    if not isinstance(entries, list):
        raise QewError(f"{config_name} hooks.{event} must be a list", 2)
    return entries
