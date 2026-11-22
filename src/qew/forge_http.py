"""Shared HTTP plumbing for backends: the real transport, token lookup, JSON calls."""

from dataclasses import dataclass
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, override
from collections.abc import Mapping

from qew import trace
from qew.forge import (
    ForgeAuth,
    ForgeRejected,
    ForgeSecondaryLimit,
    ForgeUnavailable,
    HttpRequest,
    HttpResponse,
    Transport,
)
from qew.ledger_config import Mirror

TIMEOUT_SECONDS = 30
TOKEN_HELPER_SECONDS = 10  # how long `gh auth token` may take before it counts as absent
MAX_PAGES = 500  # a runaway guard, not a size limit: 50,000 issues at 100 a page
_TOKEN_VARIABLES = {
    "github": ("GITHUB_TOKEN", "GH_TOKEN"),
    "forgejo": ("FORGEJO_TOKEN", "GITEA_TOKEN"),
    "gitlab": ("GITLAB_TOKEN",),
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    @override
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def urllib_transport(timeout: float = TIMEOUT_SECONDS) -> Transport:
    """The real transport. It never follows redirects, so a token cannot leak to another host."""
    opener = urllib.request.build_opener(_NoRedirect)

    def send(request: HttpRequest) -> HttpResponse:
        wire = urllib.request.Request(
            request.url, data=request.body, headers=dict(request.headers), method=request.method
        )
        try:
            with opener.open(wire, timeout=timeout) as response:
                return HttpResponse(response.status, dict(response.headers), response.read())
        except urllib.error.HTTPError as error:
            with error:
                return HttpResponse(error.code, dict(error.headers), error.read())
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise ForgeUnavailable(f"{request.method} {request.url}: {error}") from None

    return send


def resolve_token(mirror: Mirror, env: Mapping[str, str]) -> str:
    """Token from the mirror's `token_env` or the forge's usual variables; GitHub also asks `gh`."""
    names: list[str] = []
    custom = mirror.options.get("token_env")
    if isinstance(custom, str):
        names.append(custom)
    names.extend(_TOKEN_VARIABLES.get(mirror.forge, ()))
    for name in names:
        if env.get(name):
            return env[name]
    if mirror.forge == "github" and "QEW_NO_GH_TOKEN" not in env:
        try:
            result = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, check=False, timeout=TOKEN_HELPER_SECONDS,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    raise ForgeAuth(f"no token for {mirror.forge} {mirror.repo}: set {' or '.join(names) or 'token_env'}")


def split_repo(mirror: Mirror) -> tuple[str, str, str]:
    """(scheme://host, owner-or-namespace path, repo) of the mirror URL; GitLab paths may nest."""
    scheme, _, rest = mirror.repo.partition("://")
    host, _, path = rest.partition("/")
    namespace, _, repo = path.rstrip("/").rpartition("/")
    return f"{scheme}://{host}", namespace, repo


def api_base(mirror: Mirror, default: str) -> str:
    """The mirror's `api` override (self-hosted, tests) or the forge's default API root."""
    override = mirror.options.get("api")
    return str(override).rstrip("/") if isinstance(override, str) else default


DEFAULT_WAIT = 60.0  # a limit answer that names no wait
SECONDARY_WAIT = 300.0  # a secondary (abuse) limit that names no wait: these blocks last minutes
MAX_WAIT = 3600.0


def wait_from(headers: Mapping[str, str]) -> float | None:
    """Seconds the forge asked for through Retry-After or an exhausted X-RateLimit-Reset, else None."""
    lowered = {name.lower(): value for name, value in headers.items()}
    try:
        if "retry-after" in lowered:
            return min(max(float(lowered["retry-after"]), 1.0), MAX_WAIT)
        if lowered.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in lowered:
            return min(max(float(lowered["x-ratelimit-reset"]) - time.time(), 1.0), MAX_WAIT)
    except ValueError:
        return DEFAULT_WAIT
    return None


@dataclass(frozen=True)
class NotModified:
    """A conditional GET answered 304: the cached copy is still current. A result, never an error."""

    headers: Mapping[str, str]


def header(headers: Mapping[str, str], name: str) -> str | None:
    """One response header by case-insensitive name."""
    lowered = name.lower()
    return next((value for key, value in headers.items() if key.lower() == lowered), None)


@dataclass
class RateWatch:
    """What this process has seen of the forge's quota: HTTP calls made, the last `x-ratelimit-remaining` an answer carried and
    the `x-ratelimit-reset` (epoch seconds) it came with."""

    calls: int = 0
    remaining: int | None = None
    reset: float | None = None


WATCH = RateWatch()  # the core REST limit; GraphQL and the rest have their own (`watch_for`)
_WATCHES: dict[str, RateWatch] = {"core": WATCH}
RESERVE = 500  # calls a background pass leaves for merges and the person at the keyboard (QEW_RATE_RESERVE overrides, 0 turns it off)


class RateReserve(ForgeUnavailable):
    """The background worker stands down: fewer calls are left than the reserve. Never retried; a later pass tries again."""


def reset_in(headers: Mapping[str, str]) -> float | None:
    """Seconds until the quota returns when the answer says none is left (`x-ratelimit-remaining: 0`), else None."""
    left, reset = header(headers, "x-ratelimit-remaining"), header(headers, "x-ratelimit-reset")
    if left is None or left.strip() != "0" or reset is None:
        return None
    try:
        return max(float(reset) - time.time(), 1.0)
    except ValueError:
        return None


def _reserve() -> int:
    raw = os.environ.get("QEW_RATE_RESERVE", "")
    return int(raw) if raw.strip().isdigit() else RESERVE


# -- where the calls go: one line per command in `.qew/forge-calls.log`, added up by `qew doctor`

CALLS_LOG = "forge-calls.log"
CALLS_KEEP_S = 2 * 3600.0
CALLS_TRIM_BYTES = 256 * 1024
_verb: list[str] = []


def account(verb: str) -> None:
    """Log this process's forge calls under `verb` when it exits (once, and only when it made any)."""
    if not _verb:
        import atexit

        atexit.register(_write_account)
    _verb[:] = [verb]


def _ledger_store() -> Path | None:
    from qew import ledger_config
    from qew.errors import QewError

    try:
        return ledger_config.load().ledger_path / ".qew"
    except QewError:
        return None


def _write_account() -> None:
    calls = {name: watch.calls for name, watch in _WATCHES.items() if watch.calls}
    store = _ledger_store() if calls and _verb else None
    if store is None or not store.is_dir():
        return
    path = store / CALLS_LOG
    line = json.dumps({"at": round(time.time(), 1), "pid": os.getpid(), "verb": _verb[0], "calls": calls}, separators=(",", ":")) + "\n"
    try:
        if path.exists() and path.stat().st_size > CALLS_TRIM_BYTES:
            recent = [row for row in path.read_text(encoding="utf-8").splitlines(keepends=True) if _fresh(row)]
            path.write_text("".join(recent), encoding="utf-8")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)  # one short append: concurrent commands do not interleave inside a line
    except OSError:
        return


def _fresh(row: str, within: float = CALLS_KEEP_S) -> bool:
    try:
        return float(json.loads(row)["at"]) > time.time() - within
    except (ValueError, KeyError, TypeError):
        return False


def calls_in_the_last_hour(store: Path) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """(calls by resource, calls by verb and resource) of the commands logged in the last hour."""
    totals: dict[str, int] = {}
    by_verb: dict[str, dict[str, int]] = {}
    try:
        rows = (store / CALLS_LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return totals, by_verb
    for row in rows:
        if not _fresh(row, 3600.0):
            continue
        entry = json.loads(row)
        for resource, count in entry["calls"].items():
            totals[resource] = totals.get(resource, 0) + int(count)
            verb = by_verb.setdefault(str(entry["verb"]), {})
            verb[resource] = verb.get(resource, 0) + int(count)
    return totals, by_verb


def watch_for(resource: str) -> RateWatch:
    """The accounting of one rate-limit resource (`core`, `graphql`, `search`): GitHub keeps separate budgets, and an answer from one
    must not overwrite what another said."""
    return _WATCHES.setdefault(resource, RateWatch())


def _resource_of(request: HttpRequest) -> str:
    return "graphql" if request.url.split("?", 1)[0].rstrip("/").endswith("/graphql") else "core"


def _noted(response: HttpResponse, resource: str = "core") -> HttpResponse:
    named = header(response.headers, "x-ratelimit-resource")
    watch = watch_for(named.strip().lower() if named else resource)
    watch.calls += 1
    left = header(response.headers, "x-ratelimit-remaining")
    if left is not None and left.strip().isdigit():
        watch.remaining = int(left)
        until = header(response.headers, "x-ratelimit-reset")
        watch.reset = float(until) if until is not None and until.strip().replace(".", "", 1).isdigit() else None
    return response


def _hold_reserve(resource: str = "core") -> None:
    """In the background worker, refuse the next call while the last answer for its resource said fewer than the reserve remain and the quota has not reset."""
    watch = watch_for(resource)
    if not os.environ.get("QEW_BACKGROUND") or watch.remaining is None or watch.reset is None:
        return
    until = watch.reset - time.time()
    if until > 0 and watch.remaining < _reserve():
        raise RateReserve(f"rate reserve: {watch.remaining} {resource} calls left, below the {_reserve()} kept for merges; the quota resets in {until:.0f}s",
                          until)


def _timed(transport: Transport, request: HttpRequest) -> HttpResponse:
    resource = _resource_of(request)
    _hold_reserve(resource)
    if not trace.enabled():
        return _noted(transport(request), resource)
    begun = time.perf_counter()
    status: object = "error"
    try:
        response = _noted(transport(request), resource)
        status = response.status
        return response
    finally:
        trace.http(request.method, request.url, status, time.perf_counter() - begun)


def call_json(
    transport: Transport,
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload: Any | None = None,
    ok: tuple[int, ...] = (200, 201, 204),
) -> tuple[Any, Mapping[str, str]]:
    """One JSON request; maps HTTP failures to the typed forge errors. Returns (json, headers)."""
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    merged = {"Accept": "application/json", **headers}
    if body is not None:
        merged["Content-Type"] = "application/json"
    return _interpret(_timed(transport, HttpRequest(method, url, merged, body)), ok)


def get_conditional(
    transport: Transport, url: str, headers: Mapping[str, str], etag: str = "",
) -> NotModified | tuple[Any, Mapping[str, str]]:
    """GET with `If-None-Match: etag` (when given): NotModified on 304, else (json, headers) like call_json."""
    sent = {"Accept": "application/json", **headers}
    if etag:
        sent["If-None-Match"] = etag
    response = _timed(transport, HttpRequest("GET", url, sent, None))
    if response.status == 304:
        return NotModified(response.headers)
    return _interpret(response, (200,))


def _resets(headers: Mapping[str, str]) -> str:
    """` (core limit; resets at 21:46Z, in about 27 min)` when the answer carries the reset of the limit it hit, else nothing."""
    reset = header(headers, "x-ratelimit-reset")
    if reset is None or not reset.strip().isdigit():
        return ""
    named = header(headers, "x-ratelimit-resource")
    at = time.strftime("%H:%MZ", time.gmtime(int(reset)))
    minutes = max(int((int(reset) - time.time()) / 60) + 1, 1)
    return f" ({named.strip().lower() if named else 'primary'} limit; resets at {at}, in about {minutes} min)"


def _interpret(response: HttpResponse, ok: tuple[int, ...]) -> tuple[Any, Mapping[str, str]]:
    if response.status in ok:
        if not response.body:
            return None, response.headers
        try:
            return json.loads(response.body.decode("utf-8")), response.headers
        except (ValueError, UnicodeError):
            raise ForgeRejected(response.status, "response is not valid JSON") from None
    detail = response.body.decode("utf-8", "replace")[:300]
    wait = wait_from(response.headers)
    limited = response.status == 429 or (response.status == 403 and (wait is not None or "rate limit" in detail.lower()))
    if limited:
        if "secondary rate limit" in detail.lower():
            raise ForgeSecondaryLimit(f"HTTP {response.status}: {detail}", wait if wait is not None else SECONDARY_WAIT)
        raise ForgeUnavailable(f"HTTP {response.status}: {detail}{_resets(response.headers)}", wait if wait is not None else DEFAULT_WAIT)
    if response.status in (401, 403):
        raise ForgeAuth(f"HTTP {response.status}: {detail}")
    if response.status >= 500:
        raise ForgeUnavailable(f"HTTP {response.status}: {detail}", wait or 0.0)
    raise ForgeRejected(response.status, detail)
