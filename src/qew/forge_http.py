"""Shared HTTP plumbing for backends: the real transport, token lookup, JSON calls."""

import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any, List, Mapping, Optional, Tuple

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
            return HttpResponse(error.code, dict(error.headers), error.read())
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise ForgeUnavailable(f"{request.method} {request.url}: {error}") from None

    return send


def resolve_token(mirror: Mirror, env: Mapping[str, str]) -> str:
    """Token from the mirror's `token_env` or the forge's usual variables; GitHub also asks `gh`."""
    names: List[str] = []
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


def split_repo(mirror: Mirror) -> Tuple[str, str, str]:
    """(scheme://host, owner-or-namespace path, repo) of the mirror URL; GitLab paths may nest."""
    scheme, _, rest = mirror.repo.partition("://")
    host, _, path = rest.partition("/")
    namespace, _, repo = path.rstrip("/").rpartition("/")
    return f"{scheme}://{host}", namespace, repo


def api_base(mirror: Mirror, default: str) -> str:
    """The mirror's `api` override (self-hosted, tests) or the forge's default API root."""
    override = mirror.options.get("api")
    return str(override).rstrip("/") if isinstance(override, str) else default


def call_json(
    transport: Transport,
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload: Optional[Any] = None,
    ok: Tuple[int, ...] = (200, 201, 204),
) -> Tuple[Any, Mapping[str, str]]:
    """One JSON request; maps HTTP failures to the typed forge errors. Returns (json, headers)."""
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    merged = {"Accept": "application/json", **headers}
    if body is not None:
        merged["Content-Type"] = "application/json"
    response = transport(HttpRequest(method, url, merged, body))
    if response.status in ok:
        if not response.body:
            return None, response.headers
        try:
            return json.loads(response.body.decode("utf-8")), response.headers
        except (ValueError, UnicodeError):
            raise ForgeRejected(response.status, "response is not valid JSON") from None
    detail = response.body.decode("utf-8", "replace")[:300]
    if response.status in (401, 403):
        raise ForgeAuth(f"HTTP {response.status}: {detail}")
    if response.status == 429 or response.status >= 500:
        raise ForgeUnavailable(f"HTTP {response.status}: {detail}")
    raise ForgeRejected(response.status, detail)
