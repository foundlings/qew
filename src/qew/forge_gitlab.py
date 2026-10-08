"""GitLab backend stub: every capability is `Unsupported` until implemented."""

from typing import Mapping

from qew.forge import Forge, Transport
from qew.ledger_config import Mirror


class GitLab(Forge):
    name = "gitlab"

    def __init__(self, mirror: Mirror, transport: Transport, env: Mapping[str, str]) -> None:
        self.mirror = mirror
        self.transport = transport
        self.env = env
