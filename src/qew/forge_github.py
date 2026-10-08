"""GitHub backend (stub: every capability is unsupported until implemented)."""

from typing import Mapping, Tuple

from qew.forge import Forge, Transport, Unsupported
from qew.ledger_config import Mirror


def api_roots(mirror: Mirror) -> Tuple[str, str]:
    raise Unsupported("api_roots")


class GitHub(Forge):
    name = "github"

    def __init__(self, mirror: Mirror, transport: Transport, env: Mapping[str, str]) -> None:
        self.mirror = mirror
