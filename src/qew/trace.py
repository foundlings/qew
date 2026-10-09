"""`QEW_TRACE=1`: per-phase wall time and every forge HTTP call on stderr (a worker's stderr is `.qew/sync.log`).

A diagnostic only: it never changes behavior and prints nothing unless the variable is set.
"""

from contextlib import contextmanager
import os
import sys
import time
from collections.abc import Callable, Iterator

_START = time.perf_counter()


def enabled() -> bool:
    return bool(os.environ.get("QEW_TRACE"))


def note(label: str, seconds: float) -> None:
    if enabled():
        print(f"trace {time.perf_counter() - _START:7.2f}s +{seconds:6.2f}s {label}", file=sys.stderr, flush=True)


@contextmanager
def phase(label: str) -> Iterator[None]:
    begun = time.perf_counter()
    try:
        yield
    finally:
        note(label, time.perf_counter() - begun)


def http(method: str, url: str, status: object, seconds: float) -> None:
    note(f"http {method} {url.split('://', 1)[-1].split('?')[0]} -> {status}", seconds)


def traced[**P, R](label: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator: time the call under `label` when tracing."""
    def wrap(function: Callable[P, R]) -> Callable[P, R]:
        def timed(*args: P.args, **kwargs: P.kwargs) -> R:
            with phase(label):
                return function(*args, **kwargs)
        timed.__name__ = function.__name__
        timed.__doc__ = function.__doc__
        return timed
    return wrap
