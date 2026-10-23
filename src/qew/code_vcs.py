"""Ask the code repo's VCS (jj or git) whether a change is an ancestor of the target branch."""

from enum import Enum
import os
from pathlib import Path
import re
import subprocess

from qew.errors import QewError

_GIT_ID = re.compile(r"[0-9a-fA-F]{7,64}")
_JJ_ID = re.compile(r"[a-z0-9]{4,64}")
_JJ_READ = ["jj", "--ignore-working-copy", "--no-pager"]  # a resolution never needs the working copy snapshotted


def detect(root: Path) -> str:
    """`jj` when the checkout is Jujutsu (colocated or not), else `git`."""
    return "jj" if (root / ".jj").is_dir() else "git"


def _run(root: Path, argv: list[str]) -> subprocess.CompletedProcess[str]:
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


def run_git(root: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    """One `git` command in the checkout, never prompting; the caller reads the return code."""
    return _run(root, ["git", *argv])


def _jj_target(root: Path, target: str) -> str:
    """The revset naming the target branch. A bookmark that two sides moved is conflicted and `present(main)` refuses it, so the
    revset then names every side (a change on either side is on the branch); a name nothing carries is exit 3."""
    probe = _run(root, _JJ_READ + ["log", "--no-graph", "-r", f"present({target})", "-T", 'commit_id ++ "\\n"'])
    if probe.returncode == 0 and probe.stdout.strip():
        return f"({target})"
    if "conflicted" in probe.stderr and "@" not in target:
        sides = _run(root, _JJ_READ + ["log", "--no-graph", "-r", f'bookmarks(exact:"{target}")', "-T", 'commit_id ++ "\\n"'])
        if sides.returncode == 0 and sides.stdout.strip():
            return f'bookmarks(exact:"{target}")'
    raise QewError(f"target `{target}` does not exist in this repo", 3)


def _jj_change(change: str) -> str:
    """The revset for a cited change: every commit of the change id (a divergent one is refused as a bare symbol)."""
    return f"change_id({change})" + (f" | commit_id({change})" if _HEX.fullmatch(change) else "")


def valid_change(vcs: str, change: str) -> bool:
    return bool((_JJ_ID if vcs == "jj" else _GIT_ID).fullmatch(change))


def current_change(root: Path, vcs: str) -> str:
    """The change to cite when none is named: git HEAD, or jj `@` when it has changes else `@-`."""
    if vcs == "git":
        result = _run(root, ["git", "rev-parse", "HEAD"])
        if result.returncode != 0:
            raise QewError(f"cannot read git HEAD: {result.stderr.strip()}", 3)
        return str(result.stdout.strip())
    for revision in ("@ & ~empty()", "@-"):
        result = _run(root, ["jj", "log", "--no-graph", "-r", revision, "-T", 'change_id.short(12) ++ "\\n"'])
        if result.returncode != 0:
            raise QewError(f"cannot read the jj working copy: {result.stderr.strip()}", 3)
        if result.stdout.strip():
            return str(result.stdout.split()[0])
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
    revset = _jj_target(root, target)
    result = _run(root, _JJ_READ + ["log", "--no-graph", "-r", f"::({revset}) & ({_jj_change(change)})", "-T", 'commit_id ++ "\\n"'])
    return result.returncode == 0 and bool(result.stdout.strip())


_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@+-]*")


def valid_branch(name: str) -> bool:
    """A branch name safe to hand to git or jj: no leading `-`, no revision operators (`..`), no spaces."""
    return bool(_BRANCH.fullmatch(name)) and ".." not in name


class Landing(Enum):
    LANDED = "landed"  # the change is the target or one of its ancestors
    NOT_ANCESTOR = "not an ancestor"  # the checkout knows the change and it is not in the target
    UNKNOWN = "unknown to this checkout"  # the checkout cannot resolve the change (typo, or never fetched)


def landing(root: Path, vcs: str, change: str, target: str) -> tuple[Landing, str]:
    """Where `change` stands against `target`, with the full commit id when it resolves.

    A missing target branch is an environment failure (exit 3), never a statement about the change.
    """
    if not valid_change(vcs, change) or not valid_branch(target):
        raise QewError(f"refusing to query the VCS with `{change}` / `{target}`", 2)
    if vcs == "git":
        if _run(root, ["git", "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}"]).returncode != 0:
            raise QewError(f"target branch `{target}` does not exist in this repo", 3)
        found = _run(root, ["git", "rev-parse", "--verify", "--quiet", f"{change}^{{commit}}"])
        full = found.stdout.strip()
        if found.returncode != 0 or not full:
            return Landing.UNKNOWN, ""
        merged = _run(root, ["git", "merge-base", "--is-ancestor", full, target]).returncode == 0
        return (Landing.LANDED if merged else Landing.NOT_ANCESTOR), full
    revset = _jj_target(root, target)
    found = _run(root, _JJ_READ + ["log", "--no-graph", "-r", _jj_change(change), "-T", 'commit_id ++ "\\n"'])
    ids = found.stdout.split() if found.returncode == 0 else []
    if not ids:
        return Landing.UNKNOWN, ""
    inside = _run(root, _JJ_READ + ["log", "--no-graph", "-r", f"::({revset}) & ({_jj_change(change)})", "-T", 'commit_id ++ "\\n"'])
    landed = inside.stdout.split() if inside.returncode == 0 else []
    return (Landing.LANDED, landed[0]) if landed else (Landing.NOT_ANCESTOR, ids[0])


_HEX = re.compile(r"[0-9a-f]+")
_CHUNK = 100  # ids per revset when the ones not on the target are looked up


def landing_many(root: Path, vcs: str, changes: list[str], target: str) -> dict[str, tuple[Landing, str]]:
    """`landing` for many changes in a few read-only calls: the target's ancestors once, then one revset for the rest."""
    wanted = list(dict.fromkeys(changes))
    if not valid_branch(target) or not all(valid_change(vcs, c) for c in wanted):
        raise QewError(f"refusing to query the VCS with an unusable change or target `{target}`", 2)
    if len(wanted) <= 2:
        return {change: landing(root, vcs, change, target) for change in wanted}
    if vcs == "git":
        return _landing_many_git(root, wanted, target)
    return _landing_many_jj(root, wanted, target)


def _landing_many_git(root: Path, wanted: list[str], target: str) -> dict[str, tuple[Landing, str]]:
    if _run(root, ["git", "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}"]).returncode != 0:
        raise QewError(f"target branch `{target}` does not exist in this repo", 3)
    history = _run(root, ["git", "rev-list", target])
    ancestors = set(history.stdout.split()) if history.returncode == 0 else set()
    try:
        checked = subprocess.run(["git", "cat-file", "--batch-check"], cwd=root, input="".join(f"{c}^{{commit}}\n" for c in wanted),
                                 capture_output=True, text=True, check=False, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except OSError as error:
        raise QewError(f"cannot run git: {error}", 3) from None
    found: dict[str, tuple[Landing, str]] = {}
    for change, line in zip(wanted, checked.stdout.splitlines(), strict=False):
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "commit":
            found[change] = (Landing.LANDED if parts[0] in ancestors else Landing.NOT_ANCESTOR), parts[0]
        else:
            found[change] = (Landing.UNKNOWN, "")
    return {change: found.get(change, (Landing.UNKNOWN, "")) for change in wanted}


def _landing_many_jj(root: Path, wanted: list[str], target: str) -> dict[str, tuple[Landing, str]]:
    revset = _jj_target(root, target)
    history = _run(root, _JJ_READ + ["log", "--no-graph", "-r", f"::({revset})", "-T", 'change_id ++ " " ++ commit_id ++ "\\n"'])
    on_target = [tuple(line.split()) for line in history.stdout.splitlines() if len(line.split()) == 2] if history.returncode == 0 else []
    found: dict[str, tuple[Landing, str]] = {}
    for change in wanted:
        hit = next((commit for change_id, commit in on_target if change_id.startswith(change) or commit.startswith(change)), None)
        if hit is not None:
            found[change] = (Landing.LANDED, hit)
    rest = [c for c in wanted if c not in found]
    for start in range(0, len(rest), _CHUNK):
        chunk = rest[start:start + _CHUNK]
        revset = " | ".join(f"change_id({c})" + (f" | commit_id({c})" if _HEX.fullmatch(c) else "") for c in chunk)
        seen = _run(root, _JJ_READ + ["log", "--no-graph", "-r", revset, "-T", 'change_id ++ " " ++ commit_id ++ "\\n"'])
        known = [tuple(line.split()) for line in seen.stdout.splitlines() if len(line.split()) == 2] if seen.returncode == 0 else []
        for change in chunk:
            hit = next((commit for change_id, commit in known if change_id.startswith(change) or commit.startswith(change)), None)
            found[change] = (Landing.NOT_ANCESTOR, hit) if hit is not None else (Landing.UNKNOWN, "")
    return found
