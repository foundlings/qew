"""The tracked git pre-push hook: `.githooks/pre-push` plus `core.hooksPath`.

The script is a tracked file, so switching the hook off shows up in the diff.
"""

from pathlib import Path
import subprocess
from typing import Optional

from qew.errors import QewError
from qew.hook_config import install_managed_file, remove_managed_file

_MARKER = "# Managed by Qew: git-pre-push-v1"
_PATH = (".githooks", "pre-push")
_DIRECTORY = ".githooks"
_SCRIPT = f"""#!/bin/sh
{_MARKER}
command -v qew >/dev/null 2>&1 || {{ echo "qew is not on PATH; qew pre-push check skipped" >&2; exit 0; }}
exec qew hook vcs-check --pre-push
"""


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, text=True, check=False)
    except OSError as error:
        raise QewError(f"cannot run git: {error}", 3) from None


def _hooks_path(root: Path) -> Optional[str]:
    result = _git(root, "config", "--local", "--get", "core.hooksPath")
    return result.stdout.strip() if result.returncode == 0 else None


def install(root: Path) -> bool:
    if not (root / ".git").exists():
        raise QewError("the git pre-push hook needs a git checkout (no .git at the repository root)", 2)
    current = _hooks_path(root)
    if current not in (None, _DIRECTORY):
        raise QewError(f"git config core.hooksPath is already {current!r}; Qew will not take it over", 2)
    changed = install_managed_file(root, _PATH, _SCRIPT, _MARKER, new_file_mode=0o755)
    if current is None:
        result = _git(root, "config", "--local", "core.hooksPath", _DIRECTORY)
        if result.returncode != 0:
            raise QewError(f"cannot set core.hooksPath: {result.stderr.strip()}", 3)
        changed = True
    return changed


def remove(root: Path) -> bool:
    changed = remove_managed_file(root, _PATH, _MARKER)
    if (root / ".git").exists() and _hooks_path(root) == _DIRECTORY and not _others(root):
        if _git(root, "config", "--local", "--unset", "core.hooksPath").returncode == 0:
            changed = True
    return changed


def _others(root: Path) -> bool:
    directory = root / _DIRECTORY
    return directory.is_dir() and any(directory.iterdir())
