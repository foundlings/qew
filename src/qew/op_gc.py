"""Automatic collection of a jj ledger's operation log, by a detached collector that holds the write lock only for a bounded step.

Every jj command that changes the ledger adds an operation, and nothing removes them: a busy ledger reached 2.2 GB of operation
history. A write starts a collector (at most once a day) that abandons the operations older than `[replication] op_log_keep_days`
(14), with no verb to remember. The write itself never runs or waits for it.

`jj op abandon ..X` rewrites every operation after X, so its time grows with the operations that are KEPT (about 7 ms each),
not with those dropped. Another jj command adding an operation meanwhile would bring the old operations back, so the step runs
under the ledger write lock. To keep that hold short the collector keeps at most as many recent operations as fit
`op_log_max_hold_s` at the rate it measured last time: the keep window is a ceiling, the hold budget a floor on what is dropped.
The slow `jj util gc` that follows runs outside the write lock, under the collector's own `gc.lock` (one collector at a time).
"""

from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
import os
from pathlib import Path
import subprocess
import sys
import time

from qew import filelock
from qew.errors import QewError
from qew.ledger_config import Config
from qew.ledger_repo import JJ_READ, LedgerRepo

DAY_S = 86400.0
GC_TIMEOUT_S = 900.0
DEFAULT_RATE_S = 0.007  # seconds to rewrite one kept operation, until a collection has measured the real one
MIN_KEEP_OPS = 10  # the newest operations always stay, whatever the budget
LOCK_WAIT_S = 120.0  # how long the collector waits its turn for the write lock (it yields to waiting writers)


def _cache(config: Config) -> Path:
    return config.ledger_path / ".qew"


def start(config: Config, repo: LedgerRepo) -> None:
    """Start the detached collector when a day passed since the last attempt; never waits for it and never fails the write."""
    days = config.replication.op_log_keep_days
    if repo.vcs != "jj" or days <= 0:
        return
    cache = _cache(config)
    marker = cache / "last-gc"
    if marker.exists() and time.time() - marker.stat().st_mtime < DAY_S:
        return
    if filelock.is_held(cache / "gc.lock"):
        return
    marker.touch()  # before the spawn: a slow or failing collection is not retried by every write
    try:
        with open(cache / "gc.log", "ab") as handle:
            subprocess.Popen([sys.executable, "-m", "qew", "gc"], cwd=config.root, stdin=subprocess.DEVNULL, stdout=handle, stderr=handle,
                             start_new_session=True, env={**os.environ, "QEW_NO_FETCH": "1"})
    except OSError as error:
        print(f"qew: warning: could not start the op-log collector ({error}); it is retried tomorrow", file=sys.stderr)


def choose(oldest_first: list[tuple[str, float]], cutoff: float, keep_at_most: int) -> str | None:
    """The newest operation to abandon: everything up to it goes. The window drops what is older than `cutoff`; the budget drops
    all but the newest `keep_at_most`; whichever drops more wins, and the newest operation (the one the ledger stands on) never goes."""
    droppable = oldest_first[:-1]
    by_window = -1
    for index, (_, seconds) in enumerate(droppable):
        if seconds >= cutoff:
            break
        by_window = index
    by_budget = len(oldest_first) - max(keep_at_most, MIN_KEEP_OPS) - 1
    last = min(max(by_window, by_budget), len(droppable) - 1)
    return droppable[last][0] if last >= 0 else None


def _rate(cache: Path) -> float:
    try:
        measured = float((cache / "gc-rate").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_RATE_S
    return measured if measured > 0 else DEFAULT_RATE_S


def collect(config: Config, repo: LedgerRepo, exclusive: Callable[[], AbstractContextManager[None]]) -> str:
    """One collection, in the foreground. `exclusive` takes the ledger write lock, waiting for its turn. Returns what happened."""
    cache = _cache(config)
    cache.mkdir(exist_ok=True)
    with ExitStack() as stack:
        try:
            stack.enter_context(filelock.hold(cache / "gc.lock", verb="gc collector", wait_s=0, what="the op-log collector"))
        except filelock.LockTimeout:
            return "a collection is already running"
        listing = repo.run([*JJ_READ, "op", "log", "--no-graph", "-T", 'id ++ " " ++ time.end().format("%s") ++ "\\n"'], timeout=GC_TIMEOUT_S).stdout
        operations = [(parts[0], float(parts[1])) for parts in (line.split() for line in listing.splitlines()) if len(parts) == 2]
        oldest_first = [op for op in reversed(operations) if op[1] > 0]  # the root operation (time 0) always stays
        rate = _rate(cache)
        target = choose(oldest_first, time.time() - config.replication.op_log_keep_days * DAY_S, int(config.replication.op_log_max_hold_s / rate))
        if target is None:
            report = "nothing to collect"
        else:
            dropped = [identifier for identifier, _ in oldest_first].index(target) + 1
            kept = len(oldest_first) - dropped
            with exclusive():
                began = time.monotonic()
                repo.run(["jj", "--no-pager", "status"], timeout=GC_TIMEOUT_S)  # bring the working copy to the newest operation, which the ignore-working-copy reads never do
                repo.run(["jj", "--no-pager", "op", "abandon", f"..{target}"], timeout=GC_TIMEOUT_S)  # with the working copy: it records the operation it stands on
                spent = time.monotonic() - began
            if kept >= MIN_KEEP_OPS:
                (cache / "gc-rate").write_text(f"{spent / kept:.6f}\n", encoding="utf-8")
            report = f"abandoned {dropped} operation(s), kept {kept}; the write lock was held {spent:.1f}s"
        repo.run([*JJ_READ, "util", "gc"], timeout=GC_TIMEOUT_S, check=False)  # outside the write lock: only this collector is held back
        (cache / "gc-done").write_text(f"{time.time()}\n", encoding="utf-8")
        return report


def run_detached(config: Config, repo: LedgerRepo, exclusive: Callable[[], AbstractContextManager[None]]) -> str:
    """The collector process: a failure is logged for the owner, never raised into anything."""
    try:
        return collect(config, repo, exclusive)
    except filelock.LockTimeout as error:
        return f"the write lock stayed busy ({error}); retried tomorrow"
    except QewError as error:
        return f"collecting the jj operation log failed ({error}); retried tomorrow"
