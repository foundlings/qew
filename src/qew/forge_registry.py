"""Build the backend for a mirror target."""

import importlib
import os
from typing import cast
from collections.abc import Mapping

from qew.errors import QewError
from qew.forge import Forge, Transport
from qew.ledger_config import Mirror

_BACKENDS = {
    "github": ("qew.forge_github", "GitHub"),
    "forgejo": ("qew.forge_forgejo", "Forgejo"),
    "gitlab": ("qew.forge_gitlab", "GitLab"),
}
ALIASES = {"gitea": "forgejo"}


def canonical(forge: str) -> str:
    return ALIASES.get(forge, forge)


def build(mirror: Mirror, transport: Transport | None = None, env: Mapping[str, str] | None = None) -> Forge:
    forge = canonical(mirror.forge)
    if forge not in _BACKENDS:
        raise QewError(f"unknown forge `{mirror.forge}`; known: {', '.join(sorted((*_BACKENDS, *ALIASES)))}", 1)
    module, name = _BACKENDS[forge]
    try:
        backend = getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError):
        raise QewError(f"the {forge} backend is not available in this build", 1) from None
    from qew.forge_http import TIMEOUT_SECONDS, urllib_transport  # the HTTP stack loads only when a forge is built

    timeout = mirror.options.get("timeout", TIMEOUT_SECONDS)
    environment = dict(os.environ if env is None else env)
    if transport is None:  # only the real HTTP transport retries; a transport handed in is used as given
        from qew import forge_retry

        retries, max_wait, base = forge_retry.settings(mirror.options, environment.get("QEW_RETRY_BASE_S", ""))
        transport = forge_retry.retrying(urllib_transport(float(cast(str | float, timeout))), retries, max_wait, base,
                                         rate=forge_retry.rate_settings(mirror.options, environment))
    return cast(Forge, backend(mirror, transport, environment))
