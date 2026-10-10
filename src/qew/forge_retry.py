"""Retry forge calls that are safe to repeat when the forge answers 5xx or the connection fails.

Wraps a `Transport`: a GET, PUT, PATCH or DELETE, a GraphQL query and a short list of idempotent GraphQL mutations and
sub-issue calls are repeated with exponential backoff and jitter (a `Retry-After` is honoured); every other POST, above all
a create, is attempted once so a 500 whose request was applied can never duplicate. The last answer is returned when the
retries run out, and the caller maps it to the typed error as before.
"""

import json
import random
import re
import threading
import time
from collections.abc import Callable

from qew.errors import QewError
from qew.forge import ForgeUnavailable, HttpRequest, HttpResponse, Transport

DEFAULT_RETRIES = 4
DEFAULT_MAX_WAIT = 60.0
DEFAULT_BASE = 2.0
_SAFE_METHODS = ("GET", "HEAD", "PUT", "PATCH", "DELETE")
_SAFE_MUTATIONS = {"AddItem", "SetStatus", "SetCard", "SetCardField", "UpdateView", "UpdateStatusField", "EnableAutoMerge", "MarkReady"}
_OPERATION = re.compile(r"\b(query|mutation)\s+(\w+)")

_lock = threading.Lock()
_retried = 0


def retried_calls() -> int:
    """How many retries this process has made so far (for the sync summary)."""
    with _lock:
        return _retried


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
             sleep: Callable[[float], None] = time.sleep, jitter: Callable[[], float] = random.random) -> Transport:
    """`transport` that repeats a repeatable request up to `retries` times after a 5xx or a connection failure."""
    if retries <= 0:
        return transport

    def send(request: HttpRequest) -> HttpResponse:
        safe = repeatable(request)
        attempt = 0
        while True:
            try:
                response = transport(request)
            except ForgeUnavailable:
                if not safe or attempt >= retries:
                    raise
                answer_wait = 0.0
            else:
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
