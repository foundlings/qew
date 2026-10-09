"""Pace forge creates under a backend's content-creation limits, shared by every sync worker.

The documented limits are a guess for any one account, so a secondary-limit block teaches the pace: the number of
creates that came before it becomes this target's budget for a 30 minute window, and a clean run that reached that
pace raises it again.
"""

from collections import deque
import json
import math
from pathlib import Path
import threading
import time
from collections.abc import Callable

LEARNED_SPAN = 1800.0  # seconds a learned budget covers
LEARNED_SHARE = 0.8  # of the budget a block showed
MIN_BUDGET = 3  # smallest window limit a learned budget can impose
MIN_EVIDENCE = 20  # a block after fewer recorded creates was probably caused by something else and teaches nothing
TTL = 21600.0  # seconds a learned budget lasts after it was set or raised; then the documented default applies again
GROWTH = 1.25  # a clean run that reached the learned pace raises the budget by this factor
KEEP = 7200.0  # seconds of create history kept


class PaceStop(Exception):
    """A create must wait longer than the sync may: stop starting items; a rerun continues."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"the next create may go in {int(seconds)}s")
        self.seconds = seconds


class Pacer:
    """Sliding windows of recent create times; 0 for a limit means none. Slots are reserved in order across threads."""

    def __init__(self, per_minute: int, per_hour: int, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, extra: tuple[tuple[int, float], ...] = ()) -> None:
        self.clock, self.sleep = clock, sleep
        self._lock = threading.Lock()
        limits = ((per_minute, 60.0), (per_hour, 3600.0), *extra)
        self._windows = [(deque[float](maxlen=limit), limit, span) for limit, span in limits if limit > 0]

    def seed(self, ages: list[float]) -> None:
        """Count creates made `ages` seconds ago (an earlier run) against the windows."""
        now = self.clock()
        with self._lock:
            for window, _limit, span in self._windows:
                for age in sorted(ages, reverse=True):
                    if age < span:
                        window.append(now - age)

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


class PaceState:
    """What this machine knows about one target's creates, kept beside the other local sync state (never committed)."""

    def __init__(self, directory: Path, wall: Callable[[], float] = time.time) -> None:
        self._creates = directory / "creates.json"
        self._learned = directory / "pace.json"
        self._wall = wall
        self._lock = threading.Lock()

    def _read(self, path: Path, empty: list[float] | dict[str, float]) -> list[float] | dict[str, float]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return empty  # a damaged cache only costs pacing accuracy; the ledger is untouched
        return value if isinstance(value, type(empty)) else empty

    def _write(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def _times(self) -> list[float]:
        found = self._read(self._creates, [])
        assert isinstance(found, list)
        raw: list[object] = list(found)  # read from disk: not trusted to hold numbers
        now = self._wall()
        return [float(t) for t in raw if isinstance(t, (int, float)) and 0 <= now - t < KEEP]

    def ages(self) -> list[float]:
        now = self._wall()
        return [now - t for t in self._times()]

    def record_create(self) -> None:
        with self._lock:
            self._write(self._creates, [*self._times(), self._wall()])

    def _budget(self) -> tuple[int, float] | None:
        learned = self._read(self._learned, {})
        assert isinstance(learned, dict)
        budget: object = learned.get("budget")  # read from disk: not trusted to be a number
        span: object = learned.get("span", LEARNED_SPAN)
        at: object = learned.get("at")
        if isinstance(at, (int, float)) and self._wall() - at > TTL:
            return None
        if isinstance(budget, (int, float)) and budget > 0 and isinstance(span, (int, float)) and span > 0:
            return int(budget), float(span)
        return None

    def window(self) -> tuple[int, float] | None:
        """(creates, seconds) the learned budget allows, or None before any block."""
        learned = self._budget()
        if learned is None:
            return None
        budget, span = learned
        return max(MIN_BUDGET, int(budget * LEARNED_SHARE)), span

    def record_block(self) -> None:
        """A secondary-limit block: the creates that preceded it in the window (the blocked one excluded) are the budget."""
        with self._lock:
            span = (self._budget() or (0, LEARNED_SPAN))[1]
            made = sum(1 for age in self.ages() if age < span) - 1
            if made >= MIN_EVIDENCE:
                self._write(self._learned, {"budget": made, "span": span, "at": self._wall()})

    def relax(self) -> None:
        with self._lock:
            learned = self._budget()
            if learned is not None:
                self._write(self._learned, {"budget": math.ceil(learned[0] * GROWTH), "span": learned[1], "at": self._wall()})
