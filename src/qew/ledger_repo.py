"""The ledger checkout as a VCS repository (git, or jj colocated): commit, fetch, push."""

import os
from pathlib import Path
import subprocess
from typing import List, Optional, Sequence

from qew.errors import QewError

# Directories and files whose changes are qew's own writes (never a human's notes).
OWNED = ("items", "events", "mirrors", "boards", "proposals")


class LedgerRepo:
    def __init__(self, path: Path, branch: str = "main") -> None:
        self.path = path
        self.branch = branch
        if (path / ".jj").is_dir():
            self.vcs: Optional[str] = "jj"
        elif (path / ".git").exists():
            self.vcs = "git"
        else:
            self.vcs = None

    # -- plumbing
    def run(self, argv: Sequence[str], timeout: Optional[float] = None, check: bool = True) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                list(argv), cwd=self.path, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=timeout, check=False, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except subprocess.TimeoutExpired:
            raise QewError(f"`{' '.join(argv[:3])}` timed out after {timeout}s", 3) from None
        except OSError as error:
            raise QewError(f"cannot run `{argv[0]}`: {error}", 3) from None
        if check and result.returncode != 0:
            raise QewError(f"`{' '.join(argv[:4])}` failed: {(result.stderr or result.stdout).strip()[:300]}", 3)
        return result

    def exclude_cache(self) -> None:
        """Keep `.qew/` (lock, log) out of the repository without committing anything."""
        exclude = self.path / ".git" / "info" / "exclude"
        if self.vcs is None or not exclude.parent.is_dir():
            return
        text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if ".qew/" not in text.split():
            exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n") + ".qew/\n", encoding="utf-8")

    # -- state
    def dirty_owned(self) -> List[str]:
        """Changed or untracked paths inside qew's own directories."""
        if self.vcs == "git":
            out = self.run(["git", "status", "--porcelain", "--untracked-files=all", "--", *OWNED]).stdout
            return sorted({line[3:].strip() for line in out.splitlines() if line[3:].strip()})
        if self.vcs == "jj":
            out = self.run(["jj", "--no-pager", "diff", "--summary"]).stdout
            paths = {line[2:].strip() for line in out.splitlines() if line[2:].strip()}
            return sorted(p for p in paths if p.split("/", 1)[0] in OWNED)
        return []

    def has_remote(self) -> bool:
        if self.vcs is None:
            return False
        return bool(self.run(["git", "remote"], check=False).stdout.strip())

    # -- writing
    def commit(self, paths: Sequence[str], message: str) -> None:
        if self.vcs == "git":
            self.run(["git", "add", "-A", "--", *paths])
            try:
                self.run(["git", "commit", "-q", "-m", message, "--", *paths])
            except QewError:
                self.run(["git", "reset", "-q", "--", *paths], check=False)
                raise
        elif self.vcs == "jj":
            self.run(["jj", "--no-pager", "commit", "-m", message, *[f'root-file:"{p}"' for p in paths]])
            self.run(["jj", "--no-pager", "bookmark", "set", self.branch, "-r", "@-"])
