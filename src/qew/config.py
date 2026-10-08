"""Tracked repository policy for Qew's authority boundary."""

from dataclasses import dataclass, replace
from enum import Enum
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, Optional

from qew import __version__
from qew.errors import QewError
from qew.vcs import checkout_repository


class RepositoryMode(str, Enum):
    OVERLAY = "overlay"
    REPOSITORY = "repository"


@dataclass(frozen=True)
class RepositoryConfig:
    mode: RepositoryMode
    project: Optional[int] = None
    minimum_version: Optional[str] = None
    max_active_claims: Optional[int] = None

    @property
    def managed_only(self) -> bool:
        return self.mode is RepositoryMode.OVERLAY


_target_repository: Optional[str] = None


def use_repository(repository: str) -> None:
    """Record the repository this command acts on, once it is known.

    The checkout's mode and claim cap describe the repository the checkout is of.
    """
    global _target_repository
    _target_repository = repository


def _is_foreign() -> bool:
    if _target_repository is None:
        return False
    own = checkout_repository()
    return own is not None and own.casefold() != _target_repository.casefold()


def repository_root(start: Optional[Path] = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() or (candidate / ".jj").exists():
            return candidate
    raise QewError("not inside a Git or jj repository", 2)


def load_config(start: Optional[Path] = None) -> RepositoryConfig:
    path = repository_root(start) / ".qew" / "config.json"
    if path.is_symlink():
        raise QewError("Qew configuration must not be a symlink", 2)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise QewError(
            "Qew is not initialized; run qew init --mode overlay or --mode repository",
            2,
        ) from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise QewError(f"cannot read Qew configuration: {error}", 2) from error
    config = _decode(value)
    if _is_foreign():
        # Another repository: never trust this checkout's "every issue is ours" mode
        # or its claim cap; only issues Qew manages are in scope.
        return replace(config, mode=RepositoryMode.OVERLAY, max_active_claims=None)
    return config


def linked_project(repository: str, start: Optional[Path] = None) -> Optional[int]:
    """The Project linked in this checkout's config, for the repository it is of.

    The link belongs to the checkout's own repository: another `--repo` must not
    reuse it, or its issues land on this repository's board. A checkout whose
    origin remote cannot be read keeps the link.
    """
    project = load_config(start).project
    if project is None:
        return None
    own = checkout_repository()
    if own is not None and own.casefold() != repository.casefold():
        return None
    return project


def write_config(
    mode: RepositoryMode,
    start: Optional[Path] = None,
    project: Optional[int] = None,
) -> bool:
    root = repository_root(start)
    directory = root / ".qew"
    path = directory / "config.json"
    if directory.is_symlink() or path.is_symlink():
        raise QewError("Qew configuration path must not be a symlink", 2)
    existing = None
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise QewError(f"cannot read Qew configuration: {error}", 2) from error
    # Keep fields this Qew does not own: a newer Qew or a repository may have
    # added them, and rewriting must not drop them silently.
    desired = dict(existing) if isinstance(existing, dict) else {}
    desired.update({"mode": mode.value, "schema_version": 1})
    if project is not None:
        desired["project"] = _project_number(project)
    elif "project" in desired:
        desired["project"] = _project_number(desired["project"])
    if existing == desired:
        return False
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".config.json.", suffix=".tmp", dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(desired, output, separators=(",", ":"), sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise QewError(f"cannot write Qew configuration: {error}", 2) from error
    return True


def _decode(value: Any) -> RepositoryConfig:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise QewError("unsupported Qew configuration", 2)
    raw_mode = value.get("mode")
    try:
        mode = RepositoryMode(raw_mode)
    except (TypeError, ValueError) as error:
        raise QewError("Qew mode must be overlay or repository", 2) from error
    project = _project_number(value["project"]) if "project" in value else None
    minimum = _minimum_version(value["minimum_version"]) if "minimum_version" in value else None
    if minimum is not None and _version_tuple(minimum) > _version_tuple(__version__):
        raise QewError(
            f"this checkout requires Qew {minimum} or newer, but this is Qew {__version__}; "
            "upgrade with scripts/setup_user.py from a Qew clone",
            2,
        )
    cap = _claim_cap(value["max_active_claims"]) if "max_active_claims" in value else None
    return RepositoryConfig(
        mode=mode,
        project=project,
        minimum_version=minimum,
        max_active_claims=cap,
    )


_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def _minimum_version(value: Any) -> str:
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise QewError("Qew minimum_version must be MAJOR.MINOR.PATCH", 2)
    return value


def _version_tuple(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


def _claim_cap(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise QewError("Qew max_active_claims must be a positive integer", 2)
    return value


def _project_number(value: Any) -> int:
    # A number survives renaming the board; a title would silently unlink it.
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise QewError("Qew project must be a positive project number", 2)
    return value
