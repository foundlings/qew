"""Pace forge creates under a backend's content-creation limits (per minute and per hour), shared by every sync worker."""

from collections import deque
import threading
import time
from collections.abc import Callable


class PaceStop(Exception):
    """A create must wait longer than the sync may: stop starting items; a rerun continues."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"the next create may go in {int(seconds)}s")
        self.seconds = seconds


class Pacer:
    """Sliding windows of recent create times; 0 for a limit means none. Slots are reserved in order across threads."""

    def __init__(self, per_minute: int, per_hour: int, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.clock, self.sleep = clock, sleep
        self._lock = threading.Lock()
        self._windows: list[tuple[deque[float], int, float]] = [(deque(maxlen=limit), limit, span) for limit, span in ((per_minute, 60.0), (per_hour, 3600.0)) if limit > 0]

    def acquire(self, allow: float | None = None) -> float:
        """Wait until a create may go and return 0.0. With `allow`, return the wait instead (and reserve nothing)
        when it would exceed `allow` seconds."""
        with self._lock:
            now = self.clock()
            start = now
            for window, limit, span in self._windows:
                if len(window) >= limit:
                    start = max(start, window[0] + span)
            wait = start - now
            if allow is not None and wait > allow:
                return wait
            for window, _limit, _span in self._windows:
                window.append(start)
        if wait > 0:
            self.sleep(wait)
        return 0.0
