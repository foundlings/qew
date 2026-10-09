"""The detached background sync worker: queue it after a write, run it under its own lock.

The foreground command never waits for a forge and never prints sync output: at most `sync: queued`.
Pending work is derived (see sync_plan), so a failed or killed worker needs no outbox: it leaves a
`.qew/sync.pending` marker and the next command starts another.
"""

import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Optional

from qew import filelock, trace
from qew.ledger_config import Config

_backoff = 0.0  # seconds a rate-limited forge asked this process to wait; the worker records it
LOG_LIMIT = 200_000
LOG_KEEP = 50_000


def cache_dir(config: Config) -> Path:
    path = config.ledger_path / ".qew"
    path.mkdir(exist_ok=True)
    return path


def worker_running(config: Config) -> bool:
    return filelock.is_held(cache_dir(config) / "sync.lock")


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


def enqueue(config: Config, item_ids) -> None:
    """Remember which items a write touched: the worker syncs exactly those (a union, so bursts cost one run)."""
    ids = sorted(set(item_ids))
    if not ids:
        return
    with open(cache_dir(config) / "sync.queue", "a", encoding="utf-8") as handle:
        handle.write("".join(f"{item_id}\n" for item_id in ids))


def take_queue(config: Config) -> list:
    """Drain the queued item ids (each once); ids queued while this runs land in a fresh file for the next round."""
    path = cache_dir(config) / "sync.queue"
    taken = path.with_name("sync.queue.taking")
    try:
        os.replace(path, taken)
    except FileNotFoundError:
        return []
    ids = sorted(set(taken.read_text(encoding="utf-8").split()))
    taken.unlink(missing_ok=True)
    return ids


def catch_up_pending(config: Config) -> bool:
    """A failed pass left the marker: the next pass syncs the whole target, not only the queued items."""
    return (cache_dir(config) / "sync.pending").exists()


def queue(config: Config, item_ids=()) -> None:
    """Queue the touched items and start one detached worker unless one is running or the forge asked for a pause."""
    cache = cache_dir(config)
    enqueue(config, item_ids)
    if retry_at(config) > time.time():
        return
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
    lock = filelock.hold(cache_dir(config) / "sync.lock", verb="sync worker", wait_s=0, what="the sync worker")
    try:
        lock.__enter__()
    except filelock.LockTimeout:
        return  # another worker is running: it will pick the pending work up
    try:
        _run_locked(config, run_once)
    finally:
        lock.__exit__(None, None, None)


def _run_locked(config: Config, run_once) -> None:
    global _backoff
    _backoff = 0.0
    if trace.enabled():
        try:
            spawned = float((cache_dir(config) / "sync.spawned").read_text(encoding="utf-8"))
            trace.note("worker start lag since spawn", time.time() - spawned)
        except (OSError, ValueError):
            pass
    failed = False
    for _ in range(3):
        before = newest_event(config)
        failed = not run_once()
        if failed or _backoff > 0 or newest_event(config) == before:
            break
    mark(config, failed)
