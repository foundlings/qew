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

    def __init__(self, path: Path, what: str, holder: JsonObject | None, waited: float) -> None:
        by = _describe(holder)
        super().__init__(f"{what} is locked by {by}; waited {waited:.0f}s (lock file {path.name}, "
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


POLITE_MAX_S = 120.0  # the longest a polite taker yields to waiters before it asks for the lock anyway
WAITER_STALE_S = 600.0  # a waiter marker older than this belongs to a process that is gone


def _marker(path: Path) -> Path:
    return path.with_name(f"{path.name}.waiting.{os.getpid()}.{threading.get_ident()}")


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
        pid, _, thread = marker.name.rsplit(".waiting.", 1)[-1].partition(".")
        try:
            fresh = time.time() - marker.stat().st_mtime < WAITER_STALE_S
        except OSError:
            continue
        if pid.isdigit() and thread != str(threading.get_ident()) and fresh and _alive(int(pid)):
            found += 1
    return found


@contextmanager
def hold(path: Path, verb: str = "", who: str = "", wait_s: float = DEFAULT_WAIT_S, what: str = "the ledger",
         polite: bool = False) -> Iterator[None]:
    """Hold the lock at `path` for the block; wait up to `wait_s` seconds with backoff and jitter, then raise LockTimeout.

    A waiter leaves a marker beside the lock. A `polite` taker (the background sync, which asks again and again) lets
    waiters go first, for at most POLITE_MAX_S, so a long sync never keeps an interactive verb out."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | _BINARY, 0o600)
    started = time.monotonic()
    attempt = 0
    marker = _marker(path)
    try:
        if polite:
            while waiters(path) and time.monotonic() - started < POLITE_MAX_S:
                time.sleep(0.05)
            started = time.monotonic()
        while not _try_lock(fd):
            if time.monotonic() - started >= wait_s:
                raise LockTimeout(path, what, read_holder(path), time.monotonic() - started)
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
