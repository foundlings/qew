"""Ask the code repo's VCS (jj or git) whether a change is an ancestor of the target branch."""

import os
from pathlib import Path
import re
import subprocess
from typing import List, Optional

from qew.errors import QewError

_GIT_ID = re.compile(r"[0-9a-fA-F]{7,64}")
_JJ_ID = re.compile(r"[a-z0-9]{4,64}")


def detect(root: Path) -> str:
    """`jj` when the checkout is Jujutsu (colocated or not), else `git`."""
    return "jj" if (root / ".jj").is_dir() else "git"


def _run(root: Path, argv: List[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv,
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except OSError as error:
        raise QewError(f"cannot run `{' '.join(argv)}`: {error}", 3) from None


def valid_change(vcs: str, change: str) -> bool:
    return bool((_JJ_ID if vcs == "jj" else _GIT_ID).fullmatch(change))


def current_change(root: Path, vcs: str) -> str:
    """The change to cite when none is named: git HEAD, or jj `@` when it has changes else `@-`."""
    if vcs == "git":
        result = _run(root, ["git", "rev-parse", "HEAD"])
        if result.returncode != 0:
            raise QewError(f"cannot read git HEAD: {result.stderr.strip()}", 3)
        return result.stdout.strip()
    for revision in ("@ & ~empty()", "@-"):
        result = _run(root, ["jj", "log", "--no-graph", "-r", revision, "-T", 'change_id.short(12) ++ "\\n"'])
        if result.returncode != 0:
            raise QewError(f"cannot read the jj working copy: {result.stderr.strip()}", 3)
        if result.stdout.strip():
            return result.stdout.split()[0]
    raise QewError("no change to cite; pass --change", 2)


def repository_url(root: Path) -> str:
    result = _run(root, ["git", "config", "--get", "remote.origin.url"])
    url = result.stdout.strip() if result.returncode == 0 else ""
    return url or f"local:{root.name}"


def is_ancestor(root: Path, vcs: str, change: str, target: str) -> bool:
    """True when `change` is `target` or one of its ancestors; False when it is not or does not resolve."""
    if not valid_change(vcs, change) or target.startswith("-") or not target:
        return False
    if vcs == "git":
        if _run(root, ["git", "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}"]).returncode != 0:
            raise QewError(f"target branch `{target}` does not exist in this repo", 3)
        if _run(root, ["git", "rev-parse", "--verify", "--quiet", f"{change}^{{commit}}"]).returncode != 0:
            return False
        return _run(root, ["git", "merge-base", "--is-ancestor", change, target]).returncode == 0
    probe = _run(root, ["jj", "log", "--no-graph", "-r", f"present({target})", "-T", 'commit_id ++ "\\n"'])
    if probe.returncode != 0 or not probe.stdout.strip():
        raise QewError(f"target `{target}` does not exist in this repo", 3)
    result = _run(root, ["jj", "log", "--no-graph", "-r", f"::({target}) & ({change})", "-T", 'commit_id ++ "\\n"'])
    return result.returncode == 0 and bool(result.stdout.strip())
