"""Shared HTTP plumbing for backends: the real transport, token lookup, JSON calls."""

from dataclasses import dataclass
import json
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, override
from collections.abc import Mapping

from qew import trace
from qew.forge import (
    ForgeAuth,
    ForgeRejected,
    ForgeUnavailable,
    HttpRequest,
    HttpResponse,
    Transport,
)
from qew.ledger_config import Mirror

TIMEOUT_SECONDS = 30
TOKEN_HELPER_SECONDS = 10  # how long `gh auth token` may take before it counts as absent
MAX_PAGES = 50
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


def _timed(transport: Transport, request: HttpRequest) -> HttpResponse:
    if not trace.enabled():
        return transport(request)
    begun = time.perf_counter()
    status: object = "error"
    try:
        response = transport(request)
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
        if wait is None and "secondary rate limit" in detail.lower():
            wait = SECONDARY_WAIT
        raise ForgeUnavailable(f"HTTP {response.status}: {detail}", wait if wait is not None else DEFAULT_WAIT)
    if response.status in (401, 403):
        raise ForgeAuth(f"HTTP {response.status}: {detail}")
    if response.status >= 500:
        raise ForgeUnavailable(f"HTTP {response.status}: {detail}", wait or 0.0)
    raise ForgeRejected(response.status, detail)
