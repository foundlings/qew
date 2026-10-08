"""Check that every ledger item cited by pushed commits has evidence for that commit."""

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Iterable, List, Optional, Tuple

from qew.errors import QewError
from qew.ledger_event import State
from qew.ledger_store import Snapshot
from qew.vcs_refs import cited_ids

_ZERO = set("0")
_FIELD = "\x1f"
_RECORD = "\x1e"
_MIN_ID = 4


@dataclass(frozen=True)
class Commit:
    commit_id: str
    change_id: str  # jj change id; empty under git
    message: str

    @property
    def cite(self) -> str:
        return self.change_id or self.commit_id


def _run(root: Path, argv: List[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, cwd=root, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    except OSError as error:
        raise QewError(f"cannot run `{' '.join(argv)}`: {error}", 3) from None


def git_pushed(root: Path, pre_push_input: str) -> List[Commit]:
    """Commits a `git push` would send, from the pre-push hook's stdin lines."""
    commits: List[Commit] = []
    for line in pre_push_input.splitlines():
        parts = line.split()
        if len(parts) != 4:
            raise QewError(f"unexpected pre-push line: {line!r}", 2)
        _, local, _, remote = parts
        if set(local) <= _ZERO:
            continue  # a deletion sends no commits
        attempts = ([[local, "--not", remote]] if not set(remote) <= _ZERO else []) + [[local, "--not", "--remotes"]]
        for revisions in attempts:
            result = _run(root, ["git", "log", f"--format=%H{_FIELD}%B{_RECORD}", *revisions])
            if result.returncode == 0:
                commits.extend(_parse(result.stdout, with_change=False))
                break
        else:
            raise QewError(f"cannot list the commits to push for {local}", 3)
    return commits


def jj_revisions(root: Path, revset: str) -> List[Commit]:
    template = f'commit_id ++ "{_FIELD}" ++ change_id.short(12) ++ "{_FIELD}" ++ description ++ "{_RECORD}"'
    result = _run(root, ["jj", "log", "--no-graph", "-r", revset, "-T", template])
    if result.returncode != 0:
        raise QewError(f"jj could not list {revset}: {result.stderr.strip()}", 3)
    return _parse(result.stdout, with_change=True)


def _parse(output: str, with_change: bool) -> List[Commit]:
    commits = []
    for record in output.split(_RECORD):
        if not record.strip():
            continue
        fields = record.strip("\n").split(_FIELD)
        if with_change:
            commits.append(Commit(fields[0], fields[1], fields[2]))
        else:
            commits.append(Commit(fields[0], "", fields[1]))
    return commits


def _covers(recorded: str, commit: Commit) -> bool:
    for known in (commit.commit_id, commit.change_id):
        if known and len(recorded) >= _MIN_ID and (known.startswith(recorded) or recorded.startswith(known)):
            return True
    return False


def report(snapshot: Snapshot, commits: Iterable[Commit]) -> Tuple[List[str], List[str]]:
    """(lines for stdout, refusals for stderr) for the cited ids of the commits."""
    lines: List[str] = []
    refusals: List[str] = []
    for commit in commits:
        for cited in cited_ids(commit.message):
            if cited not in snapshot.items:
                refusals.append(f"{commit.cite} cites {cited}, which is not a ledger item")
                if f"unknown {cited}" not in lines:
                    lines.append(f"unknown {cited}")
                continue
            if f"known {cited}" not in lines:
                lines.append(f"known {cited}")
            folded = snapshot.folds[cited]
            if folded.state in (State.COMPLETED, State.CLOSED):
                continue
            if folded.state == State.DRAFT:
                refusals.append(f"{commit.cite} cites {cited}, a draft; only approved work may be delivered")
            elif not any(_covers(change, commit) for event in folded.evidence for change in event.get("change_ids")):
                refusals.append(f"{commit.cite} cites {cited} without evidence; run `qew evidence {cited} --change {commit.cite}`")
    return lines, refusals


def message_ids(snapshot: Snapshot, message: str) -> Tuple[List[str], bool]:
    """Report lines for a bare message and whether any cited id is unknown."""
    lines = []
    unknown = False
    for cited in cited_ids(message):
        known = cited in snapshot.items
        unknown = unknown or not known
        lines.append(f"{'known' if known else 'unknown'} {cited}")
    return lines, unknown


def jj_push_problems(snapshot: Snapshot, root: Path, revset: str) -> Optional[List[str]]:
    """Refusals for a `jj git push`; None when jj cannot answer (the guard then stays silent)."""
    try:
        commits = jj_revisions(root, revset)
    except QewError:
        return None
    return report(snapshot, commits)[1]
