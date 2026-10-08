"""Push the code repo and the ledger repo to every forge target, so any forge can become primary.

Per target a git remote `qew-<slug>` is kept for each repo and the target branch is pushed (never forced).
The clone URLs are explicit `git_url` (code repo) and `ledger_git_url` (ledger repo) keys of the `[[mirror]]`
entry: they are never derived from the API repo URL. Each target and repo is isolated; a failure is a report
line, never an exception. Creating the forge repositories is out of scope: they must exist.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional

from qew import ledger_config
from qew.ledger_config import Config, Mirror

PUSH_TIMEOUT_S = 120


def remote_name(slug: str) -> str:
    return "qew-" + re.sub(r"[^A-Za-z0-9.-]", "-", slug)


def _url_problem(url: object) -> Optional[str]:
    if not isinstance(url, str) or not url or re.search(r"[\s\x00-\x1f\x7f]", url):
        return "must be a clone URL or path without whitespace"
    if url.startswith("-") or "::" in url:
        return "must not start with `-` or use a remote helper (`::`)"
    return None


def _git(path: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *arguments], capture_output=True, text=True, timeout=PUSH_TIMEOUT_S,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, check=False)


def _push_one(path: Path, branch: str, name: str, url: str) -> str:
    """`pushed BRANCH` or `failed: why`."""
    try:
        if _git(path, "rev-parse", "--verify", "-q", f"refs/heads/{branch}").returncode != 0:
            return f"failed: no branch `{branch}` in {path}"
        current = _git(path, "config", "--get", f"remote.{name}.url")
        if current.returncode != 0:
            added = _git(path, "remote", "add", name, url)
        elif current.stdout.strip() != url:
            added = _git(path, "remote", "set-url", name, url)
        else:
            added = current
        if added.returncode != 0:
            return f"failed: {added.stderr.strip() or 'cannot set the remote'}"
        pushed = _git(path, "push", name, f"refs/heads/{branch}:refs/heads/{branch}")
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"failed: {error}"
    if pushed.returncode != 0:
        lines = [line for line in pushed.stderr.strip().splitlines() if line.strip()]
        return f"failed: {lines[-1] if lines else 'push rejected'}"
    return f"pushed {branch}"


def push_target(config: Config, mirror: Mirror) -> List[str]:
    slug = ledger_config.mirror_slug(mirror)
    name = remote_name(slug)
    wanted = [("code", config.root, config.target, mirror.options.get("git_url")),
              ("ledger", config.ledger_path, config.replication.branch, mirror.options.get("ledger_git_url"))]
    lines = []
    if all(url is None for *_, url in wanted):
        return [f"repos {slug}: set git_url and ledger_git_url in the [[mirror]] entry to push the repos (never guessed)"]
    for label, path, branch, url in wanted:
        if url is None or (label == "code" and config.root == config.ledger_path):
            continue
        problem = _url_problem(url)
        outcome = f"failed: {'git_url' if label == 'code' else 'ledger_git_url'} {problem}" if problem else _push_one(path, branch, name, url)
        lines.append(f"repos {slug} {label}: {outcome}")
    return lines


def push_everywhere(config: Config) -> List[str]:
    """Report lines for every target; offline runs and ledgers without targets do nothing."""
    if config.replication.offline or not (config.root / ledger_config.CONFIG).is_file():
        return []
    lines = []
    for mirror in config.mirrors:
        lines.extend(push_target(config, mirror))
    return lines
