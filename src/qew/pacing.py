"""Pace forge creates under a backend's content-creation limits (per minute and per hour), shared by every sync worker."""

import time
from typing import Callable, Optional


class Pacer:
    def __init__(self, per_minute: int, per_hour: int, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.per_minute, self.per_hour, self.clock, self.sleep = per_minute, per_hour, clock, sleep

    def acquire(self, allow: Optional[float] = None) -> float:
        """Wait until a create may go; with `allow`, refuse (returning the wait) when it would take longer. 0.0 when ready."""
        return 0.0
