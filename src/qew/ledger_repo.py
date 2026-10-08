"""The ledger checkout as a VCS repository (git, or jj colocated): commit, fetch, push."""

import os
from pathlib import Path
import subprocess
from typing import List, Optional, Sequence

from qew import ledger_lineage
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

    def unpushed(self) -> int:
        """Commits on the branch the remote does not have (0 without a remote)."""
        if not self.has_remote():
            return 0
        if self.vcs == "git":
            ref = f"origin/{self.branch}"
            if self.run(["git", "rev-parse", "--verify", "-q", ref], check=False).returncode != 0:
                return int(self.run(["git", "rev-list", "--count", "HEAD"]).stdout.strip() or 0)
            return int(self.run(["git", "rev-list", "--count", f"{ref}..HEAD"]).stdout.strip() or 0)
        remote = f'remote_bookmarks(exact:"{self.branch}", exact:"origin")'
        return len(self._jj_ids(f'bookmarks(exact:"{self.branch}") ~ ::({remote})')) and len(
            self._jj_ids(f'::bookmarks(exact:"{self.branch}") ~ ::({remote}) ~ empty()'))

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

    # -- replication (best effort; the caller decides what a failure means)
    def fetch_merge(self, timeout: float) -> str:
        """Bring remote commits in. Returns `ok`, `no-remote` or `offline`; a real conflict raises."""
        if not self.has_remote():
            return "no-remote"
        if self.vcs == "git":
            return self._git_fetch_merge(timeout)
        return self._jj_fetch_merge(timeout)

    def _git_fetch_merge(self, timeout: float) -> str:
        try:
            fetched = self.run(["git", "fetch", "-q", "origin"], timeout=timeout, check=False)
        except QewError:
            return "offline"
        if fetched.returncode != 0:
            return "offline"
        remote = f"origin/{self.branch}"
        if self.run(["git", "rev-parse", "--verify", "-q", remote], check=False).returncode != 0:
            return "ok"
        self._refuse_foreign(self.run(["git", "show", f"{remote}:qew-ledger.toml"], check=False), remote)
        counts = self.run(["git", "rev-list", "--left-right", "--count", f"HEAD...{remote}"]).stdout.split()
        ahead, behind = int(counts[0]), int(counts[1])
        if behind == 0:
            return "ok"
        if ahead == 0:
            self.run(["git", "merge", "-q", "--ff-only", remote])
            return "ok"
        merged = self.run(["git", "merge", "-q", "--no-edit", "-m", "qew: merge", remote], check=False)
        if merged.returncode != 0:
            files = self.run(["git", "diff", "--name-only", "--diff-filter=U"], check=False).stdout.split()
            self.run(["git", "merge", "--abort"], check=False)
            raise QewError(f"ledger conflict while merging {remote}: {', '.join(files) or 'unknown files'}; "
                           f"resolve it in {self.path} (a conflict is a ledger bug; qew never resolves it)", 1)
        return "ok"

    def _refuse_foreign(self, remote_file: subprocess.CompletedProcess, remote: str) -> None:
        """A remote ledger with another identity (a fork or its origin) is never merged."""
        local = self.path / "qew-ledger.toml"
        if remote_file.returncode != 0 or not local.is_file():
            return
        ledger_lineage.refuse_foreign_merge(local.read_text(encoding="utf-8"), remote_file.stdout, remote)

    def _jj_ids(self, revset: str) -> List[str]:
        out = self.run(["jj", "--no-pager", "log", "--no-graph", "-r", revset, "-T", 'commit_id ++ "\n"']).stdout
        return out.split()

    def _jj_fetch_merge(self, timeout: float) -> str:
        fetched = self.run(["jj", "--no-pager", "git", "fetch"], timeout=timeout, check=False)
        if fetched.returncode != 0:
            return "offline"
        remote = self._jj_ids(f'remote_bookmarks(exact:"{self.branch}", exact:"origin")')
        sides = self._jj_ids(f'bookmarks(exact:"{self.branch}")')
        if not remote or not sides:
            return "ok"
        tip = remote[0]
        self._refuse_foreign(self.run(["jj", "--no-pager", "file", "show", "-r", tip, "qew-ledger.toml"], check=False), f"{self.branch}@origin")
        local = [x for x in sides if x != tip]  # a conflicted bookmark lists the remote tip as one side
        if not local:
            if len(sides) == 1:
                self._move_working_copy()
            return "ok"
        containing = [x for x in local if self._jj_ids(f"{tip} & ::{x}")]
        if containing:  # local already has everything the remote has
            if len(sides) > 1:
                self.run(["jj", "--no-pager", "bookmark", "set", self.branch, "-r", containing[0]])
            return "ok"
        if all(self._jj_ids(f"{x} & ::{tip}") for x in local):  # remote is ahead: fast-forward
            self.run(["jj", "--no-pager", "bookmark", "set", self.branch, "-r", tip])
            self._move_working_copy()
            return "ok"
        sides = local
        parents = list(dict.fromkeys([*sides, tip]))
        self.run(["jj", "--no-pager", "new", *parents, "-m", "qew: merge"])
        if self._jj_ids("conflicts()"):
            files = self.run(["jj", "--no-pager", "resolve", "--list"], check=False).stdout.split()[::2]
            self.run(["jj", "--no-pager", "undo"], check=False)
            raise QewError(f"ledger conflict while merging {self.branch}@origin: {', '.join(files) or 'unknown files'}; "
                           f"resolve it in {self.path} (a conflict is a ledger bug; qew never resolves it)", 1)
        self.run(["jj", "--no-pager", "bookmark", "set", self.branch, "-r", "@"])
        self.run(["jj", "--no-pager", "new"])
        return "ok"

    def _move_working_copy(self) -> None:
        """After the branch moved, re-seat an empty working copy on its tip (qew's own commits leave it empty)."""
        if self._jj_ids("@ & empty()") and self._jj_ids(f'bookmarks(exact:"{self.branch}") ~ ::@'):
            self.run(["jj", "--no-pager", "new", self.branch])

    def push_strict(self, timeout: float) -> str:
        """Push and say why it failed: `ok`, `rejected` (the remote moved: fetch and merge) or `offline`."""
        if self.push(timeout) == "ok":
            return "ok"
        return "offline" if self.fetch_merge(timeout) == "offline" else "rejected"

    def push(self, timeout: float) -> str:
        """Push the branch. Returns `ok`, `no-remote` or `offline`."""
        if not self.has_remote():
            return "no-remote"
        if self.vcs == "git":
            pushed = self.run(["git", "push", "-q", "origin", f"{self.branch}:{self.branch}"], timeout=timeout, check=False)
        else:
            pushed = self.run(["jj", "--no-pager", "git", "push", "--bookmark", self.branch], timeout=timeout, check=False)
        return "ok" if pushed.returncode == 0 else "offline"
