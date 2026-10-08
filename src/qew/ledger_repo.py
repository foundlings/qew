"""The ledger checkout as a VCS repository (git, or jj colocated): commit, fetch, push."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Dict, List, Optional, Sequence

from qew import trace
from qew import ledger_lineage
from qew.errors import QewError

# Directories and files whose changes are qew's own writes (never a human's notes).
OWNED = ("items", "events", "mirrors", "boards", "views", "proposals")
SHOWN_CONFLICTS = 5


MERGE_MARKER = "merge-conflict.json"  # in the ledger's local-only .qew/ cache
MAX_DISCARDED_CHARS = 20_000


def conflict_message(remote: str, files, path) -> str:
    """One short line: a few conflicting files and the count, never the whole list."""
    files = list(files)
    shown = ", ".join(files[:SHOWN_CONFLICTS]) or "unknown files"
    more = f" and {len(files) - SHOWN_CONFLICTS} more ({len(files)} files)" if len(files) > SHOWN_CONFLICTS else (f" ({len(files)} files)" if len(files) > 1 else "")
    return (f"ledger conflict while merging {remote}: {shown}{more}; see `qew conflicts`, then "
            f"`qew resolve ledger --take ours|theirs --reason TEXT` (or resolve it by hand in {path}); qew never picks a side itself")


class LedgerConflict(QewError):
    """The remote and the local ledger changed the same file differently and cannot merge."""

    def __init__(self, remote: str, files: Sequence[str], path: Path, ours: str, theirs: str) -> None:
        super().__init__(conflict_message(remote, files, path), 1)
        self.remote = remote
        self.files = list(files)
        self.ours = ours
        self.theirs = theirs


@dataclass(frozen=True)
class MergeConflict:
    """What the last failed merge left behind: the files and the two commits that disagree."""
    remote: str
    files: tuple
    ours: str
    theirs: str


@dataclass(frozen=True)
class Resolution:
    """A settled merge: the files it touched and the text of the side that was not kept (None: absent there)."""
    took: str
    discarded: Dict[str, Optional[str]]


def only_mirrors(files) -> bool:
    """Mirror files hold forge bookkeeping that older clients rewrote everywhere; the remote's copy is as good."""
    files = list(files)
    return bool(files) and all(f.startswith("mirrors/") for f in files)


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
    @trace.traced("ledger fetch+merge")
    def fetch_merge(self, timeout: float) -> str:
        """Bring remote commits in. Returns `ok`, `no-remote` or `offline`; a real conflict raises `LedgerConflict`."""
        if not self.has_remote():
            return "no-remote"
        try:
            result = self._git_fetch_merge(timeout) if self.vcs == "git" else self._jj_fetch_merge(timeout)
        except LedgerConflict as conflict:
            self._marker().parent.mkdir(exist_ok=True)
            self._marker().write_text(json.dumps({"remote": conflict.remote, "files": conflict.files,
                                                  "ours": conflict.ours, "theirs": conflict.theirs}), encoding="utf-8")
            raise
        if result == "ok":
            self._marker().unlink(missing_ok=True)
        return result

    # -- a merge conflict agents settle with `qew resolve ledger`
    def _marker(self) -> Path:
        return self.path / ".qew" / MERGE_MARKER

    def pending_conflict(self) -> Optional[MergeConflict]:
        """The unresolved merge conflict recorded by the last failed fetch, or None (also when it was merged since)."""
        try:
            data = json.loads(self._marker().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise QewError(f"{self._marker()} is unreadable: {error}", 1) from None
        found = MergeConflict(data["remote"], tuple(data["files"]), data["ours"], data["theirs"])
        if self._contains(found.theirs):
            self._marker().unlink(missing_ok=True)
            return None
        return found

    def _contains(self, commit: str) -> bool:
        """Whether the checked-out ledger already has `commit` in its history."""
        if self.vcs == "git":
            return self.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"], check=False).returncode == 0
        return bool(self._jj_ids(f"{commit} & ::@"))  # not the bookmark: a diverged bookmark lists the remote tip as a side

    def file_at(self, revision: str, relative: str) -> Optional[str]:
        """The text of a ledger file at a commit, or None when that commit has no such file."""
        argv = (["git", "show", f"{revision}:{relative}"] if self.vcs == "git"
                else ["jj", "--no-pager", "file", "show", "-r", revision, relative])
        shown = self.run(argv, check=False)
        return shown.stdout if shown.returncode == 0 else None

    def resolve_merge(self, take: str, timeout: float) -> Resolution:
        """Merge the remote now, settling every conflicted file by taking `ours` or `theirs` whole.

        Only ledger-owned files are ever settled; a conflict anywhere else aborts the merge untouched. The text of
        the side not kept is returned so the caller can record it (it also stays in the merge's other parent).
        """
        if take not in ("ours", "theirs"):
            raise QewError(f"a ledger merge is settled with ours or theirs, not {take}", 2)
        if not self.has_remote():
            raise QewError("no ledger merge conflict: the ledger has no remote", 2)
        if self.vcs == "git":
            return self._git_resolve(take, timeout)
        return self._jj_resolve(take, timeout)

    def _foreign(self, files: Sequence[str]) -> List[str]:
        return [f for f in files if f.split("/")[0] not in OWNED]

    def _refuse_unowned(self, foreign: Sequence[str]) -> QewError:
        return QewError(f"{', '.join(foreign)} is not a file qew owns; qew never settles it: resolve it by hand in {self.path}", 2)

    def _git_resolve(self, take: str, timeout: float) -> Resolution:
        remote = f"origin/{self.branch}"
        self.run(["git", "fetch", "-q", "origin"], timeout=timeout, check=False)
        self.run(["git", "merge", "-q", "--no-commit", "--no-ff", remote], check=False)
        files = self.run(["git", "diff", "--name-only", "--diff-filter=U"], check=False).stdout.split()
        if not files:
            self.run(["git", "merge", "--abort"], check=False)
            self._marker().unlink(missing_ok=True)
            raise QewError("no ledger merge conflict to resolve", 2)
        foreign = self._foreign(files)
        if foreign:
            self.run(["git", "merge", "--abort"], check=False)
            raise self._refuse_unowned(foreign)
        discarded: Dict[str, Optional[str]] = {}
        other_stage, kept_flag = (":3", "--ours") if take == "ours" else (":2", "--theirs")
        for relative in files:
            shown = self.run(["git", "show", f"{other_stage}:{relative}"], check=False)
            discarded[relative] = shown.stdout if shown.returncode == 0 else None
            checked = self.run(["git", "checkout", kept_flag, "--", relative], check=False)
            if checked.returncode == 0:
                self.run(["git", "add", "--", relative])
            else:  # the kept side deleted the file
                self.run(["git", "rm", "-q", "-f", "--", relative])
        self.run(["git", "commit", "-q", "--no-edit", "-m", f"qew: merge (conflict resolved: took {take}: {', '.join(files)})"])
        self._marker().unlink(missing_ok=True)
        return Resolution(take, discarded)

    def _jj_resolve(self, take: str, timeout: float) -> Resolution:
        self.run(["jj", "--no-pager", "git", "fetch"], timeout=timeout, check=False)
        remote = self._jj_ids(f'remote_bookmarks(exact:"{self.branch}", exact:"origin")')
        sides = self._jj_ids(f'bookmarks(exact:"{self.branch}")')
        if not remote or not sides:
            raise QewError("no ledger merge conflict to resolve", 2)
        tip = remote[0]
        local = [x for x in sides if x != tip]
        if not local or self._jj_ids(f"{tip} & ::{local[0]}"):
            self._marker().unlink(missing_ok=True)
            raise QewError("no ledger merge conflict to resolve", 2)
        self.run(["jj", "--no-pager", "new", *local, tip, "-m", f"qew: merge (conflict resolved: took {take})"])
        files = [line.split()[0] for line in self.run(["jj", "--no-pager", "resolve", "--list"], check=False).stdout.splitlines() if line.strip()]
        if not files:
            self.run(["jj", "--no-pager", "undo"], check=False)
            self._marker().unlink(missing_ok=True)
            raise QewError("no ledger merge conflict to resolve", 2)
        foreign = self._foreign(files)
        if foreign:
            self.run(["jj", "--no-pager", "undo"], check=False)
            raise self._refuse_unowned(foreign)
        dropped = tip if take == "ours" else local[0]
        discarded = {relative: self.file_at(dropped, relative) for relative in files}
        self.run(["jj", "--no-pager", "resolve", "--tool", ":ours" if take == "ours" else ":theirs"])
        self.run(["jj", "--no-pager", "bookmark", "set", self.branch, "-r", "@"])
        self.run(["jj", "--no-pager", "new"])
        self._marker().unlink(missing_ok=True)
        return Resolution(take, discarded)

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
            if only_mirrors(files):
                self.run(["git", "checkout", "--theirs", "--", *files])
                self.run(["git", "add", "--", *files])
                self.run(["git", "commit", "-q", "--no-edit", "-m", "qew: merge (mirror bookkeeping taken from the remote)"])
                return "ok"
            self.run(["git", "merge", "--abort"], check=False)
            ours = self.run(["git", "rev-parse", "HEAD"]).stdout.strip()
            theirs = self.run(["git", "rev-parse", remote]).stdout.strip()
            raise LedgerConflict(remote, files, self.path, ours, theirs)
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
            files = [line.split()[0] for line in self.run(["jj", "--no-pager", "resolve", "--list"], check=False).stdout.splitlines() if line.strip()]
            if only_mirrors(files):
                self.run(["jj", "--no-pager", "resolve", "--tool", ":theirs"])
            else:
                self.run(["jj", "--no-pager", "undo"], check=False)
                raise LedgerConflict(f"{self.branch}@origin", files, self.path, local[0], tip)
        self.run(["jj", "--no-pager", "bookmark", "set", self.branch, "-r", "@"])
        self.run(["jj", "--no-pager", "new"])
        return "ok"

    def _move_working_copy(self) -> None:
        """After the branch moved, re-seat an empty working copy on its tip (qew's own commits leave it empty)."""
        if self._jj_ids("@ & empty()") and self._jj_ids(f'bookmarks(exact:"{self.branch}") ~ ::@'):
            self.run(["jj", "--no-pager", "new", self.branch])

    @trace.traced("ledger push (strict)")
    def push_strict(self, timeout: float) -> str:
        """Push and say why it failed: `ok`, `rejected` (the remote moved: fetch and merge) or `offline`."""
        if self.push(timeout) == "ok":
            return "ok"
        return "offline" if self.fetch_merge(timeout) == "offline" else "rejected"

    @trace.traced("ledger push")
    def push(self, timeout: float) -> str:
        """Push the branch. Returns `ok`, `no-remote` or `offline`."""
        if not self.has_remote():
            return "no-remote"
        if self.vcs == "git":
            pushed = self.run(["git", "push", "-q", "origin", f"{self.branch}:{self.branch}"], timeout=timeout, check=False)
        else:
            pushed = self.run(["jj", "--no-pager", "git", "push", "--bookmark", self.branch], timeout=timeout, check=False)
        return "ok" if pushed.returncode == 0 else "offline"
