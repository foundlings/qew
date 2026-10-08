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
import time
from typing import Iterator, Optional

from qew.errors import QewError

try:
    import fcntl
    BACKEND = "fcntl"
except ImportError:  # Windows
    fcntl = None
    import msvcrt
    BACKEND = "msvcrt"

LOCK_BYTE = 2 ** 30  # Windows locks this one byte, far past the holder text, so waiters can still read the text
DEFAULT_WAIT_S = 60.0
_BINARY = getattr(os, "O_BINARY", 0)


class LockTimeout(QewError):
    """The lock stayed taken for the whole wait; the message names who holds it."""

    def __init__(self, path: Path, what: str, holder: Optional[dict], waited: float) -> None:
        by = _describe(holder)
        super().__init__(f"{what} is locked by {by}; waited {waited:.0f}s (lock file {path.name}, "
                         "released automatically when that process ends)", 3)


def _describe(holder: Optional[dict]) -> str:
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


def read_holder(path: Path) -> Optional[dict]:
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


@contextmanager
def hold(path: Path, verb: str = "", who: str = "", wait_s: float = DEFAULT_WAIT_S, what: str = "the ledger") -> Iterator[None]:
    """Hold the lock at `path` for the block; wait up to `wait_s` seconds with backoff and jitter, then raise LockTimeout."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | _BINARY, 0o600)
    started = time.monotonic()
    attempt = 0
    try:
        while not _try_lock(fd):
            if time.monotonic() - started >= wait_s:
                raise LockTimeout(path, what, read_holder(path), time.monotonic() - started)
            time.sleep(min(0.25, 0.01 * 2 ** min(attempt, 5)) + random.uniform(0, 0.02))
            attempt += 1
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
        os.close(fd)
