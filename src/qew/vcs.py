"""Derive the branch a pull request is made from: git, or Jujutsu."""

from functools import lru_cache
import os
from pathlib import Path
import re
import subprocess

from qew.errors import QewError

_COMMIT_IDS = 'commit_id ++ "\\n"'


def synced_branch(head: str | None = None) -> str:
    """Return the pushed branch to open a pull request from.

    An explicit head is trusted: the caller has pushed it and knows its name.
    """
    if head is not None:
        if not head.strip() or head.startswith("-") or any(c in head for c in '\n"\\'):
            raise QewError("invalid --head branch name", 2)
        return head
    if _in_jujutsu():
        return _jujutsu_branch()
    return _git_branch()


def _in_jujutsu() -> bool:
    return _jujutsu_root() is not None


def _jujutsu_root() -> Path | None:
    """The nearest checkout root, if it is a Jujutsu one (`.jj` before `.git`)."""
    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        if (directory / ".jj").is_dir():
            return directory
        if (directory / ".git").exists():
            return None
    return None


def workspace_git_dir() -> Path | None:
    """The real git store of a Jujutsu workspace that has no `.git`, else None.

    A secondary workspace's `.jj/repo` is a file naming the main repository's
    `.jj/repo`, whose `store/git_target` names the git store. A colocated
    checkout has its own `.git`, and gh finds that unaided.
    """
    root = _jujutsu_root()
    if root is None or (root / ".git").exists():
        return None
    try:
        repo = root / ".jj" / "repo"
        if repo.is_file():
            repo = (root / ".jj" / repo.read_text(encoding="utf-8").strip()).resolve()
        store = repo / "store"
        git_dir = (store / (store / "git_target").read_text(encoding="utf-8").strip()).resolve()
    except (OSError, UnicodeError) as error:
        raise QewError(
            f"cannot resolve the git store of this jj workspace ({root}): {error}; "
            "pass --repo OWNER/REPO",
            3,
        ) from error
    if not git_dir.is_dir():
        raise QewError(
            f"cannot resolve the git store of this jj workspace ({root}): "
            f"{git_dir} is not a directory; pass --repo OWNER/REPO",
            3,
        )
    return git_dir


_REMOTE = re.compile(r"[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


@lru_cache(maxsize=1)
def checkout_repository() -> str | None:
    """OWNER/REPO named by this checkout's `origin` remote, or None when unreadable."""
    try:
        git_dir = workspace_git_dir()
        command = ["git"] + (["--git-dir", str(git_dir)] if git_dir is not None else [])
        result = subprocess.run(
            [*command, "config", "--get", "remote.origin.url"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, QewError):
        return None
    match = _REMOTE.search(result.stdout.strip()) if result.returncode == 0 else None
    return f"{match.group(1)}/{match.group(2)}" if match else None


def _git_branch() -> str:
    branch = _run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"], silent_failure=True
    ).strip()
    if not branch:
        raise QewError(
            "pull requests require a named branch (HEAD is detached); "
            "pass --head BRANCH for a pushed branch",
            3,
        )
    if _run(["git", "status", "--porcelain"]).strip():
        raise QewError("pull requests require a clean working tree", 3)
    upstream = _run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]
    ).strip()
    if not upstream.endswith(f"/{branch}"):
        raise QewError("current branch must track its matching remote branch", 3)
    counts = _run(["git", "rev-list", "--left-right", "--count", "@{upstream}...HEAD"]).split()
    if counts != ["0", "0"]:
        raise QewError("current branch must exactly match its upstream", 3)
    return branch


def _jujutsu_branch() -> str:
    names = _bookmarks("@")
    if not names:
        if _run(["jj", "log", "--no-graph", "-r", "@ & ~empty()", "-T", _COMMIT_IDS]).strip():
            raise QewError(
                "pull requests require a clean working copy: @ has changes on no bookmark", 3
            )
        names = _bookmarks("@-")
    if len(names) != 1:
        found = ", ".join(names) or "none"
        raise QewError(
            f"expected exactly one bookmark at the working copy, found {found}; "
            "pass --head BRANCH",
            3,
        )
    name = names[0]
    if any(c in name for c in '"\\'):
        raise QewError("invalid bookmark name; pass --head BRANCH", 3)
    local = f'bookmarks(exact:"{name}")'
    remote = f'remote_bookmarks(exact:"{name}", exact:"origin")'
    if not _run(["jj", "log", "--no-graph", "-r", remote, "-T", _COMMIT_IDS]).strip():
        raise QewError(f"bookmark {name} is not pushed to origin", 3)
    drift = _run(
        ["jj", "log", "--no-graph", "-r", f"({remote}..{local}) | ({local}..{remote})",
         "-T", _COMMIT_IDS]
    ).strip()
    if drift:
        raise QewError("current branch must exactly match its upstream", 3)
    return name


def _bookmarks(revision: str) -> list[str]:
    output = _run(["jj", "bookmark", "list", "-r", revision, "-T", 'name ++ "\\n"'])
    return sorted({line for line in output.splitlines() if line})


def _run(argv: list[str], silent_failure: bool = False) -> str:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
    except OSError as error:
        raise QewError(f"cannot run `{' '.join(argv)}`: {error}", 3) from error
    if result.returncode != 0:
        # `symbolic-ref --quiet` exits 1 and prints nothing when HEAD is detached.
        if silent_failure and result.returncode == 1 and not result.stderr.strip():
            return ""
        cause = result.stderr.strip() or "no output"
        raise QewError(f"`{' '.join(argv)}` failed (exit {result.returncode}): {cause}", 3)
    return result.stdout
