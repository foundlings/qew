"""Automatic collection of a jj ledger's operation log.

Every jj command that changes the ledger adds an operation, and nothing removes them: a busy ledger reached 2.2 GB of operation
history. A write collects the operations older than `[replication] op_log_keep_days` (14) at most once a day, with no verb to
remember. Only a contiguous run of old operations from the oldest is abandoned, so nothing recent goes, and a second process
that finds the collection running skips it.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
import sys
import time

from qew import filelock
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_repo import JJ_READ, LedgerRepo

DAY_S = 86400.0
GC_TIMEOUT_S = 900.0


def maybe_collect(config: Config, repo: LedgerRepo, exclusive: Callable[[], AbstractContextManager[None]]) -> None:
    """`exclusive` is the ledger write lock, taken without waiting: another jj command adding an operation while the old ones are
    abandoned would leave the working copy on an operation the log no longer has, so a busy ledger simply collects on a later write."""
    days = config.replication.op_log_keep_days
    if repo.vcs != "jj" or days <= 0:
        return
    cache = config.ledger_path / ".qew"
    marker = cache / "last-gc"
    if marker.exists() and time.time() - marker.stat().st_mtime < DAY_S:
        return
    try:
        with exclusive():
            marker.touch()  # before the work: a slow or failing collection is not retried by every write
            _collect(repo, time.time() - days * DAY_S)
    except filelock.LockTimeout:
        return  # a writer holds the lock: the marker is untouched, so a later write tries again
    except QewError as error:
        print(f"qew: warning: collecting the jj operation log failed ({error}); it is retried tomorrow", file=sys.stderr)


def _collect(repo: LedgerRepo, cutoff: float) -> None:
    listing = repo.run([*JJ_READ, "op", "log", "--no-graph", "-T", 'id ++ " " ++ time.end().format("%s") ++ "\\n"'], timeout=GC_TIMEOUT_S).stdout
    operations = [(parts[0], float(parts[1])) for parts in (line.split() for line in listing.splitlines()) if len(parts) == 2]
    oldest_first = [op for op in reversed(operations) if op[1] > 0]  # the root operation (time 0) always stays
    target = None
    for identifier, seconds in oldest_first[:-1]:  # the newest operation is the current one: never abandoned
        if seconds >= cutoff:
            break
        target = identifier
    if target is None:
        return
    repo.run(["jj", "--no-pager", "status"], timeout=GC_TIMEOUT_S)  # bring the working copy to the newest operation, which the ignore-working-copy reads never do
    repo.run(["jj", "--no-pager", "op", "abandon", f"..{target}"], timeout=GC_TIMEOUT_S)  # with the working copy: it records the operation it stands on
    repo.run([*JJ_READ, "util", "gc"], timeout=GC_TIMEOUT_S, check=False)
