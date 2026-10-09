"""Build the backend for a mirror target."""

import importlib
import os
from typing import cast
from collections.abc import Mapping

from qew.errors import QewError
from qew.forge import Forge, Transport
from qew.forge_http import TIMEOUT_SECONDS, urllib_transport
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
    timeout = mirror.options.get("timeout", TIMEOUT_SECONDS)
    return cast(Forge, backend(mirror, transport or urllib_transport(float(cast(str | float, timeout))), dict(os.environ if env is None else env)))
