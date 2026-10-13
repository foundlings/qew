"""Watchman for a jj ledger: jj stats every file of the working copy on each snapshot, and a ledger is one file per event.

With `fsmonitor.backend = "watchman"` jj asks watchman which files changed instead (a 171,000-file ledger snapshots in 0.4 s
instead of 2 s, and qew commits snapshot once per write). A git ledger has nothing to configure.
"""

from enum import Enum
import shutil
import subprocess
from pathlib import Path

from qew.errors import QewError

INSTALL = "brew install watchman"


class State(Enum):
    NOT_JJ = "not a jj ledger"
    MISSING = "watchman is not installed"
    DISABLED = "watchman is installed but not enabled for this ledger"
    ENABLED = "watchman is enabled"


def _jj(ledger: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["jj", "--ignore-working-copy", "--no-pager", *arguments], cwd=ledger, capture_output=True, text=True, check=False)
    except OSError as error:
        raise QewError(f"cannot run jj: {error}", 3) from None


def state(ledger: Path) -> State:
    if not (ledger / ".jj").is_dir():
        return State.NOT_JJ
    configured = _jj(ledger, "config", "get", "fsmonitor.backend")
    if configured.returncode == 0 and configured.stdout.strip() == "watchman":
        return State.ENABLED
    return State.DISABLED if shutil.which("watchman") else State.MISSING


def warning(ledger: Path) -> str | None:
    """One line saying what to do, or None when the ledger needs nothing."""
    found = state(ledger)
    if found is State.MISSING:
        return f"warning: {found.value}; every commit of this jj ledger stats every file. Install it with `{INSTALL}`, then run `qew doctor --fix`"
    if found is State.DISABLED:
        return f"warning: {found.value}; run `qew doctor --fix`"
    return None


def enable(ledger: Path) -> None:
    """Turn watchman on for this ledger's repository only (never the user's global jj config)."""
    found = state(ledger)
    if found is State.MISSING:
        raise QewError(f"{found.value}: `{INSTALL}` first", 4)
    if found is State.NOT_JJ:
        raise QewError(f"{ledger} is not a jj ledger: nothing to enable", 2)
    if found is State.DISABLED:
        done = _jj(ledger, "config", "set", "--repo", "fsmonitor.backend", "watchman")
        if done.returncode != 0 or state(ledger) is not State.ENABLED:
            raise QewError(f"could not enable watchman in {ledger}: {done.stderr.strip()[:200]}", 4)
