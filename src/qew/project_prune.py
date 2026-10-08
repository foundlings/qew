"""`qew project prune --from OWNER/REPO`: remove another repository's cards from a shared board."""

from typing import Callable, Optional

from qew import forge_registry
from qew.forge import Forge
from qew.ledger_config import Config, Mirror


def prune(config: Config, wanted: Optional[str], source: str, build: Callable[[Mirror], Forge] = forge_registry.build) -> str:
    return ""
