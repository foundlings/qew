"""Advisory file locks that die with their holder: the one place qew touches the OS locking APIs.

The lock is a kernel byte-range/whole-file lock (POSIX `flock`, Windows `msvcrt.locking`), so it is released
when the process exits for any reason, `kill -9` included. A lock FILE left behind is therefore inert and is never
cleaned up. The text inside the file (actor, verb, pid, host, start time) is written only so a waiter can say who it
waits for; it is never read to decide whether the lock is taken.
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import random
import socket
import threading
import time
from types import ModuleType
from typing import Any
from collections.abc import Iterator

from qew.errors import QewError
from qew.jsontypes import JsonObject

fcntl: ModuleType | None
msvcrt: Any  # only importable on Windows
try:
    import fcntl as _fcntl_module
    fcntl = _fcntl_module
    BACKEND = "fcntl"
except ImportError:  # Windows
    fcntl = None
    import msvcrt as _msvcrt_module
    msvcrt = _msvcrt_module
    BACKEND = "msvcrt"

LOCK_BYTE = 2 ** 30  # Windows locks this one byte, far past the holder text, so waiters can still read the text
DEFAULT_WAIT_S = 60.0
_BINARY = getattr(os, "O_BINARY", 0)


class LockTimeout(QewError):
    """The lock stayed taken for the whole wait; the message names who holds it."""

    def __init__(self, path: Path, what: str, holder: JsonObject | None, waited: float, ahead: int = 0) -> None:
        by = _describe(holder)
        queue = f", {ahead} waiting ahead" if ahead else ""
        super().__init__(f"{what} is locked by {by}; waited {waited:.0f}s{queue} (lock file {path.name}, "
                         "released automatically when that process ends)", 3)


def _describe(holder: JsonObject | None) -> str:
    if not holder:
        return "another qew process"
    who = f"{holder['who']}'s " if holder.get("who") else ""
    verb = f"`{holder['verb']}` " if holder.get("verb") else "process "
    age = f", {max(0, time.time() - float(holder['since'])):.0f}s" if isinstance(holder.get("since"), (int, float)) else ""
    return f"{who}{verb}(pid {holder.get('pid', '?')}{age})"


def _try_lock(fd: int) -> bool:
    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True
    os.lseek(fd, LOCK_BYTE, os.SEEK_SET)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    finally:
        os.lseek(fd, 0, os.SEEK_SET)
    return True


def _unlock(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    os.lseek(fd, LOCK_BYTE, os.SEEK_SET)
    try:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.lseek(fd, 0, os.SEEK_SET)


def read_holder(path: Path) -> JsonObject | None:
    """What the last holder wrote about itself; advisory text only, never a liveness test."""
    try:
        value = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    except (OSError, UnicodeError, ValueError, IndexError):
        return None
    return value if isinstance(value, dict) else None


def is_held(path: Path) -> bool:
    """True while some live process holds the lock; takes and drops it at once, never creating the file."""
    try:
        fd = os.open(path, os.O_RDWR | _BINARY)
    except OSError:
        return False
    try:
        if _try_lock(fd):
            _unlock(fd)
            return False
        return True
    finally:
        os.close(fd)


SHORT_POLL_S = 0.001


@contextmanager
def short(path: Path, wait_s: float, what: str = "the file") -> Iterator[None]:
    """A lock for a section of microseconds (an append, a rename): asked for again every millisecond, with none of `hold`'s queue of
    waiters, markers and backoff, which are made for holds of seconds. A holder that dies frees it, as with `hold`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | _BINARY, 0o600)
    try:
        deadline = time.monotonic() + wait_s
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise LockTimeout(path, what, read_holder(path), wait_s)
            time.sleep(SHORT_POLL_S)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


POLITE_MAX_S = 120.0  # the longest a polite taker yields to waiters before it asks for the lock anyway
WAITER_STALE_S = 600.0  # a waiter marker older than this belongs to a process that is gone
QUEUE_FRESH_S = 5.0  # a waiting taker touches its marker every quarter second; one untouched this long is stopped, not queued
DEFER_MAX_S = 20.0  # the longest a taker yields to earlier waiters before it asks for the lock anyway


def _marker(path: Path, arrival_ns: int) -> Path:
    """`LOCK.waiting.PID.THREAD.ARRIVAL`: who waits and when it first found the lock taken."""
    return path.with_name(f"{path.name}.waiting.{os.getpid()}.{threading.get_ident()}.{arrival_ns}")


def _parse(marker: Path) -> tuple[int, str, int] | None:
    pid, _, rest = marker.name.rsplit(".waiting.", 1)[-1].partition(".")
    thread, _, arrival = rest.partition(".")
    return (int(pid), thread, int(arrival)) if pid.isdigit() and arrival.isdigit() else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists, not ours to signal
    return True


def waiters(path: Path) -> int:
    """How many other live processes are waiting for the lock at `path` (they leave a marker beside it while they wait)."""
    found = 0
    for marker in path.parent.glob(f"{path.name}.waiting.*"):
        parsed = _parse(marker)
        try:
            fresh = time.time() - marker.stat().st_mtime < WAITER_STALE_S
        except OSError:
            continue
        if parsed and parsed[1] != str(threading.get_ident()) and fresh and _alive(parsed[0]):
            found += 1
    return found


def ahead(path: Path, arrival_ns: int, mine: Path | None = None) -> int:
    """How many live, recently touching waiters arrived before `arrival_ns`. A marker of a dead process, of a stopped one that
    no longer touches it, or one that cannot be read does not count, so a crashed or frozen waiter never holds the queue."""
    found = 0
    for marker in path.parent.glob(f"{path.name}.waiting.*"):
        parsed = _parse(marker)
        if parsed is None or marker == mine:
            continue
        try:
            fresh = time.time() - marker.stat().st_mtime < QUEUE_FRESH_S
        except OSError:
            continue
        if fresh and (parsed[2], parsed[0]) < (arrival_ns, os.getpid()) and _alive(parsed[0]):
            found += 1
    return found


@contextmanager
def hold(path: Path, verb: str = "", who: str = "", wait_s: float = DEFAULT_WAIT_S, what: str = "the ledger",
         polite: bool = False) -> Iterator[None]:
    """Hold the lock at `path` for the block; wait up to `wait_s` seconds with backoff and jitter, then raise LockTimeout.

    A waiter leaves a marker beside the lock and goes after the waiters that arrived before it (at most DEFER_MAX_S; dead
    and stopped ones do not count). A `polite` taker (the background sync, which asks again and again) lets all waiters go
    first, for at most POLITE_MAX_S, so a long sync never keeps an interactive verb out."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | _BINARY, 0o600)
    started = time.monotonic()
    attempt = 0
    arrival = time.time_ns()
    marker = _marker(path, arrival)
    deferring: float | None = None  # when this taker first stood behind an earlier waiter
    try:
        if polite:
            while waiters(path) and time.monotonic() - started < POLITE_MAX_S:
                time.sleep(0.05)
            started = time.monotonic()
        while True:
            queue = 0 if polite else ahead(path, arrival, marker)
            if queue and deferring is None:
                deferring = time.monotonic()
            # Earlier waiters go first, but only as a courtesy: past DEFER_MAX_S the kernel lock is tried regardless, so a
            # waiter that is alive yet never takes its turn can delay this one and never lock it out.
            if not queue or time.monotonic() - (deferring or 0.0) >= DEFER_MAX_S:
                if _try_lock(fd):
                    break
            if time.monotonic() - started >= wait_s:
                raise LockTimeout(path, what, read_holder(path), time.monotonic() - started, queue)
            if not polite:
                try:
                    marker.touch()
                except OSError:
                    pass
            time.sleep(min(0.25, 0.01 * 2 ** min(attempt, 5)) + random.uniform(0, 0.02))
            attempt += 1
        marker.unlink(missing_ok=True)
        try:
            text = json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "verb": verb, "who": who, "since": time.time()})
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, (text + "\n").encode("utf-8"))
        except OSError:
            pass  # the text is a courtesy to waiters; the lock is what counts
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        marker.unlink(missing_ok=True)
        os.close(fd)
