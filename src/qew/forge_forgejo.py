"""Forgejo/Gitea backend (RED stub): every capability is still unimplemented."""

from typing import Mapping

from qew.forge import Forge, Transport
from qew.ledger_config import Mirror


class Forgejo(Forge):
    name = "forgejo"

    def __init__(self, mirror: Mirror, transport: Transport, env: Mapping[str, str]) -> None:
        self.mirror = mirror
        self.transport = transport
        self.env = env
