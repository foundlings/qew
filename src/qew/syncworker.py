"""The detached background sync worker: queue it after a write, run it under its own lock.

The foreground command never waits for a forge and never prints sync output: at most `sync: queued`.
Pending work is derived (see sync_plan), so a failed or killed worker needs no outbox: it leaves a
`.qew/sync.pending` marker and the next command starts another.
"""

import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Optional

from qew.ledger_config import Config

_backoff = 0.0  # seconds a rate-limited forge asked this process to wait; the worker records it
LOG_LIMIT = 200_000
LOG_KEEP = 50_000


def cache_dir(config: Config) -> Path:
    path = config.ledger_path / ".qew"
    path.mkdir(exist_ok=True)
    return path


def worker_running(config: Config) -> bool:
    handle = open(cache_dir(config) / "sync.lock", "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        handle.close()
    return False


def _trim_log(log: Path) -> None:
    if log.exists() and log.stat().st_size > LOG_LIMIT:
        log.write_bytes(log.read_bytes()[-LOG_KEEP:])


def set_backoff(seconds: float) -> None:
    """A sync round met a rate limit: the worker marks pending work with the earliest retry time."""
    global _backoff
    _backoff = max(_backoff, seconds)


def retry_at(config: Config) -> float:
    """Epoch seconds before which no worker should start (0 when no backoff is recorded)."""
    flag = cache_dir(config) / "sync.pending"
    if not flag.exists():
        return 0.0
    for line in flag.read_text(encoding="utf-8").splitlines():
        if line.startswith("retry_at="):
            try:
                return float(line[len("retry_at="):])
            except ValueError:
                return 0.0
    return 0.0


def queue(config: Config) -> str:
    """Start one detached worker unless one is running or the forge asked for a pause; returns the one line the foreground may print."""
    cache = cache_dir(config)
    if retry_at(config) > time.time():
        return "sync: queued (paused until the forge's rate limit resets)"
    if not worker_running(config):
        log = cache / "sync.log"
        _trim_log(log)
        with open(log, "ab") as handle:
            subprocess.Popen(
                [sys.executable, "-m", "qew", "sync"], cwd=config.root, stdin=subprocess.DEVNULL,
                stdout=handle, stderr=handle, start_new_session=True,
                env={**os.environ, "QEW_BACKGROUND": "1", "QEW_NO_FETCH": "1"},
            )
        (cache / "sync.spawned").write_text(str(time.time()), encoding="utf-8")
    return "sync: queued"


def retry_if_pending(config: Config) -> None:
    """A read command restarts the worker when the last run left work, at most every sync_retry_s."""
    cache = cache_dir(config)
    if not (cache / "sync.pending").exists() or worker_running(config) or retry_at(config) > time.time():
        return
    spawned = cache / "sync.spawned"
    if spawned.exists() and time.time() - float(spawned.read_text() or 0) < config.replication.sync_retry_s:
        return
    queue(config)


def mark(config: Config, pending: bool) -> None:
    flag = cache_dir(config) / "sync.pending"
    if pending:
        wait = f"\nretry_at={time.time() + _backoff}\n" if _backoff > 0 else "\n"
        flag.write_text(str(time.time()) + wait, encoding="utf-8")
    else:
        flag.unlink(missing_ok=True)


def newest_event(config: Config) -> str:
    events = config.ledger_path / "events"
    if not events.is_dir():
        return ""
    return max((p.stem for d in events.iterdir() if d.is_dir() for p in d.glob("*.json")), default="")


def run_worker(config: Config, run_once) -> None:
    """Hold the worker lock, sync until nothing new arrived meanwhile (at most 3 rounds), keep the marker honest."""
    handle = open(cache_dir(config) / "sync.lock", "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return
    global _backoff
    _backoff = 0.0
    try:
        failed = False
        for _ in range(3):
            before = newest_event(config)
            failed = not run_once()
            if failed or _backoff > 0 or newest_event(config) == before:
                break
        mark(config, failed)
    finally:
        handle.close()
