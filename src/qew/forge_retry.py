"""Retry forge calls that are safe to repeat when the forge answers 5xx or the connection fails.

Wraps a `Transport`: a GET, PUT, PATCH or DELETE, a GraphQL query and a short list of idempotent GraphQL mutations and
sub-issue calls are repeated with exponential backoff and jitter (a `Retry-After` is honoured); every other POST, above all
a create, is attempted once so a 500 whose request was applied can never duplicate. The last answer is returned when the
retries run out, and the caller maps it to the typed error as before.
"""

from dataclasses import dataclass
import json
import random
import re
import threading
import time
from collections.abc import Callable, Mapping

from qew.errors import QewError
from qew.forge import ForgeUnavailable, HttpRequest, HttpResponse, Transport

DEFAULT_RETRIES = 4
DEFAULT_MAX_WAIT = 60.0
DEFAULT_BASE = 2.0
_SAFE_METHODS = ("GET", "HEAD", "PUT", "PATCH", "DELETE")
_SAFE_MUTATIONS = {"AddItem", "SetStatus", "SetCard", "SetCardField", "UpdateView", "UpdateStatusField", "EnableAutoMerge", "MarkReady"}
_OPERATION = re.compile(r"\b(query|mutation)\s+(\w+)")

RATE_RETRIES = 6  # waits for a rate limit before the item is given up on; creates are never repeated, so they teach the pace instead
RATE_WAIT_BACKGROUND = 300.0  # the longest single wait in the background worker: nobody is waiting for it
RATE_WAIT_INTERACTIVE = 60.0  # a verb a person is waiting on gives up sooner and says when to try again
RATE_BASE_FACTOR = 15.0  # a rate limit starts at 15 times the 5xx backoff base (30 s by default), doubling

_lock = threading.Lock()
_retried = 0
_rate_waits = 0
_rate_waited = 0.0
_gate_until = 0.0  # monotonic time before which no call is sent: a limit hit by one worker pauses them all


def retried_calls() -> int:
    """How many retries this process has made so far (for the sync summary)."""
    with _lock:
        return _retried


def rate_summary() -> tuple[int, float]:
    """(waits, seconds waited) for rate limits in this process, for the sync summary."""
    with _lock:
        return _rate_waits, _rate_waited


@dataclass(frozen=True)
class RateSettings:
    retries: int
    max_wait: float
    base: float


def rate_settings(options: Mapping[str, object], environ: Mapping[str, str]) -> RateSettings:
    """Rate-limit waits from a mirror's `forge_rate_retries` / `forge_rate_max_wait` (env QEW_RATE_RETRIES overrides the count)."""
    retries: object = environ.get("QEW_RATE_RETRIES") or options.get("forge_rate_retries", RATE_RETRIES)
    try:
        count = int(retries) if isinstance(retries, (int, str)) and not isinstance(retries, bool) else -1
    except ValueError:
        count = -1
    wait = options.get("forge_rate_max_wait", RATE_WAIT_BACKGROUND if environ.get("QEW_BACKGROUND") else RATE_WAIT_INTERACTIVE)
    if not 0 <= count <= 20 or not isinstance(wait, (int, float)) or isinstance(wait, bool) or wait <= 0:
        raise QewError("[[mirror]] forge_rate_retries is 0 to 20 and forge_rate_max_wait a positive number of seconds", 1)
    try:
        base = float(environ["QEW_RETRY_BASE_S"]) if environ.get("QEW_RETRY_BASE_S") else DEFAULT_BASE
    except ValueError:
        raise QewError("QEW_RETRY_BASE_S must be a number of seconds", 1) from None
    return RateSettings(count, float(wait), base)


def gate_wait(sleep: Callable[[float], None] = time.sleep) -> None:
    """Block while a rate-limit pause is open."""
    while True:
        with _lock:
            remaining = _gate_until - time.monotonic()
        if remaining <= 0:
            return
        sleep(min(remaining, 5.0))


def rate_backoff(attempt: int, asked: float, settings: RateSettings, sleep: Callable[[float], None] = time.sleep,
                 jitter: Callable[[], float] = random.random) -> None:
    """Wait out a rate limit: what the forge asked for, else 30 s doubling per attempt with jitter, never past the cap.
    Every worker of this process waits with this one (the gate), so a burst does not keep re-hitting the wall."""
    global _gate_until, _rate_waits, _rate_waited
    delay = min(max(asked, settings.base * RATE_BASE_FACTOR * 2 ** attempt * (0.75 + 0.5 * jitter())), settings.max_wait)
    with _lock:
        _rate_waits += 1
        _rate_waited += delay
        _gate_until = max(_gate_until, time.monotonic() + delay)
    gate_wait(sleep)


def rate_limited(response: HttpResponse) -> bool:
    """True when the answer is a rate limit (429, or a 403 that says so): the request was refused, not applied."""
    if response.status == 429:
        return True
    return response.status == 403 and b"rate limit" in response.body.lower()


def repeatable(request: HttpRequest) -> bool:
    """True when sending the request again after a failed answer cannot do harm."""
    if request.method in _SAFE_METHODS:
        return True
    if request.method != "POST":
        return False
    if request.url.rstrip("/").endswith("/sub_issues"):
        return True  # adding the same sub-issue again (replace_parent) lands in the same state
    if request.url.rstrip("/").endswith("/graphql") and request.body:
        try:
            query = json.loads(request.body.decode("utf-8")).get("query", "")
        except (ValueError, UnicodeError, AttributeError):
            return False
        found = _OPERATION.search(query) if isinstance(query, str) else None
        return found is not None and (found.group(1) == "query" or found.group(2) in _SAFE_MUTATIONS)
    return False


def settings(options: dict[str, object], environ_base: str) -> tuple[int, float, float]:
    """(retries, max wait, base delay) from a mirror's options and the QEW_RETRY_BASE_S override."""
    retries = options.get("forge_retries", DEFAULT_RETRIES)
    wait = options.get("forge_retry_max_wait", DEFAULT_MAX_WAIT)
    if type(retries) is not int or not 0 <= retries <= 20:
        raise QewError("[[mirror]] forge_retries must be a whole number between 0 and 20", 1)
    if not isinstance(wait, (int, float)) or isinstance(wait, bool) or wait <= 0:
        raise QewError("[[mirror]] forge_retry_max_wait must be a positive number of seconds", 1)
    try:
        base = float(environ_base) if environ_base else DEFAULT_BASE
    except ValueError:
        raise QewError("QEW_RETRY_BASE_S must be a number of seconds", 1) from None
    return retries, float(wait), base


def retrying(transport: Transport, retries: int, max_wait: float, base: float = DEFAULT_BASE,
             sleep: Callable[[float], None] = time.sleep, jitter: Callable[[], float] = random.random,
             rate: RateSettings | None = None) -> Transport:
    """`transport` that repeats a repeatable request up to `retries` times after a 5xx or a connection failure, and waits
    out a rate limit (`rate`) for the same requests: a refused request was never applied, but a create is left to the
    pacer, which learns the pace from the block."""
    if retries <= 0 and rate is None:
        return transport

    def send(request: HttpRequest) -> HttpResponse:
        safe = repeatable(request)
        attempt = 0
        limited = 0
        while True:
            gate_wait(sleep)
            try:
                response = transport(request)
            except ForgeUnavailable:
                if not safe or attempt >= retries:
                    raise
                answer_wait = 0.0
            else:
                if rate is not None and safe and limited < rate.retries and rate_limited(response):
                    asked = response.headers.get("Retry-After") or response.headers.get("retry-after") or ""
                    rate_backoff(limited, float(asked) if asked.replace(".", "", 1).isdigit() else 0.0, rate, sleep, jitter)
                    limited += 1
                    continue
                if response.status < 500 or not safe or attempt >= retries:
                    return response
                asked = response.headers.get("Retry-After") or response.headers.get("retry-after") or ""
                answer_wait = float(asked) if asked.replace(".", "", 1).isdigit() else 0.0
            global _retried
            with _lock:
                _retried += 1
            delay = max(answer_wait, base * 2 ** (attempt + 1) * (0.75 + 0.5 * jitter()))
            sleep(min(delay, max_wait))
            attempt += 1

    return send
